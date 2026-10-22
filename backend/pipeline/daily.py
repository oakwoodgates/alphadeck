"""The daily call-of-record cron (M2b) — the platform feeding itself.

Before the per-thesis loop, the pass first refreshes the SHARED SPY/IWM benchmark tape so the calls read
FRESH data (Band-02: ``ingest_benchmarks`` → ``benchmark_rs``). That leg lives in ``run_daily_pass``
alongside the SPAC-radar leg and shares its discipline — own connection, lazy import, ``allow_live``-gated,
fail-open (a refresh fault is a passenger, it never fails the call cron).

Once a day, for every thesis (tenant intrinsic per-thesis):
  1. refresh its back-half facts — ``ingest_thesis`` (incremental + fail-visible; M2a);
  1b. refresh its members' quarterly fundamentals (the ``revenue_acceleration`` / ``share_creep`` inputs)
     with the FILING-GATED companyfacts pull (Q17), right after step 1 re-pulled the members' submissions
     indexes, so a quarter filed today reaches tonight's call; fail-open, it never costs the record;
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

C3 — A HAND-TYPED PAST ``--asof`` IS REFUSED (exit 2). A pass computes its night with ``known_at = now``,
so ``--asof <a night older than the last expected one>`` would append a SCORED call-of-record that knew
things that night did not. Such an as-of runs only with ``--catch-up`` (the sidecar's guarded catch-up),
``--late-ok`` (a deliberate late record; the sidecar's scheduled run passes it for an overshot sleep) or
``--no-live`` (records nothing); a missed night is ``pipeline.backfill``'s, reconstructed on the PUBLIC clock.

EXIT CODE (the contract a wrapper reads): **0 when the night has a call-of-record** for at least one
thesis — including a healthy-quiet night where nothing changed, a partial night where some theses errored,
an empty roster, and a deliberate ``--no-live`` pass. **1 ONLY when the night has no record at all** (the
pass crashed, or every thesis was withheld/errored for a real reason). The sidecar's one retry fires on
that, so the code answers "does tonight have a record?", never "did anything go wrong" — errors are
reported through the printed summary and the R4 health page. See ``_failed_to_record``.
**75 (C1, ``run_lock.EXIT_ALREADY_RUNNING``) when this invocation did not run at all** because another pass
or a backfill holds the cross-process call-of-record lock: one loud line, no rows, no artifact. It is
neither 0 (this invocation produced no record, and the holder may be a pre-``RUN_AT`` pass that does not
count for the night) nor 1 (this is not a failure of the night — the running pass is making its record).
A wrapper that retries any non-zero exit (the sidecar does) retries it once, later, under ``--catch-up`` —
a no-op if the running pass recorded the night, exactly right if it did not.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone, tzinfo
from functools import partial
from pathlib import Path
from time import perf_counter
from uuid import UUID

import psycopg

from db.session import connect, name_session, set_statement_timeout
from domain.config import DEFAULT_CONFIG, config_hash
from domain.feed_kinds import FEED_KINDS, StaleFeedLabel, feed_kind
from domain.market_time import market_now, market_today, market_tz
from domain.settings import Settings, get_settings
from domain.thesis import Thesis
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.form25 import delisted_class_is_other
from notify import DigestThesis, HealthEvent, Notifier, TransitionEvent, get_notifier
from pipeline import pass_timings, push_policy
from pipeline.call_for_thesis import call_for_thesis
from pipeline.cron_run_log import (
    RUN_STATUS_RUNNING,
    already_ran_live,
    amend_run_timings,
    mark_run_crashed,
    previous_ingest_skips,
    previous_name_errors,
    previous_settle_audit_end,
    previous_split_defects,
    previous_stale_tapes,
    write_cron_run_log,
    write_run_start,
)
from pipeline.edgar_coverage import EdgarCoverage, company_sets, coverage_of
from pipeline.ingest_fundamentals import NameResult as FundamentalsResult
from pipeline.ingest_fundamentals import PassMemo
from pipeline.ingest_skips import SkipDiff, diff_skips, known_keys
from pipeline.ingest_thesis import (
    NameResult,
    ingest_thesis,
    print_held_lines,
    print_withheld_line,
)
from pipeline.insider_amendment_scan import (
    AmendmentScan,
    previous_amendment_keys,
    run_amendment_scan,
    run_amendment_sweep,
)
from pipeline.insider_amendment_scan import report_lines as amendment_report_lines
from pipeline.name_errors import NameIngestError, dedup_name_errors, name_errors_of
from pipeline.night_coverage import NightTiming, night_timing, read_on_time_theses
from pipeline.pass_timings import per_thesis, timed
from pipeline.price_feed import (
    ExpectedPriceMiss,
    excuse_expected_misses,
    expected_miss_reasons,
    mark_acknowledged,
)
from pipeline.rename_probe import RenameProbe, run_rename_probe
from pipeline.rename_probe import describe as describe_rename
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    PassAlreadyRunning,
    PassLock,
    acquire_pass_lock,
)
from pipeline.schedule import last_expected_asof, parse_run_at
from pipeline.self_audit import (
    AuditContext,
    AuditResult,
    failed_labels,
    run_audit,
    settle_window_since,
)
from pipeline.split_basis import SplitScan, run_split_scan
from pipeline.split_basis import report_lines as split_report_lines
from pipeline.successor_cik import SuccessorScan, previous_successor_keys, run_successor_scan
from pipeline.successor_cik import report_lines as successor_report_lines
from pipeline.tape_health import StaleTape, stale_fund_shares, stale_tapes
from repositories import calls_repo, price_feed_repo, thesis_repo
from securities import master

_log = logging.getLogger(__name__)


def _pass_connect() -> psycopg.Connection:
    """A DB connection for the daily PASS — ``connect()`` plus the C2 session ``statement_timeout``
    (``Settings.cron_statement_timeout_s``), so a lock wait or a stuck read raises inside the pass's
    per-thesis / per-leg isolation instead of hanging the whole sequential loop (no artifact, no page)
    until the sidecar's wall-clock kill. EVERY connection ``run_daily_pass`` opens comes from here (the
    loop, the refresh / sweep / radar legs, the self-audit); ``run_daily`` itself takes its connection as a
    parameter and is unchanged. ``connect()`` FIRST, so an unreachable DB raises the connect error itself.
    """
    conn = connect()
    try:
        set_statement_timeout(conn, get_settings().cron_statement_timeout_s)
    except BaseException:
        conn.close()
        raise
    # W1 — the name another writer sees when it finds a company this pass holds (fail-open, cosmetic)
    name_session(conn, "alphadeck-writer:daily-pass")
    return conn


def _is_set(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def env_fingerprint(
    notifier: Notifier,
    *,
    environ: Mapping[str, str] | None = None,
    settings: Settings | None = None,
) -> dict:
    """I2 — the NON-SECRET environment the pass ran under, for the run-of-record artifact. The clock config
    as VALUES (``TZ`` / the market zone / the shell's ``RUN_AT`` / ``Settings.cron_run_at`` — none is a
    secret, and a drift between them is exactly what this exists to show) and every secret or delivery
    switch as a set/unset BOOLEAN only (the User-Agent, the Slack webhook, the Polygon key, the heartbeat
    URL) — **a value is never written**. ``notifier`` is the class actually in use: ``LogNotifier`` on a
    live pass means a bad night pages nobody. ``environ`` / ``settings`` are injectable for tests.
    """
    env = os.environ if environ is None else environ
    s = settings or get_settings()
    return {
        "tz": env.get("TZ"),
        "market_tz": s.market_tz,
        "run_at": env.get("RUN_AT"),
        "cron_at": s.cron_run_at,
        "user_agent_set": _is_set(s.user_agent),
        "slack_webhook_set": _is_set(s.slack_webhook_url),
        "polygon_api_key_set": _is_set(s.polygon_api_key),
        "heartbeat_url_set": _is_set(env.get("HEARTBEAT_URL")),
        "notifier": type(notifier).__name__,
    }


def _crash_label(exc: BaseException) -> str:
    """``"<ExceptionType>: <first line, clipped>"`` — the H3 page's description of the crash. Clipped so a
    long driver message cannot flood the page; one line so it reads in Slack."""
    msg = " ".join(str(exc).split())[:200]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _page_crash(notifier: Notifier, asof: date | None, exc: BaseException) -> None:
    """H3 — send ONE health page for a pass that died on an unhandled exception, then return so the caller
    re-raises the ORIGINAL. FAIL-OPEN by contract: nothing raised in here (a notifier bug, a Slack outage,
    even a second interrupt) may replace or mask the exception being reported."""
    try:
        notifier.notify_health(
            HealthEvent(
                asof=asof or datetime.now(timezone.utc).date(),
                theses=0,
                withheld_no_live=0,
                withheld_failure=0,
                errored=0,
                edgar_fetches=0,
                frozen=False,
                crashed=_crash_label(exc),
            )
        )
    except BaseException:  # noqa: BLE001 — the page can never mask the original exception
        _log.exception("the crash page itself failed (fail-open — re-raising the original)")


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
    # EDGAR network pulls this thesis's ingest ATTEMPTED (`EdgarClient.live_fetches` — a cache miss or a
    # stale-mutable refresh, counted before the request, so a pull that then failed counts too). Recorded
    # into the cron run log (R3) as `edgar_fetches`. Since O5 it is not what the freeze page reads.
    edgar_fetches: int = 0
    # O5 — the attempts that COMPLETED (`EdgarClient.live_completed` — a 2xx body arrived): THE FREEZE
    # DETECTOR. A frozen index and a healthy nothing-filed night produce identical fact tallies (0
    # appended); this is the one number that differs, and unlike the attempt count it also reads 0 when
    # every pull FAILED (the 2026-07-22 empty User-Agent: 255 attempts, 0 data). 0 across the theses a live
    # pass processed = no EDGAR data reached it = a freeze (`assess_health`). None = not measured: a
    # thesis a --catch-up SKIPPED (no client was built), and every result rebuilt from an artifact written
    # before O5 (which `assess_health` then judges by the rule that pass was paged under).
    edgar_fetches_completed: int | None = None
    # THE RATIO FLOOR's per-company facts (``pipeline/edgar_coverage.py``): the CIKs of the EDGAR-ELIGIBLE
    # companies this thesis's ingest walked (a CIK, and its EDGAR legs ran), and those among them with >= 1
    # COMPLETED EDGAR fetch while they ran. SETS, not counts: the pass judges the UNION across its theses (a
    # company another thesis fetched minutes earlier is a fresh-cache hit here, and fresh for the pass). None =
    # not measured — a thesis a --catch-up SKIPPED, and every result rebuilt from an artifact (the Admin
    # history re-judges the floor from the artifact's run-level coverage instead).
    edgar_ciks: frozenset[str] | None = None
    edgar_ciks_fresh: frozenset[str] | None = None
    # R2a — why the call-of-record was WITHHELD (not recorded), or None if it was recorded/attempted. A run
    # that didn't meaningfully refresh has no business writing the log of record: "no-live" (a cache-only dev
    # run, Source A) or "total ingest failure" (the ingest raised, or EVERY name errored, Source C). A healthy
    # OR partial run still records (the partial one marked via the calls ingest_fresh column, R2b).
    withheld_reason: str | None = None
    # G5a/F1 — this thesis's names whose monitored FEED has gone stale (judged below against this run's
    # asof): price tapes and ETF fund-shares samples together, each row carrying its `kind`. A MONITOR
    # field: it rides on the run result and the run-of-record artifact and NEVER on the CallCard —
    # a day-varying card field would flap record_if_changed's substance compare and break the cron's
    # idempotency. Empty on a --no-live pass (not evaluated) and on a healthy universe. G5b — a price row
    # whose name has a SEC delisting form carries `closed_at`: it stays in this inventory (never dropped,
    # #9) but renders quietly as "closed" and is skipped from the loud newly-stale page (#7/WB#3).
    tape_stale: tuple[StaleTape, ...] = ()
    # M2 — why a --catch-up pass SKIPPED this thesis outright (no ingest, no call): it already holds an
    # ON-TIME call-of-record for the night (`night_coverage.on_time_window`). None = processed as usual.
    # A skipped thesis HAS its night's record — every "did the night record" reader counts it as such.
    skipped: str | None = None
    # T3 — this thesis's EXPECTED price 404s (`pipeline/price_feed.py`): a vendor 404 on a name whose tape
    # tonight's pass classified CLOSED, or that the operator ACKNOWLEDGED unpriceable. Removed from the
    # names' errors (so they do not spoil `ingest_fresh`) and kept HERE — on the artifact and in one quiet
    # report line — so the excuse stays visible (#9). Empty on a --no-live pass (nothing is classified).
    expected_price_misses: tuple[ExpectedPriceMiss, ...] = ()
    # Q17 — this thesis's FUNDAMENTALS refresh (step 1b, ``_refresh_fundamentals``): one result per member
    # it handled (a member another thesis already handled this pass is not repeated), and a thesis-level
    # failure of that step, if any. Printed as one pass summary; never part of ``error`` — a fundamentals
    # fault is a passenger and never withholds or fails the call.
    fundamentals: tuple[FundamentalsResult, ...] = ()
    fundamentals_error: str | None = None


@timed("fundamentals")  # X5 — the step's wall time on a metered pass (`pipeline/pass_timings.py`)
def _refresh_fundamentals(
    conn: psycopg.Connection,
    thesis: Thesis,
    names: list[NameResult],
    client: EdgarClient,
    memo: PassMemo,
) -> list[FundamentalsResult]:
    """Q17 STEP 1b — the filing-gated companyfacts refresh for the members THIS thesis's ``ingest_thesis``
    just walked (``names``): their submissions indexes were re-pulled moments ago at the recurring TTL, so
    the gate (``ingest/edgar/companyfacts_gate.py``) reads TONIGHT's filings, and a 10-Q accepted today is
    stored before this thesis's call is assembled. ``client`` is that same per-thesis recurring client (its
    companyfacts pulls count in the thesis's ``edgar_fetches`` — they ARE reaches-out); ``memo`` spans the
    pass, so a company is pulled at most once however many theses hold it. A name the ingest did not walk
    (a stubbed or failed ingest) is not refreshed here, and a member missing from the master was already
    reported by the ingest."""
    from pipeline.ingest_fundamentals import ingest_fundamentals_for_securities

    secs = []
    for n in names:
        if n.security_id in memo.securities:
            continue
        if getattr(n, "lock_busy", None):
            # W1 — the ingest did not walk it (another writer held the company past the wait): it is not
            # refreshed here either, rather than waiting out the same holder a second time
            continue
        sec = master.get(conn, n.security_id, tenant_id=thesis.tenant_id)
        if sec is not None:
            secs.append(sec)
    return ingest_fundamentals_for_securities(
        conn, secs, client=client, tenant_id=thesis.tenant_id, memo=memo
    )


def _print_fundamentals(results: list[ThesisRunResult]) -> None:
    """The pass's ONE fundamentals summary (the per-thesis step reports nothing itself): quarters appended,
    names refreshed, the X8 known-absent count folded into the line, then one ERROR line per real failure —
    a FRESH 404 included — and one WARNING line per thesis whose whole step failed. Loud only when nonzero.
    """
    fund = [f for r in results for f in r.fundamentals]
    quarters = sum(f.appended for f in fund)
    known_absent = sum(1 for f in fund if f.error and f.known_absent)
    errs = [f.error for f in fund if f.error and not f.known_absent]
    absent = f", {known_absent} known without companyfacts (404, cached)" if known_absent else ""
    print(f"fundamentals refresh: +{quarters} quarters, {len(fund)} names{absent}")
    for err in errs:
        print(f"  fundamentals refresh ERROR: {err}")
    for r in results:
        if r.fundamentals_error:
            print(f"WARNING: {r.name}: fundamentals refresh failed: {r.fundamentals_error}")


def _acknowledged_ids(conn: psycopg.Connection) -> set[UUID]:
    """Q8 — the security ids the operator ACKNOWLEDGED unpriceable (an active ``price_feed_ack``), read
    ONCE before the loop. FAIL-OPEN TOWARD LOUD: a read that fails returns ``set()`` with one warning line —
    every unpriceable name then stays a loud repair row and its 404 an error for this one pass (a repeat
    page at worst; never a name silenced on a guess). The read runs in a SAVEPOINT (``conn.transaction()``),
    so a fault rolls back only itself and never poisons — or discards — the caller's transaction."""
    try:
        with conn.transaction():
            return price_feed_repo.acknowledged_security_ids(conn)
    except Exception as e:  # noqa: BLE001 — never the reason a pass does not run
        print(
            f"WARNING: could not read the acknowledged-unpriceable names ({e}) — every unpriceable name "
            "stays LOUD this pass and its price 404 counts as an error"
        )
        return set()


