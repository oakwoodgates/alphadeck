"""The Operator Admin ops surface (Slice 1) — a READ surface over the cron's own instrumentation plus
ONE explicit trigger, for the laptop-deploy reality (a sleep-loop cron that misses nights, containers
that don't restart): "is the record current, did last night's run actually work, and run it NOW if not."

Bounds (the slice's invariants):
- **Pure ops surface.** The status/history reads own no tables and WRITE NOTHING (test-proved by
  counting every public table before/after) — they read the calls log's MAX(asof), the run-of-record
  artifacts (``data/cron_runs/``), and the schedule math. No LLM anywhere near this router.
- **Operator-initiated only.** ``POST /run-daily`` fires the full (live-EDGAR) daily pass and exists
  ONLY behind an explicit click — cost is the operator's to spend, never ambient. Reads may poll; the
  trigger never does.
- **Honest loudness.** Staleness is measured against the last EXPECTED scheduled run (Mon-Fri +
  RUN_AT — ``pipeline/schedule.py``), so a weekend never cries wolf; and a bad LAST run (freeze /
  errors / total ingest failure) is its own loud ``unhealthy`` verdict, peer to ``stale`` — the R1
  freeze must never hide behind a green "healthy".

Auth stays deferred project-wide (these routes ride the same tenancy seam as the rest).
"""

from __future__ import annotations

from datetime import date, datetime, time
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn
from app.schemas_api import (
    AdminCronOut,
    AdminRecordOut,
    AdminRunJobRef,
    AdminRunJobStatus,
    AdminRunOut,
    AdminRunsOut,
    AdminStaleTapeOut,
    AdminStatusOut,
    AdminTapeOut,
    BackupCreateIn,
    BackupJobRef,
    BackupJobStatus,
    BackupOut,
    BackupsOut,
)
from domain.feed_kinds import PRICE, StaleFeedLabel, feed_kind, stale_feed_bits
from domain.market_time import market_now, market_tz
from domain.settings import get_settings
from notify import HealthEvent
from pipeline.backup import BackupInfo, list_backups, run_backup
from pipeline.backup_job import BackupRunInFlight, start_backup_job
from pipeline.backup_job import get_job as get_backup_job
from pipeline.cron_run_log import build_run_payload, list_run_logs
from pipeline.daily import ThesisRunResult, assess_health, run_daily_pass
from pipeline.daily_job import DailyRunInFlight, get_job, start_daily_job
from pipeline.schedule import (
    expected_runs_behind,
    last_expected_asof,
    missed_asofs,
    parse_run_at,
    scheduled_window,
)
from repositories import calls_repo

router = APIRouter(prefix="/admin", tags=["admin"])

# The benign marker (mirrors HealthEvent.label's wording): a --no-live withhold is a dev-run note, not
# an alarm — the cron verdict must not read "unhealthy" because someone hand-ran a cache-only pass.
_BENIGN_MARK = "not an error"


def _now() -> datetime:
    """The MARKET wall clock (``domain/market_time.market_now`` — an explicit ``ZoneInfo``, no longer the
    container's ambient TZ) — a seam so tests pin the clock; the schedule functions themselves are pure
    over the injected now. Aware, unlike the ``datetime.now()`` it replaced; ``last_expected_asof`` reads
    it via ``.date()`` / ``.time()``, and ``.time()`` drops the tzinfo, so the RUN_AT comparison against a
    naive ``time`` still holds (a test may keep pinning this with a naive datetime)."""
    return market_now()


