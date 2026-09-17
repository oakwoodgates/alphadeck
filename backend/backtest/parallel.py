"""PER-THESIS PARALLELISM over one frozen mirror.

The theses are independent: each replays its own basket against the same Parquet files and writes nothing.
So the sweep fans out by thesis, N worker processes over the SAME mirror directory, and the results are
reassembled in a deterministic order.

**Why processes and not threads.** The work is CPU-bound Python — the detector pass and the assembler —
under the GIL, so threads would serialize exactly the part worth parallelizing. DuckDB is opened
per-worker over read-only Parquet, which is what makes a shared mirror safe: nothing is written, so there
is no coordination to get wrong.

**Where this sits in the cost picture, honestly.** B5a's PIT port was the 25x; this is the multiple after
it, and it is bounded by the LARGEST thesis rather than by the worker count — a 196-name basket over a
year is ~7 min on its own, so six workers over twelve theses converge on that, not on one twelfth of the
total. Sharding a large thesis by member would lift that ceiling, but it cannot be done by splitting this
loop: `assemble_call` needs EVERY member's events to rank a basket and pick a headline, so a member-shard
worker would have to return events rather than snapshots and be re-joined before assembly. That is a real
change to the seam, not a tuning knob, so it is deliberately not attempted here.

**Determinism is the acceptance test, not a nice-to-have.** A run is an addressable artifact that a
promotion cites, so `--workers N` must produce byte-identical episodes to `--workers 1`. Results are keyed
by thesis id and reassembled in the caller's order, never in completion order.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.config import CallConfig
from replay.harness import ReplayResult, RosterSource, replay_thesis_with_roster
from replay.schema import CallSnapshot

# The worker's payload: everything it needs, all picklable. `spawn` is the start method on Windows, so a
# worker re-imports this module and gets nothing from the parent's memory -- which is also why the mirror
# is passed as a PATH rather than an open connection.
_Job = tuple[str, str, str, str, str, str, str, str]


def _worker(job: _Job) -> tuple[str, list[str], tuple[str, int, int]]:
    """Replay ONE thesis in a fresh process. Returns JSON-ish primitives so nothing depends on the parent
    and the child's imports stay minimal."""
    mirror, thesis_id, start, end, pin, cfg_json, tenant_id, known_at_mode = job

    from db.session import connect
    from replay.pit import connect_mirror
    from repositories import thesis_repo

    cfg = CallConfig.model_validate_json(cfg_json)
    conn = connect()
    con = connect_mirror(mirror)
    try:
        thesis = thesis_repo.get(conn, UUID(thesis_id))
        if thesis is None:
            return thesis_id, [], ("no_sessions", 0, 0)
        snaps, source = replay_thesis_with_roster(
            con,
            thesis,
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            known_at=datetime.fromisoformat(pin),
            cfg=cfg,
            tenant_id=UUID(tenant_id),
            conn=conn,
            known_at_mode=known_at_mode,
        )
        return (
            thesis_id,
            [s.model_dump_json() for s in snaps],
            (source.source, source.fallback_days, source.total_days),
        )
    finally:
        con.close()
        conn.close()


def replay_all_parallel(
    conn: psycopg.Connection,
    mirror_dir: str | Path,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    known_at_mode: str = "pin",
    workers: int = 1,
) -> ReplayResult:
    """The parallel twin of ``replay.harness.replay_all`` — same inputs, byte-identical output.

    ``workers <= 1`` runs the serial harness untouched, so the default path is the code that is already
    proven rather than a one-worker special case of a new one.
    """
    from repositories import thesis_repo

    theses = thesis_repo.list_all(conn)  # archived EXCLUDED (the locked default)
    if workers <= 1 or len(theses) <= 1:
        from replay.harness import replay_all
        from replay.pit import connect_mirror

        con = connect_mirror(mirror_dir)
        try:
            return replay_all(
                conn,
                con,
                start=start,
                end=end,
                known_at=known_at,
                cfg=cfg,
                tenant_id=tenant_id,
                known_at_mode=known_at_mode,
            )
        finally:
            con.close()

    mirror = str(Path(mirror_dir))
    cfg_json = cfg.model_dump_json()
    jobs: list[_Job] = [
        (
            mirror,
            str(t.id),
            start.isoformat(),
            end.isoformat(),
            known_at.isoformat(),
            cfg_json,
            str(tenant_id),
            known_at_mode,
        )
        # BIGGEST FIRST. The wall clock is the longest single thesis, so starting the 196-name basket last
        # would leave five idle workers waiting on it. Longest-processing-time-first is the standard
        # scheduling heuristic and costs nothing here, since basket size is already in hand.
        for t in sorted(theses, key=lambda t: len(t.basket), reverse=True)
    ]

    collected: dict[str, tuple[list[str], tuple[str, int, int]]] = {}
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        for thesis_id, snaps, source in pool.map(_worker, jobs):
            collected[thesis_id] = (snaps, source)

    # REASSEMBLED IN THE CALLER'S ORDER, never completion order -- a run is an addressable artifact and
    # its episode file must not depend on which worker finished first.
    timelines: dict[UUID, list[CallSnapshot]] = {}
    roster_sources: dict[UUID, RosterSource] = {}
    for t in theses:
        snaps, source = collected.get(str(t.id), ([], ("no_sessions", 0, 0)))
        timelines[t.id] = [CallSnapshot.model_validate_json(s) for s in snaps]
        roster_sources[t.id] = RosterSource(
            source=source[0], fallback_days=source[1], total_days=source[2]
        )
    return ReplayResult(timelines=timelines, roster_sources=roster_sources)


def default_workers() -> int:
    """A sane default when the operator does not say: one per core, capped at 6 — the same `-n 6` the test
    suite settled on, and past that the shared Postgres roster reads become the contended resource rather
    than the CPU."""
    return max(1, min(6, os.cpu_count() or 1))