def _catch_up_skip_set(conn: psycopg.Connection, asof: date) -> dict[UUID, datetime]:
    """M2 — ``thesis_id -> its earliest ON-TIME recorded_at`` for ``asof`` (``night_coverage``: a
    non-reconstructed row inside ``[RUN_AT on asof, the next weekday's 06:00 ET)``), counted in the table.
    FAIL-OPEN TOWARD RUNNING: a read that fails (or a malformed ``RUN_AT``) returns ``{}`` with one loud
    line — the catch-up then processes every thesis, exactly as it did before M2 (a re-record of a late
    night is the bounded cost; a skipped thesis with no record would be the silent gap)."""
    try:
        skip = read_on_time_theses(
            conn, asof, run_at=parse_run_at(get_settings().cron_run_at), tz=market_tz()
        )
        conn.rollback()  # end the read transaction; the loop opens its own per-thesis work
        return skip
    except Exception as e:  # noqa: BLE001 — never the reason a catch-up does not run
        conn.rollback()
        print(
            f"WARNING: catch-up could not read the on-time rows for {asof} ({e}) — processing EVERY "
            "thesis (an on-time row may be superseded by a late one)"
        )
        return {}


def _push_timing(asof: date) -> NightTiming:
    """N1 — ``night_coverage.night_timing`` for a transition pushed NOW (the market clock): "late" when the
    night's on-time window has closed (a catch-up / ``--late-ok`` pass), "past" for an earlier night still on
    time, else "current". FAIL-OPEN: it runs inside the per-thesis ``try`` that rolls the call-of-record back
    on any exception, so a clock or zone fault here degrades to "current" (the push reads as before N1) and
    can never cost the night its record."""
    try:
        return night_timing(asof, market_now(), tz=market_tz())
    except Exception:  # noqa: BLE001 — a label never risks the record
        return "current"


