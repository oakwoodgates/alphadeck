"""V1 — the nightly SELF-AUDIT: the pass checks the record it just left, and pages when it is not right.

"100% correct" is a claim the cron must re-earn every night, not a one-time verdict
(``docs/temp/cron-correctness-plan-2026-10-16.md`` §2 / V1). At the end of each pass, after the per-thesis
loop has committed, ``run_audit`` runs a list of independent CHECKS; their results ride the run-of-record
artifact as ``audit: [{check, ok, detail}]`` and any failure pages through the R4 health seam
(``HealthEvent.audit_failed``). The Admin page re-reads the same list from the artifact.

THREE OUTCOMES, never two: ``ok = True`` (checked, holds), ``ok = False`` (checked, FAILS — pages), and
``ok = None`` (NOT APPLICABLE to this pass — e.g. a daytime manual pass cannot leave the night's on-time
row, and a ``--no-live`` pass records nothing). ``None`` is never read as a pass: skipped is not passed.

PHASE-1 CHECKS (the first cut — later phases append to ``DEFAULT_CHECKS``):

- ``on_time_rows`` — every thesis in the night's roster has at least one NON-reconstructed ``calls`` row for
  the pass's as-of recorded at/after that night's ``RUN_AT`` in market time. It COUNTS THE TABLE (the grouped
  per-thesis stamps), never the deduped read and never the pass's own results — a pass that believes it
  recorded is exactly what this must not trust. Since M1 the rule is ``pipeline/night_coverage.py``'s, the
  SAME definition the Admin hole check judges every recent night by: the roster is the theses created
  before the cutoff and not archived before it (it was "not archived NOW"; a thesis created after the
  cutoff — mid-pass — was never in the pass's walk, and one archived mid-pass still owed its row).
- ``code_sha_known`` — the pass's ``code_sha`` (``Settings.image_sha``) is known. 84 cron rows carried a NULL
  sha 09-18..09-28 (MEASURED) because an image was rebuilt without ``GIT_SHA``; the rows stay an honest
  NULL, this makes it loud the night it happens.
- ``every_thesis_finished`` — every thesis the pass started reached its call-of-record outcome (appended or
  unchanged; a deliberate ``--no-live`` withhold counts as finished). An errored call step or a
  total-ingest-failure withhold is a thesis the pass started and did not finish. (A pass that dies MID-loop
  never reaches this audit — the H3 crash page and M1's start artifact, left ``running`` / ``crashed``,
  cover it.)

PHASE-4 CHECK (T4):

- ``split_defects_flagged`` — the split-basis monitor (``pipeline/split_basis.py``) ran over tonight's tapes,
  so every defect it can see is flagged (on the artifact; the new ones in tonight's page). It REPORTS the
  count and passes with defects present — there is no "acknowledged" state, and failing on a standing,
  already-flagged defect would page every night — and FAILS only when the scan could not run.

PHASE-7 CHECK (the settle guard, audited — 2026-10-21):

- ``no_unsettled_bars`` — no ``fact_price_eod`` row (benchmarks included) for a bar dated ``d`` was RECORDED
  before ``d``'s settle instant (``d`` at ``Settings.price_settle_time`` in market time). The rule has been
  enforced at INGEST since D2 (#393, ``ingest/prices/settle.py``); this checks the TABLE every night, because
  the coming open / provisional-close runs make it the one hard guard (the server note's §4). It judges the
  rows recorded since the previous pass that judged them (``bars_since`` — see ``settle_window_since`` for
  the window and why it never reaches before the guard shipped), so each stored bar is judged by one pass
  and a historical, pre-guard violation never pages.

S2 CHECK (the split list):

- ``splits_applied`` — every split in force on the list is applied to its stored tape (no step the size of the
  split at its first session). FAILS for a recent one (the price leg applies a split on its first night, so a
  standing step days later is the prevention not working); an older one is the repair tool's backlog, named
  in the detail and passed — the T4 check's reason not to page a standing, known defect every night.

ADDING A CHECK: write ``def check_x(ctx: AuditContext) -> AuditResult`` (pure over ``ctx``; open the DB only
through ``ctx.conn()``), append it to ``DEFAULT_CHECKS``. Each check is isolated: one that RAISES is reported
as a FAILED check naming the exception (a broken audit is itself an alarm — never a silent skip), and the
rest still run.

BOUNDS: READ-ONLY (the audit's connection is ``read_only``; it writes no row), value-free (counts and short
ids — never a thesis NAME, because the details ride the health page), and no ambient clock (the pass's
``started_at`` / ``asof`` and the configured ``RUN_AT`` / zone are passed in).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import TYPE_CHECKING

import psycopg

from pipeline.night_coverage import read_night_coverage

if TYPE_CHECKING:  # keep the layering one-way (no runtime import of the cron module)
    from pipeline.daily import ThesisRunResult
    from pipeline.split_basis import SplitScan

# The prefix ``run_audit`` gives a check that RAISED. Shared with ``cron_run_log.previous_settle_audit_end``,
# which must not read a check that could not run as one that judged its window.
COULD_NOT_RUN = "the check could not run"

# The settle check's name on the artifact (``check_no_unsettled_bars``).
SETTLE_CHECK = "no_unsettled_bars"

# THE SETTLE GUARD'S PROD DEPLOY — the floor of every settle-audit window. D2 (#393, merged 2026-10-16
# 17:33Z) reached prod in the deploy whose pre-deploy backup is stamped 2026-10-17T03:23:20Z
# (``pre-deploy-d3-t1-d1d2-q3``); the Q3 repair that deploy shipped ran at 03:25:34Z. MEASURED on prod
# 2026-10-21 (read-only): 5,597 stored rows were recorded before their bar's settle instant, the NEWEST at
# 2026-10-16 00:00Z, and NONE since this instant — 2,999 real-time stamps from nine market-hours / early
# passes (2026-07-09 .. 2026-10-13; CDRE's 2026-10-13 bar at 17:08 ET is one) and 2,598 SPY/IWM rows the
# pre-D1 benchmark leg backdated to ``d 00:00 UTC``. Those are history the guard was built to stop; a check
# that judged them would fail every night forever, so no window reaches before this instant.
SETTLE_GUARD_DEPLOYED_AT = datetime(2026, 10, 17, 3, 23, 20, tzinfo=timezone.utc)

# The window when no previous pass judged the table (the first pass after this check ships, or a run home
# with no artifacts): the last week, floored at the guard's deploy. Bounded so a lost history stays cheap.
SETTLE_FALLBACK_LOOKBACK = timedelta(days=7)

# The overlap the window keeps behind the previous judging pass's ``finished_at``: that instant is the
# process clock, ``recorded_at`` the database's, so the window starts a minute early rather than risk a
# gap between them. A violation inside that minute is judged twice — a repeat page at worst, never a miss.
SETTLE_WINDOW_OVERLAP = timedelta(minutes=1)

# How many violating bars the detail names (``TICKER d``); the rest are counted.
_SETTLE_LABELS = 8


@dataclass(frozen=True)
class AuditResult:
    """One check's verdict. ``ok``: True holds · False FAILS (pages) · None not applicable to this pass."""

    check: str
    ok: bool | None
    detail: str

    def to_json(self) -> dict:
        return {"check": self.check, "ok": self.ok, "detail": self.detail}


