from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

import backtest.parallel as parallel_mod  # noqa: E402
from backtest.parallel import default_workers, replay_all_parallel  # noqa: E402
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG  # noqa: E402
from domain.enums import State  # noqa: E402
from pipeline.seed import seed_hims, seed_unh  # noqa: E402
from replay.export import export_snapshot  # noqa: E402
from replay.harness import replay_all  # noqa: E402
from replay.pit import connect_mirror  # noqa: E402

# B5b — PER-THESIS PARALLELISM. The acceptance test is DETERMINISM, not speed: a run is an addressable
# artifact that a promotion cites, so `--workers N` must produce byte-identical timelines and roster
# provenance to `--workers 1`. Two theses (UNH + HIMS) are required because `replay_all_parallel` falls
# back to the serial harness when `len(theses) <= 1`; a short window covering HIMS's warm -> arm arc
# keeps the multiprocess round-trip non-vacuous and fast.

_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2026, 5, 25), date(2026, 6, 10)


def _dump(result):
    return {
        str(tid): [s.model_dump_json() for s in snaps]
        for tid, snaps in sorted(result.timelines.items(), key=lambda kv: str(kv[0]))
    }


def test_parallel_workers_match_serial_timelines_and_roster_provenance(db, tmp_path, monkeypatch):
    """Two theses over one frozen mirror: `workers=2` actually executes via `ProcessPoolExecutor` and
    produces byte-identical per-day `CallSnapshot` timelines and `RosterSource` provenance to both
    `workers=1` and the direct serial `replay_all` harness."""
    seed_unh(db)
    seed_hims(db)
    db.commit()
    export_snapshot(db, tmp_path, workspace=DEFAULT_TENANT_ID)

    pool_uses = {"n": 0}

    class _CountingPool(ProcessPoolExecutor):
        def __init__(self, *args, **kwargs):
            pool_uses["n"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(parallel_mod, "ProcessPoolExecutor", _CountingPool)

    con = connect_mirror(tmp_path)
    try:
        direct = replay_all(db, con, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG)
    finally:
        con.close()

    serial = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=1
    )
    assert pool_uses["n"] == 0, "workers=1 must stay on the serial harness"

    parallel = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=2
    )
    assert pool_uses["n"] == 1, "workers=2 with two theses must enter ProcessPoolExecutor"

    # Non-vacuity: both theses replayed real sessions and HIMS armed inside the window.
    assert len(serial.timelines) == 2
    assert all(len(snaps) > 0 for snaps in serial.timelines.values())
    assert any(
        s.state is State.ARMED for snaps in serial.timelines.values() for s in snaps
    ), "window should include a real Armed snapshot"

    assert _dump(direct) == _dump(serial) == _dump(parallel)
    assert serial.roster_sources == parallel.roster_sources
    assert serial.note() == parallel.note()


def test_the_default_worker_count_is_bounded(monkeypatch):
    """Capped at 6 -- the same bound the test suite settled on. Past that the shared Postgres roster reads
    become the contended resource rather than the CPU, so more workers buy contention, not throughput.
    """
    monkeypatch.setattr("backtest.parallel.os.cpu_count", lambda: 64)
    assert default_workers() == 6
    monkeypatch.setattr("backtest.parallel.os.cpu_count", lambda: 2)
    assert default_workers() == 2
    monkeypatch.setattr("backtest.parallel.os.cpu_count", lambda: None)
    assert default_workers() == 1