def _problems(health: HealthEvent | None) -> list[str]:
    """The health event decomposed into per-problem lines (alarms first, the benign no-live note last —
    ``HealthEvent.label``'s own order and wording, kept in step with it); ``[]`` for a clean run."""
    if health is None:
        return []
    out: list[str] = []
    if health.frozen:
        out.append(
            f"FROZEN — 0 EDGAR fetches across {health.theses} theses (the cache never refreshed)"
        )
    if health.withheld_failure:
        out.append(f"{health.withheld_failure} call(s) WITHHELD — TOTAL INGEST FAILURE")
    if health.errored:
        out.append(f"{health.errored} thesis error(s)")
    # G4 — the shared-input alarms (a stale SPY/IWM tape silently degrades benchmark_rs for every call
    # that night). NOT benign: these sit above the no-live note and DO make the cron verdict unhealthy.
    if health.benchmark_leg_failed:
        out.append("BENCHMARK REFRESH LEG FAILED — the SPY/IWM tape did not refresh tonight")
    if health.benchmark_errors:
        out.append(
            f"{health.benchmark_errors} benchmark refresh error(s) — benchmark_rs read a stale tape"
        )
    if health.withheld_no_live:
        out.append(
            f"{health.withheld_no_live} call(s) withheld — no-live "
            f"(a cache-only run, {_BENIGN_MARK})"
        )
    # G5a/F1 — a newly stale FEED (a price tape, an ETF's fund-shares samples, or both — one line per
    # feed kind, from the shared `stale_feed_bits` so this row and the notifier's page cannot drift).
    # Every line deliberately carries the BENIGN marker, so it shows on the run row and pages through the
    # notifier but does NOT make the one-word cron verdict `unhealthy`: the cron did its job, the FEED has
    # a gap for the operator to repair. (Honest loudness cuts both ways — an `unhealthy` chip that really
    # means "a vendor renamed a ticker" would teach the operator to ignore the chip.) The freshness PANEL
    # is where this lives; these lines are its breadcrumb on the run.
    out.extend(
        f"{bit} — a feed gap to repair, {_BENIGN_MARK} in this run"
        for bit in stale_feed_bits(health.tape_stale_new)
    )
    return out


def _admin_run_out(payload: dict) -> AdminRunOut:
    """One parsed run-of-record payload -> the wire row. STRICT over the artifact's own schema — a
    missing/malformed key raises and the CALLER skips that artifact fail-open (the run-log read
    discipline). ``healthy``/``problems`` re-derive via ``assess_health`` over results RECONSTRUCTED
    from the per-thesis entries (the pure assessor reads only counts the artifact carries — the same
    verdict the run itself paged on, re-readable forever from the file). A ``catch_up`` pass skips the
    FREEZE predicate exactly as the run itself did (it ran inside the EDGAR TTL — ~0 fetches is correct),
    so the history never shows a catch-up as unhealthy; an artifact from before the key reads False.

    The RUN-LEVEL keys added after the original schema (``catch_up``, and G4's benchmark counts) are read
    with ``.get`` ON PURPOSE, unlike the strict ``payload[...]`` above: this function is strict by design and
    its callers skip a RAISING artifact fail-open, so reading a new key strictly would make every artifact
    written before the deploy unparseable — silently blanking the entire run history and ``last_run``.
    """
    asof = date.fromisoformat(payload["asof"])
    allow_live = payload["mode"] == "live"
    catch_up = bool(payload.get("catch_up", False))
    # G4 — re-derive the benchmark alarm from the artifact, so the history's verdict matches what the run
    # paged (see the note on .get above: an artifact from before these keys reads clean, not broken).
    benchmark_errors = int(payload.get("benchmark_errors") or 0)
    benchmark_leg_failed = bool(payload.get("benchmark_leg_failed", False))
    # G5a/F1 — the night's newly-stale entries, re-read so the history shows the same per-feed page this run
    # emitted. TWO shapes on disk, both valid: {kind, label} objects (current) and BARE STRINGS from every
    # artifact written before fund shares were monitored — those are price entries, and reading them as such
    # is what keeps the whole pre-deploy history rendering instead of raising into the caller's skip.
    tape_stale_new = tuple(
        (
            StaleFeedLabel(kind=str(x.get("kind") or PRICE.key), label=str(x.get("label") or ""))
            if isinstance(x, dict)
            else StaleFeedLabel(kind=PRICE.key, label=str(x))
        )
        for x in (payload.get("tape_stale_new") or ())
    )
    results = [
        ThesisRunResult(
            thesis_id=UUID(t["id"]),
            name=str(t.get("name") or ""),
            recorded=t.get("recorded"),
            transition=t.get("transition"),
            error=t.get("error"),
            edgar_fetches=int(t.get("edgar_fetches") or 0),
            withheld_reason=t.get("withheld_reason"),
        )
        for t in payload["theses"]
    ]
    health = assess_health(
        results,
        asof=asof,
        allow_live=allow_live,
        freeze_check=not catch_up,
        benchmark_errors=benchmark_errors,
        benchmark_leg_failed=benchmark_leg_failed,
        tape_stale_new=tape_stale_new,
    )
    summary = payload["summary"]
    return AdminRunOut(
        ran_at=str(payload["started_at"]),
        finished_at=str(payload["finished_at"]),
        duration_s=float(payload["duration_s"]),
        asof=asof,
        mode=str(payload["mode"]),
        theses=int(summary["theses"]),
        appended=int(summary["appended"]),
        unchanged=int(summary["unchanged"]),
        withheld=int(summary["withheld"]),
        errored=int(summary["errored"]),
        transitions=int(summary["transitions"]),
        edgar_fetches=int(payload["edgar_fetches"]),
        healthy=health is None,
        problems=_problems(health),
        catch_up=catch_up,
    )