@dataclass
class AuditContext:
    """Everything a check may read, passed in (no ambient clock, no settings read inside a check).
    ``conn()`` opens ONE read-only connection lazily, on the first check that needs the DB, and
    ``close()`` releases it — ``run_audit`` calls that in ``finally``."""

    asof: date
    started_at: datetime  # aware — the pass's start instant
    allow_live: bool
    run_kind: str
    code_sha: str | None
    results: Sequence[ThesisRunResult]
    run_at: time
    tz: tzinfo
    connect: Callable[[], psycopg.Connection]
    # T4 — the pass's split-basis scan (``pipeline/split_basis.run_split_scan``); None = the pass did not
    # hand one in (a caller older than T4) — read as not applicable, never as a pass
    split: SplitScan | None = None
    # The SETTLE check (``check_no_unsettled_bars``): the market-time wall clock a bar dated ``d`` settles at
    # (``Settings.price_settle_time``), and the window start — rows RECORDED at/after ``bars_since`` are
    # judged (``settle_window_since``). Either None = the caller handed none in: not applicable, never a pass.
    settle_at: time | None = None
    bars_since: datetime | None = None
    _conn: psycopg.Connection | None = field(default=None, repr=False)

    @property
    def cutoff(self) -> datetime:
        """That night's ``RUN_AT`` in market time — the on-time bar (the Admin covered-day rule, G2c)."""
        return datetime.combine(self.asof, self.run_at, tzinfo=self.tz)

    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            c = self.connect()
            c.read_only = True  # the audit reads; structurally it cannot write a row
            self._conn = c
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