def run_daily(
    conn: psycopg.Connection,
    *,
    asof: date | None = None,
    known_at: datetime | None = None,
    allow_live: bool = True,
    force_refresh: bool = True,
    user_agent: str | None = None,
    notifier: Notifier | None = None,
    run_kind: str = "manual",
    catch_up: bool = False,
    known_skips: Collection[str] | None = None,
) -> list[ThesisRunResult]:
    """Run the daily pass over every thesis. ``asof`` defaults to today, ``known_at`` to now (a live read).
    Returns one ``ThesisRunResult`` per thesis. Never raises for a single thesis — failures are captured.

    ``force_refresh`` defaults to **True**: the daily path is recurring, so it re-pulls fresh bars (bypassing
    a stale cache hit) — otherwise the cron would re-ingest the same frozen cache every day and never see a
    new bar. It threads to the price source (``eod_loader.fetch_eod``).

    ``run_kind`` (0044) is stamped on every row this pass records: ``"cron"`` when the sidecar fired it
    (``scripts/daily_cron.sh`` passes ``--run-kind cron``), ``"manual"`` otherwise — a hand-typed CLI run or
    the Admin "Run daily now" button, both of which are operator-kicked. The DEFAULT is ``"manual"`` on
    purpose: the sidecar and an operator invoke the SAME entry point, so only an explicit flag can tell them
    apart, and defaulting the other way would let any hand run masquerade as the nightly record. MEASURED,
    and the reason this exists: the record's two largest arm bursts were weekend manual runs on deploy days.

    ``catch_up`` (M2, operator Q4 = "never"): a catch-up NEVER re-records a thesis that already has an
    ON-TIME call-of-record for the night. MEASURED 2026-10-12: the 22:30 pass died after 4 theses recorded
    on-time rows (22:42-22:43); the next day's 16:57 boot catch-up re-ran all 12, and ``record_if_changed``
    appended new versions for the 4 because restated bars had changed their cards — so next-day knowledge
    superseded the honest on-time rows (AI Memory & Storage seq 1334 at 22:42 -> seq 1367 at 17:04) and the
    Scoreboard scores the late ones. Now such a thesis is SKIPPED ENTIRELY — no ingest, no assemble, no
    write (``ThesisRunResult.skipped`` says why) — read once, before the loop, from the table
    (``_catch_up_skip_set``). A thesis whose only row is late, daytime or reconstructed is processed as
    before. The scheduled run and the Admin button pass ``False``: they re-version on purpose.
    """
    asof = asof or market_today()
    # ONE explicit cfg for the whole pass, used for BOTH the assemble and its fingerprint. Holding it in a
    # local (rather than letting `call_for_thesis` default it and hashing DEFAULT_CONFIG separately) is what
    # makes the stamp HONEST: if this pass ever runs a non-default cfg, the hash follows it automatically
    # instead of describing a policy that did not produce these cards.
    cfg = DEFAULT_CONFIG
    cfg_hash = config_hash(cfg)
    code_sha = get_settings().image_sha
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
    # M2 — the catch-up's skip set: theses that already hold an ON-TIME row for this night
    skip = _catch_up_skip_set(conn, asof) if catch_up else {}
    tz = market_tz() if skip else None
    # Q8/T3 — the operator's acknowledged-unpriceable names, read once (a live pass only: nothing is
    # classified on a --no-live pass, so there is nothing to mark or excuse)
    acknowledged = _acknowledged_ids(conn) if allow_live else set()
    # Q17 — the fundamentals refresh's pass memory: one companyfacts pull per company per pass
    fund_memo = PassMemo()
    # O4 (Q13) — this pass's newly-armed-names digest, one line per already-armed thesis
    digest: list[DigestThesis] = []
    out: list[ThesisRunResult] = []
    # X5 — `per_thesis` makes each thesis a timing scope on a metered pass (a pass-through otherwise)
    for thesis in per_thesis(thesis_repo.list_all(conn)):
        res = ThesisRunResult(thesis_id=thesis.id, name=thesis.name)
        on_time_at = skip.get(thesis.id)
        if on_time_at is not None:
            # M2 — the night's honest on-time record stands; nothing later may supersede it
            res.skipped = (
                f"on-time call-of-record already recorded "
                f"{on_time_at.astimezone(tz).strftime('%Y-%m-%d %H:%M %Z')}"
            )
            out.append(res)
            continue
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
        # interactive path). Since Q17 ``ingest_fundamentals`` builds a recurring client too: on it the
        # ``companyfacts/`` class is FILING-GATED, keyed off the very submissions index this loop re-pulls
        # (``ingest/edgar/companyfacts_gate.py``).
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
                known_skips=known_skips,  # Q18 — mutes the lines of filings known to skip
            )
        except Exception as e:  # noqa: BLE001 — one thesis's ingest never aborts the cron
            conn.rollback()
            res.error = f"ingest: {e}"
        # (1b) Q17 — THE FUNDAMENTALS REFRESH, HERE and not before the loop: the companyfacts gate keys off
        # each member's submissions index, and the ingest above JUST re-pulled those at the recurring TTL —
        # so the gate reads TONIGHT's filings and a quarter accepted today is stored before this thesis's
        # call is assembled (a pre-loop leg read last night's indexes: one pass late). Live passes only.
        # FAILURE ISOLATION: the step commits per name and rolls back per name; a fault escaping it is
        # rolled back here and recorded on `fundamentals_error`, never on `error` — it can never withhold or
        # fail the call. A rollback HERE discards nothing: every ingest leg above already committed or rolled
        # back, and the call's own transaction has not begun. (Not a SAVEPOINT: the step commits per name,
        # which a savepoint forbids — the savepoint trap is a fault INSIDE the call's transaction, and this
        # step never runs in it.)
        if allow_live and res.ingested:
            try:
                res.fundamentals = tuple(
                    _refresh_fundamentals(conn, thesis, res.ingested, edgar_client, fund_memo)
                )
            except Exception as e:  # noqa: BLE001 — a fundamentals fault is a passenger
                conn.rollback()
                res.fundamentals_error = str(e)
        # capture the count even on a thesis-level failure — a mid-ingest raise still made real network
        # pulls, and "0 fetches" must mean the freeze, not "we bailed before the counter was read"
        res.edgar_fetches = edgar_client.live_fetches
        res.edgar_fetches_completed = edgar_client.live_completed  # O5 — what the freeze page reads
        # the ratio floor's per-company facts (a thesis whose ingest raised walked nothing: two empty sets)
        res.edgar_ciks, res.edgar_ciks_fresh = company_sets(res.ingested)
        # G5a/F1 — THE FEED-RECENCY JUDGMENT, both feeds. `ingest_thesis` reported each name's EDGES (facts:
        # the latest stored price bar, and for an ETF sleeve the latest stored shares sample); the judgment
        # belongs HERE because this is the layer that holds the run's `asof` — time stays a parameter, never
        # an ambient clock inside the ingest unit. Gated on `allow_live`: a cache-only pass must not reset
        # the newly-stale baseline the next live pass diffs against (its artifact records
        # `tape_evaluated: false`), and it is withheld from the record anyway.
        # ONE settings read, TWO thresholds: the feeds have different cadences (a price tape gets a bar
        # every session; a sleeve's sample carries the source page's own stated as-of date, measurably up
        # to two days behind the pull), so one number cannot serve both. Either at <= 0 disables that feed's
        # monitor independently — `is_tape_stale` honors it too; the gates here just skip the work. The two
        # row sets ride ONE field because they are the same question about two feeds; each row carries its
        # `kind`, and the dedup is per (kind, security) so a name stale on both is reported once per feed.
        if allow_live:
            s = get_settings()
            rows: tuple[StaleTape, ...] = ()
            if s.tape_stale_days > 0:
                rows += stale_tapes(
                    res.ingested,
                    asof=asof,
                    stale_days=s.tape_stale_days,
                    closed_before_days=s.tape_closed_before_days,
                    closed_after_days=s.tape_closed_after_days,
                    # Q6 — a Form 25 that removed ANOTHER class (warrants, units ...) is not a close;
                    # its document is read through this thesis's client (forms/* cache forever)
                    removes_other_class=partial(delisted_class_is_other, edgar_client),
                )
            if s.fund_shares_stale_days > 0:
                rows += stale_fund_shares(
                    res.ingested, asof=asof, stale_days=s.fund_shares_stale_days
                )
            # Q8 — an UNPRICEABLE row the operator acknowledged goes quiet (no newly-stale page, a quiet
            # count on Admin) but stays in the inventory (#9). Only an unpriceable row can be marked.
            rows = mark_acknowledged(rows, acknowledged)
            res.tape_stale = rows
            # T3 — EXPECTED price 404s: a vendor 404 on a name tonight's classification calls CLOSED, or
            # that the operator acknowledged unpriceable, is not a name error — so it does not count toward
            # `ingest_errors` below, does not make `ingest_fresh` false, and does not page. Moved onto
            # `expected_price_misses` (artifact + one quiet report line), never dropped. A renamed (not yet
            # adopted) or unacknowledged unpriceable name's 404 stays an error: it needs the operator. The
            # fetch itself still ran — a closed name whose tape revives is noticed because it did
            # (`pipeline/price_feed.py`).
            reasons = expected_miss_reasons(rows)
            if reasons:
                res.ingested, res.expected_price_misses = excuse_expected_misses(
                    res.ingested, reasons
                )
        # R2a — THE RECORDING GATE: a run that didn't meaningfully refresh must not write the log of record.
        # TWO conditions, closing two do-nothing shapes that a fact-count test can't (a --no-live run over a
        # warm cache is fast, clean, appends nothing, and does NOT error):
        #   - no-live (Source A) → allow_live False. A cache-only dev run has no business recording, period.
        #   - total ingest failure (Source C) → the ingest raised (res.error), OR names existed and EVERY one
        #     errored. NOT "appended 0" — a current thesis appends 0 on a HEALTHY run and MUST still record.
        # A partial failure (some errored, some clean) DOES record, marked via ingest_fresh (R2b).
        ingest_errors = sum(1 for x in res.ingested if x.error)
        # W1 — names whose company another writer (a Workbench ingest, a CLI run) held past the bounded wait:
        # their legs did not run in THIS pass. NOT errors — they never count toward the recording gate below,
        # so a Workbench ingest can never withhold (or fail) a night — but the call does rest on a name this
        # pass did not refresh, so the row is stamped `ingest_fresh=false` (R2b's meaning, exactly).
        lock_busy = sum(1 for x in res.ingested if getattr(x, "lock_busy", None))
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
            card = timed("call")(call_for_thesis)(
                conn, thesis.id, asof, known_at=known_at, cfg=cfg, record=False
            )
            # (4) TRANSITION DETECTION (the notify seam): compare state/verdict against the
            # call-of-record AT OR BEFORE this as-of — the material-change line (trigger churn /
            # provenance noise version the log via record_if_changed without being transitions; a
            # state or verdict MOVE is what an operator would want to be told about). First-ever
            # call = no prior = no event. Delivery is the adapter's concern (v1: a loud log line).
            #
            # F2 — `<=`, NOT `<`: THE BASELINE IS THE LATEST RECORD AT OR BEFORE THIS AS-OF. Under
            # `<` a SECOND pass on the same day compared against YESTERDAY and re-pushed a
            # transition it had already announced hours earlier (MEASURED on dev 2026-09-13: two
            # later passes each re-reported the same transitions the first pass had reported, while
            # appending 0 rows — an Admin "Run daily now" after the nightly did the same). `<=`
            # picks the same-day earlier row when there is one, so one move is announced ONCE. It
            # can never reach a FUTURE row, so a catch-up for a past night still compares against
            # that night's own row when one exists, else its predecessor — never a later night's.
            # Why this is the RIGHT baseline and not merely a quieter one: it is the SAME row
            # `record_if_changed` compares against (`calls_repo.record_if_changed` — `c.asof ==
            # card.asof`), so "unchanged card -> no row appended" and "unchanged card -> no page"
            # become ONE rule instead of two that disagree within a day.
            # ACCEPTED NUANCE: `latest_for_thesis` includes `reconstructed` rows by default, so a
            # catch-up of a past night that already holds a reconstructed row compares against it.
            # A page about a stale night is noise either way, and narrowing the include flag here
            # would be a behavior change beyond this fix (#337 scoped that ruling to the Scoreboard).
            prior = next(
                (c for c in calls_repo.latest_for_thesis(conn, thesis.id) if c.asof <= asof), None
            )
            evt: TransitionEvent | None = None
            if prior is not None and (
                prior.state is not card.state or prior.verdict is not card.verdict
            ):
                # The event carries what the Q13 push policy classifies on (grade + armed-name count, off
                # the two cards) and, enriched FAIL-OPEN inside a SAVEPOINT, the company-level detail and a
                # quiet push's card-carried reasons (`pipeline/push_policy.py` — the SAVEPOINT-in-the-cron-
                # loop trap is documented there: this runs inside the try that rolls the record back).
                # N1 — a push about a past or LATE night says so ("asof D, recorded late").
                evt = push_policy.transition_event(
                    conn, thesis, prior, card, asof=asof, timing=_push_timing(asof)
                )
                timed("notify")(notifier.notify)(evt)
                res.transition = evt.label
            # R2b — stamp the run's ingest health on the recorded row (PROVENANCE, off the card). We reach
            # here only on a healthy OR partial ingest; ingest_fresh False marks the partial one so the
            # Scoreboard can discount a call resting on names that failed to refresh.
            res.recorded = timed("record")(calls_repo.record_if_changed)(
                conn,
                card,
                thesis.tenant_id,
                ingest_fresh=(ingest_errors == 0 and lock_busy == 0),
                ingest_errors=ingest_errors,
                # 0044 — the RUN IDENTITY, stamped beside the ingest health and outside `_canonical`'s
                # compare, so a deploy or a dial edit alone never re-records an unchanged card.
                config_hash=cfg_hash,
                code_sha=code_sha,
                run_kind=run_kind,
            )
            conn.commit()
            # O4 (Q13) — names newly armed inside an already-armed thesis, for the pass-end digest. AFTER
            # the commit (the record is safe) and never raising; a name this thesis's push already listed
            # (an upgrade push) is not repeated.
            entry = push_policy.digest_entry(
                conn, thesis, prior, card, covered=push_policy.covers_names(evt)
            )
            if entry is not None:
                digest.append(entry)
        except Exception as e:  # noqa: BLE001 — one thesis's call never aborts the cron
            conn.rollback()
            res.error = (f"{res.error}; " if res.error else "") + f"call: {e}"
        out.append(res)
    # O4 (Q13) — ONE quiet digest per pass, after the loop, only when a name newly armed (fail-open: a digest
    # fault never costs a record or another push). A catch-up / late pass carries N1's label.
    if digest:
        # X5 — its own pass-level leg (the re-arm history read + the push)
        timed("digest")(push_policy.send_digest)(
            conn, notifier, digest, asof=asof, timing=_push_timing(asof)
        )
    return out


