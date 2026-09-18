"""B — THE BASKET'S MOVE, TWO WAYS, ON ONE PRICED POPULATION.

`excess` measures an armed window against the basket's EQUAL-WEIGHT MEAN move: what an equal-weight basket
position earned. That is a real portfolio answer and it is kept exactly as it was. It is also the wrong
denominator for the question this platform asks.

A thematic basket is right-skewed by construction — the operator assembles names around a narrative and a
minority carry the theme. One moonshot in a twenty-name basket lifts the MEAN by a twentieth of its own
move and leaves the TYPICAL member exactly where it was. So "the algorithm beat the basket" read off the
mean can mean "the algorithm missed the one name that carried it", and "the algorithm lost to the basket"
can mean "the algorithm beat every name except the one that ran". The MEDIAN asks what the typical member
did — the same question the name-selection null asks — and that is the figure a timing platform has to
answer to.

Both ride every draw, over the SAME priced members, and the headline is one constant
(`pooled.HEADLINE_EXCESS`). The two tests that carry the argument are the symmetric basket (where they are
identical, so a difference is always skew and never arithmetic) and the skewed one (where they disagree on
the SIGN).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtest import manifest as mf
from backtest.nulls import BasketMove, _BasketBenchmark
from backtest.pooled import HEADLINE_EXCESS, PooledReport

_T0 = date(2026, 6, 1)
_T1 = _T0 + timedelta(days=30)


@dataclass(frozen=True)
class _Score:
    """What `score_window` hands back — only the two fields the benchmark reads."""

    forward_return: float
    exit_date: date


def _bench_over(monkeypatch, returns: dict[str, float | None]) -> tuple[_BasketBenchmark, dict]:
    """A benchmark over a hand-specified basket: ``{label: forward_return}``, ``None`` = no tape.

    The contract is stated with DIRECT INPUTS rather than by staging a tape and hoping it produces the
    shape under test — the arithmetic is what is being pinned, so the arithmetic is what is fed in.
    """
    import backtest.nulls as nulls_mod

    ids = {label: uuid.uuid4() for label in returns}
    by_id = {ids[label]: r for label, r in returns.items()}
    calls: dict[str, int] = {"n": 0}

    def fake_score(_realized, sid, entry, exit_):
        calls["n"] += 1
        r = by_id.get(sid)
        return None if r is None else _Score(forward_return=r, exit_date=exit_)

    monkeypatch.setattr(nulls_mod, "score_window", fake_score)
    tid = uuid.uuid4()
    bench = _BasketBenchmark(realized=object(), roster_at=lambda _t, _d: list(by_id))
    return bench, {"tid": tid, "ids": ids, "calls": calls}


# --- the two baskets that carry the argument ----------------------------------------------------------------


def test_a_SYMMETRIC_basket_gives_both_benchmarks_the_same_number(monkeypatch):
    """When the basket is symmetric the mean IS the typical member, so the two excess figures agree to
    the last decimal. This is what makes a DIFFERENCE between them meaningful: it can only ever be skew,
    never a change of arithmetic."""
    bench, ctx = _bench_over(monkeypatch, {"a": -0.10, "b": 0.00, "c": 0.10})
    move = bench(ctx["tid"], _T0, _T1)
    assert move.mean == pytest.approx(0.0)
    assert move.median == pytest.approx(0.0)
    assert move.mean == pytest.approx(move.median)
    assert move.n == 3


def test_a_SKEWED_basket_parts_them_and_they_disagree_on_the_SIGN(monkeypatch):
    """The finding in one test, with numbers chosen so the reading FLIPS rather than merely shifting.

    Four names went nowhere and one ran +100%. An armed episode returning +5% beat every name in the
    basket except the one that ran — and against the equal-weight mean it reads as a 16-point LOSS.
    """
    bench, ctx = _bench_over(
        monkeypatch, {"a": 0.00, "b": 0.01, "c": 0.02, "d": 0.03, "moonshot": 1.00}
    )
    move = bench(ctx["tid"], _T0, _T1)
    assert move.mean == pytest.approx(0.212)  # dragged by one name
    assert move.median == pytest.approx(0.02)  # the typical member, untouched by it
    armed = 0.05
    assert armed - move.mean == pytest.approx(-0.162)  # "lost to the basket"
    assert armed - move.median == pytest.approx(0.03)  # "beat the typical name"


def test_the_MEAN_based_figure_is_exactly_what_it_was_before_B(monkeypatch):
    """Byte-for-byte the old arithmetic: the sum over the priced members, divided by how many priced.
    B adds a statistic; it must not move one."""
    rets = {"a": -0.04, "b": 0.11, "c": 0.02, "d": None}
    bench, ctx = _bench_over(monkeypatch, rets)
    priced = [r for r in rets.values() if r is not None]
    assert bench(ctx["tid"], _T0, _T1).mean == pytest.approx(sum(priced) / len(priced))


def test_both_statistics_are_taken_over_the_SAME_priced_population(monkeypatch):
    """A member with no tape is in NEITHER figure, and `n` is the denominator both were taken over —
    so the two can never describe different baskets."""
    bench, ctx = _bench_over(
        monkeypatch, {"a": 0.10, "b": 0.20, "no_tape": None, "also_none": None}
    )
    move = bench(ctx["tid"], _T0, _T1)
    assert move.n == 2
    assert move.mean == pytest.approx(0.15) and move.median == pytest.approx(0.15)


def test_a_basket_that_priced_NOTHING_benches_to_nothing_not_to_zero(monkeypatch):
    """Zero would be a claim — "the basket went nowhere". None is the measurement: there was no basket
    to measure against, and the excess is unknowable rather than flat."""
    bench, ctx = _bench_over(monkeypatch, {"a": None, "b": None})
    assert bench(ctx["tid"], _T0, _T1) == BasketMove(mean=None, median=None, n=0)


def test_the_median_costs_NO_extra_priced_window(monkeypatch):
    """Both statistics come off the one list of returns the benchmark already prices, and the memo still
    holds — so B adds nothing to the cost of a run, which is why it could be added at all."""
    bench, ctx = _bench_over(monkeypatch, {"a": 0.1, "b": 0.2, "c": 0.3})
    bench(ctx["tid"], _T0, _T1)
    assert ctx["calls"]["n"] == 3  # one priced window per member, and not one more for the median
    bench(ctx["tid"], _T0, _T1)
    assert ctx["calls"]["n"] == 3  # ...and the second ask is the memo


# --- what the report says -------------------------------------------------------------------------------------


def test_the_report_carries_both_figures_and_NAMES_the_headline():
    """A stored report has to say how it was meant to be read. The surface reads this field rather than
    deciding for itself, exactly as it reads the sweep's `plateau_rule`."""
    r = PooledReport()
    assert r.headline_excess == HEADLINE_EXCESS == "median"
    blob = json.loads(r.model_dump_json())
    assert blob["headline_excess"] == "median"


