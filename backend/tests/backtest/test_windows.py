from __future__ import annotations

import threading
from datetime import date, datetime, timezone

import pytest

from backtest import manifest as mf
from backtest import store
from backtest.windows import DEFAULT_WINDOW_DAYS, default_concurrency, tile
from domain.config import DEFAULT_CONFIG

# S1 -- THE WINDOW IS THE UNIT OF WORK.
#
# A year-long run is not one on this box: `--workers` parallelizes the replay phase only, so the null
# draws run serially on one process start to finish, and a 1-year run did not finish in 3h40m. Separate
# window PROCESSES are what parallelize the nulls. That changes what a curve point IS -- the pooled read
# across its windows -- and it makes the old sub-window split of one long run into cross-WINDOW agreement
# between genuinely separate measurements.
#
# Three things have to hold for that to be trustworthy, and they are what this file tests: the tiling
# covers the pass exactly, concurrent runs cannot drop each other's registry rows, and the analysis pools
# and re-splits the same numbers the artifacts carry.

_T0, _T1 = date(2025, 9, 1), date(2026, 9, 14)


# --- the tiling ---------------------------------------------------------------------------------------


def test_the_windows_are_disjoint_gapless_and_cover_the_span_exactly():
    """A reader must never have to wonder which weeks fell between two points."""
    w = tile(_T0, _T1)
    assert w[0][0] == _T0 and w[-1][1] == _T1
    for (_, a_hi), (b_lo, _) in zip(w, w[1:], strict=False):
        assert (b_lo - a_hi).days == 1  # contiguous: no overlap and no gap
    assert all(lo <= hi for lo, hi in w)


def test_a_year_tiles_into_nine_six_week_windows():
    """The number the pass sizing was arithmetic on, pinned so it cannot drift silently."""
    assert len(tile(_T0, _T1, DEFAULT_WINDOW_DAYS)) == 9


def test_the_tail_is_ABSORBED_rather_than_left_as_a_stub():
    """A final window much shorter than the others has too few sessions for its own delta to mean much,
    and a stub point in a curve reads as a result. Dropping it instead would silently shorten the pass.
    """
    w = tile(date(2026, 1, 1), date(2026, 2, 20), 42)  # 51 days: 42 + a 9-day remainder
    assert len(w) == 1
    assert w == [(date(2026, 1, 1), date(2026, 2, 20))]


def test_a_span_shorter_than_one_window_is_one_window():
    assert tile(date(2026, 1, 1), date(2026, 1, 10), 42) == [(date(2026, 1, 1), date(2026, 1, 10))]


def test_a_single_day_span_is_a_window():
    assert tile(_T0, _T0, 42) == [(_T0, _T0)]


def test_a_zero_length_window_is_refused_rather_than_looping_forever():
    with pytest.raises(ValueError):
        tile(_T0, _T1, 0)


def test_the_concurrency_default_is_this_box_not_the_cpu_count():
    """MEASURED: six replay workers bought ~2.8 usable cores here. Two concurrent window jobs saturate
    that; the cap stays at 3 even on a bigger box, because a pass that saturates its machine measures
    contention as well as dials."""
    assert 1 <= default_concurrency() <= 3
    assert default_concurrency() == 2


# --- the run id and the pass id -------------------------------------------------------------------------


def test_two_windows_of_one_point_get_two_run_ids_in_the_same_second():
    """The reason the window joined the id. A tiling pass launches a point's window runs CONCURRENTLY, so
    they differ only by window and land in the same second by construction -- without it the second one
    dies on `create_run_dir`, exactly as the two fact clocks did before CW."""
    now = datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc)
    a = mf.make_run_id(DEFAULT_CONFIG, hypothesis="H5", now=now, window_start=date(2025, 9, 1))
    b = mf.make_run_id(DEFAULT_CONFIG, hypothesis="H5", now=now, window_start=date(2025, 10, 13))
    assert a != b
    assert "-w20250901-" in a and "-w20251013-" in b  # legible in a directory listing