AuditCheck = Callable[[AuditContext], AuditResult]


def _short(ids: Sequence[object], limit: int = 12) -> str:
    """Short ids for a page-safe detail (never a thesis NAME): the first 8 hex of each, bounded."""
    shown = ", ".join(str(i)[:8] for i in ids[:limit])
    more = f", +{len(ids) - limit} more" if len(ids) > limit else ""
    return shown + more


def check_on_time_rows(ctx: AuditContext) -> AuditResult:
    """(a) Every thesis in the night's roster has a non-reconstructed row for ``asof`` recorded at/after the
    night's ``RUN_AT`` — counted in the ``calls`` table (``night_coverage``, the Admin's own definition).
    Not applicable to a ``--no-live`` pass (it withholds by design) or to a pass that STARTED before the
    cutoff (a daytime pass runs on the prior session's bars; the night's on-time row is the post-close
    pass's to write)."""
    name = "on_time_rows"
    if not ctx.allow_live:
        return AuditResult(
            name, None, "not applicable — a --no-live pass records nothing by design"
        )
    if ctx.started_at < ctx.cutoff:
        return AuditResult(
            name,
            None,
            f"not applicable — a daytime pass (started before {ctx.run_at:%H:%M} {ctx.tz}); the "
            f"night's on-time row is the post-close pass's to write",
        )
    # M1 — ONE definition of "covered", shared with the Admin hole check (`pipeline/night_coverage.py`):
    # a non-reconstructed row at/after the cutoff, per thesis in the night's roster (created before the
    # cutoff, not archived before it), counted in the TABLE.
    cov = read_night_coverage(ctx.conn(), [ctx.asof], run_at=ctx.run_at, tz=ctx.tz)[ctx.asof]
    ctx.conn().rollback()  # end the read-only transaction; nothing to keep
    missing = sorted(cov.missing, key=str)
    total = len(cov.expected)
    bar = f"for {ctx.asof.isoformat()} recorded at/after {ctx.run_at:%H:%M} {ctx.tz}"
    if not total:
        return AuditResult(name, True, "no active theses — nothing to record")
    if not missing:
        return AuditResult(
            name, True, f"{total} of {total} active theses have an on-time row {bar}"
        )
    return AuditResult(
        name,
        False,
        f"{len(missing)} of {total} active theses have NO on-time row {bar} "
        f"(counted in the calls table; thesis ids {_short(missing)})",
    )


def check_code_sha_known(ctx: AuditContext) -> AuditResult:
    """(b) The pass's ``code_sha`` is known. Not applicable to a ``--no-live`` pass (it records no row
    for the sha to describe)."""
    name = "code_sha_known"
    if not ctx.allow_live:
        return AuditResult(name, None, "not applicable — a --no-live pass records nothing")
    if ctx.code_sha:
        return AuditResult(name, True, f"code_sha {ctx.code_sha}")
    return AuditResult(
        name,
        False,
        f"code_sha UNKNOWN on a {ctx.run_kind} pass — the image was built without GIT_SHA, so tonight's "
        "rows carry an honest NULL code_sha (rebuild with GIT_SHA=$(git rev-parse HEAD))",
    )


def check_every_thesis_finished(ctx: AuditContext) -> AuditResult:
    """(c) Every thesis the pass started reached its call-of-record outcome: ``recorded`` is a bool
    (appended / unchanged), the withhold was the deliberate ``no-live`` one, or a catch-up skipped it
    because its on-time row already stood (M2)."""
    name = "every_thesis_finished"
    started = len(ctx.results)
    if not started:
        return AuditResult(name, True, "no theses on the roster — nothing to finish")
    errored = [r.thesis_id for r in ctx.results if r.error]
    failed_withhold = [
        r.thesis_id
        for r in ctx.results
        if not r.error and r.withheld_reason and r.withheld_reason != "no-live"
    ]
    # M2 — a thesis a --catch-up SKIPPED (its on-time row already stood) is finished: the night has its row
    finished = sum(
        1
        for r in ctx.results
        if not r.error
        and (isinstance(r.recorded, bool) or r.withheld_reason == "no-live" or r.skipped)
    )
    if finished == started:
        return AuditResult(name, True, f"{finished} of {started} theses finished")
    bits = []
    if errored:
        bits.append(f"{len(errored)} errored (ids {_short(errored)})")
    if failed_withhold:
        bits.append(
            f"{len(failed_withhold)} withheld on a total ingest failure (ids {_short(failed_withhold)})"
        )
    other = started - finished - len(errored) - len(failed_withhold)
    if other > 0:
        bits.append(f"{other} with no outcome")
    return AuditResult(name, False, f"{finished} of {started} theses finished — " + "; ".join(bits))


