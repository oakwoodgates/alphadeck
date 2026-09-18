from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import pytest

from backtest import store
from backtest.pair import Split, _split, main, pair_point

# C — SEPARATING TIMING FROM COMPOSITION.
#
# A curve point's delta answers two questions at once. Moving a liveness dial changes the `exit_by` of
# episodes the baseline also armed (TIMING) and changes WHICH episodes arm at all (COMPOSITION), and a
# pooled median cannot tell them apart. The first pre-registered pass ran straight into it: revenue_accel
# at 60 d was the largest delta in the pass and arrived with n 1968 against 2614.
#
# The geometries below are the two real ones from that pass, not invented shapes: a point that admits a
# strict SUBSET (shortening a horizon) and one that admits a strict SUPERSET (lengthening it). The third
# case — partial overlap in both directions — is what actually occurred, because a dial both drops and adds.
#
# The load-bearing subtlety, and the reason `n_changed` exists: a dial that moves a MINORITY of the shared
# episodes has a paired median of exactly 0.0, because the untouched majority decides it. Without the count
# beside it, that reads as "the dial did nothing" when the truth can be "it did a great deal to 29% of
# them". On the real pass the paired median is +0.00% while the median change AMONG THE MOVED is +8.35%.

_T = str(uuid.UUID(int=0xA1))


def _k(sid: int, day: int) -> tuple[str, str, str]:
    return (_T, str(uuid.UUID(int=0xB0 + sid)), f"2026-01-{day:02d}")


def _rows(pairs: dict[tuple[str, str, str], float | None]) -> list[dict]:
    return [
        {"thesis_id": k[0], "security_id": k[1], "arm_date": k[2], "forward_return": v}
        for k, v in pairs.items()
    ]


@pytest.fixture
def store_with(tmp_path):
    """Write outcomes.parquet for a run id, and a curve that cites it. Artifacts only — the tool reads
    nothing else, which is what lets a finished pass be re-interrogated without a re-run."""
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("thesis_id", pa.string()),
            ("security_id", pa.string()),
            ("arm_date", pa.string()),
            ("forward_return", pa.float64()),
        ]
    )

    def write(run_id: str, pairs: dict) -> None:
        d = store.runs_root(tmp_path) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(_rows(pairs), schema=schema), d / "outcomes.parquet")

    def curve(dial: str, points: list[tuple], windows: int = 1) -> dict:
        """points = [(value, is_baseline, [run_id per window]), ...]"""
        c = {
            "dial_names": [dial],
            "pass_id": "20260918T000000Z-public-test",
            "windows": [[f"2026-01-0{i + 1}", f"2026-01-0{i + 2}"] for i in range(windows)],
            "points": [
                {
                    "dials": {dial: v},
                    "is_baseline": b,
                    "run_ids": ids,
                    "config_short": f"cfg{v}",
                }
                for v, b, ids in points
            ],
        }
        p = tmp_path / "sweeps" / f"{c['pass_id']}-{dial}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(c), encoding="utf-8")
        return c

    return write, curve, tmp_path


# --- the arithmetic, on the three real geometries ---------------------------------------------------------


def test_a_strict_subset_is_all_composition_when_nothing_moved():
    """Shortening a horizon that drops episodes without re-timing the survivors. Every shared return is
    identical, so the dial improved no decision — it only stopped making some."""
    base = {_k(1, 5): -0.10, _k(2, 6): -0.20, _k(3, 7): 0.05, _k(4, 8): 0.15}
    point = {_k(3, 7): 0.05, _k(4, 8): 0.15}  # the two losers are simply not armed
    s = _split(point, base)
    assert (s.n_shared, s.n_only_baseline, s.n_only_point) == (2, 2, 0)
    assert s.n_changed == 0 and s.median_change_when_changed is None
    assert s.shared_delta == 0.0 and s.paired_delta == 0.0
    assert s.median_only_baseline == pytest.approx(-0.15)  # the dropped ones were the bad ones
    assert s.median_only_point is None