def _threshold(doc: dict, key: str, live: int) -> int:
    """One threshold off the artifact (F6), falling back to the live setting only when the artifact does
    not carry it at all (every artifact written before F6).

    The ``is not None`` check is the point, and ``or`` would be a bug: **0 is a real stored value** meaning
    that feed's monitor was DISABLED for the pass. Under ``or`` a stored 0 would silently render as
    whatever the setting says today — exactly the misdescription F6 exists to stop, inverted."""
    v = doc.get(key)
    return int(v) if isinstance(v, int) and not isinstance(v, bool) else live


def _tape_out(payloads: list[dict]) -> AdminTapeOut | None:
    """The FEED freshness panel (G5a price tapes · F1 fund shares), built from the newest artifact that
    actually EVALUATED recency — ARTIFACT-SOURCED on purpose, not a fresh DB scan: this router's bound is
    "a read surface over the cron's own instrumentation" that owns no tables, and a second definition of
    "stale" living in a query here could drift from the one the page fired on. The cost is that the panel
    reads "as of the last pass", which is the right granularity for a nightly feed.

    ``None`` = no pass has looked yet (the quiet state right after deploy, and for a ``--no-live``-only
    history). Rows are deduped by ``kind`` + ``security_id`` across theses — a name held by two theses is
    ONE dead feed, while a name stale on BOTH feeds is two rows with two repairs — and the first thesis
    that saw it names it. A row without a ``kind`` (a pre-F1 artifact) reads as a price row. A ticker-less
    row is kept and rendered by id (#9). A row with ``closed_at`` set (G5b) is a CLOSED name (delisted /
    acquired / deregistered) — it stays in the list (never dropped, #9) and the FE renders it quietly.
    Fail-open per artifact, mirroring the run-history read: a malformed row is skipped, never an exception.

    Both THRESHOLDS come off the artifact (F6) — see ``_threshold`` for why a stored 0 must survive.
    """
    settings = get_settings()
    for doc in payloads:
        if doc.get("mode") != "live" or not doc.get("tape_evaluated", False):
            continue
        rows: dict[str, AdminStaleTapeOut] = {}
        for t in doc.get("theses") or []:
            if not isinstance(t, dict):
                continue
            for s in t.get("tape_stale") or []:
                try:
                    kind = feed_kind(s.get("kind")).key
                    key = f"{kind}:{s['security_id']}"
                    if key in rows:  # same feed + security under a second thesis — one dead feed
                        continue
                    rows[key] = AdminStaleTapeOut(
                        security_id=UUID(str(s["security_id"])),
                        ticker=s.get("ticker"),
                        edge=date.fromisoformat(s["edge"]) if s.get("edge") else None,
                        thesis=str(t.get("name") or ""),
                        kind=kind,
                        # G5b — the delisting classification: a date here reclassifies the row from "stale
                        # — repair" to "closed — stopped trading <date>" (a pre-G5b artifact lacks the key
                        # and reads None — a plain stale-repair row, the correct reading).
                        closed_at=(
                            date.fromisoformat(s["closed_at"]) if s.get("closed_at") else None
                        ),
                    )
                except Exception:  # noqa: BLE001 — skip a malformed row, never blank the panel
                    continue
        # the newly-stale entries are {kind, label} objects now; older artifacts hold bare strings. Either
        # way this field is the LABELS — stringifying the objects would put raw dicts on the wire.
        newly = [
            str(x.get("label") or "") if isinstance(x, dict) else str(x)
            for x in (doc.get("tape_stale_new") or [])
        ]
        return AdminTapeOut(
            asof=date.fromisoformat(doc["asof"]),
            ran_at=str(doc["started_at"]),
            stale_days=_threshold(doc, "tape_stale_days", settings.tape_stale_days),
            fund_shares_stale_days=_threshold(
                doc, "fund_shares_stale_days", settings.fund_shares_stale_days
            ),
            stale=list(rows.values()),
            newly_stale=newly,
        )
    return None