def assess_health(
    results: list[ThesisRunResult],
    *,
    asof: date,
    allow_live: bool,
    catch_up: bool = False,
    freeze_check: bool = True,
    benchmark_errors: int = 0,
    benchmark_leg_failed: bool = False,
    tape_stale_new: tuple[StaleFeedLabel, ...] = (),
    name_errors_new: tuple[str, ...] = (),
    audit_failed: tuple[str, ...] = (),
    split_defects_new: tuple[str, ...] = (),
    successor_cik_new: tuple[str, ...] = (),
    insider_amendments_new: tuple[str, ...] = (),
    insider_amendments_more: int = 0,
    edgar_coverage: EdgarCoverage | None = None,
) -> HealthEvent | None:
    """R4 — the run's pageable health, or None when the run was clean (loudness marks the exception). Unhealthy
    = a FREEZE (a live run during which no EDGAR fetch COMPLETED across the theses it processed — the R1
    cache-never-refreshed signal, and since O5 also the every-pull-failed one — or, by the RATIO FLOOR, one in
    which fewer than the floor of its EDGAR-eligible companies got fresh SEC data), any WITHHELD call (no-live /
    total ingest failure), any thesis ERROR, or a failed BENCHMARK refresh. Pure over the collected results,
    so it is unit-testable without a DB; ``main`` emits it through the notifier.

    THE RATIO FLOOR (operator decision 2026-10-21; ``pipeline/edgar_coverage.py`` holds the exact ratio) —
    ``edgar_coverage`` is the pass's per-COMPANY freshness: of the distinct EDGAR-eligible companies (CIKs) its
    processed theses ingested, how many had >= 1 completed EDGAR fetch anywhere in the pass, and the floor it
    is judged under. A live pass whose ``fresh < floor x eligible`` is FROZEN beside the zero rule — the July
    shape the zero rule missed (1-4 fetches across 255-494 names: a stale cache served, nothing errored).
    PASSED IN, like the benchmark counts: the pass computes it once (``coverage_of``) for both the artifact
    and this page, and the Admin history re-reads it off the artifact (``EdgarCoverage.from_payload``) with
    the floor THAT pass was judged under. ``None`` = the floor is not judged: an artifact written before it
    existed keeps the rule it was paged under, and a pre-O5 result never reaches the floor at all.

    O5 — THE FREEZE PREDICATE, two corrections (``docs/FEED_LOOP.md`` "Known gaps"):

    - **COMPLETED fetches, not attempts.** The attempt count rises before the request, so a pass whose every
      pull failed read as a fetching night: MEASURED 2026-07-22 21:59 ET, an empty User-Agent raised on all
      255 pulls before a byte left the box and the run logged ``255 EDGAR fetches``, not frozen (the R2
      withhold caught that night; a partial outage would not have been caught). The predicate reads
      ``ThesisRunResult.edgar_fetches_completed`` (2xx bodies received); ``edgar_fetches`` stays on the
      artifact and the page as the attempt count, so "255 attempted, 0 completed" reads as what it was.
    - **The catch-up exemption narrowed to what a catch-up legitimately skips.** It was WHOLESALE
      (``freeze_check=not catch_up``) because a catch-up ran inside the EDGAR cache's 12h TTL right after the
      scheduled run, where ~0 fetches was correct. Since G1 the recurring client's TTL is five minutes, so a
      catch-up re-pulls every company it ingests like any pass: the sidecar's retry waits 20 minutes, a boot
      catch-up after a killed pass processes the theses that pass never reached, and a late-wake catch-up
      follows a full pass. What a catch-up legitimately does NOT fetch is the theses M2 SKIPPED (their
      on-time row stands; no ingest, no client): those are left out of the predicate — for every pass, by
      construction — and the theses it PROCESSED are judged like any other pass's. ACCEPTED residual, loud by
      construction: a catch-up whose every processed company was fetched in the previous five minutes (only
      possible right behind a pass killed mid-thesis, which already paged) pages FROZEN once — a repeat page,
      never a silenced freeze.

    THE RULE A RESULT WAS ASSESSED UNDER IS ITS OWN. Results that carry no completed count (rebuilt by the
    Admin history from an artifact written before O5) are judged by the rule that pass was PAGED under —
    attempts, the whole roster, a catch-up exempt wholesale (``catch_up``) — so the history keeps showing the
    verdict each night actually paged. ``freeze_check=False`` skips the predicate outright (no production
    caller passes it since O5; tests asserting another page over fetch-less results use it).

    G4 — the BENCHMARK refresh legs are PASSENGERS in ``run_daily_pass`` (fail-open, own connection), and
    their faults used to reach stdout only: a SPY/IWM tape that did not refresh silently degrades
    ``benchmark_rs`` for every call that night, which is exactly the shape of failure this pager exists to
    make visible. Two distinct counts because they are different news: ``benchmark_errors`` = individual
    benchmark pulls that failed (1 of 2 = a partly stale tape), ``benchmark_leg_failed`` = the leg itself
    raised before producing per-benchmark results (no refresh happened at all). Both are passed IN rather
    than derived, because the legs run outside ``run_daily`` and this function stays pure.

    G5a/F1 — ``tape_stale_new`` are the ``(kind, label)`` pairs for names whose monitored FEED went stale
    **since the last-known inventory of the evaluated live passes** — each thesis as the newest pass that
    covered it saw it, a skipped-thesis ``--catch-up`` included (``cron_run_log.previous_stale_tapes`` reads
    that per-thesis carry-forward, keyed on ``kind:security_id``; this function stays pure, so the cross-run
    comparison is passed IN like the benchmark counts). Both feeds ride one argument and the page groups
    them by kind, because a price tape and a fund-shares sample stop for different reasons and are repaired
    differently. NEWLY stale only, on purpose: a handful of known-dead feeds must not re-page every night
    forever (inverse loudness — a page true of every night carries no information). It pages, but it is NOT an alarm about the
    CRON: the run did its job, the FEED has a gap the operator repairs (the vendor price symbol for a tape;
    a fund/ticker/source check for a sleeve) — so ``app/routers/admin.py::_problems`` renders it with the
    benign marker and the one-word cron verdict stays about the cron.

    O1 — ``name_errors_new`` are the ``"<ticker or id> (<leg>)"`` labels of names whose ingest leg started
    failing since the same per-thesis last-known inventory (``cron_run_log.previous_name_errors``; the diff
    lives in ``run_daily_pass``, passed IN like the stale-tape diff). Newly failing only — the known-failing
    names collapse to a quiet count in the printed report — and benign-class like a stale feed: the thesis
    still recorded.

    V1 — ``audit_failed`` are the self-audit's FAILED checks (``self_audit.failed_labels``). A real alarm:
    the pass did not leave the night the record it claims (a thesis with no on-time row, an unknown
    ``code_sha``, a thesis it started and never finished). A not-applicable check never reaches here.

    T4 — ``split_defects_new`` are the labels of split-basis defects (``pipeline/split_basis.py``) NEW since
    the previous evaluated pass. Benign-class like a stale feed: a vendor TAPE defect, flag only — the call
    is unchanged and the run did its job — so it pages once and never makes the Admin verdict unhealthy.

    T7 — ``successor_cik_new`` are the labels of members whose company now files under a NEW CIK
    (``pipeline/successor_cik.py``), NEW since the previous evaluated pass. Benign-class the same way: a
    data gap the operator's Adopt repairs, not a cron fault — it pages once.

    R8-A / R8-B — ``insider_amendments_new`` are the labels of Form 4/A amendments NEWLY owed the operator's
    decision the page names (every in-window one, then the others up to the cap — since R8-B only an
    ambiguous link the system cannot resolve), ``insider_amendments_more`` the count of the rest
    (``pipeline/insider_amendment_scan.py``). Benign-class the same way: the decision is the operator's, the
    run did its job — it pages once. A system application never reaches here.
    """
    theses = len(results)
    # split withheld by its ACTUAL reason — a --no-live dev run is benign, a total failure is an alarm; a page
    # must report which one, not a generic "(no-live / total ingest failure)" that reads as a fault either way
    withheld_no_live = sum(1 for r in results if r.withheld_reason == "no-live")
    withheld_failure = sum(1 for r in results if r.withheld_reason == "total ingest failure")
    errored = sum(1 for r in results if r.error)
    edgar_fetches = sum(r.edgar_fetches for r in results)  # ATTEMPTS — reported, the pre-O5 verdict
    # O5 — the theses the pass PROCESSED: a --catch-up's M2-skipped theses ingested nothing by construction
    processed = [r for r in results if not r.skipped]
    edgar_fetches_completed: int | None = None
    freeze_theses: int | None = None
    if processed and all(r.edgar_fetches_completed is not None for r in processed):
        edgar_fetches_completed = sum(r.edgar_fetches_completed or 0 for r in processed)
        freeze_theses = len(processed)
        # the RATIO FLOOR beside the zero rule: too few of the eligible companies got fresh SEC data
        starved = edgar_coverage is not None and edgar_coverage.starved
        frozen = freeze_check and allow_live and (edgar_fetches_completed == 0 or starved)
    else:
        # the PRE-O5 rule, for results rebuilt from an artifact that carries no completed count — and for a
        # pass that processed nothing (every thesis skipped, or no roster), which it can never call frozen
        frozen = freeze_check and not catch_up and allow_live and theses > 0 and edgar_fetches == 0
    if not (
        withheld_no_live
        or withheld_failure
        or errored
        or frozen
        or benchmark_errors
        or benchmark_leg_failed
        or tape_stale_new
        or name_errors_new
        or audit_failed
        or split_defects_new
        or successor_cik_new
        or insider_amendments_new
        or insider_amendments_more
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
        edgar_fetches_completed=edgar_fetches_completed,
        freeze_theses=freeze_theses,
        edgar_companies=edgar_coverage.eligible if edgar_coverage is not None else None,
        edgar_companies_fresh=edgar_coverage.fresh if edgar_coverage is not None else None,
        freeze_completed_floor=edgar_coverage.floor if edgar_coverage is not None else None,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
        tape_stale_new=tape_stale_new,
        name_errors_new=tuple(name_errors_new),
        audit_failed=tuple(audit_failed),
        split_defects_new=tuple(split_defects_new),
        successor_cik_new=tuple(successor_cik_new),
        insider_amendments_new=tuple(insider_amendments_new),
        insider_amendments_more=insider_amendments_more,
    )


@dataclass
class DailyPassOutcome:
    """One COMPLETED daily pass: the per-thesis results plus the run metadata the artifact carries —
    what the admin "run now" job needs to shape its poll result exactly like a parsed run log.
    ``log_path`` is the written run-of-record artifact (or ``None`` — the write is fail-open). ``catch_up``
    = the pass was a ``--catch-up`` (recorded on the artifact; since O5 its freeze page judges the theses it
    processed, like any pass — ``assess_health``).

    ``benchmark_errors`` / ``benchmark_leg_failed`` (G4) carry the SHARED-INPUT refresh leg's outcome off
    the pass, because the admin "run now" job rebuilds its poll payload from THIS object
    (``app/routers/admin.py``) — without them that payload would disagree with the artifact on disk about
    whether the night was healthy. ``tape_evaluated`` / ``tape_stale_new`` (G5a) ride along for the same
    reason: the cross-run stale diff is computed ONCE, in the pass, and both the artifact and that poll
    payload must report the same thing."""

    results: list[ThesisRunResult]
    asof: date
    allow_live: bool
    started_at: datetime
    finished_at: datetime
    log_path: Path | None
    catch_up: bool = False
    benchmark_errors: int = 0
    benchmark_leg_failed: bool = False
    # G5a/F1: did this pass EVALUATE feed recency (live + at least one monitor enabled), and which names
    # became stale since the previous evaluated live pass — (kind, label) pairs, the label being the ticker
    # or the security id when ticker-less. The THRESHOLDS each feed was judged under ride here too (F6):
    # the Admin panel describes an inventory, and describing it with a number the pass did not use — because
    # the setting changed between the pass and the read — would misstate what is on screen. 0 is a real
    # value (that feed's monitor was off), so readers must check for presence, never truthiness.
    tape_evaluated: bool = False
    tape_stale_new: tuple[StaleFeedLabel, ...] = ()
    tape_stale_days: int = 0
    fund_shares_stale_days: int = 0
    # I1/I2 — the run identity + the non-secret env fingerprint (``env_fingerprint``) the artifact carries
    code_sha: str | None = None
    config_hash: str | None = None
    run_kind: str | None = None
    env: dict | None = None
    # O1 — the per-name error diff, computed ONCE in the pass (the artifact and the Admin poll payload must
    # agree): was it evaluated (a live pass), the NEWLY failing (security, leg) rows, the known-failing count
    name_errors_evaluated: bool = False
    name_errors_new: tuple[NameIngestError, ...] = ()
    name_errors_known: int = 0
    # V1 — the self-audit's results (``pipeline/self_audit.py``)
    audit: tuple[AuditResult, ...] = ()
    # T4 — the split-basis monitor's outcome (``pipeline/split_basis.py``): computed ONCE in the pass, so
    # the artifact and the Admin poll payload carry the same inventory and the same new-defect diff
    split: SplitScan = field(default_factory=SplitScan)
    # T2 — the rename probe's outcome (`pipeline/rename_probe.py`): the splice-verified recommendations for
    # tonight's renamed tapes, computed ONCE in the pass so the artifact, the printed report and the Admin
    # panel all read the same probe
    rename_probe: RenameProbe = field(default_factory=RenameProbe)
    # T7 — the successor-CIK detector's outcome (`pipeline/successor_cik.py`), computed ONCE in the pass
    successor: SuccessorScan = field(default_factory=SuccessorScan)
    # Q18 — the skipped-filing diff (``pipeline/ingest_skips.py``): computed ONCE in the pass, so the
    # artifact and the Admin poll payload carry the same inventory, the same known counts and the same new
    skips: SkipDiff = field(default_factory=SkipDiff)
    # X5 — where the pass spent its time, requests and memory (`pipeline/pass_timings.py`): the snapshot the
    # artifact was first written with, replaced by the FINAL block once the post-artifact legs ran
    timings: dict | None = None
    # R8-A — the Form 4/A inventory owed an operator decision (``pipeline/insider_amendment_scan.py``),
    # computed ONCE in the pass so the artifact, the printed report and the page agree
    amendments: AmendmentScan = field(default_factory=AmendmentScan)
    # the freeze page's RATIO FLOOR (``pipeline/edgar_coverage.py``): the pass's per-company EDGAR freshness
    # and the floor it was judged under, computed ONCE so the artifact, the Admin poll payload and the page
    # agree. None = not measured (the pass processed no thesis).
    edgar_coverage: EdgarCoverage | None = None

    def payload_kwargs(self) -> dict:
        """Every run-level field ``cron_run_log.build_run_payload`` / ``write_cron_run_log`` take, from this
        outcome — ONE mapping, so the artifact the pass writes and the payload the Admin "Run daily now"
        poll rebuilds cannot drift by a forgotten keyword (G4/G5a each had to be threaded twice)."""
        return {
            "asof": self.asof,
            "allow_live": self.allow_live,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "catch_up": self.catch_up,
            "benchmark_errors": self.benchmark_errors,
            "benchmark_leg_failed": self.benchmark_leg_failed,
            "tape_evaluated": self.tape_evaluated,
            "tape_stale_new": self.tape_stale_new,
            "tape_stale_days": self.tape_stale_days,
            "fund_shares_stale_days": self.fund_shares_stale_days,
            "code_sha": self.code_sha,
            "config_hash": self.config_hash,
            "run_kind": self.run_kind,
            "env": self.env,
            "name_errors_evaluated": self.name_errors_evaluated,
            "name_errors_new": self.name_errors_new,
            "name_errors_known": self.name_errors_known,
            "audit": self.audit,
            "split": self.split,
            "rename_probe": self.rename_probe,
            "successor": self.successor,
            "skips": self.skips,
            "timings": self.timings,
            "amendments": self.amendments,
            "edgar_coverage": self.edgar_coverage,
        }