def test_a_strict_superset_shows_what_the_added_episodes_were_worth():
    """Lengthening a horizon that admits later arms — the activist shape. The addition is the whole
    effect, and its median is the number that says whether the new episodes were worth arming."""
    base = {_k(1, 5): 0.02, _k(2, 6): 0.04}
    point = {_k(1, 5): 0.02, _k(2, 6): 0.04, _k(3, 7): -0.30, _k(4, 8): -0.20}
    s = _split(point, base)
    assert (s.n_shared, s.n_only_baseline, s.n_only_point) == (2, 0, 2)
    assert s.median_only_point == pytest.approx(-0.25)
    assert s.n_changed == 0


def test_partial_overlap_reports_both_directions():
    """What a real dial does: drops some, adds others, and re-times what remains."""
    base = {_k(1, 5): -0.10, _k(2, 6): -0.20, _k(3, 7): 0.05}
    point = {_k(2, 6): -0.05, _k(3, 7): 0.05, _k(4, 8): 0.30}
    s = _split(point, base)
    assert (s.n_shared, s.n_only_baseline, s.n_only_point) == (2, 1, 1)
    assert s.n_changed == 1 and s.median_change_when_changed == pytest.approx(0.15)
    assert s.median_only_baseline == pytest.approx(-0.10)
    assert s.median_only_point == pytest.approx(0.30)


def test_the_counts_partition_both_populations():
    """The identity that makes the three components readable as one population each."""
    base = {_k(i, i): 0.01 * i for i in range(1, 8)}
    point = {_k(i, i): 0.02 * i for i in range(4, 11)}
    s = _split(point, base)
    assert s.n_shared + s.n_only_point == len(point)
    assert s.n_shared + s.n_only_baseline == len(base)


# --- the subtlety the real pass exposed -------------------------------------------------------------------


def test_a_MINORITY_move_leaves_the_paired_median_at_zero_and_says_how_many_moved():
    """THE READING TRAP, from the real pass. 561 of 1931 shared episodes moved and the paired median was
    exactly +0.00% — because the untouched 70% decides it — while the median change among the moved was
    +8.35%. Reporting the paired median alone would have said "the dial does nothing to shared episodes",
    which is the opposite of what happened."""
    base = {_k(i, 1 + i): 0.0 for i in range(10)}
    point = dict(base)
    for i in range(3):  # a minority, moved a long way
        point[_k(i, 1 + i)] = 0.50
    s = _split(point, base)
    assert s.paired_delta == 0.0, "the untouched majority decides the paired median"
    assert s.n_changed == 3
    assert s.median_change_when_changed == pytest.approx(0.50)
    assert s.n_shared == 10


def test_a_MAJORITY_move_does_reach_the_paired_median():
    """The mirror case, so the zero above is read as a property of the distribution and not of the code."""
    base = {_k(i, 1 + i): 0.0 for i in range(10)}
    point = {k: (0.50 if i < 6 else 0.0) for i, k in enumerate(base)}
    s = _split(point, base)
    assert s.paired_delta == pytest.approx(0.50)
    assert s.n_changed == 6


def test_an_episode_with_no_forward_return_is_omitted_not_zeroed():
    """An unpriced episode is not comparable on either side, and counting it as 0.0 would invent a
    measurement — the same rule the scorer holds."""
    base = {_k(1, 5): 0.10, _k(2, 6): None}
    point = {_k(1, 5): 0.10, _k(2, 6): 0.20}
    import pyarrow as pa  # noqa: F401  (the fixture's import guard already ran)

    s = _split(
        {k: v for k, v in point.items() if v is not None},
        {k: v for k, v in base.items() if v is not None},
    )
    assert s.n_shared == 1 and s.n_only_point == 1 and s.n_only_baseline == 0


# --- end to end, through the curve ------------------------------------------------------------------------