def check_split_defects_flagged(ctx: AuditContext) -> AuditResult:
    """(d) T4 — "every split-basis defect flagged" (plan §2 criterion 6): the split-basis monitor RAN over
    tonight's tapes, so every defect it can see is on the artifact and every NEW one is in tonight's page.

    It passes WITH defects present, on purpose, and reports their count. A check that failed whenever a
    defect EXISTED would fail every night a vendor keeps a half-adjusted tape (VWAV's has stood for weeks)
    and page nightly for a known, already-flagged defect — the opposite of inverse loudness — and "this one
    is acknowledged" would need new operator state this monitor does not have. So the audit's job is the
    one it can honestly verify: the monitor looked, and nothing it found went unflagged. FAILS when the scan
    could not run on a live pass (a broken monitor is an alarm, never a silent skip); not applicable to a
    ``--no-live`` pass (no scan by design) or a caller that handed in no scan."""
    name = "split_defects_flagged"
    if not ctx.allow_live:
        return AuditResult(name, None, "not applicable — a --no-live pass does not scan the tapes")
    scan = ctx.split
    if scan is None:
        return AuditResult(name, None, "not applicable — no split-basis scan on this pass")
    if scan.error:
        return AuditResult(
            name,
            False,
            f"the split-basis monitor could not run ({scan.error}) — tonight's tapes were not checked",
        )
    if not scan.evaluated:
        return AuditResult(name, None, "not applicable — the split-basis scan did not evaluate")
    if not scan.defects:
        return AuditResult(name, True, "no split-basis defect on the active basket's tapes")
    return AuditResult(
        name,
        True,
        f"{len(scan.defects)} split-basis defect(s) flagged ({len(scan.new)} new — paged once; "
        f"{scan.known} known) — flag only, the call is unchanged",
    )


def settle_window_since(started_at: datetime, previous_end: datetime | None) -> datetime:
    """The settle check's window start: rows RECORDED at/after it are judged. PURE (the caller reads the
    artifact — ``cron_run_log.previous_settle_audit_end``).

    - **Since the previous judging pass** — ``previous_end`` is the ``finished_at`` of the newest finished
      pass whose audit JUDGED this check (passed or failed; one that could not run judged nothing), less
      ``SETTLE_WINDOW_OVERLAP``. Every price write lands before a pass's ``finished_at`` (the benchmark leg
      runs before the loop, the thesis price legs inside it), so that pass's own audit judged its own bars,
      and ANY writer since — the Workbench pull, the on-promote ingest, a repair CLI, a future open or close
      run in another process — is judged tonight. Each stored bar is judged by one pass (two, inside the
      one-minute overlap): a violation pages once, never nightly. A pass judges whatever was written since
      the last judging pass however long ago that was, so a cron that was down for a week leaves no gap.
    - **No previous judging pass** (the first pass after this check ships, an empty run home): the last
      ``SETTLE_FALLBACK_LOOKBACK``.
    - **Never before ``SETTLE_GUARD_DEPLOYED_AT``**: the history measured there predates the guard, and
      judging it would fail every night (the operator rule: pre-guard violations must not page nightly).
    """
    since = (
        previous_end - SETTLE_WINDOW_OVERLAP
        if previous_end is not None
        else started_at - SETTLE_FALLBACK_LOOKBACK
    )
    return max(since, SETTLE_GUARD_DEPLOYED_AT)


