"""The daily call-of-record cron (M2b) — the platform feeding itself.

Before the per-thesis loop, the pass first refreshes the two SHARED call-logic inputs so those calls read
FRESH data (Band-02): the SPY/IWM benchmark tape (``ingest_benchmarks`` → ``benchmark_rs``) and every
basket's quarterly revenue (``ingest_fundamentals`` → ``revenue_acceleration``). Both legs live in
``run_daily_pass`` alongside the SPAC-radar leg and share its discipline — own connection, lazy import,
``allow_live``-gated, fail-open (a refresh fault is a passenger, it never fails the call cron).

Once a day, for every thesis (tenant intrinsic per-thesis):
  1. refresh its back-half facts — ``ingest_thesis`` (incremental + fail-visible; M2a);
  2. assemble TODAY's call WITHOUT writing — ``call_for_thesis(asof=today, known_at=now, record=False)``;
  3. append the call-of-record ONLY if it changed — ``calls_repo.record_if_changed``.

Discipline:
- **Per-thesis isolation** — each thesis is its own unit; one thesis's failure is captured + skipped,
  never fatal to the run (the cron must finish the rest).
- **Idempotent** — a same-day re-run on unchanged facts appends ZERO rows; a genuine change appends EXACTLY
  ONE new versioned row (``record_if_changed``). Safe to re-run / catch up a missed day.
- **No-lookahead** — ``asof=today``, ``known_at=now`` (never backdated).
- **Option B** — it ingests FACTS and appends the call-of-record (the write-only log); it builds NO
  read-serving signal/score cache. Calls still re-derive on read.
- **Scoreboard-ready, not coupled** — one clean versioned row per (thesis, day); same-day re-runs collapse
  via ``latest_for_thesis``' DISTINCT ON. No Scoreboard code here.

    python -m pipeline.daily                 # asof=today, live ingest
    python -m pipeline.daily --asof 2026-06-10 --no-live
    python -m pipeline.daily --catch-up --asof 2026-09-08   # the sidecar's late-wake / boot catch-up:
                                                            # a no-op if a LIVE pass for that asof already
                                                            # ran at/after that night's RUN_AT
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg

from db.session import connect
from domain.enums import State
from domain.market_time import market_today, market_tz
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from notify import ArmedName, HealthEvent, Notifier, TransitionEvent, get_notifier
from pipeline.call_for_thesis import call_for_thesis
from pipeline.cron_run_log import already_ran_live, write_cron_run_log
from pipeline.ingest_thesis import NameResult, ingest_thesis
from pipeline.schedule import parse_run_at
from repositories import calls_repo, thesis_repo
from securities import master

_log = logging.getLogger(__name__)


@dataclass
class ThesisRunResult:
    """Per-thesis outcome. ``recorded``: True = a new call-of-record was appended, False = unchanged (no
    row), None = the call step failed (see ``error``). ``transition``: the state/verdict move vs the
    PRIOR as-of's call-of-record (None = no move — the overwhelmingly common, quiet case)."""

    thesis_id: UUID
    name: str
    ingested: list[NameResult] = field(default_factory=list)
    recorded: bool | None = None
    transition: str | None = None
    error: str | None = None
    # EDGAR network pulls during this thesis's ingest — the FREEZE DETECTOR. A frozen index and a healthy
    # nothing-filed night produce identical fact tallies (0 appended); this is the one number that differs.
    # 0 across the whole run = the cache never refreshed = the R1 freeze, visible instead of hiding behind
    # a plausible "quiet day". Recorded into the cron run log (R3); R4 pages on it.
    edgar_fetches: int = 0
    # R2a — why the call-of-record was WITHHELD (not recorded), or None if it was recorded/attempted. A run
    # that didn't meaningfully refresh has no business writing the log of record: "no-live" (a cache-only dev
    # run, Source A) or "total ingest failure" (the ingest raised, or EVERY name errored, Source C). A healthy
    # OR partial run still records (the partial one marked via the calls ingest_fresh column, R2b).
    withheld_reason: str | None = None


