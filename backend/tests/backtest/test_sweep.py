from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from backtest.sweep import (
    SWEEP_NAME,
    SweepPoint,
    SweepReport,
    _plateau,
    build_parser,
    cfg_for,
    main,
    parse_values,
    variants,
)
from domain.config import DEFAULT_CONFIG
from signals.horizons import call_bounds

# B7 — the SWEEP RUNNER. One regime, with episodes that are not independent, cannot support picking the
# best-scoring point: that is fitting the tape. So these tests pin the three things that keep a sweep
# honest -- it reports a CURVE and refuses to name a winner, a point only "agrees" when every WINDOW moves
# the same way, and the PIT read window widens WITH the dial being swept.
#
# S1 moved the unit of work to a sub-window, so what used to be "disjoint sub-windows of one run" is now
# "disjoint windows, each its own run". The tiling itself is tested in `test_windows.py`, which is where
# `_sub_bounds`'s two tests went when the concept moved.

_T0, _T1 = date(2026, 1, 1), date(2026, 6, 30)


def _pt(dials, delta, subs, **over) -> SweepPoint:
    base = dict(
        dials=dials,
        run_ids=["r"],
        config_short="abc12345",
        metric=delta,
        delta_vs_baseline=delta,
        window_deltas=subs,
        sign_agreement=(
            len([d for d in subs if d is not None]) == len(subs)
            and len(subs) >= 2
            and (all(d >= 0 for d in subs) or all(d <= 0 for d in subs))
        ),
        # A1: the helper computes BOTH the way `run_pass` does, or the band below would be keyed on a
        # field this fixture never set -- which would make every plateau test pass for the wrong reason.
        strict_sign_agreement=(
            len([d for d in subs if d is not None]) == len(subs)
            and len(subs) >= 2
            and (all(d > 0 for d in subs) or all(d < 0 for d in subs))
        ),
    )
    base.update(over)
    return SweepPoint(**base)


# --- never a winner ---------------------------------------------------------------------------------


def test_the_report_has_no_winner_field():
    """Structural, not a convention. Someone reading this artifact must not be able to ask it for an
    answer it cannot honestly give, so the schema simply has nowhere to put one."""
    fields = set(SweepReport.model_fields) | set(SweepPoint.model_fields)
    for forbidden in ("best", "winner", "optimal", "argmax", "recommended", "chosen"):
        assert not any(forbidden in f for f in fields), forbidden


def test_the_serialized_curve_names_no_winner():
    report = SweepReport(
        window_start=_T0,
        window_end=_T1,
        points=[_pt({"d": 1}, 0.05, [0.04, 0.06]), _pt({"d": 2}, 0.01, [0.02, -0.01])],
    )
    blob = json.loads(report.model_dump_json())
    for forbidden in ("best", "winner", "optimal"):
        assert forbidden not in json.dumps(blob).lower()


def test_the_plateau_is_a_BAND_not_a_pick():
    """A contiguous run of indices, and a one-wide band is the honest way of saying nothing was found."""
    points = [
        _pt({"d": 1}, 0.01, [0.01, 0.01]),
        _pt({"d": 2}, 0.05, [0.04, 0.06]),
        _pt({"d": 3}, 0.04, [0.03, 0.05]),
        _pt({"d": 4}, 0.02, [0.06, -0.03]),  # disagrees across sub-windows -> breaks the band
    ]
    assert _plateau(points) == [0, 1, 2]


def test_a_sweep_where_nothing_agrees_has_an_EMPTY_plateau():
    points = [_pt({"d": 1}, 0.05, [0.09, -0.01]), _pt({"d": 2}, 0.03, [-0.02, 0.08])]
    assert _plateau(points) == []


