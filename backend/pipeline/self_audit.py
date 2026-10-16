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

- ``on_time_rows`` — every ACTIVE (non-archived) thesis has at least one NON-reconstructed ``calls`` row for
  the pass's as-of recorded at/after that night's ``RUN_AT`` in market time. It COUNTS THE TABLE (one
  ``EXISTS`` per thesis against ``calls``), never the deduped read and never the pass's own results — a
  pass that believes it recorded is exactly what this must not trust.
- ``code_sha_known`` — the pass's ``code_sha`` (``Settings.image_sha``) is known. 84 cron rows carried a NULL
  sha 09-18..09-28 (MEASURED) because an image was rebuilt without ``GIT_SHA``; the rows stay an honest
  NULL, this makes it loud the night it happens.
- ``every_thesis_finished`` — every thesis the pass started reached its call-of-record outcome (appended or
  unchanged; a deliberate ``--no-live`` withhold counts as finished). An errored call step or a
  total-ingest-failure withhold is a thesis the pass started and did not finish. (A pass that dies MID-loop
  writes no artifact at all — that is the H3 crash page's job, and the start-artifact in Phase 3.)

ADDING A CHECK: write ``def check_x(ctx: AuditContext) -> AuditResult`` (pure over ``ctx``; open the DB only
through ``ctx.conn()``), append it to ``DEFAULT_CHECKS``. Each check is isolated: one that RAISES is reported
as a FAILED check naming the exception (a broken audit is itself an alarm — never a silent skip), and the
rest still run.

BOUNDS: READ-ONLY (the audit's connection is ``read_only``; it writes no row), value-free (counts and short
ids — never a thesis NAME, because the details ride the health page), and no ambient clock (the pass's
``started_at`` / ``asof`` and the configured ``RUN_AT`` / zone are passed in).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, tzinfo
from typing import TYPE_CHECKING

import psycopg

if TYPE_CHECKING:  # keep the layering one-way (no runtime import of the cron module)
    from pipeline.daily import ThesisRunResult


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
    """(a) Every active thesis has a non-reconstructed row for ``asof`` recorded at/after the night's
    ``RUN_AT`` — counted in the ``calls`` table. Not applicable to a ``--no-live`` pass (it withholds by
    design) or to a pass that STARTED before the cutoff (a daytime pass runs on the prior session's bars;
    the night's on-time row is the post-close pass's to write)."""
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
    with ctx.conn().cursor() as cur:
        cur.execute(
            """SELECT t.id,
                      EXISTS (SELECT 1 FROM calls c
                               WHERE c.thesis_id = t.id AND c.asof = %(asof)s
                                 AND NOT c.reconstructed AND c.recorded_at >= %(cutoff)s) AS on_time
                 FROM thesis t
                WHERE t.archived_at IS NULL
                ORDER BY t.id""",
            {"asof": ctx.asof, "cutoff": ctx.cutoff},
        )
        rows = cur.fetchall()
    ctx.conn().rollback()  # end the read-only transaction; nothing to keep
    missing = [r["id"] for r in rows if not r["on_time"]]
    total = len(rows)
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
    (appended / unchanged), or the withhold was the deliberate ``no-live`` one."""
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
    finished = sum(
        1
        for r in ctx.results
        if not r.error and (isinstance(r.recorded, bool) or r.withheld_reason == "no-live")
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


DEFAULT_CHECKS: tuple[AuditCheck, ...] = (
    check_on_time_rows,
    check_code_sha_known,
    check_every_thesis_finished,
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
                    AuditResult(
                        name, False, f"the check could not run: {type(exc).__name__}: {msg}"
                    )
                )
    finally:
        ctx.close()
    return out


def failed_labels(audit: Sequence[AuditResult]) -> tuple[str, ...]:
    """The page lines for the FAILED checks (``ok is False`` only — a not-applicable check never pages)."""
    return tuple(f"{a.check}: {a.detail}" for a in audit if a.ok is False)
