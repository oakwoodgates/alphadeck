"""A1 — READ A CURVE ON THE FAMILY ITS DIAL CAN TOUCH, and say what each window actually did.

Two findings from phase 1 are what this file exists for, and both are arithmetic rather than opinion:

1. **A dial that touches a small family cannot move a median over the whole pool.** MEASURED on the
   phase-1 artifacts (n=2614, pooled median -4.209%): moving all 46 `ratified_catalyst`-keyed episodes to
   +infinity shifts the pooled median by at most **0.995 pp**, and all 2 `theme`-keyed episodes by at most
   **0.073 pp** — the order statistic simply does not travel further than that. A flat catalyst curve over
   the pool is therefore not evidence about the catalyst dial; it is evidence about the pool. The slice
   asks the question the dial can answer.

2. **The pre-registered agreement rule cannot tell "did not move" from "could not have moved."** It is
   `all(d >= 0) or all(d <= 0)`, so a window whose delta is exactly 0.0 satisfies BOTH tests and counts as
   agreeing. On a catalyst slice of phase 1, two of the nine windows hold no catalyst-keyed episode at
   all — so a sweep could report agreement across nine windows, two of which were incapable of
   disagreeing.

The rule already run against is NOT changed here: `sign_agreement` keeps its semantics exactly, the
plateau still keys on it, and `strict_sign_agreement` + `window_status` ride beside it as the proposed
alternative and the evidence for it. A pass already read keeps reading the way it was read.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backtest import store
from backtest.sweep import STATUS_MARK, Ladder, MetricSliceError, WindowStatus, parse_slice

# Two windows, disjoint, in the shape `tile` produces.
_W1 = (date(2026, 1, 1), date(2026, 2, 11))
_W2 = (date(2026, 2, 12), date(2026, 3, 25))
_DIAL = "insider_core_alpha_liveness_days"
_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def sliceable_runs(tmp_path, monkeypatch):
    """Hand-written run directories carrying BOTH artifacts, and a stubbed launcher.

    Distinct from `test_windows.fake_runs`, which writes outcomes only: a slice is a join from
    `episodes.parquet` (which carries the algorithm attributes) onto `outcomes.parquet` (which carries the
    returns) on `(thesis_id, security_id, arm_date)`, so a fixture that omits identity cannot exercise it.
    """
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    import backtest.sweep as sweep_mod

    def _write(run_id: str, rows: list[tuple[str, str, float, str | None]]) -> None:
        """rows = (arm_date, security, forward_return, key1_source). `key1_source=None` writes an
        episode with no Key-1 at all, which is what a run written before the attribute existed looks
        like."""
        d = store.runs_root(tmp_path) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"thesis_id": "t1", "security_id": sec, "arm_date": a, "forward_return": r}
                    for a, sec, r, _k in rows
                ],
                schema=pa.schema(
                    [
                        ("thesis_id", pa.string()),
                        ("security_id", pa.string()),
                        ("arm_date", pa.string()),
                        ("forward_return", pa.float64()),
                    ]
                ),
            ),
            d / "outcomes.parquet",
        )
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {"thesis_id": "t1", "security_id": sec, "arm_date": a, "key1_source": k}
                    for a, sec, _r, k in rows
                ],
                schema=pa.schema(
                    [
                        ("thesis_id", pa.string()),
                        ("security_id", pa.string()),
                        ("arm_date", pa.string()),
                        ("key1_source", pa.string()),
                    ]
                ),
            ),
            d / "episodes.parquet",
        )

    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod.mf, "mirror_hash", lambda p: "c" * 64)
    yield _write, monkeypatch, sweep_mod, tmp_path


def _sweep(sweep_mod, db, root, *, windows, metric_slice=None, values=(180, 90)):
    return sweep_mod.run_sweep(
        db,
        grid={_DIAL: list(values)},
        windows=list(windows),
        pin=_PIN,
        hypothesis="A1: read the curve on the family the dial can touch",
        decision_rule="a plateau on the SLICE, and nothing read against the pool",
        root=root,
        metric_slice=metric_slice,
    )


# --- the slice reads the family, and its n is the family's ------------------------------------------------


def test_a_sliced_curve_reads_ONLY_its_family(sliceable_runs, db):
    """The finding in one test. The catalyst episodes move a lot and the rest do not move at all; over the
    pool the median barely stirs, and on the slice the dial's effect is the whole number."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    noise = [("2026-01-05", f"n{i}", 0.00, "insider") for i in range(9)]
    _write("base-w1", noise + [("2026-01-06", "c1", 0.00, "ratified_catalyst")])
    _write("var-w1", noise + [("2026-01-06", "c1", 0.80, "ratified_catalyst")])
    _write("base-w2", [("2026-02-20", "c2", 0.00, "ratified_catalyst")])
    _write("var-w2", [("2026-02-20", "c2", 0.80, "ratified_catalyst")])
    order = ["base-w1", "base-w2", "var-w1", "var-w2"]
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: order)

    pooled = _sweep(sweep_mod, db, root, windows=(_W1, _W2))
    sliced = _sweep(
        sweep_mod,
        db,
        root,
        windows=(_W1, _W2),
        metric_slice=("key1_source", "ratified_catalyst"),
    )

    pooled_point = next(p for p in pooled.points if p.dials[_DIAL] == 90)
    sliced_point = next(p for p in sliced.points if p.dials[_DIAL] == 90)

    # the pool: 11 episodes, 9 of them inert -- the median does not reach the movers
    assert pooled_point.n_scored == 11
    assert pooled_point.delta_vs_baseline == pytest.approx(0.0)
    # the slice: the two episodes the dial touches, and the whole effect
    assert sliced_point.n_scored == 2
    assert sliced_point.delta_vs_baseline == pytest.approx(0.80)
    assert sliced.metric_slice == "key1_source=ratified_catalyst"
    assert pooled.metric_slice == ""