def run_daily_pass(
    *,
    asof: date | None = None,
    allow_live: bool = True,
    notifier: Notifier | None = None,
    catch_up: bool = False,
    run_kind: str = "manual",
    run_lock: PassLock | None = None,
    on_started: Callable[[datetime, date], None] | None = None,
) -> DailyPassOutcome:
    """The cron's FULL unit of work, under the cross-process call-of-record lock and the crash page.

    C1 — THE LOCK. The whole pass — the refresh pre-legs, the per-thesis loop, the artifact, the post-legs —
    runs while this process holds the session-level advisory lock (``pipeline/run_lock.py``), so two passes
    (the sidecar and the Admin button, a hand ``docker exec`` and a retry, a pass and a backfill) can never
    run ``record_if_changed``'s read-compare-append against each other. ``run_lock`` = a lock the CALLER
    already took (``main`` takes it first so it can re-check the ``--catch-up`` guard under it); ownership
    TRANSFERS — this function releases it when the pass ends, however it ends. ``None`` = take it here.
    Another holder raises ``PassAlreadyRunning`` before anything runs: nothing is written and nothing is
    paged (a refusal is not a crash — the running pass pages its own night).

    H3 — THE CRASH PAGE. Any other exception escaping the pass (an unreachable DB at connect, a bug outside
    the per-thesis isolation) sends ONE health page through ``notifier`` (``HealthEvent.crashed``) and is
    then re-raised UNCHANGED — the page is fail-open (``_page_crash``), so it can never mask the original.
    (The shell's own page and the external heartbeat cover a process that is KILLED, where no Python runs.)

    M1 — THE START ARTIFACT. The moment the lock is held, the pass writes its run artifact with
    ``status: "running"`` (``_write_start_artifact``: the as-of, mode, run kind, identity and env), and
    ``_run_daily_pass_locked`` FINALIZES that same file to ``"finished"`` at its end — one file per pass, so
    a pass that dies mid-loop is no longer invisible (10-12: no artifact, Admin read healthy ~18 h). On the
    crash path above the file is re-marked ``"crashed"`` (only while it still reads ``running`` — a pass
    that finalized and then raised keeps its finished record); a hard kill leaves ``running``, which the
    ``--catch-up`` guard never credits and the Admin page alarms on past the run timeout. Every one of these
    writes is fail-open: none can fail or mask the pass.

    ``on_started(started_at, asof)`` is called once the start artifact is written (fail-open): the Admin
    button's bounded runner (``pipeline/bounded_pass.py``) learns from it which artifact to mark ``crashed``
    if it has to stop the pass at the run timeout."""
    notifier = notifier or get_notifier()
    lock = run_lock
    resolved_asof = asof
    started_at: datetime | None = None
    timer: pass_timings.PassTimer | None = None
    timer_token = None
    try:
        resolved_asof = asof or market_today()
        if lock is None:
            lock = acquire_pass_lock(holder=run_kind)
        started_at = datetime.now(timezone.utc)
        # X5 — the pass's meter (`pipeline/pass_timings.py`), ACTIVE for the whole pass: measurement only,
        # fail-open (a meter that cannot start leaves the pass unmeasured, never failed)
        timer = pass_timings.start()
        timer_token = pass_timings.activate(timer)
        start_path = _write_start_artifact(
            notifier,
            asof=resolved_asof,
            allow_live=allow_live,
            started_at=started_at,
            catch_up=catch_up,
            run_kind=run_kind,
        )
        _checkpoint_timings(timer, start_path, started_at)
        if on_started is not None:
            try:
                on_started(started_at, resolved_asof)
            except Exception:  # noqa: BLE001 — a hook never fails or masks the pass
                _log.exception("the on_started hook failed (fail-open — the pass runs regardless)")
        outcome = _run_daily_pass_locked(
            asof=resolved_asof,
            allow_live=allow_live,
            notifier=notifier,
            catch_up=catch_up,
            run_kind=run_kind,
            started_at=started_at,
        )
        _finish_timings(outcome, timer)
        return outcome
    except PassAlreadyRunning:
        raise  # a refusal, not a crash — nothing ran, nothing to page
    except BaseException as exc:
        if not isinstance(exc, (SystemExit, GeneratorExit)):
            _page_crash(notifier, resolved_asof, exc)
            if started_at is not None:
                _mark_crashed(started_at, exc, timer)
        raise
    finally:
        pass_timings.deactivate(timer_token)
        if lock is not None:
            lock.release()


def _checkpoint_timings(
    timer: pass_timings.PassTimer | None, start_path: Path | None, started_at: datetime
) -> None:
    """X5 — after each thesis, write the timings so far onto this pass's START artifact (still ``running``),
    so a pass that is KILLED mid-loop (the C2 run timeout, the OOM killer, the VM) still leaves where it
    spent its time — the cold-ingest pass the timeout stops is the one the scale question is about. In place
    and only while the file reads ``running`` and is this pass's (``amend_run_timings``); fail-open.
    """
    if timer is None or start_path is None:
        return

    def _checkpoint(timings: dict) -> None:
        amend_run_timings(
            start_path, started_at=started_at, timings=timings, status=RUN_STATUS_RUNNING
        )

    timer.on_thesis_end = _checkpoint


def _finish_timings(outcome: DailyPassOutcome, timer: pass_timings.PassTimer | None) -> None:
    """X5 — the FINAL timings block, taken after the post-artifact legs (the health page, the shell sweep,
    the SPAC radar): onto the outcome (so the Admin poll payload and the printed summary read it) and into
    the finished artifact in place (``amend_run_timings`` — only while it reads ``finished`` and is this
    pass's). Fail-open: a fault here leaves the artifact's first snapshot standing."""
    try:
        final = pass_timings.snapshot(timer)
        if final is None:
            return
        outcome.timings = final
        if outcome.log_path is not None:
            amend_run_timings(outcome.log_path, started_at=outcome.started_at, timings=final)
    except Exception:  # noqa: BLE001 — measurement never costs the pass
        _log.exception(
            "final pass timings failed (fail-open — the artifact keeps its first snapshot)"
        )


def _write_start_artifact(
    notifier: Notifier,
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    catch_up: bool,
    run_kind: str,
) -> Path | None:
    """M1 — the pass's START artifact (``cron_run_log.write_run_start``), carrying the same identity the
    finalized artifact will (``Settings.image_sha``, the ``DEFAULT_CONFIG`` hash, the run kind, the non-secret
    env fingerprint). FAIL-OPEN as a whole — the identity reads included: a pass whose start record cannot
    be written still runs (and still finalizes its artifact at the end). Returns the start file's path (X5
    checkpoints the timings onto it), or ``None``."""
    try:
        return write_run_start(
            asof=asof,
            allow_live=allow_live,
            started_at=started_at,
            catch_up=catch_up,
            code_sha=get_settings().image_sha,
            config_hash=config_hash(DEFAULT_CONFIG),
            run_kind=run_kind,
            env=env_fingerprint(notifier),
        )
    except Exception:  # noqa: BLE001 — the start record is best-effort, never the pass's driver
        _log.exception("cron start artifact failed (fail-open — the pass runs regardless)")
        return None


def _mark_crashed(
    started_at: datetime, exc: BaseException, timer: pass_timings.PassTimer | None = None
) -> None:
    """M1 — re-mark this pass's start artifact ``crashed`` on the H3 crash path, carrying the X5 timings
    up to the crash. Fail-open against EVERYTHING (``BaseException``): it runs while another exception is
    propagating and must never replace it."""
    try:
        mark_run_crashed(
            started_at=started_at,
            crashed_at=datetime.now(timezone.utc),
            error=_crash_label(exc),
            timings=pass_timings.snapshot(timer) if timer is not None else None,
        )
    except BaseException:  # noqa: BLE001 — the marker can never mask the original exception
        _log.exception("the crash marker itself failed (fail-open — re-raising the original)")


