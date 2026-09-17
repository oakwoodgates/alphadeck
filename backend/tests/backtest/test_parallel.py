from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from backtest.parallel import default_workers, replay_all_parallel  # noqa: E402
from domain.config import DEFAULT_CONFIG  # noqa: E402
from pipeline.seed import seed_unh  # noqa: E402
from replay.export import export_snapshot  # noqa: E402

# B5b — PER-THESIS PARALLELISM. The acceptance test is DETERMINISM, not speed: a run is an addressable
# artifact that a promotion cites, so `--workers N` must produce byte-identical timelines to `--workers 1`.
# Speed is measured on the dev copy, not asserted here -- a timing assertion on a shared CI box is a flake
# waiting to happen, and a wrong-but-fast answer is worse than a slow one.

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2026, 6, 1)


def _dump(result):
    return {
        str(tid): [s.model_dump_json() for s in snaps]
        for tid, snaps in sorted(result.timelines.items(), key=lambda kv: str(kv[0]))
    }


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_parallel_is_byte_identical_to_serial(db, tmp_path):
    """The whole claim. Same mirror, same window, same cfg -- the only difference is how many processes
    did it, and that must not reach the output."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)

    serial = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=1
    )
    parallel = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=2
    )
    assert _dump(serial) == _dump(parallel)


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_roster_provenance_survives_the_round_trip(db, tmp_path):
    """`RosterSource` is the label behind every replayed episode and it crosses a process boundary as
    primitives. If it were dropped or defaulted, the artifact would claim a point-in-time roster it never
    read -- exactly what F4 added the field to prevent."""
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)

    serial = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=1
    )
    parallel = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=2
    )
    assert serial.roster_sources == parallel.roster_sources
    assert serial.note() == parallel.note()


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_one_worker_takes_the_serial_path_untouched(db, tmp_path):
    """`workers <= 1` runs the harness that is already proven, rather than a one-worker special case of a
    new code path. Asserted by equality with the harness's own output."""
    from replay.harness import replay_all
    from replay.pit import connect_mirror

    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)

    con = connect_mirror(tmp_path)
    try:
        direct = replay_all(db, con, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG)
    finally:
        con.close()
    via = replay_all_parallel(
        db, tmp_path, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG, workers=1
    )
    assert _dump(direct) == _dump(via)


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