def test_every_sliced_figure_carries_the_SLICES_n_not_the_pools(sliceable_runs, db):
    """`n_episodes` and `n_scored` are the slice's counts, so no figure on a sliced curve can be read
    against the pool by accident."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    rows = [("2026-01-05", "a", 0.01, "insider"), ("2026-01-06", "b", 0.02, "ratified_catalyst")]
    _write("base-w1", rows)
    _write("var-w1", rows)
    _write("base-w2", [("2026-02-20", "c", 0.03, "insider")])
    _write("var-w2", [("2026-02-20", "c", 0.03, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(sweep_mod, db, root, windows=(_W1, _W2), metric_slice=("key1_source", "insider"))
    for p in rep.points:
        assert p.n_episodes == 2 and p.n_scored == 2  # of 3 in the pool


def test_an_unsliced_curve_never_reads_the_episodes_file(sliceable_runs, db):
    """The extra read is paid only when a slice is asked for. Proved by making the read EXPLODE: an
    unsliced pass must not touch `_episode_slice` at all."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs

    def _boom(*a, **k):
        raise AssertionError("an unsliced curve read episodes.parquet")

    monkeypatch.setattr(sweep_mod, "_episode_slice", _boom)
    _write("base-w1", [("2026-01-05", "a", 0.01, "insider")])
    _write("var-w1", [("2026-01-05", "a", 0.05, "insider")])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    rep = _sweep(sweep_mod, db, root, windows=(_W1,))
    assert rep.points and rep.metric_slice == ""


# --- what a window DID, and what it could not have done ----------------------------------------------------