def _run_daily_pass_locked(
    *,
    asof: date,
    allow_live: bool,
    notifier: Notifier,
    catch_up: bool,
    run_kind: str,
    started_at: datetime | None = None,
) -> DailyPassOutcome:
    """The pass BODY, run by ``run_daily_pass`` while it holds the call-of-record lock (C1) and inside its
    crash page (H3). The cron's FULL unit of work as ONE callable: connect → ``run_daily`` → write the
    run-of-record artifact (R3, fail-open) → emit the health page (R4). Extracted from ``main`` UNCHANGED
    so the admin "Run daily now" trigger fires the exact pass the nightly cron does — a manual run writes the same
    artifact (it shows in the run history) and pages through the same health seam. ``main`` keeps the CLI
    concerns only (args, the R6 catch-up guard, the printed report + exit code).

    ``catch_up`` (the CLI's ``--catch-up``, threaded by ``main``) makes M2 SKIP every thesis whose on-time
    row already stands, and the artifact records it. Its freeze page is no longer skipped
    wholesale (O5, ``assess_health``): it judges the theses the pass PROCESSED by their COMPLETED EDGAR
    fetches, so a catch-up whose pulls all failed pages like any pass, while its skipped theses (which fetch
    nothing by construction) are left out. The ingest, the recording gate, and the call-of-record are
    byte-identical to a scheduled pass.

    G4 — the benchmark refresh leg's outcome is now CARRIED, not just printed: its counts go into the
    run-of-record artifact and into ``assess_health``, so a night that ran ``benchmark_rs`` on a stale
    SPY/IWM tape pages like an errored thesis instead of scrolling past on stdout.

    ``run_kind`` (0044) passes straight to ``run_daily`` and is stamped on every recorded row. It DEFAULTS to
    ``"manual"``, which is exactly right for this function's two non-CLI callers: the Admin "Run daily now"
    button is an operator-kicked run and should say so. Only ``main`` (via ``--run-kind``) and therefore only
    the sidecar can stamp ``"cron"``.

    I1/I2 / O1 / V1 (Phase 1B) — after the per-thesis loop the pass also stamps its run identity + env
    fingerprint, diffs the per-name ingest errors against the previous evaluated pass, and runs the
    self-audit; all three ride the artifact (``DailyPassOutcome.payload_kwargs``) and the R4 page. Every
    connection it opens carries the C2 ``statement_timeout`` (``_pass_connect``).

    ``started_at`` (M1) is the instant ``run_daily_pass`` stamped on the START artifact; the final write
    reuses it, so it lands on — and finalizes — that same file. ``None`` stamps it here (a direct caller).
    """
    started_at = started_at or datetime.now(timezone.utc)
    # G4 — the benchmark leg's outcome, initialized CLEAN so a --no-live pass (which skips the leg) reports
    # nothing to page. `benchmark_errors` counts individual benchmark pulls that failed; the separate
    # `benchmark_leg_failed` marks the leg raising before it produced any per-benchmark result, because "1
    # of 2 benchmarks is stale" and "nothing refreshed at all" are different news and a single count cannot
    # say which. The fundamentals refresh runs per thesis inside `run_daily` (Q17) and reports below.
    benchmark_errors = 0
    benchmark_leg_failed = False
    # Band-02 shared-input refresh — run BEFORE run_daily so the per-thesis calls read FRESH data: the
    # SPY/IWM benchmark tape (benchmark_rs). It mirrors the SPAC-radar leg below — OWN connection + lazy
    # import + fail-open — so a refresh can NEVER touch run_daily's per-thesis edgar_fetches freeze counter
    # or the recording gate. A refresh fault is a passenger, never the driver: print loud, keep going.
    # Skipped on --no-live (a cache-only dev run can't refresh/record; `python -m pipeline.ingest_*` is the
    # manual cache-only path). The quarterly FUNDAMENTALS refresh is NOT here any more (Q17 zero-lag): it
    # runs per thesis inside `run_daily`, after that thesis's ingest re-pulled tonight's submissions
    # indexes — before the loop the gate could only read last night's, a pass late.
    if allow_live:
        try:  # (a) benchmarks first
            from pipeline.ingest_benchmarks import ingest_benchmarks

            # force_refresh=True: the recurring path MUST bypass a stale cache HIT and re-pull fresh SPY/IWM
            # bars — else the tape freezes and benchmark_rs degrades silently (the #72 lesson; the same
            # reason run_daily force-refreshes ingest_thesis).
            bench_conn = _pass_connect()
            try:
                bench_results = timed("benchmarks")(ingest_benchmarks)(
                    bench_conn, allow_live=True, force_refresh=True
                )
            finally:
                bench_conn.close()
            bench_bars = sum(r.bars_appended for r in bench_results)
            bench_errs = [r.error for r in bench_results if r.error]
            benchmark_errors = len(bench_errs)  # G4 — carried to the artifact + the health page
            # D1/D2 — in-window restatements and unsettled (today's in-progress) bars, only when nonzero
            bench_rs = sum(r.bars_restated for r in bench_results)
            bench_wh = sum(r.bars_withheld for r in bench_results)
            rs = f", {bench_rs} restated" if bench_rs else ""
            wh = f", {bench_wh} unsettled bar(s) withheld" if bench_wh else ""
            print(
                f"benchmarks refresh: +{bench_bars} bars{rs}{wh}, {len(bench_results)} benchmarks"
            )
            for err in bench_errs:  # loud only when nonzero (loudness marks the exception)
                print(f"  benchmarks refresh ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — a refresh leg is a passenger, never the driver
            benchmark_leg_failed = True  # G4 — still fail-open for the RUN, but no longer silent
            print(f"WARNING: benchmarks refresh leg failed: {e}")
    # Q18 — THE SKIPPED-FILING BASELINE, read BEFORE the loop: the filing legs print a line for every
    # filing they skip, and an unstorable filing is re-tried (and re-printed) every pass — MEASURED ~440
    # lines a night, ~73% of the log, all audited as legacy pre-XML Form 4s and owner-filed 13Ds. A filing
    # that skipped the same way in its thesis's last-known inventory (the per-thesis carry-forward, so a
    # catch-up counts for the theses it covered — `previous_ingest_skips`) is "known": the legs
    # record it without re-printing, and the diff below counts it. `None` (no baseline yet) mutes nothing.
    # The read is fail-open by construction (`previous_ingest_skips` skips a bad artifact) and changes
    # only what is PRINTED — what is stored, skipped or raised is identical.
    prior_skips = timed("baselines")(previous_ingest_skips)()
    # R8-B — THE FORM 4/A SWEEP (`pipeline/insider_amendment_scan.run_amendment_sweep`): apply every Form 4/A
    # resolved to a definite original that a pre-R8-B ingest left without a decision (one SYSTEM `apply` each,
    # `recorded_at = now` — a replay pinned before tonight still reads the pending state it held). BEFORE the
    # per-thesis loop, so tonight's calls read the applied state (new 4/As are applied at ingest, inside the
    # loop). Idempotent (an amendment with any decision is never touched — the operator's stay the operator's);
    # own connection + transaction; fail-open (a fault is printed loud and rides the artifact via the scan).
    amendment_sweep = timed("amendment_sweep")(run_amendment_sweep)(
        allow_live=allow_live, connect=_pass_connect
    )
    if amendment_sweep.error:
        print(f"WARNING: Form 4/A sweep failed: {amendment_sweep.error}")
    conn = _pass_connect()
    try:
        results = run_daily(
            conn,
            asof=asof,
            allow_live=allow_live,
            notifier=notifier,
            run_kind=run_kind,
            catch_up=catch_up,
            known_skips=known_keys(prior_skips),
        )
    finally:
        conn.close()
    if allow_live:
        _print_fundamentals(results)
    finished_at = datetime.now(timezone.utc)
    # G5a — THE NEWLY-STALE DIFF, computed BEFORE the artifact is written (ordering is load-bearing: once
    # this run's artifact exists it would be its own "previous" and nothing could ever read as newly stale).
    # The diff is keyed on security_id, NOT ticker — a ticker-less name must still be able to page, and a
    # ticker changing under a name is half of why this monitor exists. `previous_stale_tapes()` returns None
    # when no evaluated live artifact exists yet (the first pass after the deploy): that is deliberately
    # treated as "everything currently stale is news", so the operator gets the inventory ONCE instead of a
    # silent first night. Thereafter only genuinely-new names page (a handful of known-dead tapes must not
    # re-page forever). A --no-live pass evaluates nothing and records `tape_evaluated: false`, so it can
    # never reset the baseline the next live pass diffs against.
    settings = get_settings()
    tape_stale_days = settings.tape_stale_days
    fund_shares_stale_days = settings.fund_shares_stale_days
    # "Evaluated" = this pass LOOKED at at least one feed. Either threshold alone is enough, so disabling
    # one monitor never demotes the artifact as a baseline for the other. The one consequence, deliberate
    # and never silent: re-ENABLING a feed later diffs against an artifact holding none of its rows, so its
    # whole current inventory pages once — the same designed behavior as the first pass after deploy.
    tape_evaluated = allow_live and (tape_stale_days > 0 or fund_shares_stale_days > 0)
    tape_stale_new: tuple[StaleFeedLabel, ...] = ()
    if tape_evaluated:
        # The diff key is `kind:security_id` — the KIND matters as much as the id now: one name can go
        # stale on its price tape and later on its fund-shares sample, and the second is news the operator
        # has not been told, with a different repair. Keyed on the id (never the ticker) because a
        # ticker-less name must still be able to page and a ticker can change under a name.
        #
        # G5b — CLOSED tapes are EXCLUDED from the diff, so a name that stopped trading (delisted / acquired
        # / deregistered) NEVER pages as newly-stale: it is not a feed gap to repair and paging "set the
        # vendor price symbol" for it would be wrong (#7/WB#3 — a page true of an expected event is noise).
        # It stays in each thesis's `tape_stale` (so the panel renders it quietly as "closed" and #9 holds
        # — never dropped from the monitor); only the loud NEWLY-STALE page skips it. T1 — the baseline
        # (`previous_stale_tapes`) skips closed rows too, so a name RECLASSIFIED closed → repair (VRME, whose
        # "closed" rested on a 2025 warrant delisting) is absent from it and pages ONCE, as the news it is.
        # Q8 — an ACKNOWLEDGED unpriceable row is quiet the same way (`StaleTape.quiet`): the operator
        # ratified "keep it, unpriced", so it never pages; a REVOKED acknowledgment is absent from the
        # baseline (which skips quiet rows too) and pages ONCE as the news it then is.
        current = {
            f"{s.kind}:{s.security_id}": s for r in results for s in r.tape_stale if not s.quiet
        }
        # The baseline is PER THESIS (`cron_run_log.carried_inventory`): each thesis as the newest pass that
        # COVERED it saw it — so a --catch-up that skipped theses (M2) counts for the ones it covered, a name
        # it first flagged pages once, and a name it saw clear pages again when it comes back.
        prior = timed("baselines")(previous_stale_tapes)()
        new_keys = sorted(current) if prior is None else sorted(set(current) - prior)
        tape_stale_new = tuple(
            StaleFeedLabel(kind=current[k].kind, label=current[k].label) for k in new_keys
        )
    # O1 — THE NEWLY-FAILING NAME DIFF, the stale-tape diff's twin and computed in the same place for the
    # same reason (before this run's artifact exists, or it would be its own baseline). Keyed on
    # `security_id:leg` (`pipeline/name_errors.py`): the ticker can change under a name, and the error TEXT
    # can embed a URL or retry count that would read as "new" every night. Deduped across theses — one dead
    # leg is one row. `previous_name_errors()` is None before any evaluated artifact exists (the first pass
    # after this deploys), which pages the current inventory ONCE; thereafter only a newly-failing
    # (name, leg) pages and the known ones collapse to a quiet count (`_report`). A --no-live pass's errors
    # are cache misses, not tonight's feed: it records its rows but is not evaluated (never a baseline).
    name_errors_evaluated = allow_live
    name_errors_new: tuple[NameIngestError, ...] = ()
    name_errors_known = 0
    if name_errors_evaluated:
        current_errors = dedup_name_errors(name_errors_of(x for r in results for x in r.ingested))
        prior_errors = timed("baselines")(previous_name_errors)()
        new_error_keys = (
            sorted(current_errors)
            if prior_errors is None
            else sorted(set(current_errors) - prior_errors)
        )
        name_errors_new = tuple(current_errors[k] for k in new_error_keys)
        name_errors_known = len(current_errors) - len(name_errors_new)
    # Q18 — THE SKIPPED-FILING DIFF, the name-error diff's twin, computed here for the same reason (before
    # this pass's artifact exists, or it would be its own baseline) against the SAME baseline the legs
    # muted on. Deduped across theses (one filing is one row). A --no-live pass is recorded but not
    # evaluated: it is never a baseline.
    skips = diff_skips(
        (s for r in results for x in r.ingested for s in getattr(x, "ingest_skips", ())),
        prior_skips,
        evaluated=allow_live,
    )
    # T4 — THE SPLIT-BASIS MONITOR (`pipeline/split_basis.py`): one read-only, windowed scan of the active
    # basket union's stored tapes for a mixed split basis (an island, a lone cliff, a flapping bar). AFTER
    # `run_daily`, so it reads the bars tonight's price legs just re-versioned; BEFORE this run's artifact
    # is written, for the same reason as the two diffs above (once the artifact exists it would be its own
    # baseline). Flag only: nothing it finds reaches a detector or the card (Q10 is open). Fail-open for
    # the pass — a scan fault comes back as `split.error`, which the self-audit fails on (and so pages).
    split = timed("split_scan")(run_split_scan)(
        asof=asof,
        allow_live=allow_live,
        connect=_pass_connect,
        previous=previous_split_defects,
        clock=perf_counter,
    )
    # T2 — THE RENAME PROBE (`pipeline/rename_probe.py`, operator Q7 = nightly): for every stopped price
    # tape T1 marked `renamed`, ONE vendor fetch per EDGAR-listed candidate into a throwaway cache, spliced
    # against our stored tape (>= 5 shared dates, median close ratio within 1% = verified). A pending
    # RECOMMENDATION only — it writes nothing; the operator's Adopt on the Admin page applies it. Fail-open
    # for the pass (a fault rides `rename_probe.error`, printed loud and shown on Admin). Nothing to probe
    # (the common night) opens no connection and makes no request.
    rename_probe = timed("rename_probe")(run_rename_probe)(
        results=results,
        asof=asof,
        allow_live=allow_live,
        connect=_pass_connect,
        clock=perf_counter,
    )
    # T7 — THE SUCCESSOR-CIK DETECTOR (`pipeline/successor_cik.py`): for EVERY ingested member (fresh tapes
    # too), a stored CIK whose submissions list no tickers + SEC's ticker map assigning our symbol to another
    # CIK = the company now files under a successor issuer, and our filing legs read the dead CIK. A pending
    # RECOMMENDATION (the operator's Adopt applies it); BEFORE the artifact, so its own rows are never the
    # baseline it diffs against. Fail-open for the pass (a fault rides `successor.error`).
    successor = timed("successor_scan")(run_successor_scan)(
        results=results,
        allow_live=allow_live,
        connect=_pass_connect,
        previous=previous_successor_keys,
        clock=perf_counter,
    )
    # R8-A / R8-B — THE FORM 4/A INVENTORY (`pipeline/insider_amendment_scan.py`): every stored 4/A of the
    # active basket union OWED the operator's decision (since R8-B only what the system cannot resolve — an
    # ambiguous link touching a signal row inside the review horizon), read whole from the amendment tables and
    # diffed against the last evaluated pass so a NEW one pages once; plus the count of SYSTEM applications
    # recorded since this pass started (the sweep above + tonight's ingest — one quiet report line, never a
    # page). BEFORE the artifact, for the same reason as the diffs above. Fail-open (a fault rides `error`).
    amendments = timed("amendment_scan")(run_amendment_scan)(
        asof=asof,
        allow_live=allow_live,
        connect=_pass_connect,
        previous=previous_amendment_keys,
        clock=perf_counter,
        since=started_at,
        sweep=amendment_sweep,
    )
    # I1/I2 — THE RUN IDENTITY on the artifact: the same sha / cfg hash / run kind `run_daily` stamped on
    # every row it wrote (the identical expressions — `run_daily` holds its cfg local as DEFAULT_CONFIG and
    # reads `image_sha` the same way), plus the non-secret env fingerprint the Admin page alarms on.
    code_sha = settings.image_sha
    cfg_hash = config_hash(DEFAULT_CONFIG)
    env = env_fingerprint(notifier)
    # V1 — THE SELF-AUDIT, after the loop committed and before the artifact is written (its results ride
    # the artifact). It reads the TABLE through its own read-only connection (`_pass_connect`, so it carries
    # the statement ceiling too). Building the context can fail on a malformed RUN_AT / market zone — that
    # is reported as a FAILED audit, never a silently absent one, and never a failed pass.
    # The SETTLE check judges the price rows recorded since the previous pass that judged them (read from
    # the artifacts BEFORE this pass's own is finalized — its start artifact is `running`, never a baseline),
    # floored at the guard's deploy (`self_audit.settle_window_since`).
    try:
        settle_prev = timed("baselines")(previous_settle_audit_end)()
        audit_ctx = AuditContext(
            asof=asof,
            started_at=started_at,
            allow_live=allow_live,
            run_kind=run_kind,
            code_sha=code_sha,
            results=results,
            run_at=parse_run_at(settings.cron_run_at),
            tz=market_tz(),
            connect=_pass_connect,
            split=split,
            settle_at=settings.price_settle_time,
            bars_since=settle_window_since(started_at, settle_prev),
        )
        audit: tuple[AuditResult, ...] = tuple(timed("self_audit")(run_audit)(audit_ctx))
    except Exception as e:  # noqa: BLE001 — a broken audit is an alarm, never a failed pass
        audit = (AuditResult("audit", False, f"the self-audit could not start: {_crash_label(e)}"),)
    # THE FREEZE PAGE's RATIO FLOOR — the per-company EDGAR freshness over the theses this pass processed,
    # judged against the floor in force NOW; computed once for the artifact (so the Admin history re-judges
    # it under this same floor, whatever the setting says later) and the page below.
    edgar_coverage = coverage_of(results, floor=settings.freeze_completed_floor)
    outcome = DailyPassOutcome(
        results=results,
        asof=asof,
        allow_live=allow_live,
        started_at=started_at,
        finished_at=finished_at,
        log_path=None,
        catch_up=catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
        tape_evaluated=tape_evaluated,
        tape_stale_new=tape_stale_new,
        tape_stale_days=tape_stale_days,
        fund_shares_stale_days=fund_shares_stale_days,
        code_sha=code_sha,
        config_hash=cfg_hash,
        run_kind=run_kind,
        env=env,
        name_errors_evaluated=name_errors_evaluated,
        name_errors_new=name_errors_new,
        name_errors_known=name_errors_known,
        audit=audit,
        split=split,
        rename_probe=rename_probe,
        successor=successor,
        skips=skips,
        # X5 — the timings so far; `run_daily_pass` amends the artifact with the final block (post-legs)
        timings=pass_timings.snapshot(),
        amendments=amendments,
        edgar_coverage=edgar_coverage,
    )
    # R3 — the cron's run-of-record, so the next freeze is noticed by the platform, not by eye. Written
    # AFTER the run from the collected results (write-only, no DB); fail-open, so it never fails the cron.
    # M1 — the path is the started-at instant, so this FINALIZES the start artifact (status "finished").
    outcome.log_path = timed("artifact_write")(write_cron_run_log)(
        results, **outcome.payload_kwargs()
    )
    # R4 — the DURABLE page: a freeze / withheld / errored run alerts through the notifier (Slack when
    # configured, else a loud log line). Healthy runs are silent. This is what makes the platform notice its
    # own blindness — the gap that let R1 hide 11+ days. Fail-open (notify_health never raises). O5: the
    # FREEZE predicate reads COMPLETED fetches over the theses the pass processed — a catch-up's M2-skipped
    # theses are out of it and the rest are judged like any pass (`catch_up` matters only when re-deriving
    # a pre-O5 artifact). G4: the benchmark leg's counts page here too, and they are on the artifact above
    # so the admin history re-derives the SAME verdict this run paged (never a green row over a night that
    # alerted).
    health = assess_health(
        results,
        asof=asof,
        allow_live=allow_live,
        catch_up=catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
        tape_stale_new=tape_stale_new,
        # O1 + V1 — the newly-failing names and the failed audit checks page through the SAME event
        name_errors_new=tuple(e.label for e in name_errors_new),
        audit_failed=failed_labels(audit),
        # T4 — the NEW split-basis defects page once (benign-class: a vendor tape defect, flag only)
        split_defects_new=split.new_labels,
        # T7 — the NEW successor-CIK rows page once (benign-class: a data gap the operator's Adopt repairs)
        successor_cik_new=successor.new_labels,
        # R8-A — the NEW Form 4/A amendments owed a decision page once (benign-class)
        insider_amendments_new=amendments.new_labels,
        insider_amendments_more=amendments.new_more,
        # the RATIO FLOOR: too few of the eligible companies got fresh SEC data pages FROZEN too
        edgar_coverage=edgar_coverage,
    )
    if health is not None:
        timed("health_page")(notifier.notify_health)(health)
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

            sweep_conn = _pass_connect()
            try:
                sw = timed("shell_sweep")(run_shell_sweep)(
                    sweep_conn, allow_live=True, reenrich=(asof.weekday() == 0)
                )
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
    # The nightly leg passes the notifier so a shell that transitions INTO `announced` AND matches a
    # thesis fires an ANNOUNCED-only page (#7); the page runs after the radar's own commits and is
    # fail-open inside run_spac_radar, so it can never roll back radar facts or fail this leg.
    if allow_live:
        try:
            from radar.spac import run_spac_radar

            radar_conn = _pass_connect()
            try:
                rr = timed("spac_radar")(run_spac_radar)(
                    radar_conn, until=asof, days=3, allow_live=True, notifier=notifier
                )
            finally:
                radar_conn.close()
            print(f"SPAC radar: {rr.summary}")
            for err in rr.errors:
                print(f"  SPAC radar ERROR: {err}")
        except Exception as e:  # noqa: BLE001 — the radar is a passenger, never the driver
            print(f"WARNING: SPAC radar leg failed: {e}")
    return outcome


def print_lock_waits(results: list[ThesisRunResult]) -> None:
    """W1 — what the per-company write lock cost this pass, deduped per security across theses; nothing at
    all on the common night when no other writer held a company.

    - LOUD, one line per name: a company another writer (a Workbench ingest, a CLI run) held past the bounded
      wait, so THIS pass did not run its legs — the holder was writing them; the thesis recorded anyway with
      ``ingest_fresh=false``. Never dropped (#9): it is on the artifact as ``company_lock_waits``.
    - quiet, ONE line: the names the pass waited for and then ingested (the wait worked as designed).
    """
    busy: dict[UUID, str] = {}
    waited: dict[UUID, float] = {}
    for r in results:
        for x in r.ingested:
            if getattr(x, "lock_busy", None):
                busy[x.security_id] = f"{x.ticker or x.security_id}: {x.lock_busy}"
            elif getattr(x, "lock_waited_s", 0.0):
                waited[x.security_id] = max(x.lock_waited_s, waited.get(x.security_id, 0.0))
    if busy:
        print(
            f"COMPANY LOCK — {len(busy)} name(s) NOT ingested by this pass (another writer held the "
            "company past the wait; its theses recorded with ingest_fresh=false):"
        )
        for line in busy.values():
            print(f"  {line}")
    if waited:
        print(
            f"{len(waited)} name(s) waited for another writer's company lock, then ingested "
            f"(longest {max(waited.values()):.0f}s)"
        )


def _report(
    results: list[ThesisRunResult],
    *,
    name_errors_new: tuple[NameIngestError, ...] = (),
    name_errors_known: int = 0,
    name_errors_evaluated: bool = True,
    split: SplitScan | None = None,
    probe: RenameProbe | None = None,
    successor: SuccessorScan | None = None,
    skips: SkipDiff | None = None,
    amendments: AmendmentScan | None = None,
) -> int:
    """Print a per-thesis summary; return the number of theses that ERRORED.

    That count is still printed and still pages (``assess_health`` -> ``HealthEvent.errored``), but it is
    no longer the process exit signal — ``_failed_to_record`` (below) is. A night that recorded 11 of 12
    theses is a night with a call-of-record, and exiting non-zero for it made the sidecar sleep 20 minutes
    and fire a retry the guard then no-opped (F4).

    O1 — the per-name ingest errors, split by NOVELTY (the pass computed the diff; ``DailyPassOutcome``):
    one LOUD line per newly failing (name, leg) with its reason, and ONE quiet line counting the known ones
    (the same names and legs as the last evaluated pass — a renamed / closed / never-priced name errors every
    night, and re-printing those forever would bury the new one). A pass that did not evaluate (``--no-live``)
    prints its error count quietly, undiffed. The full reasons live in the run artifact either way.

    M2 — the theses a ``--catch-up`` SKIPPED (an on-time row already stands) collapse into ONE quiet line
    instead of a per-thesis line each: nothing happened to them, on purpose.

    T4 — the split-basis monitor's block (``split_basis.report_lines``): NEW defects loud, one line each;
    the KNOWN ones one quiet count; a failed scan loud; nothing on a clean or un-scanned night.

    T2 / Q8 / T3 — a RENAMED row in the loud stale block carries its splice-probe verdict (``probe``: the
    verified successor to Adopt, or why none verified); an UNPRICEABLE row is named as such; an
    ACKNOWLEDGED unpriceable row moves to its own quiet lowercase line (the operator ratified it); the
    excused EXPECTED price 404s collapse to ONE quiet line; a failed probe is one loud line.

    T7 — the successor-CIK block (``successor_cik.report_lines``): NEW rows loud, known ones one quiet line.
    """
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    withheld = [r for r in results if r.withheld_reason]
    errored = [r for r in results if r.error]
    skipped = [r for r in results if r.skipped]
    for r in results:
        if r.skipped:
            continue  # M2 — summarized in the one quiet line below
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
        # P1 — TRANSACTIONS the sanity bound refused (a filer's impossible date: a leading-zero year, or a
        # year after the accession's own filing year). Loud only when nonzero, like every other exception
        # count on this line. It is an AGGREGATE for a gate to watch; the per-row detail (accession,
        # insider, sequence, raw date, reason) already printed in full during the ingest leg above — the
        # count accompanies that report and must never be read as a substitute for it.
        rejected = sum(x.form4_txn_rejected for x in r.ingested)
        rj = f" · {rejected} insider txn REJECTED (impossible date)" if rejected else ""
        rv = sum(x.price_bars_reversioned for x in r.ingested)
        rvs = (
            f" · {rv} bars RE-VERSIONED (restated)" if rv else ""
        )  # a split re-base — loud only then
        frv = sum(x.fund_shares_reversioned for x in r.ingested)
        frvs = (
            f" · {frv} fund shares RE-VERSIONED (restated)" if frv else ""
        )  # a corrected same-day count — loud only then
        print(f"  {r.name}: +{facts} facts{sk}{rj}{rvs}{frvs} · {mark}")
    # transitions get their own LOUD block — and only when there ARE any (loudness marks the
    # exception; the common all-quiet night prints nothing here)
    transitions = [r.transition for r in results if r.transition]
    if transitions:
        print("TRANSITIONS:")
        for t in transitions:
            print(f"  {t}")
    # W1 — the per-company write lock (`db/write_lock.py`), deduped ACROSS theses per security
    print_lock_waits(results)
    # G5a/F1 — the stale-feed inventory, deduped ACROSS theses per (kind, security) — one dead feed, not one
    # per holder — and printed only when there is one (loudness marks the exception). ONE BLOCK PER FEED
    # KIND, each headed with that feed's own noun and repair advice from `domain/feed_kinds`, because the
    # two are not the same problem and a merged list would give half the rows the wrong instruction. stdout
    # is the first place an operator looks at a cron run; the durable copies are the run-log artifact and
    # the Admin freshness panel.
    #
    # G5b — CLOSED tapes (a name that delisted / was acquired / deregistered) are split OUT of the loud
    # STALE-repair blocks and printed in their own QUIET block: they need no repair, so listing them under
    # "set the vendor price symbol" would give the wrong instruction, and shouting an expected event is
    # noise (#7). Quiet by casing: the STALE headers are UPPERCASE (loud), the closed header is lowercase.
    stale = {f"{s.kind}:{s.security_id}": s for r in results for s in r.tape_stale}
    recs = probe.by_security() if probe is not None else {}
    for kind in FEED_KINDS.values():
        rows = [s for s in stale.values() if feed_kind(s.kind).key == kind.key and not s.quiet]
        if not rows:
            continue
        print(f"STALE {kind.noun.upper()}S ({kind.advice}):")
        for s in rows:
            # T1 — a renamed row names the symbol(s) EDGAR lists now; T2 — and the splice probe's verdict
            # (the verified successor to Adopt on the Admin page). Shown, NEVER adopted: the write is the
            # operator's click (#3).
            renamed = (
                f" · renamed — EDGAR lists {', '.join(s.edgar_tickers or ())}" if s.renamed else ""
            )
            if s.renamed and s.security_id in recs:
                renamed += f" · {describe_rename(recs[s.security_id])}"
            # Q8 — never priced, and neither closed nor renamed: set a symbol, acknowledge, or remove
            unpriceable = (
                " · UNPRICEABLE — no stored bar ever; set a price symbol, acknowledge it, or remove it "
                "(Admin page)"
                if s.unpriceable
                else ""
            )
            print(
                f"  {s.label}: last {kind.edge_noun} {s.edge.isoformat() if s.edge else 'never'}"
                f"{renamed}{unpriceable}"
            )
    acked = [s for s in stale.values() if s.acknowledged]
    if acked:
        # Q8 — the operator acknowledged these ("keep it, unpriced"): quiet, still listed (#9)
        print(
            f"{len(acked)} acknowledged unpriceable name(s) — kept unpriced by the operator (quiet; "
            f"revocable on the Admin page): {', '.join(s.label for s in acked)}"
        )
    if probe is not None and probe.error:
        print(
            f"RENAME PROBE FAILED — {probe.error} (tonight's renamed tapes have no splice verdict; "
            "the rows above still name EDGAR's symbols)"
        )
    closed = [s for s in stale.values() if s.closed]
    if closed:
        print(
            "closed tapes (stopped trading — a delisting form filed near the last bar, no repair needed):"
        )
        for s in closed:
            edge = s.edge.isoformat() if s.edge else "never"
            print(f"  {s.label}: last bar {edge} · delisting filed {s.closed_at.isoformat()}")
    # O1 — NEW name errors loud (uppercase header, one line each with the reason), KNOWN ones one quiet
    # count (lowercase — the closed-tape block's casing convention); nothing at all on a clean night.
    if name_errors_new:
        print(
            "NAME ERRORS — NEWLY FAILING since the last pass (the thesis still recorded; reasons also "
            "in the run artifact):"
        )
        for e in name_errors_new:
            print(f"  {e.label}: {e.error}")
    if name_errors_evaluated and name_errors_known:
        print(
            f"{name_errors_known} known name error(s) — the same names and legs as the last pass "
            "(quiet; reasons in the run artifact)"
        )
    if not name_errors_evaluated:
        undiffed = len(dedup_name_errors(name_errors_of(x for r in results for x in r.ingested)))
        if undiffed:
            print(
                f"{undiffed} name error(s) — not diffed (a --no-live pass is never a baseline); "
                "reasons in the run artifact"
            )
    if split is not None:
        for line in split_report_lines(split):
            print(line)
    # T7 — the successor-CIK block: NEW rows loud, known ones one quiet line, a failed scan loud
    if successor is not None:
        for line in successor_report_lines(successor):
            print(line)
    # R8-A / R8-B — the Form 4/A block: NEW owed items loud, a failed sweep/scan loud, ONE quiet count line
    # (system applications this pass + the owed inventory)
    if amendments is not None:
        for line in amendment_report_lines(amendments):
            print(line)
    # T3 — the EXPECTED price 404s (closed / acknowledged names), deduped across theses: ONE quiet line
    expected: dict[UUID, ExpectedPriceMiss] = {}
    for r in results:
        for m in r.expected_price_misses:
            expected.setdefault(m.security_id, m)
    if expected:
        names = ", ".join(f"{m.ticker or m.security_id} ({m.reason})" for m in expected.values())
        print(
            f"{len(expected)} expected price 404(s) — closed or acknowledged-unpriceable names, not "
            f"errors (ingest_fresh unaffected): {names}"
        )
    # Q18 — the skipped filings: ONE quiet line per kind (known count, new count, the change vs the last
    # pass); each NEW filing already printed its own line during the ingest above
    if skips is not None:
        for line in skips.report_lines():
            print(line)
    # D2 — names whose in-progress bar was withheld (session not yet settled): ONE quiet line, deduped
    print_withheld_line(x for r in results for x in r.ingested)
    # Q11 / T5 — names whose older restated bars were held (a vendor revert awaiting a second pull): one
    # quiet line each, deduped across theses
    print_held_lines(x for r in results for x in r.ingested)
    if skipped:
        # M2 — one quiet line (lowercase, the known-errors convention): the catch-up left these alone
        print(
            f"{len(skipped)} thesis(es) skipped — an on-time call-of-record for the night already "
            f"stands and a catch-up never re-records one: {', '.join(r.name for r in skipped)}"
        )
    wh = f" · {len(withheld)} withheld" if withheld else ""  # loud only when it happens
    sk = f" · {len(skipped)} skipped (on-time)" if skipped else ""
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged{wh}{sk} · "
        f"{len(transitions)} transitions · {len(errored)} errored"
    )
    return len(errored)


