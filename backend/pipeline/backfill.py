"""``pipeline.backfill`` — record a MISSED night's call-of-record with a PINNED ``known_at`` (the faithful
reconstruction).

The cron sidecar missed nights (a laptop-sleep drift, closed by the target-at-schedule-time fix in
``scripts/daily_cron.sh``). The ``calls`` log is immutable and bitemporal: a row's ``recorded_at`` is
always now. A naive backfill — ``python -m pipeline.daily --asof <past>`` — computes the past night with
``known_at = now``, i.e. TODAY's knowledge: MEASURED on dev, a thesis backfilled as ARMED on nights whose
real nightly neighbors recorded INCUBATING, because its facts arrived AFTER those nights. This module is
the faithful option: it computes the missed night with ``known_at`` PINNED to a past instant, so the row
shows what the cron WOULD have logged, consistent with its recorded neighbors. The as-of gate is two-axis
(``valid_from <= asof`` AND ``recorded_at <= known_at`` — ``docs/INVARIANTS.md`` #4), and ``known_at`` is
already a parameter of ``call_for_thesis``; this CLI adds nothing to the assembly, it only pins the clock.

The recommended pin is ``finished_at`` of the FIRST live cron run after the missed night (``--known-at
next-run``): that run ingested the night's own EOD bar and its filings and excludes everything that arrived
later. A pin at 22:30 THAT night would MISS the night's bar — the next morning's run is what ingested it.

    python -m pipeline.backfill --asof 2026-09-08 --known-at 2026-09-09T13:22:07Z
    python -m pipeline.backfill --asof 2026-09-08 --known-at next-run          # resolve from the run artifacts
    python -m pipeline.backfill --asof 2026-09-08 --known-at next-run --thesis <uuid> --dry-run

Discipline (``daily.run_daily``'s, minus everything that is not a recompute):
- **A pure recompute-and-record over facts already in the store.** NO ingest, NO refresh legs, NO SPAC legs,
  NO notifier — the module imports none of them (pinned structurally by the import guard in
  ``tests/pipeline/test_backfill.py``). No ``TransitionEvent`` either: a reconstructed row is a RECORD, never
  a nag — a Slack "ARMED" for a night two weeks ago would be exactly the wrong loudness (#7).
- **Per-thesis isolation** — own try, commit-on-success / rollback-on-failure; one thesis's failure is
  captured and the rest still record.
- **Idempotent** — ``record_if_changed``'s canonical compare: a re-run with the same ``asof`` + ``known_at``
  appends ZERO rows. A DIFFERENT pin that sees different facts is a genuine change and appends one.
- **The NULL ingest stamp.** ``ingest_fresh`` / ``ingest_errors`` stay ``None``: there is no ingest in a
  backfill, NULL is the honest stamp, and it distinguishes a reconstructed row from a nightly one (which
  always carries True/False since R2b).
- **Provenance, write-only, fail-open** — one JSON per invocation under ``data/backfills/``
  (``pipeline/backfill_log.py``). It is NOT a cron run artifact: ``already_ran_live`` stays False for the
  night. A ``--dry-run`` writes NOTHING — neither a row nor an artifact.
- **Refusals, all loud (exit 2), all BEFORE a connection is opened:** ``asof >= market_today()`` (tonight is
  the cron's job); a naive ``--known-at`` (a wrong zone shifts real answers invisibly); a ``known_at``
  earlier than the as-of day begins (a pin before the night cannot see the night); ``next-run`` with no
  live run after the night in the run log.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timezone, tzinfo
from pathlib import Path
from uuid import UUID

import psycopg

from db.session import connect
from domain.market_time import market_today, market_tz
from pipeline.backfill_log import write_backfill_log
from pipeline.call_for_thesis import call_for_thesis
from pipeline.cron_run_log import list_run_logs
from repositories import calls_repo, thesis_repo

NEXT_RUN = "next-run"  # the --known-at literal that resolves the pin from the cron run log


@dataclass
class BackfillResult:
    """Per-thesis outcome. ``recorded``: True = a reconstructed call-of-record was appended, False = an
    identical row was already logged for this as-of (no row), None = dry-run or the call step failed (see
    ``error``). ``prior_state`` / ``prior_verdict``: the row already logged for this as-of before the run
    (None = the night had no row — the missed-night case)."""

    thesis_id: UUID
    name: str
    state: str | None = None
    verdict: str | None = None
    armed: int = 0
    prior_state: str | None = None
    prior_verdict: str | None = None
    recorded: bool | None = None
    error: str | None = None


# --- the pin: parsing + the next-run resolution (pure; no DB, no clock) --------------------------------


def parse_known_at(raw: str) -> datetime:
    """An explicit ``--known-at``: ISO-8601 WITH an explicit offset or ``Z``, normalized to UTC. A NAIVE
    datetime is refused loudly — a wrong zone shifts real answers invisibly (the ``market_time`` rule).
    """
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"--known-at {raw!r} is not an ISO-8601 datetime") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"--known-at {raw!r} has no timezone — pass an explicit offset or Z "
            "(a naive instant would be read in whatever zone the process happens to run in)"
        )
    return dt.astimezone(timezone.utc)


def night_start(asof: date, tz: tzinfo) -> datetime:
    """``asof`` 00:00:00 in market time — the earliest pin that can see the night at all."""
    return datetime.combine(asof, time.min, tzinfo=tz)


def night_end(asof: date, tz: tzinfo) -> datetime:
    """``asof`` 23:59:59 in market time — a run started at or before this is ON the night, not after it."""
    return datetime.combine(asof, time(23, 59, 59), tzinfo=tz)


def resolve_next_run_known_at(payloads: Iterable[dict], asof: date, tz: tzinfo) -> datetime | None:
    """The ``next-run`` policy, PURE over parsed run-of-record payloads (``cron_run_log.list_run_logs``'
    shape): the ``finished_at`` of the EARLIEST **live** run whose ``started_at`` is AFTER ``asof`` 23:59:59
    in market time, as an aware UTC datetime — or ``None`` when no such run exists.

    Why that run: its ingest saw the night's own EOD bar and its filings (the next morning's run is what
    ingested them) and nothing that arrived later. Why the bounds: a ``no-live`` run refreshed nothing (the
    R2 recording gate) so it is no evidence of what was knowable; a run started ON the as-of day (a same-day
    manual run) is not "after the night". A ``--catch-up`` pass is a live run and counts. Fail-open PER
    PAYLOAD: an artifact with a missing / unparseable / naive stamp is skipped, never a crash."""
    cutoff = night_end(asof, tz)
    best: tuple[datetime, datetime] | None = None
    for doc in payloads:
        if not isinstance(doc, dict) or doc.get("mode") != "live":
            continue
        try:
            started = datetime.fromisoformat(doc["started_at"])
            finished = datetime.fromisoformat(doc["finished_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if started.tzinfo is None or finished.tzinfo is None:
            continue  # a naive stamp cannot be compared honestly against a market-time cutoff
        if started <= cutoff:
            continue
        if best is None or started < best[0]:
            best = (started, finished)
    return best[1].astimezone(timezone.utc) if best is not None else None


# --- the run -----------------------------------------------------------------------------------------------


def run_backfill(
    conn: psycopg.Connection,
    *,
    asof: date,
    known_at: datetime,
    thesis_id: UUID | None = None,
    dry_run: bool = False,
) -> list[BackfillResult]:
    """Reconstruct the call-of-record for ``asof`` under a PINNED ``known_at`` for every non-archived thesis
    (``thesis_repo.list_all``, the cron's set) or the one named — exactly ``daily.run_daily``'s per-thesis
    isolation: each thesis in its own try, commit on success, rollback on failure, never fatal to the run.
    ``dry_run`` assembles and reports but writes NOTHING (the read transaction is rolled back). Raises
    ``LookupError`` for an unknown ``thesis_id`` (before any thesis runs)."""
    if known_at.tzinfo is None or known_at.utcoffset() is None:
        raise ValueError(
            "known_at must be timezone-aware — the pin is an instant, never a wall time"
        )
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis not found: {thesis_id}")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    out: list[BackfillResult] = []
    for thesis in theses:
        res = BackfillResult(thesis_id=thesis.id, name=thesis.name)
        try:
            # the row this night already has (the missed-night case has none) — reported, never a gate:
            # record_if_changed does its own canonical compare against exactly this row
            prior = next(
                (c for c in calls_repo.latest_for_thesis(conn, thesis.id) if c.asof == asof), None
            )
            if prior is not None:
                res.prior_state, res.prior_verdict = prior.state.value, prior.verdict.value
            # THE reconstruction: the same assembly the cron runs, with the clock pinned. record=False —
            # the append below is the ONLY write, and it is the idempotent one.
            card = call_for_thesis(conn, thesis.id, asof, known_at=known_at, record=False)
            res.state, res.verdict = card.state.value, card.verdict.value
            res.armed = len(card.armed_members)
            if dry_run:
                conn.rollback()  # nothing to keep — end the read transaction cleanly
            else:
                # ingest_fresh / ingest_errors stay None ON PURPOSE: there was no ingest (the NULL stamp)
                res.recorded = calls_repo.record_if_changed(conn, card, thesis.tenant_id)
                conn.commit()
        except Exception as e:  # noqa: BLE001 — one thesis's failure never aborts the backfill
            conn.rollback()
            res.error = f"call: {e}"
        out.append(res)
    return out


@dataclass
class BackfillPassOutcome:
    """One COMPLETED backfill invocation: the per-thesis results + the run metadata the artifact carries.
    ``log_path`` is the written provenance artifact, or ``None`` (a dry run, or the fail-open write).
    """

    results: list[BackfillResult]
    asof: date
    known_at: datetime
    known_at_policy: str
    dry_run: bool
    started_at: datetime
    finished_at: datetime
    log_path: Path | None


def run_backfill_pass(
    *,
    asof: date,
    known_at: datetime,
    known_at_policy: str,
    thesis_id: UUID | None = None,
    dry_run: bool = False,
    log_dir: Path | None = None,
) -> BackfillPassOutcome:
    """The backfill's FULL unit of work: connect → ``run_backfill`` → write the provenance artifact
    (fail-open; SKIPPED on a dry run — a dry run writes nothing anywhere). ``main`` keeps the CLI concerns
    only (args, the refusals, the printed report + exit code)."""
    started_at = datetime.now(timezone.utc)
    conn = connect()
    try:
        results = run_backfill(
            conn, asof=asof, known_at=known_at, thesis_id=thesis_id, dry_run=dry_run
        )
    finally:
        conn.close()
    finished_at = datetime.now(timezone.utc)
    log_path = None
    if not dry_run:
        log_path = write_backfill_log(
            results,
            asof=asof,
            known_at=known_at,
            known_at_policy=known_at_policy,
            dry_run=dry_run,
            started_at=started_at,
            finished_at=finished_at,
            base_dir=log_dir,
        )
    return BackfillPassOutcome(
        results=results,
        asof=asof,
        known_at=known_at,
        known_at_policy=known_at_policy,
        dry_run=dry_run,
        started_at=started_at,
        finished_at=finished_at,
        log_path=log_path,
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _report(results: list[BackfillResult], *, dry_run: bool) -> int:
    """Print a per-thesis summary (``daily._report``'s shape); return the number that errored (the process
    exit signal)."""
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            print(f"  {r.name}: ERROR: {r.error}")
            continue
        if dry_run:
            mark = "DRY-RUN (nothing written)"
        elif r.recorded:
            mark = "APPENDED"
        else:
            mark = "unchanged (identical row already logged)"
        prior = (
            f"prior row: {r.prior_state} / {r.prior_verdict}"
            if r.prior_state
            else "no row for this as-of yet"
        )
        print(f"  {r.name}: {r.state} / {r.verdict} · {r.armed} armed · {prior} · {mark}")
    dr = " · DRY-RUN (nothing written)" if dry_run else ""
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged · "
        f"{len(errored)} errored{dr}"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill a MISSED night's call-of-record with known_at PINNED to a past instant "
        "(a pure recompute-and-record over facts already in the store: no ingest, no notify)."
    )
    p.add_argument(
        "--asof", required=True, help="the missed night, YYYY-MM-DD (must be in the past)"
    )
    p.add_argument(
        "--known-at",
        required=True,
        dest="known_at",
        help="the transaction-time pin: an ISO-8601 datetime WITH an offset or Z (naive is refused), "
        f"or the literal '{NEXT_RUN}' = finished_at of the first live cron run after the night",
    )
    p.add_argument(
        "--thesis", default=None, help="one thesis id (uuid); default = every live thesis"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="assemble and print; write NOTHING (no row, no artifact)",
    )
    args = p.parse_args(argv)

    # --- the refusals: every one fires BEFORE a connection is opened ---
    try:
        asof = date.fromisoformat(args.asof)
    except ValueError:
        p.error(f"--asof {args.asof!r} is not a YYYY-MM-DD date")
    today = market_today()
    if asof >= today:
        p.error(
            f"--asof {asof} is not in the past (market today is {today}) — tonight is the cron's job; "
            "a backfill is for a MISSED night"
        )
    tz = market_tz()
    if args.known_at == NEXT_RUN:
        known_at = resolve_next_run_known_at(list_run_logs(), asof, tz)
        if known_at is None:
            p.error(
                f"no live run after {asof} in the run log — pass an explicit --known-at "
                "(the finished_at of the first run that ingested the night)"
            )
        policy = "next-run"
    else:
        try:
            known_at = parse_known_at(args.known_at)
        except ValueError as exc:
            p.error(str(exc))
        policy = "explicit"
    floor = night_start(asof, tz)
    if known_at < floor:
        p.error(
            f"--known-at {known_at.isoformat()} is BEFORE the as-of day begins "
            f"({floor.isoformat()}) — a pin earlier than the night cannot see the night"
        )
    thesis_id: UUID | None = None
    if args.thesis:
        try:
            thesis_id = UUID(args.thesis)
        except ValueError:
            p.error(f"--thesis {args.thesis!r} is not a uuid")

    # the resolved instant, in BOTH clocks, before anything happens
    dr = " · DRY-RUN" if args.dry_run else ""
    print(
        f"backfill: asof={asof} · known_at={known_at.isoformat()} (UTC) = "
        f"{known_at.astimezone(tz).isoformat()} (market) · policy={policy}{dr}"
    )
    try:
        outcome = run_backfill_pass(
            asof=asof,
            known_at=known_at,
            known_at_policy=policy,
            thesis_id=thesis_id,
            dry_run=args.dry_run,
        )
    except LookupError as exc:
        p.error(str(exc))
    if outcome.log_path is not None:
        print(f"provenance: {outcome.log_path}")
    if _report(outcome.results, dry_run=args.dry_run):
        raise SystemExit(1)  # surface partial failure non-silently (the daily discipline)


if __name__ == "__main__":
    main()