def test_a_window_with_no_episode_in_the_slice_is_UNMEASURABLE_not_zero(sliceable_runs, db):
    """The phase-1 gap, reproduced: window 2 holds no catalyst episode, so nothing about the dial could
    have shown there. The pre-registered rule counts it as agreement; the status says what it is."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "c1", 0.00, "ratified_catalyst")])
    _write("var-w1", [("2026-01-06", "c1", 0.20, "ratified_catalyst")])
    _write("base-w2", [("2026-02-20", "i1", 0.00, "insider")])  # no catalyst in this window
    _write("var-w2", [("2026-02-20", "i1", 0.90, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(
        sweep_mod,
        db,
        root,
        windows=(_W1, _W2),
        metric_slice=("key1_source", "ratified_catalyst"),
    )
    point = next(p for p in rep.points if p.dials[_DIAL] == 90)
    assert point.window_status == ["moved_up", "unmeasurable"]
    assert point.window_deltas[1] is None  # and it is NOT rendered as 0.0
    assert point.strict_sign_agreement is False


def test_an_unchanged_window_is_NOT_the_same_as_an_unmeasurable_one(sliceable_runs, db):
    """Both read as agreement under the pre-registered rule and they mean opposite things: one says the
    dial had its chance and declined it, the other says it never had one."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.10, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.10, "insider")])  # measurable, and identical
    _write("base-w2", [("2026-02-20", "b", 0.10, "insider")])
    _write("var-w2", [("2026-02-20", "b", 0.30, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(sweep_mod, db, root, windows=(_W1, _W2))
    point = next(p for p in rep.points if p.dials[_DIAL] == 90)
    assert point.window_status == ["unchanged", "moved_up"]
    assert point.window_deltas[0] == pytest.approx(0.0)  # measured, and really zero
    assert point.sign_agreement is True  # the pre-registered rule: 0.0 satisfies `all(d >= 0)`
    assert point.strict_sign_agreement is False  # the proposed one: unchanged is not movement


def test_strict_agreement_holds_when_every_window_moved_the_same_way(sliceable_runs, db):
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.05, "insider")])
    _write("base-w2", [("2026-02-20", "b", 0.00, "insider")])
    _write("var-w2", [("2026-02-20", "b", 0.07, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(sweep_mod, db, root, windows=(_W1, _W2))
    point = next(p for p in rep.points if p.dials[_DIAL] == 90)
    assert point.window_status == ["moved_up", "moved_up"]
    assert point.sign_agreement is True and point.strict_sign_agreement is True


def test_a_single_window_pass_is_not_strict_agreement_either(sliceable_runs, db):
    """One window cannot agree with anything — the same floor the pre-registered rule has."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.05, "insider")])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    rep = _sweep(sweep_mod, db, root, windows=(_W1,))
    assert all(p.strict_sign_agreement is False for p in rep.points)


def test_the_PRE_REGISTERED_field_survives_and_the_BAND_now_keys_on_strict(sliceable_runs, db):
    """Both halves of the operator's 2026-09-18 ruling in one test.

    The point below agrees under the rule the first pass was pre-registered on (one window unchanged, one
    up) and not under the strict one. The pre-registered FIELD is still on the artifact and still says
    True — nothing is rewritten — but the BAND is keyed on strict agreement from now on, so this point is
    outside it. A finished pass can still be re-read under its own rule by asking for it."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.10, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.10, "insider")])
    _write("base-w2", [("2026-02-20", "b", 0.10, "insider")])
    _write("var-w2", [("2026-02-20", "b", 0.30, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(sweep_mod, db, root, windows=(_W1, _W2))
    idx = next(i for i, p in enumerate(rep.points) if p.dials[_DIAL] == 90)
    assert rep.points[idx].sign_agreement is True  # preserved, and never rewritten
    assert rep.points[idx].strict_sign_agreement is False
    assert rep.plateau_rule == "strict_sign_agreement"
    assert idx not in rep.plateau  # the band no longer rests on a window that did not move
    # the same artifacts, re-read under the rule the first pass was registered on
    assert idx in sweep_mod._plateau(rep.points, rule="sign_agreement")


def test_the_status_lines_up_one_for_one_with_the_deltas(sliceable_runs, db):
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, "insider")])
    _write("var-w1", [("2026-01-06", "a", -0.05, "insider")])
    _write("base-w2", [("2026-02-20", "b", 0.00, "insider")])
    _write("var-w2", [("2026-02-20", "b", -0.07, "insider")])
    monkeypatch.setattr(
        sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "base-w2", "var-w1", "var-w2"]
    )
    rep = _sweep(sweep_mod, db, root, windows=(_W1, _W2))
    for p in rep.points:
        assert len(p.window_status) == len(p.window_deltas) == len(rep.windows)
    point = next(p for p in rep.points if p.dials[_DIAL] == 90)
    assert point.window_status == ["moved_down", "moved_down"]
    assert point.strict_sign_agreement is True


# --- the vocabulary is load-bearing -------------------------------------------------------------------------


def test_an_unknown_window_status_is_refused_by_the_model():
    """The four names are a validated Literal, not free text: a fifth would otherwise reach the curve
    file and the surface with nothing to render it."""
    from pydantic import ValidationError

    from backtest.sweep import SweepPoint

    SweepPoint(dials={}, config_short="abc12345", window_status=["moved_up"])
    with pytest.raises(ValidationError):
        SweepPoint(dials={}, config_short="abc12345", window_status=["went_sideways"])


def test_every_status_has_a_console_rendering():
    """A new status must not reach the CLI as a KeyError on a four-hour pass's last line."""
    import typing

    assert set(STATUS_MARK) == set(typing.get_args(WindowStatus))
    assert all(m.isascii() for m in STATUS_MARK.values())  # the console is cp1252


# --- what may be sliced, and what may not --------------------------------------------------------------------


def test_slicing_by_THESIS_is_refused_because_that_is_the_leaderboard():
    """Invariant #4, structurally. `episodes.parquet` carries `thesis_id`, so without this check a sweep
    could report a dial's effect on ONE thesis — a per-thesis ranking wearing a dial's clothes."""
    with pytest.raises(MetricSliceError, match="not an algorithm dimension"):
        Ladder([_DIAL], [{_DIAL: 90}], "h", "r", metric_slice=("thesis_id", "t1"))


def test_a_mistyped_slice_field_is_refused_rather_than_reading_as_a_null_result():
    """`key1_sources` (plural) is a REAL field and would match no episode, handing back a curve of
    unmeasurable windows that looks exactly like "the dial does nothing"."""
    with pytest.raises(MetricSliceError):
        Ladder([_DIAL], [{_DIAL: 90}], "h", "r", metric_slice=("key1_sources", "insider"))


def test_a_slice_over_a_run_with_no_episodes_file_RAISES(sliceable_runs, db):
    """The quiet version of this is a flat curve that reads as a finding."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.05, "insider")])
    (store.runs_root(root) / "var-w1" / "episodes.parquet").unlink()
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    with pytest.raises(MetricSliceError, match="no episodes.parquet"):
        _sweep(sweep_mod, db, root, windows=(_W1,), metric_slice=("key1_source", "insider"))


def test_a_slice_over_outcomes_with_no_identity_RAISES(sliceable_runs, db):
    """The join is on `(thesis_id, security_id, arm_date)`. An older artifact without identity can only
    match nothing, and "nothing" must not be served as "zero"."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, "insider")])
    _write("var-w1", [("2026-01-06", "a", 0.05, "insider")])
    pq.write_table(
        pa.Table.from_pylist(
            [{"arm_date": "2026-01-06", "forward_return": 0.05}],
            schema=pa.schema([("arm_date", pa.string()), ("forward_return", pa.float64())]),
        ),
        store.runs_root(root) / "var-w1" / "outcomes.parquet",
    )
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    with pytest.raises(MetricSliceError, match="cannot be joined"):
        _sweep(sweep_mod, db, root, windows=(_W1,), metric_slice=("key1_source", "insider"))


def test_an_episode_written_before_the_attribute_existed_matches_nothing(sliceable_runs, db):
    """A run whose episodes carry a NULL `key1_source` is not in any family. It reads as an unmeasurable
    window rather than as a silent whole-pool answer."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write("base-w1", [("2026-01-06", "a", 0.00, None)])
    _write("var-w1", [("2026-01-06", "a", 0.05, None)])
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    rep = _sweep(sweep_mod, db, root, windows=(_W1,), metric_slice=("key1_source", "insider"))
    point = next(p for p in rep.points if p.dials[_DIAL] == 90)
    assert point.n_scored == 0
    assert point.window_status == ["unmeasurable"]
    assert point.metric is None


# --- two readings of one dial are two measurements -------------------------------------------------------------


def test_the_same_config_read_on_two_slices_is_two_scored_sets(sliceable_runs, db):
    """A pass may carry one dial twice — once pooled, once sliced. They share RUNS (one config, one
    measurement of the world) but must not share the READ, or the second curve would silently serve the
    first one's numbers."""
    _write, monkeypatch, sweep_mod, root = sliceable_runs
    _write(
        "base-w1",
        [("2026-01-05", "i", 0.00, "insider"), ("2026-01-06", "c", 0.00, "ratified_catalyst")],
    )
    _write(
        "var-w1",
        [("2026-01-05", "i", 0.00, "insider"), ("2026-01-06", "c", 0.60, "ratified_catalyst")],
    )
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: ["base-w1", "var-w1"])
    pooled, sliced = sweep_mod.run_pass(
        db,
        ladders=[
            Ladder([_DIAL], [{_DIAL: 180}, {_DIAL: 90}], "pooled", "r"),
            Ladder(
                [_DIAL],
                [{_DIAL: 180}, {_DIAL: 90}],
                "sliced",
                "r",
                metric_slice=("key1_source", "ratified_catalyst"),
            ),
        ],
        windows=[_W1],
        pin=_PIN,
        hypothesis="h",
        decision_rule="r",
        root=root,
    )
    assert pooled.metric_slice == "" and sliced.metric_slice == "key1_source=ratified_catalyst"
    pooled_90 = next(p for p in pooled.points if p.dials[_DIAL] == 90)
    sliced_90 = next(p for p in sliced.points if p.dials[_DIAL] == 90)
    assert pooled_90.n_scored == 2 and sliced_90.n_scored == 1
    assert pooled_90.metric == pytest.approx(0.30)  # median of 0.00, 0.60
    assert sliced_90.metric == pytest.approx(0.60)  # the catalyst episode alone


def test_a_sliced_curve_is_kept_beside_its_pooled_twin_not_on_top_of_it(tmp_path):
    """`sweeps/<pass_id>-<dial>.json` is the only durable record of which runs formed which curve. Two
    readings of one dial in one pass would collide on that name."""
    from backtest.sweep import SweepReport, write_curve

    spine = dict(
        dial_names=[_DIAL],
        pass_id="p1",
        hypothesis="h",
        decision_rule="r",
        window_start=_W1[0],
        window_end=_W2[1],
    )
    a = SweepReport(**spine)
    b = SweepReport(**spine, metric_slice="key1_source=ratified_catalyst")
    kept_a, kept_b = write_curve(a, tmp_path), write_curve(b, tmp_path)
    assert kept_a != kept_b and kept_a.is_file() and kept_b.is_file()
    assert kept_b.name.endswith("-key1_source_ratified_catalyst.json")  # safe, and legible


# --- the CLI ---------------------------------------------------------------------------------------------------


def test_the_cli_refuses_a_PASS_WIDE_slice(capsys):
    """One slice over six curves would measure five dials on episodes they cannot reach and report the
    resulting flat lines as findings."""
    from backtest.sweep import main

    rc = main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-10",
            "--ladder",
            f"{_DIAL}=90",
            "--metric-slice",
            "key1_source=insider",
            "--hypothesis",
            "h",
            "--decision-rule",
            "r",
        ]
    )
    assert rc == 2
    assert "--ladder-metric-slice" in capsys.readouterr().err