def check_no_unsettled_bars(ctx: AuditContext) -> AuditResult:
    """(e) No EOD bar stored before it settled: no ``fact_price_eod`` row (every tenant, the SPY/IWM benchmark
    tape included) recorded at/after ``ctx.bars_since`` carries a ``recorded_at`` EARLIER than its bar date's
    settle instant — ``d`` at ``ctx.settle_at`` in ``ctx.tz`` (the D2 rule, ``ingest/prices/settle.py``). It
    COUNTS THE TABLE (every version, never the deduped read): a settled re-version stacked on an in-progress
    one hides it from every as-of read after it, and still leaves the in-progress value as what the store
    claims was known before the close.

    The settle instant is built in market time (``datetime.combine(d, settle_at, tzinfo=tz)``), so a DST
    change moves it in UTC exactly as the guard does. A row is judged by its ``recorded_at`` alone — a
    BACKDATED knowability stamp before the settle instant (the one-shot benchmark backfill's ``d 00:00 UTC``)
    fails too, because it claims what the in-progress store would have: a close known before it settled.

    Applies on every pass, ``--no-live`` included (it judges the table, not the pass); not applicable when
    the caller handed in no settle time or window. CHEAP: the read is bounded by the window AND by bar date
    (a violating row's ``d`` is at least the window start's market date less one day), and the table is
    small (404,503 rows on prod 2026-10-21; a full scan took 0.15 s)."""
    name = SETTLE_CHECK
    if ctx.settle_at is None or ctx.bars_since is None:
        return AuditResult(name, None, "not applicable — no settle time / judging window handed in")
    since = ctx.bars_since
    min_d = since.astimezone(ctx.tz).date() - timedelta(days=1)
    conn = ctx.conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.d, p.recorded_at, sm.ticker FROM fact_price_eod p "
            "LEFT JOIN security_master sm ON sm.id = p.security_id "
            "WHERE p.recorded_at >= %s AND p.d >= %s",
            (since, min_d),
        )
        rows = cur.fetchall()
    conn.rollback()  # end the read-only transaction; nothing to keep
    bad = sorted(
        (
            r
            for r in rows
            if r["recorded_at"] < datetime.combine(r["d"], ctx.settle_at, tzinfo=ctx.tz)
        ),
        key=lambda r: (r["d"], r["ticker"] or "", r["recorded_at"]),
    )
    window = (
        f"recorded since {since.astimezone(ctx.tz):%Y-%m-%d %H:%M %Z} "
        f"(settle {ctx.settle_at:%H:%M} {ctx.tz})"
    )
    if not bad:
        return AuditResult(
            name,
            True,
            f"{len(rows)} bar version(s) dated {min_d.isoformat()} or later {window}; none stored before "
            "its settle instant",
        )
    by_date = Counter(r["d"] for r in bad)
    dates = ", ".join(f"{d.isoformat()} x{n}" for d, n in sorted(by_date.items()))

    def _stamp(r: dict) -> str:
        """When the row was recorded, in market time — the date too when it is not the bar's own (a
        backdated stamp the evening BEFORE the session must not read like that session's evening).
        """
        at = r["recorded_at"].astimezone(ctx.tz)
        return f"{at:%H:%M %Z}" if at.date() == r["d"] else f"{at:%Y-%m-%d %H:%M %Z}"

    labels = ", ".join(
        f"{r['ticker'] or '?'} {r['d'].isoformat()} @ {_stamp(r)}" for r in bad[:_SETTLE_LABELS]
    )
    more = f", +{len(bad) - _SETTLE_LABELS} more" if len(bad) > _SETTLE_LABELS else ""
    return AuditResult(
        name,
        False,
        f"{len(bad)} EOD bar version(s) stored BEFORE their settle instant, {window} — by bar date: "
        f"{dates}; {labels}{more} (a close the store claims was known before its session settled — a writer "
        "bypassed ingest/prices/settle.py, or backdated its stamp)",
    )
# S2 — how recent a split's first session must be for an unapplied one to FAIL the audit: the loader applies a
# split in force on its first night (``ingest.prices.split_normalize``), so one still showing its step a few
# sessions later is the prevention not working. Older unapplied splits are the repair tool's backlog — reported,
# not paged nightly (the inverse-loudness reason ``check_split_defects_flagged`` gives).
SPLIT_APPLIED_RECENT_SESSIONS = 5