def test_a_report_written_before_B_has_no_median_and_that_is_visible():
    """Parsed back, a pre-B report's median Stat is EMPTY (n=0) rather than 0.0 — there was no median to
    quote, and an empty stat renders as "—" while a zero would read as "no excess"."""
    old = {
        "n_episodes": 3,
        "metrics": [
            {
                "name": "m",
                "claim": "c",
                "actual": {"n": 3, "median": 0.1},
                "excess": {"n": 3, "median": -0.02},
                "vs_timing": {"n": 6, "median": 0.0},
                "vs_name": {"n": 6, "median": 0.0},
            }
        ],
    }
    r = PooledReport.model_validate(old)
    assert r.metrics[0].excess.median == -0.02  # the old figure is read back untouched
    assert r.metrics[0].excess_vs_basket_median.n == 0
    assert r.metrics[0].excess_vs_basket_median.median is None


# --- the roster the benchmark is taken over, recorded rather than remembered ---------------------------------


def _entry(name: str, ids: list[uuid.UUID | None], **over) -> mf.ThesisEntry:
    base = dict(
        thesis_id=uuid.uuid4(),
        name=name,
        basket_size=len(ids),
        roster_hash=mf.roster_hash(ids),
        member_ids=[str(i) if i is not None else None for i in ids],
        roster_source="live_fallback",
        fallback_days=10,
        total_days=10,
    )
    base.update(over)
    return mf.ThesisEntry(**base)


def test_the_manifest_records_the_ROSTER_not_only_its_fingerprint():
    """The hash says WHETHER a roster changed; the ids say WHAT it was. Without them a benchmark can only
    be rebuilt by asking a live database what the basket is TODAY — which is the question a recompute
    must not have to ask."""
    ids = [uuid.uuid4(), None, uuid.uuid4()]
    e = _entry("T", ids)
    assert e.member_ids == [str(ids[0]), None, str(ids[2])]
    # and the two cannot describe different baskets: the recorded ids re-hash to the recorded hash
    assert mf.roster_hash([uuid.UUID(m) if m else None for m in e.member_ids]) == e.roster_hash