def test_the_cli_refuses_a_non_algorithm_slice_BEFORE_it_opens_a_connection(capsys, monkeypatch):
    """A typo must not cost a mirror export, and a #4 violation must not cost anything at all."""
    import backtest.sweep as sweep_mod

    def _no(*a, **k):
        raise AssertionError("connected to the database on a refused sweep")

    monkeypatch.setattr(sweep_mod, "connect", _no)
    rc = sweep_mod.main(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-10",
            "--dial",
            _DIAL,
            "--values",
            "90",
            "--metric-slice",
            "thesis_id=t1",
            "--hypothesis",
            "h",
            "--decision-rule",
            "r",
        ]
    )
    assert rc == 2
    assert "algorithm dimension" in capsys.readouterr().err


def test_a_per_ladder_slice_is_parsed_dial_first():
    """`DIAL=ATTR=VALUE`, the same `split_spec` shape as `--ladder-hypothesis`, so the three per-ladder
    flags cannot drift on what a well-formed spec is."""
    import argparse

    import backtest.sweep as sweep_mod

    args = argparse.Namespace(
        ladder=[f"{_DIAL}=90,180"],
        ladder_hypothesis=[],
        ladder_decision_rule=[],
        ladder_metric_slice=[f"{_DIAL}=key1_source=insider"],
        hypothesis="h",
        decision_rule="r",
        dial=None,
        values=None,
        grid=[],
        metric_slice=None,
    )
    (lad,) = sweep_mod._ladders_from_args(args)
    assert lad.metric_slice == ("key1_source", "insider")
    assert lad.slice_label == "key1_source=insider"

    args.ladder_metric_slice = ["not_a_dial=key1_source=insider"]
    with pytest.raises(Exception, match="no --ladder"):
        sweep_mod._ladders_from_args(args)


def test_a_slice_spec_without_a_value_is_refused():
    assert parse_slice(None, "--metric-slice") is None
    with pytest.raises(MetricSliceError):
        parse_slice("key1_source=", "--metric-slice")