def run_daily(
    conn: psycopg.Connection,
    *,
    asof: date | None = None,
    known_at: datetime | None = None,
    allow_live: bool = True,
    force_refresh: bool = True,
    user_agent: str | None = None,
    notifier: Notifier | None = None,
) -> list[ThesisRunResult]:
    """Run the daily pass over every thesis. ``asof`` defaults to today, ``known_at`` to now (a live read).
    Returns one ``ThesisRunResult`` per thesis. Never raises for a single thesis — failures are captured.

    ``force_refresh`` defaults to **True**: the daily path is recurring, so it re-pulls fresh bars (bypassing
    a stale cache hit) — otherwise the cron would re-ingest the same frozen cache every day and never see a
    new bar. It threads to the price source (``eod_loader.fetch_eod``).
    """
    asof = asof or market_today()
    notifier = notifier or get_notifier()
    # The canonical-primary health guard: a master with multi-row CIKs but ZERO is_primary flags resolves
    # every multi-sibling CIK to an ARBITRARY row (warrant / preferred / OTC foreign ordinary) — and nothing
    # errors, so the state is invisible unless something says it. The cron is the daily surface; the line
    # prints only in the broken state (loudness marks the exception) and names the one-command fix.
    for gap in master.primary_flag_gaps(conn):
        if gap["flagged_rows"] == 0:
            print(
                f"WARNING: tenant {gap['tenant_id']}: {gap['multi_row_ciks']} multi-row CIKs but ZERO "
                "is_primary flags — CIK->security resolution is picking ARBITRARY siblings; "
                "run `python -m pipeline.populate_master --live` to stamp the canonical primaries"
            )
    out: list[ThesisRunResult] = []
    for thesis in thesis_repo.list_all(conn):
        res = ThesisRunResult(thesis_id=thesis.id, name=thesis.name)
        # (1) refresh facts — ingest_thesis already isolates per-name; wrap defensively so even a thesis-level
        # failure (e.g. a malformed thesis) is captured, not fatal. A fact failure does NOT block the call.
        # a fresh client PER THESIS so its live_fetches count is that thesis's own (the freeze detector)
        #
        # G1 — THE RECURRING TTL (five minutes): A RECURRING PASS MUST NEVER READ A DAYTIME-WARM MUTABLE
        # KEY. The EDGAR cache's default 12h TTL is an INTERACTIVE dial (a Workbench re-draft within the day
        # is free); on the nightly path it was a blind spot. Every filing leg enumerates from ONE mutable
        # document per company (``submissions/CIK<10>.json``), the cron container shares the backend's
        # ``/data`` cache volume, and that file's 12h clock starts when IT was fetched — so any daytime read
        # (Admin "Run daily now", a boot catch-up, the on-promote ingest, a Workbench identity/extract pull)
        # left the index fresh enough that the 22:30 pass served it off disk and was structurally blind to
        # every filing accepted after that daytime fetch (Form 4 buys and sells, 8-Ks, 13Ds — ingested by the
        # NEXT pass, so an arm or a risk veto they caused is dated a night late). "Just run it before 10:30"
        # is not a rule to live by: the stamp is per COMPANY and a full pass takes up to an hour, so a run
        # STARTED early still leaves the companies it reaches late warm.
        # FIVE MINUTES, NOT ZERO, for a MEASURED reason: ``ingest_thesis``'s three filing legs re-read this
        # company's index milliseconds apart and those reads must stay free — at ttl=0 a file written
        # milliseconds ago is already stale, which costs 3 live fetches for 3 back-to-back reads instead of
        # 1, tripling the per-company index cost for zero freshness gain. Nothing can be filed in the five
        # minutes before 22:30 (EDGAR accepts 06:00-22:00 ET) and no daytime warmth survives five minutes.
        # Full rationale + the accepted residual: ``ingest.edgar.client.RECURRING_CACHE_TTL_S``.
        # This is the per-CLIENT dial on the recurring path — the exact parallel of ``force_refresh=True``
        # for prices (see this function's docstring) — NOT a per-call flag threaded through callers (R1 /
        # #196: "the boolean wearing a timedelta" is how the ~11-day insider freeze happened). Immutable
        # ``forms/*`` keys still cache forever, so the cost is ONE index fetch per company per pass — which
        # the nightly run already paid whenever it ran outside the TTL — and the SEC rate limiter bounds it.
        # ``python -m pipeline.ingest_thesis`` run standalone keeps the 12h default (an operator-initiated,
        # interactive path); ``ingest_fundamentals`` keeps it deliberately (see its construction site).
        edgar_client = EdgarClient(
            allow_live=allow_live, user_agent=user_agent, cache_ttl_s=RECURRING_CACHE_TTL_S
        )
        try:
            res.ingested = ingest_thesis(
                conn,
                thesis.id,
                allow_live=allow_live,
                force_refresh=force_refresh,
                user_agent=user_agent,
                edgar_client=edgar_client,
            )
        except Exception as e:  # noqa: BLE001 — one thesis's ingest never aborts the cron
            conn.rollback()
            res.error = f"ingest: {e}"
        # capture the count even on a thesis-level failure — a mid-ingest raise still made real network
        # pulls, and "0 fetches" must mean the freeze, not "we bailed before the counter was read"
        res.edgar_fetches = edgar_client.live_fetches
        # R2a — THE RECORDING GATE: a run that didn't meaningfully refresh must not write the log of record.
        # TWO conditions, closing two do-nothing shapes that a fact-count test can't (a --no-live run over a
        # warm cache is fast, clean, appends nothing, and does NOT error):
        #   - no-live (Source A) → allow_live False. A cache-only dev run has no business recording, period.
        #   - total ingest failure (Source C) → the ingest raised (res.error), OR names existed and EVERY one
        #     errored. NOT "appended 0" — a current thesis appends 0 on a HEALTHY run and MUST still record.
        # A partial failure (some errored, some clean) DOES record, marked via ingest_fresh (R2b).
        ingest_errors = sum(1 for x in res.ingested if x.error)
        if res.error:
            res.withheld_reason = "total ingest failure"  # Source C (the ingest raised)
        elif not allow_live:
            res.withheld_reason = "no-live"  # Source A
        elif res.ingested and ingest_errors == len(res.ingested):
            res.withheld_reason = "total ingest failure"  # Source C (every name errored)
        if res.withheld_reason is not None:
            # the missing `continue`: skip assemble/notify/record entirely — a call built on a failed or
            # cache-only ingest is exactly the record the freeze investigation found masquerading as real
            out.append(res)
            continue
        # (2)+(3) assemble today's call WITHOUT writing, then append only if it changed.
        try:
            card = call_for_thesis(conn, thesis.id, asof, known_at=known_at, record=False)
            # (4) TRANSITION DETECTION (the notify seam): compare state/verdict against the PRIOR
            # as-of's call-of-record — the material-change line (trigger churn / provenance noise
            # version the log via record_if_changed without being transitions; a state or verdict
            # MOVE is what an operator would want to be told about). First-ever call = no prior =
            # no event. Delivery is the adapter's concern (v1: a loud log line).
            prior = next(
                (c for c in calls_repo.latest_for_thesis(conn, thesis.id) if c.asof < asof), None
            )
            if prior is not None and (
                prior.state is not card.state or prior.verdict is not card.verdict
            ):
                # Company-level detail for the compact armed push: resolve each armed member to its
                # ticker + its distinct entry-trigger kinds. FAIL-OPEN in its OWN try/except — the
                # notifier.notify below runs inside the shared try/except that ROLLS BACK the call-of-
                # record on any exception, so this DB-touching enrichment must NEVER raise. It degrades
                # to an empty payload (the notifier then falls back to the thesis-only line). Only built
                # on a → armed transition (armed_members is empty otherwise).
                armed_payload: tuple[ArmedName, ...] = ()
                if card.state is State.ARMED and card.armed_members:
                    try:
                        sids = {m.security_id for m in card.armed_members}
                        # SAVEPOINT: isolate the enrichment read from the record's transaction. The outer
                        # conn is transactional (record_if_changed + conn.commit below), so a query-SPECIFIC
                        # fault on this SELECT — a statement timeout, a lock, a serialization failure, a
                        # transient blip — would otherwise poison the outer txn and cost this thesis its
                        # call-of-record even though the DB is writable. conn.transaction() rolls back to
                        # HERE on any fault and re-raises into the except → the outer txn stays clean for
                        # the record write. The tuple-build stays OUTSIDE the savepoint (pure Python, no DB).
                        with conn.transaction():
                            tickers = master.tickers_for(conn, sids, tenant_id=thesis.tenant_id)
                        armed_payload = tuple(
                            ArmedName(
                                ticker=tickers.get(m.security_id),
                                # distinct, order-preserving (headline kind first)
                                trigger_kinds=tuple(
                                    dict.fromkeys(t.kind.value for t in m.triggers)
                                ),
                                grade=(m.entry_grade.value if m.entry_grade else None),
                            )
                            for m in card.armed_members
                        )
                    except Exception:  # noqa: BLE001 — enrichment never risks the record
                        _log.warning(
                            "armed-notify enrichment failed for %s (fail-open)",
                            thesis.id,
                            exc_info=True,
                        )
                        armed_payload = ()
                evt = TransitionEvent(
                    thesis_id=thesis.id,
                    thesis_name=thesis.name,
                    asof=asof,
                    from_state=prior.state.value,
                    to_state=card.state.value,
                    from_verdict=prior.verdict.value,
                    to_verdict=card.verdict.value,
                    armed=armed_payload,
                )
                notifier.notify(evt)
                res.transition = evt.label
            # R2b — stamp the run's ingest health on the recorded row (PROVENANCE, off the card). We reach
            # here only on a healthy OR partial ingest; ingest_fresh False marks the partial one so the
            # Scoreboard can discount a call resting on names that failed to refresh.
            res.recorded = calls_repo.record_if_changed(
                conn,
                card,
                thesis.tenant_id,
                ingest_fresh=(ingest_errors == 0),
                ingest_errors=ingest_errors,
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — one thesis's call never aborts the cron
            conn.rollback()
            res.error = (f"{res.error}; " if res.error else "") + f"call: {e}"
        out.append(res)
    return out


def assess_health(
    results: list[ThesisRunResult],
    *,
    asof: date,
    allow_live: bool,
    freeze_check: bool = True,
    benchmark_errors: int = 0,
    benchmark_leg_failed: bool = False,
) -> HealthEvent | None:
    """R4 — the run's pageable health, or None when the run was clean (loudness marks the exception). Unhealthy
    = a FREEZE (a live run whose EDGAR fetches summed to ZERO across present theses — the R1 cache-never-
    refreshed signal), any WITHHELD call (no-live / total ingest failure), any thesis ERROR, or a failed
    BENCHMARK refresh. Pure over the collected results, so it is unit-testable without a DB; ``main`` emits it
    through the notifier.

    ``freeze_check=False`` SKIPS the frozen predicate only (withheld / errored still page): a ``--catch-up``
    pass runs inside the EDGAR cache's 12h TTL right after the scheduled run, so ~0 fetches is what a
    CORRECT catch-up looks like — the known R4 false-positive (FEED_LOOP.md "Known gaps", option B). The
    scheduled run and the Admin "Run daily now" keep the default ``True``. (Since G1 the per-thesis filing
    legs carry the five-minute RECURRING TTL, so for the scheduled pass and the Admin trigger 0 fetches is a
    genuine freeze. The exemption still EARNS its keep: a SECOND pass started within five minutes of another
    legitimately reads the first's cache for the companies it reached last.)

    G4 — the BENCHMARK refresh legs are PASSENGERS in ``run_daily_pass`` (fail-open, own connection), and
    their faults used to reach stdout only: a SPY/IWM tape that did not refresh silently degrades
    ``benchmark_rs`` for every call that night, which is exactly the shape of failure this pager exists to
    make visible. Two distinct counts because they are different news: ``benchmark_errors`` = individual
    benchmark pulls that failed (1 of 2 = a partly stale tape), ``benchmark_leg_failed`` = the leg itself
    raised before producing per-benchmark results (no refresh happened at all). Both are passed IN rather
    than derived, because the legs run outside ``run_daily`` and this function stays pure.
    """
    theses = len(results)
    # split withheld by its ACTUAL reason — a --no-live dev run is benign, a total failure is an alarm; a page
    # must report which one, not a generic "(no-live / total ingest failure)" that reads as a fault either way
    withheld_no_live = sum(1 for r in results if r.withheld_reason == "no-live")
    withheld_failure = sum(1 for r in results if r.withheld_reason == "total ingest failure")
    errored = sum(1 for r in results if r.error)
    edgar_fetches = sum(r.edgar_fetches for r in results)
    frozen = freeze_check and allow_live and theses > 0 and edgar_fetches == 0
    if not (
        withheld_no_live
        or withheld_failure
        or errored
        or frozen
        or benchmark_errors
        or benchmark_leg_failed
    ):
        return None  # healthy — no page
    return HealthEvent(
        asof=asof,
        theses=theses,
        withheld_no_live=withheld_no_live,
        withheld_failure=withheld_failure,
        errored=errored,
        edgar_fetches=edgar_fetches,
        frozen=frozen,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
    )


@dataclass
class DailyPassOutcome:
    """One COMPLETED daily pass: the per-thesis results plus the run metadata the artifact carries —
    what the admin "run now" job needs to shape its poll result exactly like a parsed run log.
    ``log_path`` is the written run-of-record artifact (or ``None`` — the write is fail-open). ``catch_up``
    = the pass was a ``--catch-up`` (recorded on the artifact; its freeze page is skipped).

    ``benchmark_errors`` / ``benchmark_leg_failed`` (G4) carry the SHARED-INPUT refresh leg's outcome off
    the pass, because the admin "run now" job rebuilds its poll payload from THIS object
    (``app/routers/admin.py``) — without them that payload would disagree with the artifact on disk about
    whether the night was healthy."""

    results: list[ThesisRunResult]
    asof: date
    allow_live: bool
    started_at: datetime
    finished_at: datetime
    log_path: Path | None
    catch_up: bool = False
    benchmark_errors: int = 0
    benchmark_leg_failed: bool = False


def run_daily_pass(
    *,
    asof: date | None = None,
    allow_live: bool = True,
    notifier: Notifier | None = None,
    catch_up: bool = False,
) -> DailyPassOutcome:
    """The cron's FULL unit of work as ONE callable: connect → ``run_daily`` → write the run-of-record
    artifact (R3, fail-open) → emit the health page (R4). Extracted from ``main`` UNCHANGED so the admin
    "Run daily now" trigger fires the exact pass the nightly cron does — a manual run writes the same
    artifact (it shows in the run history) and pages through the same health seam. ``main`` keeps the CLI
    concerns only (args, the R6 catch-up guard, the printed report + exit code).

    ``catch_up`` (the CLI's ``--catch-up``, threaded by ``main``) changes exactly two things: the artifact
    records it, and the R4 FREEZE predicate is skipped (``assess_health(freeze_check=False)`` — a catch-up
    runs inside the EDGAR 12h TTL and legitimately fetches ~0; withheld / errored still page). The ingest,
    the recording gate, and the call-of-record are byte-identical to a scheduled pass.

    G4 — the benchmark refresh leg's outcome is now CARRIED, not just printed: its counts go into the
    run-of-record artifact and into ``assess_health``, so a night that ran ``benchmark_rs`` on a stale
    SPY/IWM tape pages like an errored thesis instead of scrolling past on stdout."""
    asof = asof or market_today()
    notifier = notifier or get_notifier()
    started_at = datetime.now(timezone.utc)
    # G4 — the benchmark leg's outcome, initialized CLEAN so a --no-live pass (which skips the leg) reports
    # nothing to page. `benchmark_errors` counts individual benchmark pulls that failed; the separate
    # `benchmark_leg_failed` marks the leg raising before it produced any per-benchmark result, because "1
    # of 2 benchmarks is stale" and "nothing refreshed at all" are different news and a single count cannot
    # say which. The fundamentals leg keeps stdout-only reporting: a quarterly series tolerates a day.
    benchmark_errors = 0
    benchmark_leg_failed = False
    # Band-02 shared-input refresh — run BEFORE run_daily so the per-thesis calls read FRESH data: the
    # SPY/IWM benchmark tape (benchmark_rs) and each basket's quarterly revenue (revenue_acceleration).
    # Both legs mirror the SPAC-radar leg below — OWN connection + lazy import + fail-open — and keep their
    # OWN connections so a refresh can NEVER touch run_daily's per-thesis edgar_fetches freeze counter or
    # the recording gate. A refresh fault is a passenger, never the driver: print loud, keep going; a
    # benchmarks fault must not skip fundamentals (separate try per leg). Skipped on --no-live (a cache-only
    # dev run can't refresh/record; `python -m pipeline.ingest_*` is the manual cache-only path).
    if allow_live:
        try:  # (a) benchmarks first
            from pipeline.ingest_benchmarks import ingest_benchmarks

            # force_refresh=True: the recurring path MUST bypass a stale cache HIT and re-pull fresh SPY/IWM
            # bars — else the tape freezes and benchmark_rs degrades silently (the #72 lesson; the same
            # reason run_daily force-refreshes ingest_thesis).
            bench_conn = connect()
            try:
                bench_results = ingest_benchmarks(bench_conn, allow_live=True, force_refresh=True)
            finally:
                bench_conn.close()
            bench_bars = sum(r.bars_appended for r in bench_results)
            bench_errs = [r.error for r in bench_results if r.error]
            benchmark_errors = len(bench_errs)  # G4 — carried to the artifact + the health page
            print(f"benchmarks refresh: +{bench_bars} bars, {len(bench_results)} benchmarks")
            for err in bench_errs:  # loud only when nonzero (loudness marks the exception)
                print(f"  benchmarks refresh ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — a refresh leg is a passenger, never the driver
            benchmark_leg_failed = True  # G4 — still fail-open for the RUN, but no longer silent
            print(f"WARNING: benchmarks refresh leg failed: {e}")
        try:  # (b) then fundamentals — bare = every basket
            from pipeline.ingest_fundamentals import ingest_fundamentals

            # NO force_refresh/ttl flag: EDGAR cache freshness is key-classed / default-refresh
            # (companyfacts re-fetches on a 12h TTL when allow_live), NOT a per-call boolean (the #196
            # lesson — "the boolean wearing a timedelta" is how the ~11-day insider freeze happened).
            fund_conn = connect()
            try:
                fund_results = ingest_fundamentals(fund_conn, allow_live=True)
            finally:
                fund_conn.close()
            fund_quarters = sum(r.appended for r in fund_results)
            fund_errs = [r.error for r in fund_results if r.error]
            print(f"fundamentals refresh: +{fund_quarters} quarters, {len(fund_results)} names")
            for err in fund_errs:  # loud only when nonzero
                print(f"  fundamentals refresh ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — a refresh leg is a passenger, never the driver
            print(f"WARNING: fundamentals refresh leg failed: {e}")
    conn = connect()
    try:
        results = run_daily(conn, asof=asof, allow_live=allow_live, notifier=notifier)
    finally:
        conn.close()
    finished_at = datetime.now(timezone.utc)
    # R3 — the cron's run-of-record, so the next freeze is noticed by the platform, not by eye. Written
    # AFTER the run from the collected results (write-only, no DB); fail-open, so it never fails the cron.
    log_path = write_cron_run_log(
        results,
        asof=asof,
        allow_live=allow_live,
        started_at=started_at,
        finished_at=finished_at,
        catch_up=catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
    )
    # R4 — the DURABLE page: a freeze / withheld / errored run alerts through the notifier (Slack when
    # configured, else a loud log line). Healthy runs are silent. This is what makes the platform notice its
    # own blindness — the gap that let R1 hide 11+ days. Fail-open (notify_health never raises). A catch-up
    # skips the FREEZE predicate only (it runs inside the EDGAR TTL — ~0 fetches is correct there). G4: the
    # benchmark leg's counts page here too, and they are on the artifact above so the admin history
    # re-derives the SAME verdict this run paged (never a green row over a night that alerted).
    health = assess_health(
        results,
        asof=asof,
        allow_live=allow_live,
        freeze_check=not catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
    )
    if health is not None:
        notifier.notify_health(health)
    # The SPAC shell sweep (facts-only blank-check enrichment) — BEFORE the radar leg, so the same
    # night's run_spac_radar (which reads known_shell_ciks at its start) sees freshly-enriched
    # shells and collects their 8-K/proxy/25 events. The units-U / "Acquisition Corp" pattern only
    # SELECTS which CIKs to fetch; the SEC sicDescription is the sole shell-or-not authority (#3 —
    # the existing spacClass classifier does the flagging). Same discipline as the legs above: own
    # connection + lazy import + fail-open; a sweep fault never fails the call cron. Mondays add
    # the de-SPAC re-enrich (reenrich=True re-pulls the current Blank Checks set, least-recently-
    # enriched first) so a completed merger's SIC flip stops the flag within a week — the gate must
    # be a WEEKDAY because the cron fires Mon-Fri only (pipeline/schedule.py). Skipped on --no-live
    # (the recording-gate philosophy; `python -m pipeline.spac_sweep --live` is the manual path).
    if allow_live:
        try:
            from radar.shell_sweep import run_shell_sweep

            sweep_conn = connect()
            try:
                sw = run_shell_sweep(sweep_conn, allow_live=True, reenrich=(asof.weekday() == 0))
            finally:
                sweep_conn.close()
            print(f"shell sweep: {sw.summary}")
            for err in sw.errors:
                print(f"  shell sweep ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — the sweep is a passenger, never the driver
            print(f"WARNING: shell sweep leg failed: {e}")
    # The SPAC Radar's universe-level leg (docs/temp/spac-radar-options.md, slice 1) — deliberately
    # OUTSIDE run_daily: its own connection + EdgarClient, so it can never pollute the per-thesis
    # edgar_fetches freeze counters or the recording gate. Fail-open: a radar fault never fails the
    # call cron (printed loud, not raised). Skipped on --no-live (the recording-gate philosophy — a
    # cache-only run can't scan new indexes; the CLI `python -m pipeline.spac_radar` is the manual path).
    if allow_live:
        try:
            from radar.spac import run_spac_radar

            radar_conn = connect()
            try:
                rr = run_spac_radar(radar_conn, until=asof, days=3, allow_live=True)
            finally:
                radar_conn.close()
            print(f"SPAC radar: {rr.summary}")
            for err in rr.errors:
                print(f"  SPAC radar ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — the radar is a passenger, never the driver
            print(f"WARNING: SPAC radar leg failed: {e}")
    return DailyPassOutcome(
        results=results,
        asof=asof,
        allow_live=allow_live,
        started_at=started_at,
        finished_at=finished_at,
        log_path=log_path,
        catch_up=catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
    )


def _report(results: list[ThesisRunResult]) -> int:
    """Print a per-thesis summary; return the number that errored (the process exit signal)."""
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    withheld = [r for r in results if r.withheld_reason]
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            mark = f"ERROR: {r.error}"
        elif r.withheld_reason:
            mark = f"WITHHELD ({r.withheld_reason}) — call NOT recorded"
        elif r.recorded:
            mark = "call-of-record APPENDED"
        else:
            mark = "unchanged (no new row)"
        facts = sum(
            x.form4_appended + x.price_bars_appended + x.fund_shares_appended for x in r.ingested
        )
        skipped = sum(x.form4_skipped for x in r.ingested)
        sk = f" · {skipped} form4 skipped" if skipped else ""  # loudness marks the exception
        rv = sum(x.price_bars_reversioned for x in r.ingested)
        rvs = (
            f" · {rv} bars RE-VERSIONED (restated)" if rv else ""
        )  # a split re-base — loud only then
        frv = sum(x.fund_shares_reversioned for x in r.ingested)
        frvs = (
            f" · {frv} fund shares RE-VERSIONED (restated)" if frv else ""
        )  # a corrected same-day count — loud only then
        print(f"  {r.name}: +{facts} facts{sk}{rvs}{frvs} · {mark}")
    # transitions get their own LOUD block — and only when there ARE any (loudness marks the
    # exception; the common all-quiet night prints nothing here)
    transitions = [r.transition for r in results if r.transition]
    if transitions:
        print("TRANSITIONS:")
        for t in transitions:
            print(f"  {t}")
    wh = f" · {len(withheld)} withheld" if withheld else ""  # loud only when it happens
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged{wh} · "
        f"{len(transitions)} transitions · {len(errored)} errored"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Daily cron: refresh facts + append the call-of-record per thesis."
    )
    p.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today)")
    p.add_argument("--no-live", action="store_true", help="cache-only ingest (no network)")
    p.add_argument(
        "--catch-up",
        action="store_true",
        help="R6: run only if a LIVE pass for this asof that STARTED at/after that night's RUN_AT hasn't "
        "already run (the sidecar's boot catch-up of the last expected night, so a host that was off at "
        "RUN_AT self-heals when it comes back, and its late-wake catch-up of the nights a long sleep "
        "skipped); a no-op otherwise. A pre-open manual pass for the same asof does NOT count (it lacks "
        "the night's close). A catch-up runs inside the EDGAR cache TTL, so its ~0-fetch freeze page is "
        "skipped (withheld / errored still page).",
    )
    args = p.parse_args(argv)
    asof = date.fromisoformat(args.asof) if args.asof else market_today()
    allow_live = not args.no_live

    # R6 — catch-up guard: a `--catch-up` invocation is a no-op when a LIVE pass for this asof that STARTED
    # at/after that night's RUN_AT already ran. The sidecar fires `--catch-up` on boot for the last expected
    # night (Flag 6: a rebuild at 23:00 re-anchored to tomorrow and silently skipped tonight; the Sep 9 2026
    # host-off-at-22:30 case, back at 13:47 the next day) and on a late wake for the nights a long sleep
    # skipped. The mode-filtered run log (R3) is the memory that answers "did the night run"; a --no-live dev
    # run never satisfies it, and neither does a pre-open Admin "Run daily now" for the same asof (it ran on
    # the prior session's bars — it lacks the night's close), so neither can suppress the real catch-up. The
    # guard is pure; the config it needs (RUN_AT + the market zone) is supplied HERE, never read inside it.
    if args.catch_up:
        run_at = parse_run_at(get_settings().cron_run_at)
        tz = market_tz()
        if already_ran_live(asof, run_at=run_at, tz=tz):
            print(
                f"daily-cron: a live pass for {asof} started at/after {run_at:%H:%M} {tz.key} "
                "already ran — catch-up is a no-op"
            )
            return

    outcome = run_daily_pass(asof=asof, allow_live=allow_live, catch_up=args.catch_up)
    if _report(outcome.results):
        raise SystemExit(1)  # surface partial failure to a scheduler / wrapper, non-silently


if __name__ == "__main__":
    main()