def test_a_run_with_no_window_reads_exactly_as_it_did_before():
    """Omitted, the component is ABSENT rather than defaulted -- a standalone run's id is unchanged."""
    now = datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc)
    assert mf.make_run_id(DEFAULT_CONFIG, hypothesis=None, now=now) == (
        f"20260918T031500Z-record-default-{mf.short_hash(mf.config_hash(DEFAULT_CONFIG))}"
    )


def test_the_pass_id_carries_no_config_because_a_pass_spans_configs():
    """It is the grouping key for a CURVE, and a curve is exactly a set of different configs."""
    now = datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc)
    pid = mf.make_pass_id(hypothesis="H5: the exit_by horizon", now=now, clock="public")
    assert pid == "20260918T031500Z-public-h5-the-exit-by-horizon"
    assert mf.short_hash(mf.config_hash(DEFAULT_CONFIG)) not in pid


def test_the_run_id_parts_name_the_window_so_a_collision_can_say_so(tmp_path):
    assert "window start" in mf.RUN_ID_PARTS
    store.create_run_dir("dupe", tmp_path)
    with pytest.raises(store.RunDirExists) as exc:
        store.create_run_dir("dupe", tmp_path)
    assert "window start" in str(exc.value)


# --- the registry under concurrency ---------------------------------------------------------------------


def _summary(run_id: str, pass_id: str | None = None) -> store.RunSummary:
    return store.RunSummary(
        run_id=run_id,
        pass_id=pass_id,
        created_at=f"2026-09-18T03:15:{int(run_id[-2:]):02d}+00:00",
        config_short="abcd1234",
        config_hash="b" * 64,
        clock="public",
        known_at_mode="lockstep",
        window_start="2025-09-01",
        window_end="2025-10-12",
    )