def check_splits_applied(ctx: AuditContext) -> AuditResult:
    """(f) S2 — "every split in force is applied to the stored tape": for each split on the list in force on
    the pass's as-of, over the active basket union, the stored latest-version tape shows no step the size of
    the split at its first session (``split_normalize.boundary``: a k-sized move with the inverse volume move
    reads ``STORE_RAW``). FAILS for one whose first session is within ``SPLIT_APPLIED_RECENT_SESSIONS`` sessions
    (the price leg should have applied it); an older unapplied one passes with its ticker named (run
    ``python -m pipeline.repair_split_basis``). A split too small to read on the tape (under x1.8) is never
    reported. Not applicable to a ``--no-live`` pass."""
    from db.session import DEFAULT_TENANT_ID
    from domain.splits import applicable, listed_from_row, session_distance
    from ingest.prices.split_normalize import STORE_RAW, boundary
    from pipeline.split_basis import _UNION
    from repositories import split_repo

    name = "splits_applied"
    if not ctx.allow_live:
        return AuditResult(name, None, "not applicable — a --no-live pass re-pulls no tape")
    conn = ctx.conn()
    with conn.cursor() as cur:
        cur.execute(_UNION)
        ids = sorted({r["security_id"] for r in cur.fetchall()})
    versions = split_repo.latest_versions(conn, tenant_id=DEFAULT_TENANT_ID, security_ids=ids)
    by_sid: dict = {}
    tickers: dict = {}
    for v in versions:
        by_sid.setdefault(v.security_id, []).append(listed_from_row(v.model_dump()))
        tickers[v.security_id] = v.ticker
    checked, recent, backlog = 0, [], []
    for sid, splits in by_sid.items():
        for s in applicable(splits, on=ctx.asof):
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT ON (d) d, close, volume FROM fact_price_eod "
                    "WHERE tenant_id = %s AND security_id = %s AND d BETWEEN %s AND %s "
                    "ORDER BY d, recorded_at DESC, id DESC",
                    (
                        DEFAULT_TENANT_ID,
                        sid,
                        s.effective_date - timedelta(days=14),
                        s.effective_date + timedelta(days=14),
                    ),
                )
                window = {r["d"]: r for r in cur.fetchall()}
            state, first_night = boundary(window, (), s)
            if first_night:
                continue  # no bar on/after the first session stored yet: nothing to judge
            checked += 1
            if state != STORE_RAW:
                continue
            label = f"{tickers.get(sid) or str(sid)[:8]} {s.label} {s.effective_date.isoformat()}"
            if session_distance(s.effective_date, ctx.asof) <= SPLIT_APPLIED_RECENT_SESSIONS:
                recent.append(label)
            else:
                backlog.append(label)
    if recent:
        return AuditResult(
            name,
            False,
            f"{len(recent)} recent split(s) in force NOT applied to the stored tape (the price leg's first "
            f"night did not take): {'; '.join(recent[:12])}",
        )
    if backlog:
        return AuditResult(
            name,
            True,
            f"{checked} split(s) in force checked; {len(backlog)} older one(s) not yet applied to the stored "
            f"tape — run python -m pipeline.repair_split_basis: {'; '.join(backlog[:12])}"
            + (f" (+{len(backlog) - 12} more)" if len(backlog) > 12 else ""),
        )
    return AuditResult(name, True, f"every split in force is applied ({checked} checked)")


DEFAULT_CHECKS: tuple[AuditCheck, ...] = (
    check_on_time_rows,
    check_code_sha_known,
    check_every_thesis_finished,
    check_split_defects_flagged,
    check_no_unsettled_bars,
    check_splits_applied,
)


def run_audit(
    ctx: AuditContext, checks: Sequence[AuditCheck] = DEFAULT_CHECKS
) -> list[AuditResult]:
    """Run every check in order, each ISOLATED: one that raises is reported as a FAILED check naming the
    exception (never skipped silently), and the rest still run. Closes the audit's connection after.
    """
    out: list[AuditResult] = []
    try:
        for check in checks:
            name = getattr(check, "__name__", "check").removeprefix("check_")
            try:
                out.append(check(ctx))
            except Exception as exc:  # noqa: BLE001 — a broken check is a FAILED check, loud
                try:
                    if ctx._conn is not None:
                        ctx._conn.rollback()
                except Exception:  # noqa: BLE001 — the next check gets a fresh chance regardless
                    ctx.close()
                msg = " ".join(str(exc).split())[:200]
                out.append(
                    AuditResult(name, False, f"{COULD_NOT_RUN}: {type(exc).__name__}: {msg}")
                )
    finally:
        ctx.close()
    return out


def failed_labels(audit: Sequence[AuditResult]) -> tuple[str, ...]:
    """The page lines for the FAILED checks (``ok is False`` only — a not-applicable check never pages)."""
    return tuple(f"{a.check}: {a.detail}" for a in audit if a.ok is False)