def test_pair_point_reads_a_curve_and_pools_across_windows(store_with):
    write, curve, root = store_with
    write("b-w1", {_k(1, 5): -0.10, _k(2, 6): -0.20})
    write("b-w2", {_k(3, 7): -0.30, _k(4, 8): 0.10})
    write("p-w1", {_k(1, 5): 0.00, _k(2, 6): -0.20})
    write("p-w2", {_k(3, 7): -0.30})
    c = curve("d", [(60, False, ["p-w1", "p-w2"]), (180, True, ["b-w1", "b-w2"])], windows=2)
    rep = pair_point(c, 60, root)
    assert len(rep.per_window) == 2
    assert (rep.pooled.n_shared, rep.pooled.n_only_baseline, rep.pooled.n_only_point) == (3, 1, 0)
    assert rep.pooled.n_changed == 1
    assert rep.pooled.median_only_baseline == pytest.approx(0.10)


def test_pairing_the_baseline_against_itself_is_refused(store_with):
    """It shares its runs with itself, so every number would be a tautology."""
    write, curve, root = store_with
    write("b-w1", {_k(1, 5): 0.10})
    c = curve("d", [(180, True, ["b-w1"])])
    with pytest.raises(SystemExit) as exc:
        pair_point(c, 180, root)
    assert "baseline" in str(exc.value)


def test_a_missing_run_directory_stops_rather_than_pairing_a_hole(store_with):
    write, curve, root = store_with
    write("b-w1", {_k(1, 5): 0.10})
    c = curve("d", [(60, False, ["gone"]), (180, True, ["b-w1"])])
    with pytest.raises(SystemExit) as exc:
        pair_point(c, 60, root)
    assert "gone" in str(exc.value)


def test_the_cli_refuses_a_pass_or_dial_with_no_curve(tmp_path, capsys):
    assert (
        main(["--pass-id", "nope", "--dial", "d", "--values", "1", "--out-root", str(tmp_path)])
        == 2
    )
    assert "no curve at" in capsys.readouterr().err


def test_the_cli_emits_json_on_demand(store_with, capsys):
    write, curve, root = store_with
    write("b-w1", {_k(1, 5): -0.10, _k(2, 6): 0.20})
    write("p-w1", {_k(1, 5): 0.05})
    curve("d", [(60, False, ["p-w1"]), (180, True, ["b-w1"])])
    code = main(
        [
            "--pass-id",
            "20260918T000000Z-public-test",
            "--dial",
            "d",
            "--values",
            "60",
            "--out-root",
            str(root),
            "--json",
        ]
    )
    assert code == 0
    blob = json.loads(capsys.readouterr().out)
    assert blob[0]["pooled"]["n_shared"] == 1 and blob[0]["pooled"]["n_only_baseline"] == 1


def test_the_table_is_ascii_only(store_with):
    """It prints to a Windows console whose default codec is cp1252. A report that crashes on its own em
    dash is a report nobody reads — this cost one run of the real tool before it was caught."""
    from backtest.pair import render

    write, curve, root = store_with
    write("b-w1", {_k(1, 5): -0.10})
    write("p-w1", {_k(1, 5): 0.05})
    c = curve("d", [(60, False, ["p-w1"]), (180, True, ["b-w1"])])
    text = render(pair_point(c, 60, root))
    text.encode("cp1252")  # raises if any character is outside it
    assert "shared dmed" in text and "d|moved" in text


def test_an_empty_split_reports_nothing_rather_than_zero():
    s = _split({}, {})
    assert s == Split()
    assert s.shared_delta is None and s.median_change_when_changed is None


# --- the same arithmetic, reached through run_pass ---------------------------------------------------------