def _backup_out(info: BackupInfo) -> BackupOut:
    """One pipeline-layer ``BackupInfo`` -> the wire row (value-free: name, size, created_at, labeled).
    Shared by the create job's result, the list endpoint, and the status ``last_backup`` join."""
    return BackupOut(
        name=info.name, bytes=info.bytes, created_at=info.created_at, labeled=info.labeled
    )


def _run_at() -> time:
    """The schedule wall time off ``Settings.cron_run_at`` (env ``ALPHADECK_CRON_AT`` — the same host
    var the sidecar runs on). A malformed value is a DEPLOY error → a loud, actionable 500."""
    raw = get_settings().cron_run_at
    try:
        return parse_run_at(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=500, detail=f"ALPHADECK_CRON_AT is malformed ({raw!r}): {exc}"
        ) from exc


@router.get("/status", response_model=AdminStatusOut)
def get_admin_status(conn: psycopg.Connection = Depends(get_conn)) -> AdminStatusOut:
    """The freshness + health summary the admin page opens on — READ-ONLY (writes nothing, owns no
    tables). ``record`` measures the calls-log edge against the last EXPECTED Mon-Fri+RUN_AT run
    (container-local clock; a Friday edge on a Monday morning is CURRENT — never a weekend false
    alarm); ``edge: null`` is the quiet "record has never begun" state. ``last_run`` is the newest
    readable run-of-record artifact. ``cron.status`` is the one-word verdict: ``never_ran`` (no
    artifact), ``unhealthy`` (the last run froze / errored / totally failed / could not refresh the
    shared benchmark tape — as loud as stale, so a bad run can't hide behind green), ``stale`` (the
    record missed an expected run), ``gappy`` (the edge is current but a night inside the last
    ``ALPHADECK_ADMIN_MISSED_WINDOW`` scheduled runs has no call-of-record recorded at/after that night's
    ``RUN_AT`` — a run that fired on the wrong day, or failed after a daytime pass; the edge check alone
    cannot see either), else ``healthy``. ``record.missed_asofs`` lists the holes (empty on a clean
    window) and ``record.daytime_only_asofs`` names the subset whose only row is a pre-``RUN_AT`` one.
    ``tape`` is the FEED freshness panel (G5a price tapes · F1 ETF fund shares): every basket
    name whose stored EOD tape or fund-shares sampling has stopped, each row naming which feed, read from
    the newest run artifact that evaluated recency — ``null`` until a pass has looked. A stale feed is a
    FEED gap, not a cron fault, so it never changes ``cron.status``. A row whose ``closed_at`` is set (G5b)
    is a name that CLOSED — delisted / acquired / deregistered per a SEC delisting form — so its tape
    correctly ended and needs no repair; it stays on the panel (never dropped, #9) but renders quietly as
    "closed" and never pages.
    """
    run_at = _run_at()
    now = _now()
    edge = calls_repo.record_edge(conn)
    expected = last_expected_asof(now, run_at)
    days_behind = expected_runs_behind(edge, expected)
    stale = bool(days_behind)  # None (never begun) and 0 (current) are both quiet

    # THE HOLE CHECK (hole-aware freshness). The edge check above sees only MAX(asof): a run that fired on
    # the WRONG day — the laptop's sleep drift; a 00:24 / 09:09 wake recorded the NEXT day's asof — advances
    # the edge right over the night it skipped, and the edge read "current" over 6 empty nights in 13. So
    # scan the last N scheduled weekdays for nights with NO call-of-record at all, bounded to the window
    # AND to the record's own span (never pre-history: a fresh install has no holes). Two more read-only
    # queries (MIN(asof) + DISTINCT asof since the window's first day) — the surface still writes nothing.
    window_days = scheduled_window(expected, get_settings().admin_missed_window)
    first = calls_repo.record_first(conn) if (edge is not None and window_days) else None
    stamps = (
        calls_repo.recorded_asof_stamps(conn, since=window_days[0]) if first is not None else {}
    )
    # G2c — A NIGHT IS COVERED ONLY BY A POST-RUN_AT ROW. Existence of a row for the as-of was too weak:
    # a pre-open "Run daily now" at 09:15 writes a row for TODAY's as-of off the PRIOR session's bars, and
    # the old membership test counted it — so a night whose 22:30 pass then FAILED read as covered and the
    # hole was invisible (MEASURED on prod for 2026-09-09: two pre-open passes, the host off at 22:30).
    # The cutoff is that night's RUN_AT in MARKET time, so the comparison is made where the deploy config
    # lives (the repo read stays value-free); a next-morning catch-up row is recorded AFTER the cutoff and
    # correctly qualifies, and a `reconstructed` row's stamp is its reconstruction instant, so a backfilled
    # night still counts as covered (unchanged, deliberate).
    tz = market_tz()
    covered = {d for d, rec in stamps.items() if rec >= datetime.combine(d, run_at, tzinfo=tz)}
    missed = missed_asofs(covered, expected=expected, first=first, window=len(window_days))
    # ``daytime_only`` is a SUBSET OF ``missed``, not of every as-of that happens to carry a pre-cutoff row.
    # The difference bit on dev: a weekend manual pass leaves rows for a Saturday/Sunday as-of recorded
    # before 22:30, and a raw ``set(stamps) - covered`` listed those days — but no pass is ever SCHEDULED on
    # a weekend (``missed_asofs`` walks scheduled weekdays only), so such a day is not a hole and "daytime
    # only" is meaningless for it. Filtering through ``missed`` keeps the wire field equal to what it
    # promises: the holes whose ONLY row is a pre-``RUN_AT`` one. Ascending, since ``missed`` is.
    pre_cutoff = set(stamps) - covered
    daytime_only = [d for d in missed if d in pre_cutoff]

    if edge is None:
        reason = "the record has never begun — no call-of-record logged yet"
    elif stale:
        reason = (
            f"{days_behind} expected run(s) behind — last expected as-of {expected.isoformat()}"
        )
    elif missed:
        # the edge is current, but the window is not clean — the reason must not say "no run is missing"
        tail = f" ({len(daytime_only)} with a daytime row only)" if daytime_only else ""
        reason = (
            f"current at the edge — but {len(missed)} of the last {len(window_days)} scheduled "
            f"run(s) have no post-{run_at:%H:%M} call-of-record{tail}"
        )
    else:
        reason = "current — no scheduled run is missing"

    # The newest READABLE artifact (skip-unreadable fail-open, the run-log read discipline). The read
    # is bounded: status only needs the most recent parseable row.
    last_run: AdminRunOut | None = None
    payloads = list_run_logs(limit=20)
    for p in payloads:
        try:
            last_run = _admin_run_out(p)
            break
        except Exception:  # noqa: BLE001 — a malformed artifact is skipped, never a failed status
            continue

    if last_run is None:
        cron = AdminCronOut(
            status="never_ran",
            detail="no daily run has been recorded yet — run one below, or bring the cron sidecar up",
        )
    else:
        # the REAL alarms (frozen / total ingest failure / errors) — the benign notes excluded (a hand-run
        # dev pass, and a newly stale price tape, which is a FEED gap not a cron fault), so neither paints
        # the cron unhealthy (honest loudness; the stale tape lives on the freshness panel instead)
        alarms = [p for p in last_run.problems if _BENIGN_MARK not in p]
        if alarms:
            cron = AdminCronOut(
                status="unhealthy",
                detail=f"last run (asof {last_run.asof.isoformat()}, {last_run.mode}) needs "
                "attention: " + "; ".join(alarms),
            )
        elif stale:
            cron = AdminCronOut(
                status="stale",
                detail=f"record edge {edge.isoformat() if edge else '—'} is {days_behind} expected "
                f"run(s) behind (last expected as-of {expected.isoformat()})",
            )
        elif missed:
            # gappy: the edge is current and the last run clean, yet a recent night has no post-RUN_AT
            # record — the wrong-day shape. As loud as stale (a hole is a missing run the edge check can't
            # see); a clean window never mentions holes (honest loudness). G2c — the two shapes are named
            # DISTINCTLY, because they call for different things: a night with NOTHING at all is a run that
            # never fired, while a night whose only row is a DAYTIME one had a pass that ran on the prior
            # session's bars and then a post-close pass that failed or never happened. Marking the second is
            # the whole point of the cutoff: it used to read as covered.
            daytime_set = set(daytime_only)
            marked = ", ".join(
                f"{d.isoformat()} (daytime row only)" if d in daytime_set else d.isoformat()
                for d in missed
            )
            cron = AdminCronOut(
                status="gappy",
                detail=f"record has {len(missed)} hole(s) in the last {len(window_days)} scheduled "
                f"runs: {marked} — a run fired on the wrong day, failed after a daytime pass, or never "
                'fired (see FEED_LOOP.md "Known gaps")',
            )
        else:
            cron = AdminCronOut(
                status="healthy",
                detail=f"last run asof {last_run.asof.isoformat()} ({last_run.mode}) — "
                f"{last_run.appended} appended · {last_run.unchanged} unchanged · record edge "
                f"{edge.isoformat() if edge else 'not begun yet'}",
            )

    # The newest DB snapshot (honest visibility — the page shows the last-snapshot age). A pure directory
    # read (list_backups is skip-unreadable fail-open); None = the quiet "no snapshots yet" state. Writes
    # nothing, so the test_admin_reads_write_NOTHING gate still holds (no table is touched).
    backups = list_backups()
    last_backup = _backup_out(backups[0]) if backups else None

    # G5a — the price-tape panel, from the artifacts ALREADY read above (no second read, no DB query): the
    # "is it watched?" view for prices. None until a pass has evaluated recency.
    tape = _tape_out(payloads)

    return AdminStatusOut(
        record=AdminRecordOut(
            edge=edge,
            today=now.date(),
            expected_asof=expected,
            days_behind=days_behind,
            stale=stale,
            reason=reason,
            missed=len(missed),
            missed_asofs=missed,
            daytime_only_asofs=daytime_only,
            window_days=len(window_days),
        ),
        last_run=last_run,
        cron=cron,
        last_backup=last_backup,
        tape=tape,
    )