def _failed_to_record(results: list[ThesisRunResult]) -> bool:
    """F4 — THE EXIT SIGNAL: did this pass fail to produce a call-of-record it should have produced?

    ``True`` only when theses existed, NONE of them reached the call step, and that was not by request.
    The sidecar's retry fires on this (``scripts/daily_cron.sh``), so the question it must answer is "does
    tonight have a record?" — not "did anything at all go wrong". Before F4 the exit code was the ERRORED
    count, so a single bad thesis out of twelve made the loop wait ``RETRY_DELAY_S`` (20 min) and fire a
    ``--catch-up`` the guard immediately no-opped, while the log read "run FAILED" over a night that
    recorded eleven. Per-thesis errors keep printing and keep paging through R4; only the exit code moved.

    The ladder, one clause per reason:

    1. **No theses at all -> not a failure.** A pass over an empty roster recorded nothing because there
       was nothing to record (a fresh install, or every thesis deleted). Retrying it nightly forever would
       be pure noise.
    2. **Some thesis has a BOOL ``recorded``, or was SKIPPED by a catch-up -> not a failure.** ``True`` =
       a new row was appended, ``False`` = unchanged so no row — which is the common healthy-quiet night and
       absolutely counts as "the call step ran". ``None`` means the call step FAILED for that thesis — unless
       the thesis was ``skipped`` (M2): a catch-up left it alone BECAUSE its on-time row already stands, so
       the night has that record (a catch-up that skipped all twelve must exit 0, never page a retry).
    3. **Every withhold was ``no-live`` -> not a failure.** A cache-only pass withholds every call BY
       DESIGN (``run_daily``'s recording gate, Source A). ``python -m pipeline.daily --no-live`` is a
       documented operator command and must keep exiting 0; the cron never passes ``--no-live``.
    4. **Otherwise -> failure.** Theses existed and none recorded for a real reason (a total ingest
       failure, or the call step erroring for every one). That night has no record and the retry is
       exactly right.

    ``isinstance(..., bool)``, not ``in (True, False)``: in Python ``1 in (True, False)`` is True, so an
    integer that wandered into the field would be mistaken for an outcome.

    THE ARTIFACT TWIN: ``cron_run_log._recorded_for_some_thesis`` answers the same question — did the call
    step run for some thesis — over a PARSED ARTIFACT rather than live results, because that is what the
    ``--catch-up`` guard has to work from. The two must agree on the same pass; a test pins it. They are
    separate functions on purpose (different inputs, different fail-open directions: the guard errs toward
    RUNNING on a damaged artifact, this errs toward exit 0 on an empty roster).

    THE THIRD CODE — 75 (C1, ``run_lock.EXIT_ALREADY_RUNNING``, EX_TEMPFAIL) — never comes from here: ``main``
    exits 75 BEFORE any pass runs when another process holds the call-of-record lock. It must never be 0 —
    the sidecar reads 0 as "the night recorded" and pings the external heartbeat on it, while the holder may
    still fail — and it is not 1 either (this invocation produced nothing; the night is the holder's to
    record). Like 1, it takes the sidecar's single ``--catch-up`` retry: a no-op if the holder recorded the
    night, a real pass if it did not. ``pipeline.backfill`` exits 75 on the same refusal; the Admin
    "Run daily now" answers 409.
    """
    if not results:
        return False  # (1) nothing to record
    # (2) the call step ran for someone (or a skipped thesis's on-time row stands) — a record exists
    if any(isinstance(r.recorded, bool) or r.skipped for r in results):
        return False
    if all(r.withheld_reason == "no-live" for r in results):
        return False  # (3) a deliberate cache-only pass withholds by design
    return True  # (4) theses existed, nothing recorded, and not by request