def test_concurrent_registrations_do_not_drop_each_others_rows(tmp_path):
    """THE BUG THIS SLICE WOULD OTHERWISE INTRODUCE. `_write_index` is atomic, so no reader ever sees a
    truncated file -- but the read-modify-write around it is not, and once runs are launched concurrently
    two of them can each read the index, each append their own row, and each write, losing one. It would
    be invisible: every run directory and manifest is still on disk, only the count is short, and the
    registry's whole job is counting trials.

    Threads rather than processes on purpose -- the lock is an exclusive-create file lock, so it serializes
    threads and processes alike, and a thread test is fast enough to run on every commit."""
    n = 16
    ready = threading.Barrier(n)

    def register(i: int) -> None:
        ready.wait()  # maximize the overlap rather than hoping for it
        store.register_run(_summary(f"run-{i:02d}", pass_id="pass-1"), tmp_path)

    threads = [threading.Thread(target=register, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = store.list_runs(tmp_path)
    assert len(rows) == n, f"lost {n - len(rows)} row(s) to the race"
    assert {r.run_id for r in rows} == {f"run-{i:02d}" for i in range(n)}
    assert {r.pass_id for r in rows} == {"pass-1"}
    # ...and the lock leaves nothing behind
    assert not (tmp_path / store.INDEX_LOCK_NAME).exists()


def test_an_abandoned_lock_does_not_wedge_the_store(tmp_path):
    """A run that died holding the lock must not stop every future one. Breaking a stale lock is the right
    trade: the worst case is the lost-row race we already had with no lock at all."""
    lock = tmp_path / store.INDEX_LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    import os
    import time

    old = time.time() - 3600
    os.utime(lock, (old, old))
    store.register_run(_summary("run-01"), tmp_path)  # would hang forever without the break
    assert [r.run_id for r in store.list_runs(tmp_path)] == ["run-01"]


# --- the analysis, without running anything --------------------------------------------------------------


@pytest.fixture
def fake_runs(tmp_path, monkeypatch):
    """Hand-written run directories and a stubbed launcher, so the ANALYSIS can be tested without paying
    for a replay. What is under test here is pooling, the per-window deltas, sign agreement and the shared
    baseline — all of which read the artifacts and none of which needs the engine that wrote them.
    """
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    import backtest.sweep as sweep_mod

    written: dict[str, list[tuple[str, float]]] = {}

    def _write(run_id: str, rows: list[tuple[str, float]]) -> None:
        d = store.runs_root(tmp_path) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(
                [{"arm_date": a, "forward_return": r} for a, r in rows],
                schema=pa.schema([("arm_date", pa.string()), ("forward_return", pa.float64())]),
            ),
            d / "outcomes.parquet",
        )
        written[run_id] = rows

    yield _write, written, monkeypatch, sweep_mod, tmp_path


def test_a_point_pools_its_windows_and_splits_them_again_for_the_deltas(fake_runs, db):
    """The whole analysis change in one test: the metric is the POOLED median across a point's window
    runs, and each window's delta is recomputed from the same pooled rows by date — so the two can never
    disagree about which episodes they are describing."""
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    w2 = (date(2026, 2, 12), date(2026, 3, 25))

    # the baseline loses a little in both windows; the variant gains a little in both
    _write("base-w1", [("2026-01-10", 0.00), ("2026-01-20", 0.02)])
    _write("base-w2", [("2026-02-20", 0.00), ("2026-03-01", 0.02)])
    _write("var-w1", [("2026-01-10", 0.05), ("2026-01-20", 0.07)])
    _write("var-w2", [("2026-02-20", 0.05), ("2026-03-01", 0.07)])

    order = ["base-w1", "base-w2", "var-w1", "var-w2"]
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: order)
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)

    report = sweep_mod.run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [180, 90]},
        windows=[w1, w2],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    assert [list(w) for w in report.windows] == [list(w1), list(w2)]
    assert report.pass_id and report.concurrency == 1
    point = next(p for p in report.points if p.dials["insider_core_alpha_liveness_days"] == 90)
    assert point.run_ids == ["var-w1", "var-w2"]  # a point is N runs, all addressable
    assert point.n_scored == 4  # pooled across both windows
    assert point.metric == pytest.approx(0.06)  # median of 0.05,0.07,0.05,0.07
    assert point.delta_vs_baseline == pytest.approx(0.05)
    assert point.window_deltas == [pytest.approx(0.05), pytest.approx(0.05)]
    assert point.sign_agreement is True


def test_a_point_that_flips_between_windows_does_not_agree(fake_runs, db):
    """The question the split makes stronger: these are separate measurements, not halves of one run."""
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    w2 = (date(2026, 2, 12), date(2026, 3, 25))
    _write("base-w1", [("2026-01-10", 0.0)])
    _write("base-w2", [("2026-02-20", 0.0)])
    _write("var-w1", [("2026-01-10", 0.09)])  # helps here...
    _write("var-w2", [("2026-02-20", -0.09)])  # ...and hurts there

    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)

    report = sweep_mod.run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [180, 90]},
        windows=[w1, w2],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    point = next(p for p in report.points if p.dials["insider_core_alpha_liveness_days"] == 90)
    assert point.sign_agreement is False
    assert report.plateau == [] or 1 not in report.plateau


def test_a_single_window_pass_reports_NO_agreement_rather_than_a_vacuous_yes(fake_runs, db):
    """One window cannot agree with anything. Reporting True there would let a pass that measured one
    six-week stretch read as stable."""
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    _write("base-w1", [("2026-01-10", 0.0)])
    _write("var-w1", [("2026-01-10", 0.09)])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)
    report = sweep_mod.run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [180, 90]},
        windows=[w1],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    assert all(p.sign_agreement is False for p in report.points)