@router.get("/runs", response_model=AdminRunsOut)
def get_admin_runs(
    limit: int = Query(20, ge=1, le=200, description="how many runs, newest first"),
) -> AdminRunsOut:
    """The run history — the last N run-of-record artifacts parsed, newest first. A pure FILE read (no
    DB, no network, writes nothing); an unreadable/malformed artifact is skipped fail-open so a corrupt
    night never blanks the history."""
    runs: list[AdminRunOut] = []
    for p in list_run_logs(limit=limit):
        try:
            runs.append(_admin_run_out(p))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed history
            continue
    return AdminRunsOut(runs=runs)


@router.post("/run-daily", status_code=202, response_model=AdminRunJobRef)
def start_run_daily() -> AdminRunJobRef:
    """KICK OFF the full daily pass as a background JOB and return immediately (**202** + ``job_id``);
    poll ``GET /admin/run-daily/jobs/{job_id}``. Fires ONLY on this explicit request — never on a page
    load, mount, or poll (cost is the operator's to spend; the pass does a LIVE EDGAR pull and can run
    ~65 minutes cold). Runs the cron's EXACT unit (``run_daily_pass``): live ingest + call-of-record +
    the run-log artifact + the health page — a manual run lands in the run history like the nightly
    one. **409** when a run is already in progress (the single-slot in-process guard; a double-click
    can never stack a second pass). Safe to re-click once finished: the pass is idempotent
    (``record_if_changed`` appends nothing on unchanged facts). KNOWN LIMITATION (accepted): the guard
    cannot see the cron SIDECAR's own run in its separate container — an overlap is wasteful, never
    corrupting. The job opens its OWN DB connection (it outlives this request)."""

    def _run() -> AdminRunOut:
        outcome = run_daily_pass()  # asof=today, live — the cron's exact unit of work
        payload = build_run_payload(
            outcome.results,
            asof=outcome.asof,
            allow_live=outcome.allow_live,
            started_at=outcome.started_at,
            finished_at=outcome.finished_at,
            # G4 — carried off the outcome, or this poll payload would report a healthy run over the
            # benchmark fault the SAME pass wrote into its artifact and paged about. G5a rides along for
            # the same reason (the stale diff is computed once, inside the pass).
            benchmark_errors=outcome.benchmark_errors,
            benchmark_leg_failed=outcome.benchmark_leg_failed,
            tape_evaluated=outcome.tape_evaluated,
            tape_stale_new=outcome.tape_stale_new,
            tape_stale_days=outcome.tape_stale_days,
            fund_shares_stale_days=outcome.fund_shares_stale_days,
        )
        return _admin_run_out(payload)

    try:
        job_id = start_daily_job(_run)
    except DailyRunInFlight as exc:
        raise HTTPException(status_code=409, detail="a daily run is already in progress") from exc
    return AdminRunJobRef(job_id=job_id, status="running")