def _catch_up_is_noop(asof: date) -> bool:
    """The R6 ``--catch-up`` guard, with its config read HERE (the guard itself is pure): True — and the
    no-op line printed — when a live pass for ``asof`` that started at/after that night's ``RUN_AT``
    already recorded."""
    run_at: time = parse_run_at(get_settings().cron_run_at)
    tz: tzinfo = market_tz()
    if already_ran_live(asof, run_at=run_at, tz=tz):
        print(
            f"daily-cron: a live pass for {asof} started at/after {run_at:%H:%M} {tz.key} "
            "already ran — catch-up is a no-op"
        )
        return True
    return False


def past_asof_refusal(asof: date, *, now: datetime, run_at: time) -> str | None:
    """C3 — the refusal text when ``asof`` is OLDER than the last expected night at ``now``
    (``schedule.last_expected_asof``, market time), else ``None``. PURE (``now`` / ``run_at`` injected).

    Today's as-of is never older (a daytime manual pass for today stays allowed), nor is the last expected
    night itself (the scheduled run's own target), nor a weekend's as-of on that weekend. What is refused is
    the hand-typed ``pipeline.daily --asof <a past night>``: it assembles that night with ``known_at = now``
    — TODAY's knowledge — and appends a SCORED call-of-record. The docs warned against it; nothing refused
    it. ``main`` skips this check for ``--catch-up`` / ``--late-ok`` / ``--no-live``."""
    last = last_expected_asof(now, run_at)
    if asof >= last:
        return None
    return (
        f"--asof {asof} is older than the last expected night ({last}, market time). A pass computes its "
        "night with TODAY's knowledge (known_at = now) and would append a SCORED call-of-record that knew "
        "things that night did not. For a missed night run "
        f"`python -m pipeline.backfill --asof {asof}` (a PUBLIC-CLOCK reconstruction: marked "
        "reconstructed, reported, never scored). `--catch-up` is the sidecar's guarded catch-up; pass "
        "`--late-ok` only to record a late night on purpose."
    )


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
        "the night's close). A thesis whose on-time row already stands is skipped (M2); the freeze page "
        "judges the theses it processed, like any pass (O5).",
    )
    p.add_argument(
        "--run-kind",
        choices=("cron", "manual"),
        default="manual",
        help="F1/0044: what KIND of run this is, stamped on every call-of-record row it appends. The cron "
        "sidecar (scripts/daily_cron.sh) passes `cron`; everything else — a hand-typed run, a docker exec, "
        "the Admin button — is `manual`, the default. An explicit flag, never an env var: the sidecar and "
        "an operator invoke this exact same command, so nothing ambient can tell them apart, and a hand run "
        "must never masquerade as the nightly record. (`backfill` is stamped by pipeline.backfill, not "
        "reachable from here.)",
    )
    p.add_argument(
        "--late-ok",
        action="store_true",
        help="C3: allow an --asof OLDER than the last expected night. Without this (or --catch-up / "
        "--no-live) such an as-of is REFUSED: the pass would compute that night with TODAY's knowledge and "
        "append a SCORED call-of-record. For a missed night use `python -m pipeline.backfill --asof <night>` "
        "(public clock, marked reconstructed). The sidecar's scheduled run passes this flag, because "
        "its target night is fixed before the sleep and an overshot sleep makes it late by construction.",
    )
    args = p.parse_args(argv)
    asof = date.fromisoformat(args.asof) if args.asof else market_today()
    allow_live = not args.no_live
    # C3 — refuse a hand-typed past as-of BEFORE anything runs (no lock, no artifact, no row). Bare (no
    # --asof) is today and never older. --no-live is exempt: it withholds every call, so it cannot write
    # the scored row this refusal exists to prevent.
    if args.asof and not (args.catch_up or args.late_ok or args.no_live):
        try:
            run_at = parse_run_at(get_settings().cron_run_at)
        except ValueError as exc:
            p.error(f"ALPHADECK_CRON_AT is malformed: {exc}")
        refusal = past_asof_refusal(asof, now=market_now(), run_at=run_at)
        if refusal is not None:
            p.error(refusal)
    notifier = get_notifier()

    # R6 — catch-up guard: a `--catch-up` invocation is a no-op when a LIVE pass for this asof that STARTED
    # at/after that night's RUN_AT already ran. The sidecar fires `--catch-up` on boot for the last expected
    # night (Flag 6: a rebuild at 23:00 re-anchored to tomorrow and silently skipped tonight; the Sep 9 2026
    # host-off-at-22:30 case, back at 13:47 the next day) and on a late wake for the nights a long sleep
    # skipped. The mode-filtered run log (R3) is the memory that answers "did the night run"; a --no-live dev
    # run never satisfies it, and neither does a pre-open Admin "Run daily now" for the same asof (it ran on
    # the prior session's bars — it lacks the night's close), so neither can suppress the real catch-up. The
    # guard is pure; the config it needs (RUN_AT + the market zone) is supplied HERE, never read inside it.
    if args.catch_up and _catch_up_is_noop(asof):
        return

    # C1 — THE CROSS-PROCESS LOCK, taken HERE (before the pass) so the catch-up guard can be re-checked
    # UNDER it. A second invocation never queues: it prints one loud line, writes nothing (no calls row, no
    # artifact), pages nothing (the running pass pages its own night) and exits EXIT_ALREADY_RUNNING (75 —
    # see the module docstring). Any OTHER failure to take the lock (the DB unreachable) is a crash: ONE
    # health page (H3), then the original exception propagates (exit 1 — the night has no record).
    try:
        lock = acquire_pass_lock(holder=args.run_kind)
    except PassAlreadyRunning as exc:
        print(
            f"daily-cron: ALREADY RUNNING — {exc}. This invocation wrote NOTHING (no call-of-record, no "
            f"run artifact) and exits {EXIT_ALREADY_RUNNING}; the running pass owns the night"
        )
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    except BaseException as exc:
        _page_crash(notifier, asof, exc)
        raise
    try:
        # the TOCTOU re-check: a pass that finished (and released) between the guard above and the lock
        # has already written the night's artifact — re-reading it now turns a redundant full pass into the
        # no-op it should be. Cheap (a directory read), and only on --catch-up.
        if args.catch_up and _catch_up_is_noop(asof):
            return
        outcome = run_daily_pass(
            asof=asof,
            allow_live=allow_live,
            notifier=notifier,
            catch_up=args.catch_up,
            run_kind=args.run_kind,
            run_lock=lock,  # ownership transfers: run_daily_pass releases it when the pass ends
        )
    finally:
        lock.release()  # idempotent — a no-op after the pass released it; the backstop if it never ran
    _report(
        outcome.results,
        name_errors_new=outcome.name_errors_new,
        name_errors_known=outcome.name_errors_known,
        name_errors_evaluated=outcome.name_errors_evaluated,
        split=outcome.split,
        probe=outcome.rename_probe,
        successor=outcome.successor,
        skips=outcome.skips,
        amendments=outcome.amendments,
    )  # the per-thesis summary + the errored count, printed either way
    # X5 — ONE timings line: the total, the three slowest legs, the slowest thesis, the peak RSS
    timings_line = pass_timings.summary_line(outcome.timings)
    if timings_line:
        print(timings_line)
    # F4 — WHAT A NON-ZERO EXIT MEANS TO A WRAPPER: this night has NO call-of-record at all (the pass
    # crashed, or every thesis was withheld/errored for a real reason). It no longer means "some thesis
    # errored": the sidecar retries on this code, and a 20-minute pause plus a guard no-op bought nothing
    # for a night that recorded 11 of 12 while the log read "run FAILED". A partial failure still prints
    # per thesis and still pages through R4 (assess_health -> HealthEvent.errored) — the operator hears
    # about it, the scheduler does not act on it. Two deliberate exemptions, both exit 0: a pass over NO
    # theses (nothing to record) and a `--no-live` pass (it withholds by design). See _failed_to_record.
    if _failed_to_record(outcome.results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