def test_the_baseline_runs_ONCE_PER_WINDOW_and_says_it_is_shared(fake_runs, db):
    """Every ladder contains the production default, so re-running it per dial would re-measure the same
    thing N times. It is deduplicated by config_hash before anything launches — and the points that share
    runs are MARKED, so a shared point is never read as an independent confirmation."""
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    launched: list = []

    def fake_launch(jobs, concurrency):
        launched.extend(jobs)
        return [f"run-{i}" for i in range(len(jobs))]

    for i in range(4):
        _write(f"run-{i}", [("2026-01-10", 0.01 * i)])
    monkeypatch.setattr(sweep_mod, "_launch", fake_launch)
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)

    default = DEFAULT_CONFIG.insider_core_alpha_liveness_days
    report = sweep_mod.run_sweep(
        db,
        # the default appears TWICE in the ladder: as itself and as the baseline every ladder contains
        grid={"insider_core_alpha_liveness_days": [default, 90, default]},
        windows=[w1],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    assert len(report.points) == 3
    # three points, but only TWO distinct configs -> two jobs, not three
    assert len(launched) == 2
    shared = [p for p in report.points if p.runs_shared]
    assert len(shared) == 2 and all(p.run_ids == shared[0].run_ids for p in shared)
    assert all(p.is_baseline for p in shared)


def test_a_missing_run_is_a_CORRUPTED_STORE_and_stops_the_pass(tmp_path):
    """Tolerating it would be the worse failure. Every launched run is registered under the index lock
    before the curve is assembled, so a run id with no directory behind it means the store lost an
    artifact -- and pooling the point over FEWER windows would make its metric, its episode count and its
    per-window deltas all quietly smaller with nothing on the curve to say so. A pass that stops is
    recoverable; a curve that under-reports by an unknown amount is not."""
    from backtest.sweep import MissingRunArtifacts, _pooled

    with pytest.raises(MissingRunArtifacts) as exc:
        _pooled(["ghost-run"], tmp_path)
    assert "ghost-run" in str(exc.value)  # WHICH run
    assert str(tmp_path) in str(exc.value)  # ...and where it should have been
    # a registered directory with no outcomes is the same failure, not a quieter one
    store.create_run_dir("half-written", tmp_path)
    with pytest.raises(MissingRunArtifacts):
        _pooled(["half-written"], tmp_path)


def test_the_baseline_reports_the_point_it_ACTUALLY_used(fake_runs, db):
    """When the grid brackets today's value without containing it, the first point stands in -- and the
    curve must say so. Every delta is then relative to a CHOSEN setting rather than to today's behavior,
    which is a different claim and must not be read as "better than production"."""
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    for i in range(2):
        _write(f"run-{i}", [("2026-01-10", 0.01 * i)])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["run-0", "run-1"])
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)

    default = DEFAULT_CONFIG.insider_core_alpha_liveness_days
    report = sweep_mod.run_sweep(
        db,
        # neither value is the production default: the ladder brackets it
        grid={"insider_core_alpha_liveness_days": [default - 30, default + 30]},
        windows=[w1],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    assert report.baseline_is_default is False
    assert not any(p.is_baseline for p in report.points)
    # ...and the reported hash is the STAND-IN's, never DEFAULT_CONFIG's
    assert report.baseline_config_short == report.points[0].config_short
    assert report.baseline_config_short != (mf.short_hash(mf.config_hash(DEFAULT_CONFIG)) or "")


def test_a_grid_containing_the_default_reports_a_default_baseline(fake_runs, db):
    _write, _, monkeypatch, sweep_mod, root = fake_runs
    w1 = (date(2026, 1, 1), date(2026, 2, 11))
    for i in range(2):
        _write(f"run-{i}", [("2026-01-10", 0.01 * i)])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["run-0", "run-1"])
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)
    default = DEFAULT_CONFIG.insider_core_alpha_liveness_days
    report = sweep_mod.run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [default, 90]},
        windows=[w1],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5",
        decision_rule="plateau, not argmax",
        root=root,
    )
    assert report.baseline_is_default is True
    assert report.baseline_config_short == (mf.short_hash(mf.config_hash(DEFAULT_CONFIG)) or "")