@router.get("/run-daily/jobs/{job_id}", response_model=AdminRunJobStatus)
def get_run_daily_job(job_id: str) -> AdminRunJobStatus:
    """POLL a kicked-off daily run. ``done`` → ``result`` (the finished pass, the run-history row
    shape); ``failed`` → an operator-facing ``error``. **404** if the job is unknown / expired, or the
    registry was wiped by a restart — the run itself may still have completed server-side (the run
    history + record edge are the durable authority), so the FE shows "lost from view", never an
    infinite spinner."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="daily-run job not found (it may have expired or the server restarted — "
            "check the run history)",
        )
    return AdminRunJobStatus(
        job_id=job.job_id, status=job.status, result=job.result, error=job.error
    )


# --- Backups (Slice 4): the operator DB-snapshot button — create + list + retain (NEVER restore) ---
# Like /admin/runs, none of these takes a get_conn dep: create kicks a job (the job shells pg_dump with
# its OWN connection), the poll reads the in-process registry, and the list is a pure file read — so the
# router still owns no tables (reinforces the writes-nothing bound).


@router.post("/backup", status_code=202, response_model=BackupJobRef)
def start_backup(body: BackupCreateIn | None = None) -> BackupJobRef:
    """KICK OFF a ``pg_dump`` snapshot as a background JOB and return immediately (**202** + ``job_id``);
    poll ``GET /admin/backup/jobs/{job_id}``. Fires ONLY on this explicit request — never on a page load,
    mount, or poll (cost is the operator's to spend; the reads may poll, the trigger never does). The dump
    is READ-ONLY (``pg_dump``) and mutates no row; it writes a ``.sql`` under ``/data/backups`` (a host
    bind, so it is copyable off-box) and prunes old UNLABELED dumps (keep-last-N; a ``label`` marks a
    prune-EXEMPT snapshot). **409** when a snapshot is already in progress (the single-slot in-process
    guard — a double-click can never stack a second dump). **RESTORE is deliberately CLI-only** (a
    destructive drop-schema + reload; never a button): ``docker exec -i alphadeck-postgres-1 psql -U
    alphadeck -d alphadeck < ./data/backups/<file>``. The job opens its OWN subprocess (it outlives this
    request)."""
    label = body.label if body else None

    def _run() -> BackupOut:
        result = run_backup(label=label)  # read-only pg_dump -> a file; mutates no row
        return _backup_out(result)

    try:
        job_id = start_backup_job(_run)
    except BackupRunInFlight as exc:
        raise HTTPException(status_code=409, detail="a snapshot is already in progress") from exc
    return BackupJobRef(job_id=job_id, status="running")


@router.get("/backup/jobs/{job_id}", response_model=BackupJobStatus)
def get_backup_job_status(job_id: str) -> BackupJobStatus:
    """POLL a kicked-off snapshot. ``done`` → ``result`` (the finished ``BackupOut``, the same shape the
    list shows — the ``.sql`` on disk is the durable record regardless); ``failed`` → an operator-facing
    ``error``. **404** if the job is unknown / expired, or the registry was wiped by a restart — the dump
    may still have completed server-side (the backups list is the durable authority), so the FE shows
    "lost from view", never an infinite spinner."""
    job = get_backup_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="snapshot job not found (it may have expired or the server restarted — "
            "check the backups list)",
        )
    return BackupJobStatus(job_id=job.job_id, status=job.status, result=job.result, error=job.error)


@router.get("/backups", response_model=BackupsOut)
def get_admin_backups() -> BackupsOut:
    """The snapshot list — every ``.sql`` under the backups dir, NEWEST-FIRST. A pure FILE read (no DB,
    no network, writes nothing); an unreadable/foreign entry is skipped fail-open so a stray file never
    blanks the list. RESTORE stays a documented CLI act (see ``POST /admin/backup``); this endpoint only
    surfaces what exists."""
    return BackupsOut(backups=[_backup_out(i) for i in list_backups()])