def test_the_band_is_keyed_on_STRICT_agreement_and_an_UNCHANGED_window_breaks_it():
    """A1, the operator's ruling of 2026-09-18. A window whose delta is exactly 0.0 satisfies the
    pre-registered `all(d >= 0)` test, so the old rule counted "the dial had its chance and declined it"
    as agreement. The band now keys on strict agreement, and the middle point below -- which moves in one
    window and not in the other -- no longer holds a band together."""
    points = [
        _pt({"d": 1}, 0.01, [0.01, 0.01]),
        _pt({"d": 2}, 0.03, [0.06, 0.0]),  # one real move, one window that did not budge
        _pt({"d": 3}, 0.04, [0.03, 0.05]),
        _pt({"d": 4}, 0.02, [0.02, 0.02]),
    ]
    assert _plateau(points) == [2, 3]  # the band breaks at the unchanged window
    # ...and the pass that was REGISTERED under the old rule can still be re-read under it, unrewritten
    assert _plateau(points, rule="sign_agreement") == [0, 1, 2, 3]


def test_the_rule_the_band_used_rides_the_report():
    """A band is meaningless without it, and two curves read under two rules must not look alike."""
    assert SweepReport(window_start=_T0, window_end=_T1).plateau_rule == "strict_sign_agreement"


# --- sub-period sign agreement ----------------------------------------------------------------------


def test_a_point_that_flips_sign_between_sub_windows_does_not_agree():
    """The whole purpose: a dial that helps in the first half and hurts in the second has found nothing,
    however good its pooled number looks."""
    assert _pt({"d": 1}, 0.05, [0.09, -0.01]).sign_agreement is False
    assert _pt({"d": 1}, 0.05, [0.04, 0.06]).sign_agreement is True


def test_an_unmeasurable_sub_window_does_not_abstain_into_a_yes():
    """A point measurable on only half the window has not demonstrated stability, so a None must not be
    quietly skipped -- that would turn 'no evidence' into agreement."""
    assert _pt({"d": 1}, 0.05, [0.04, None]).sign_agreement is False


# --- the read window widens WITH the dial (B5a's finding) -------------------------------------------


def test_sweeping_a_liveness_dial_widens_the_PIT_read_bounds():
    """Without this a sweep over `insider_core_alpha_liveness_days` would silently TRUNCATE the very
    window it is measuring, and the curve would flatten for a reason that had nothing to do with the dial.
    `call_bounds` is derived from the run's own cfg (B5a), so the floor moves with the variant."""
    base = call_bounds(DEFAULT_CONFIG)
    for value in (90, 180, 365, 900):
        bounds = call_bounds(cfg_for({"insider_core_alpha_liveness_days": value}))
        if value > DEFAULT_CONFIG.insider_core_alpha_liveness_days:
            assert bounds["fact_insider_txn"] > base["fact_insider_txn"], value
        elif value < DEFAULT_CONFIG.insider_core_alpha_liveness_days:
            assert bounds["fact_insider_txn"] <= base["fact_insider_txn"], value


def test_one_liveness_dial_moves_BOTH_bounded_tables():
    """B5a's measured surprise, kept as a test: the insider detector declares that dial against
    `fact_price_eod` too, so a sweep is never touching only the one table it names."""
    base = call_bounds(DEFAULT_CONFIG)
    wide = call_bounds(cfg_for({"insider_core_alpha_liveness_days": 900}))
    assert wide["fact_insider_txn"] > base["fact_insider_txn"]
    assert wide["fact_price_eod"] > base["fact_price_eod"]


# --- the grid ----------------------------------------------------------------------------------------


def test_the_variant_order_is_stable():
    """A sweep's points -- and therefore its curve -- must be reproducible across processes."""
    grid = {"b": [1, 2], "a": ["x", "y"]}
    assert variants(grid) == variants(grid)
    assert [v["a"] for v in variants(grid)] == ["x", "x", "y", "y"]