def test_the_curve_carries_the_paired_fields_through_run_pass(tmp_path, monkeypatch, db):
    """The curve and the CLI must never drift, so both go through `backtest.pair._split`. This pins that
    the fields arrive on the point AND that the pooled/per-window split lines up with the windows.
    """
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    import backtest.sweep as sweep_mod
    from backtest.sweep import Ladder
    from domain.config import DEFAULT_CONFIG

    schema = pa.schema(
        [
            ("thesis_id", pa.string()),
            ("security_id", pa.string()),
            ("arm_date", pa.string()),
            ("forward_return", pa.float64()),
        ]
    )

    def write(run_id, pairs):
        d = store.runs_root(tmp_path) / run_id
        d.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(_rows(pairs), schema=schema), d / "outcomes.parquet")

    w1 = (date(2026, 1, 1), date(2026, 1, 15))
    w2 = (date(2026, 1, 16), date(2026, 1, 31))
    # the baseline arms four episodes; the point re-times one of the shared ones and drops another
    write("b-1", {_k(1, 5): -0.10, _k(2, 6): -0.20})
    write("b-2", {_k(3, 20): 0.05, _k(4, 21): 0.15})
    write("p-1", {_k(1, 5): 0.30})
    write("p-2", {_k(3, 20): 0.05, _k(4, 21): 0.15})

    order = ["b-1", "b-2", "p-1", "p-2"]
    monkeypatch.setattr(sweep_mod, "_launch", lambda jobs, concurrency: order)
    monkeypatch.setattr(sweep_mod, "export_snapshot", lambda *a, **k: {})
    monkeypatch.setattr(sweep_mod, "mirror_hash", lambda p: "c" * 64)

    default = DEFAULT_CONFIG.insider_core_alpha_liveness_days
    dial = "insider_core_alpha_liveness_days"
    report = sweep_mod.run_pass(
        db,
        ladders=[
            Ladder([dial], [{dial: default}, {dial: 90}], "H", "D"),
        ],
        windows=[w1, w2],
        pin=datetime(2027, 1, 1, tzinfo=timezone.utc),
        clock="public",
        hypothesis="H",
        decision_rule="D",
        root=tmp_path,
        now=datetime(2026, 9, 18, 3, 15, 0, tzinfo=timezone.utc),
    )[0]
    point = next(p for p in report.points if p.dials[dial] == 90)
    assert (point.n_shared, point.n_only_baseline, point.n_only_point) == (3, 1, 0)
    assert point.n_changed == 1
    assert point.median_change_when_changed == pytest.approx(0.40)
    assert point.median_only_baseline == pytest.approx(-0.20)
    # the untouched majority decides the paired median, here 2 of 3 unchanged
    assert point.paired_delta_vs_baseline == pytest.approx(0.0)
    assert len(point.paired_window_deltas) == len(report.windows)
    # window 1 holds the single re-timed episode; window 2 holds two unchanged ones
    assert point.paired_window_deltas[0] == pytest.approx(0.40)
    assert point.paired_window_deltas[1] == pytest.approx(0.0)
    # ...and the baseline point itself is not paired against itself
    base = next(p for p in report.points if p.is_baseline)
    assert base.paired_delta_vs_baseline is None and base.n_shared == 0
    # the banner says the paired view is a diagnostic, not the rule
    assert "diagnostic" in report.banner.lower() and "decision rule" in report.banner


def test_an_artifact_without_the_identity_columns_still_yields_a_curve(tmp_path):
    """A run written before the pairing existed, or any minimal artifact, must still produce the pooled
    metric — and must NOT be paired against, because an empty key set would read as "every episode is
    composition", which is worse than reporting no pairing at all."""
    pytest.importorskip("pyarrow")
    import pyarrow as pa
    import pyarrow.parquet as pq

    from backtest.sweep import _read_scored

    d = store.runs_root(tmp_path) / "old"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(
            [{"arm_date": "2026-01-05", "forward_return": 0.1}],
            schema=pa.schema([("arm_date", pa.string()), ("forward_return", pa.float64())]),
        ),
        d / "outcomes.parquet",
    )
    sc = _read_scored([d])
    assert sc.n_episodes == 1 and sc.values and sc.keyed == {}