def test_an_UNRESOLVED_member_survives_in_the_recorded_roster():
    """A basket row with no security is a real row. Dropping it would make a basket that LOST a
    resolution hash as if it never had one."""
    e = _entry("T", [None, None])
    assert e.member_ids == [None, None] and e.basket_size == 2


def test_a_manifest_written_before_B_reads_as_NOT_RECORDED_not_as_an_empty_basket():
    e = mf.ThesisEntry(
        thesis_id=uuid.uuid4(),
        name="old",
        basket_size=90,
        roster_hash="f" * 64,
        roster_source="live_fallback",
        fallback_days=3,
        total_days=3,
    )
    assert e.member_ids == [] and e.basket_size == 90  # the pair says which it is


# --- the retroactive recompute's refusals ----------------------------------------------------------------------


def test_a_roster_that_MOVED_is_refused_rather_than_benched_against_todays_basket():
    """The recompute's whole licence is that the basket is still the one the run replayed on. If it is
    not, the benchmark cannot be rebuilt and a number produced anyway would be a different experiment
    wearing the run's id."""
    from backtest.rebench import check_rosters

    ids = [uuid.uuid4(), uuid.uuid4()]
    e = mf.ThesisEntry(
        thesis_id=uuid.uuid4(),
        name="T",
        basket_size=2,
        roster_hash=mf.roster_hash(ids),
        roster_source="live_fallback",
        fallback_days=3,
        total_days=3,
    )
    same = check_rosters([e], {e.thesis_id: ids})
    assert [c.status for c in same] == ["unchanged"] and all(c.ok for c in same)

    moved = check_rosters([e], {e.thesis_id: [ids[1], ids[0]]})  # REORDERED is a different roster
    assert [c.status for c in moved] == ["changed"] and not moved[0].ok

    gone = check_rosters([e], {})
    assert [c.status for c in gone] == ["gone"] and not gone[0].ok


def test_a_manifest_that_carries_its_roster_needs_NO_database():
    """The durable path: after B the roster is on the artifact, so a run can be rebenched months later
    with nothing but its own bytes."""
    from backtest.rebench import check_rosters

    e = _entry("T", [uuid.uuid4()])
    checks = check_rosters([e], None)
    assert [c.status for c in checks] == ["recorded"] and checks[0].ok


def test_a_POINT_IN_TIME_roster_is_refused_because_rebuilding_it_would_be_a_re_run(tmp_path):
    """A run that read real snapshots had a roster that MOVED during its window. Reconstructing that is
    the harness's job, not a recompute's, and pretending otherwise would bench every episode against
    whatever the basket happens to be now."""
    from backtest.rebench import rebench_run

    e = _entry("T", [uuid.uuid4()], fallback_days=2, total_days=10)  # 8 snapshot days
    _write_manifest(tmp_path, [e])
    out = rebench_run(tmp_path, tmp_path, None)
    assert "point-in-time" in out.refused and out.n_scored == 0


def test_a_mirror_that_is_not_the_runs_own_mirror_is_refused(tmp_path):
    """Content-addressed, so this is checked rather than trusted: benching against a different tape is a
    different experiment, and the path a mirror sits at says nothing about what is in it."""
    from backtest.rebench import rebench_run

    _write_manifest(tmp_path, [_entry("T", [uuid.uuid4()])], mirror_hash="a" * 64)
    out = rebench_run(tmp_path, tmp_path, None)
    assert "not this run's mirror" in out.refused


def _write_manifest(
    d: Path, theses: list[mf.ThesisEntry], *, mirror_hash: str | None = None
) -> None:
    man = mf.BacktestManifest(
        run_id="r1",
        created_at="2026-09-18T00:00:00+00:00",
        window_start=_T0,
        window_end=_T1,
        pin="2027-01-01T00:00:00+00:00",
        config_hash="c" * 64,
        config_short="cccccccc",
        config_canonical_json="{}",
        theses=theses,
        mirror=mf.MirrorInfo(hash=mirror_hash or mf.mirror_hash(d)),
    )
    (d / mf.MANIFEST_NAME).write_text(man.model_dump_json(indent=2), encoding="utf-8")