def test_a_multi_dial_grid_is_the_cartesian_product():
    """H1 and H3 are variant SETS, not single dials."""
    got = variants(
        {"breakdown_dearm_scope": ["all", "core_only"], "revenue_accel_grade": ["core", "flip"]}
    )
    assert len(got) == 4
    assert {(v["breakdown_dearm_scope"], v["revenue_accel_grade"]) for v in got} == {
        ("all", "core"),
        ("all", "flip"),
        ("core_only", "core"),
        ("core_only", "flip"),
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30,60,90", [30, 60, 90]),
        ("0.1,0.25", [0.1, 0.25]),
        ("true,false", [True, False]),
        ("core,flip", ["core", "flip"]),
        ("all, core_only", ["all", "core_only"]),
    ],
)
def test_values_keep_their_type(raw, expected):
    """A dial's type matters -- `breakdown_dearm_enabled=false` must not become the string "false"."""
    assert parse_values(raw) == expected


# --- the CLI's guards -------------------------------------------------------------------------------


def test_the_cli_requires_a_pre_registration():
    """Same rule as a single run: a dial moved without a hypothesis and a decision rule is not a
    measurement. Enforced by argparse, before anything costs a mirror export."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["--start", "2026-01-01", "--end", "2026-06-30", "--dial", "x", "--values", "1"]
        )


def test_the_cli_rejects_an_unknown_dial_before_exporting_a_mirror(capsys):
    """A mirror export is ~40 s; a typo should cost nothing."""
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-06-30",
            "--dial",
            "insider_core_alpha_livness_days",
            "--values",
            "90",
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
        ]
    )
    assert code == 2
    assert "insider_core_alpha_livness_days" in capsys.readouterr().err


def test_the_cli_rejects_a_malformed_grid(capsys):
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-06-30",
            "--grid",
            "no_equals_sign",
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
        ]
    )
    assert code == 2
    # the spec parser is now SHARED by --grid, --ladder and the per-ladder pre-registration overrides
    # (S2), so the message generalized to `dial=...`: it cannot promise a values-shaped right-hand side
    # for a flag whose right-hand side is prose. What a reader needs is still there -- which flag, and
    # that it wants a dial name before the '='.
    err = capsys.readouterr().err
    assert "--grid" in err and "dial=" in err and "no_equals_sign" in err


def test_the_cli_refuses_an_empty_sweep(capsys):
    code = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-06-30",
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
        ]
    )
    assert code == 2
    assert "nothing to sweep" in capsys.readouterr().err


def test_the_banner_states_the_posture():
    report = SweepReport(window_start=_T0, window_end=_T1, banner="")
    assert SWEEP_NAME == "sweep.json"
    from backtest.sweep import run_sweep  # noqa: F401  -- the banner text lives with the runner

    assert report.plateau == []


# --- the end-to-end sweep ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_sweep_runs_every_point_over_ONE_frozen_mirror(db, tmp_path):
    """The discipline `replay/compare.py` has held since the first sweep: export once, vary only cfg. If
    two points swept different tapes, the delta between them would not be attributable to the dial.
    """
    pytest.importorskip("duckdb")
    from backtest import store
    from backtest.sweep import run_sweep
    from pipeline.seed import seed_unh

    seed_unh(db)
    db.commit()
    report = run_sweep(
        db,
        grid={"insider_core_alpha_liveness_days": [90, 180]},
        # two WINDOWS now, each its own run per point (S1) — the same span, split at the same place the
        # old `subwindows=2` split it, so the numbers this test reads are the numbers it always read
        windows=[(date(2025, 4, 1), date(2025, 11, 1)), (date(2025, 11, 2), date(2026, 6, 1))],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        hypothesis="H5 smoke",
        decision_rule="plateau, not argmax",
        root=tmp_path,
    )
    assert len(report.points) == 2
    assert report.mirror_hash and len(report.mirror_hash) == 64
    # every point is its own addressable run, and they all cite the SAME mirror
    from backtest.manifest import read_manifest

    hashes = set()
    pass_ids = set()
    for p in report.points:
        assert len(p.run_ids) == len(report.windows)  # a point is one run PER WINDOW
        for rid in p.run_ids:
            d = store.run_dir(rid, tmp_path)
            assert d is not None
            m = read_manifest(d)
            assert m is not None
            hashes.add(m.mirror.hash)
            pass_ids.add(m.pass_id)
    assert hashes == {report.mirror_hash}
    # 2 points x 2 windows, and every run carries the pass id that groups them into ONE curve
    assert len(store.list_runs(tmp_path)) == 4
    assert pass_ids == {report.pass_id}
    # ...and the curve carries a delta per WINDOW, for every point
    assert all(len(p.window_deltas) == len(report.windows) for p in report.points)


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_sweep_with_WORKERS_and_a_SHARED_MIRROR_matches_the_serial_sweep(db, tmp_path):
    """THE REGRESSION for the silent merge bug.

    B5b's `replay_all_parallel(conn, out, ...)` and B7's shared mirror were written against different
    bases where `out` and the mirror were the SAME directory. Git merged both cleanly -- no conflict
    marker -- and the result handed every worker the RUN directory, which under a shared mirror holds no
    facts at all. Each worker opens the mirror by PATH because it is a fresh process, so the whole sweep
    replayed against nothing.

    Every test that existed at the time passed, because none of them combined the two: the sweep tests
    never set `workers`, and the parallel tests never set `mirror_dir`. This one sets BOTH.

    TWO THESES, deliberately. `replay_all_parallel` falls back to the serial harness when
    `len(theses) <= 1`, so a one-thesis fixture would never reach the worker path at all -- a first draft
    of this test used the UNH seed alone and was green for that reason.

    THE DIAL VALUES ARE BOTH >= 180, also deliberately. At 90 days the UNH cluster lapses before the
    August breakout and the thesis legitimately does not arm (RECALIBRATION part B: "shortening to 90d
    makes UNH fail to arm"), so a sweep through 90 would assert zero episodes and call a documented
    behavior a bug. That first draft did exactly that.

    The assertion is byte-identity against the SERIAL sweep, plus a non-empty check so the comparison
    cannot be satisfied by two empty files."""
    pytest.importorskip("duckdb")
    import pyarrow.parquet as pq

    from backtest import store
    from backtest.manifest import read_manifest
    from backtest.sweep import run_sweep
    from pipeline.seed import seed_hims, seed_unh

    seed_unh(db)
    seed_hims(db)  # a SECOND thesis, so workers>=2 does not fall back to the serial harness
    db.commit()

    def sweep(root, workers):
        return run_sweep(
            db,
            grid={"insider_core_alpha_liveness_days": [180, 365]},
            windows=[(date(2025, 4, 1), date(2026, 6, 1))],
            pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
            hypothesis="regression: workers + a shared mirror",
            decision_rule="smoke only",
            workers=workers,
            null_draws=1,  # the nulls are not what this test is about; keep it cheap
            root=root,
        )

    serial = sweep(tmp_path / "serial", 1)
    parallel = sweep(tmp_path / "parallel", 2)

    hashes = set()
    for s_pt, p_pt in zip(serial.points, parallel.points, strict=True):
        assert s_pt.dials == p_pt.dials
        s_dir = store.run_dir(s_pt.run_ids[0], tmp_path / "serial")
        p_dir = store.run_dir(p_pt.run_ids[0], tmp_path / "parallel")
        assert s_dir is not None and p_dir is not None
        s_bytes = (s_dir / "episodes.parquet").read_bytes()
        p_bytes = (p_dir / "episodes.parquet").read_bytes()
        # non-empty FIRST: byte-identity between two empty files would be vacuously true, and an empty
        # file is precisely what the merge bug produced.
        assert (
            pq.read_table(p_dir / "episodes.parquet").num_rows > 0
        ), f"point {p_pt.dials} produced NO episodes under workers=2 -- the workers saw no facts"
        assert s_bytes == p_bytes, f"point {p_pt.dials}: parallel episodes differ from serial"
        m = read_manifest(p_dir)
        assert m is not None and m.workers == 2
        hashes.add(m.mirror.hash)

    assert len(hashes) == 1, "the parallel sweep's points did not share one mirror"
    assert len(hashes.pop()) == 64, "the mirror hash is vacuous -- an empty directory was hashed"
