"""MARGINAL PER-FAMILY NULLS — does a pocket beat ITS OWN nulls, re-drawn faithfully off the frozen mirror?

The tests split cleanly by what they prove:

- The ORACLE (slow, DB-backed, a REAL run) is the proof that the re-draw REPRODUCES a pass's own nulls --
  both of them. It seeds a MULTI-NAME thesis so the NAME null is genuinely non-empty, then asserts the
  whole-pool re-draw reproduces the stored `pooled.json` `vs_timing` AND `vs_name` count-for-count. If the
  sessions or the roster reconstruction were wrong, those figures would diverge and this fails.
- The fast tests pin the SLICING and the AGGREGATION with known answers, the leaderboard guard (#4), the
  loud empty-slice, the early refusals, and the faithful-subset property at the tool boundary -- none of
  which needs a database.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from backtest import manifest as mf
from backtest import store
from backtest.nulls import draw_nulls
from backtest.pooled import Stat
from backtest.slice_nulls import (
    SliceKeyError,
    SliceSpec,
    WindowNulls,
    _pool_windows,
    _slice_window,
    analyze_pass,
    analyze_run,
)
from domain.enums import Grade, Verdict
from replay.schema import Episode

# --- a deterministic tape, copied from tests/backtest/test_nulls.py so the arithmetic is assertable ------

_T0 = date(2026, 6, 1)
_SESSIONS = [_T0 + timedelta(days=i) for i in range(60)]


class _Realized:
    """Every name rises by its own fixed daily step, so a window's return is exactly computable. Duck-types
    the two methods `score_window` uses -- the nulls never touch a point-in-time view."""

    def __init__(self, steps: dict[uuid.UUID, float], start: float = 100.0) -> None:
        self.steps = steps
        self.start = start

    def _close(self, sid, d):
        return self.start + self.steps.get(sid, 0.0) * (d - _T0).days

    def first_close_on_or_after(self, security_id, d):
        return (d, self._close(security_id, d)) if d in _SESSIONS else None

    def bars_between(self, security_id, start, end):
        return [
            {"d": d, "close": self._close(security_id, d), "open": None, "high": None, "low": None}
            for d in _SESSIONS
            if start <= d <= end
        ]


def _ep(tid, sid, *, key1="insider", grade=Grade.CORE, arm=_T0, horizon=20) -> Episode:
    return Episode(
        thesis_id=tid,
        security_id=sid,
        is_headline=True,
        arm_date=arm,
        last_armed_date=arm,
        close_reason="window_end",
        verdict=Verdict.CORE_ENTRY,
        entry_grade=Grade.CORE,
        conviction_grade=Grade.CORE,
        confirmation_grade=grade,
        exit_by=arm + timedelta(days=horizon),
        key1_source=key1,
        co_arm_bucket="alone",
    )


def _roster_at(mapping):
    return lambda tid, _d, _m=mapping: _m.get(tid, [])


# --- the slice reads only its family, with a known answer --------------------------------------------------


def test_a_marginal_slice_reads_only_its_family_with_a_known_answer():
    """`insider` picks the insider-keyed names; the two-key slice narrows to insider AND core; and the
    arithmetic is exact on the deterministic tape: two names rising 1.0 and 2.0 a day over 20 days return
    0.20 and 0.40, median 0.30."""
    tid = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = _Realized({a: 1.0, b: 2.0, c: 3.0})
    eps = [
        _ep(tid, a, key1="insider", grade=Grade.CORE),
        _ep(tid, b, key1="insider", grade=Grade.FLIP),
        _ep(tid, c, key1="activist", grade=Grade.CORE),
    ]
    nulls = draw_nulls(
        eps, r, roster_at=_roster_at({tid: [a, b, c]}), sessions=_SESSIONS, seed="s", draws=2
    )

    sn, actual, vs_t, vs_n = _slice_window(eps, nulls, SliceSpec.parse("key1_source=insider"))
    assert {n.security_id for n in sn} == {a, b}
    assert actual.n == 2
    assert actual.median == pytest.approx(0.30)  # median of 0.20 (a) and 0.40 (b)
    assert vs_t.n > 0 and vs_n.n > 0  # both nulls are drawn on the slice

    sn2, *_ = _slice_window(
        eps, nulls, SliceSpec.parse("key1_source=insider,confirmation_grade=core")
    )
    assert {n.security_id for n in sn2} == {a}  # insider AND core -> a alone

    sn_all, *_ = _slice_window(eps, nulls, None)
    assert {n.security_id for n in sn_all} == {a, b, c}  # None is the whole pool


def test_the_slice_is_a_faithful_subset_of_the_full_draw():
    """The load-bearing property (`test_nulls.test_adding_an_episode_does_not_reshuffle_the_others`) at THIS
    tool's boundary: slicing the full draw picks the SAME draws that drawing over only the slice's episodes
    would produce -- a filter, not a fresh random draw."""
    tid = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    steps = {a: 1.0, b: 2.0, c: 3.0}
    roster = {tid: [a, b, c]}
    eps_all = [
        _ep(tid, a, key1="insider"),
        _ep(tid, b, key1="insider"),
        _ep(tid, c, key1="activist"),
    ]
    all_nulls = draw_nulls(
        eps_all,
        _Realized(steps),
        roster_at=_roster_at(roster),
        sessions=_SESSIONS,
        seed="s",
        draws=3,
    )
    sliced, *_ = _slice_window(eps_all, all_nulls, SliceSpec.parse("key1_source=insider"))

    eps_insider = [e for e in eps_all if e.key1_source == "insider"]
    subset = draw_nulls(
        eps_insider,
        _Realized(steps),
        roster_at=_roster_at(roster),
        sessions=_SESSIONS,
        seed="s",
        draws=3,
    )
    got = {n.security_id: n for n in sliced}
    assert {n.security_id for n in subset} == set(got)
    for sub in subset:
        g = got[sub.security_id]
        assert [d.entry_date for d in g.timing] == [d.entry_date for d in sub.timing]
        assert [d.security_id for d in g.name] == [d.security_id for d in sub.name]


# --- the per-window tally -----------------------------------------------------------------------------------


def _w(run_id, *, sessions, actual, vs_timing, vs_name, unmeasurable=False) -> WindowNulls:
    return WindowNulls(
        run_id=run_id,
        window_start=date(2026, 1, 1),
        window_end=date(2026, 2, 11),
        null_draws=5,
        timing_candidate_sessions=sessions,
        reproduced=True,
        slice_nulls=[],  # the tally reads the per-window Stats, not the pool
        actual=Stat() if unmeasurable else actual,
        vs_timing=vs_timing,
        vs_name=vs_name,
    )


def test_the_per_window_tally_counts_beats_over_the_windows_that_could_answer():
    """`beat X of M` counts a beat as the slice's actual median above the null median IN THAT WINDOW, over
    the measurable windows where the null was drawable. An unmeasurable window (no slice episode) is
    excluded and counted; a short window is flagged because its timing null is near-vacuous."""
    w1 = _w(
        "r1",
        sessions=40,
        actual=Stat(n=3, median=0.10),
        vs_timing=Stat(n=6, median=0.02),
        vs_name=Stat(n=6, median=0.05),
    )  # beats both
    w2 = _w(
        "r2",
        sessions=10,
        actual=Stat(n=2, median=0.01),
        vs_timing=Stat(n=4, median=0.03),
        vs_name=Stat(n=4, median=-0.01),
    )  # loses timing, beats name; SHORT window
    w3 = _w("r3", sessions=40, actual=Stat(), vs_timing=Stat(), vs_name=Stat(), unmeasurable=True)

    rep = _pool_windows([w1, w2, w3], slice_label="key1_source=insider", pass_id="p")
    assert rep.beat_timing == (1, 2)  # w1 beats, w2 loses; both measurable + drawable
    assert rep.beat_name == (2, 2)  # both beat their name null
    assert rep.n_measurable == 2 and rep.n_unmeasurable == 1
    assert rep.short_windows == ["2026-01-01..2026-02-11"]  # only w2 (< 30 sessions)
    assert rep.run_ids == ["r1", "r2", "r3"]


def test_a_measurable_window_with_no_peers_is_excluded_from_the_name_tally():
    """A single-name-thesis slice has no name null to beat -- that window cannot answer the name question
    and must not count against the tally as a loss."""
    w1 = _w(
        "r1",
        sessions=40,
        actual=Stat(n=3, median=0.10),
        vs_timing=Stat(n=6, median=0.02),
        vs_name=Stat(),
    )  # no peers -> vs_name empty
    w2 = _w(
        "r2",
        sessions=40,
        actual=Stat(n=3, median=0.10),
        vs_timing=Stat(n=6, median=0.02),
        vs_name=Stat(n=6, median=0.05),
    )
    rep = _pool_windows([w1, w2], slice_label="key1_source=insider", pass_id="p")
    assert rep.beat_timing == (2, 2)
    assert rep.beat_name == (1, 1)  # only w2 could answer the name question
    assert rep.n_measurable == 2


# --- what may be sliced, and what may not -------------------------------------------------------------------


def test_slicing_by_thesis_is_refused_because_that_is_the_leaderboard():
    """Invariant #4, structurally -- the same guard `sweep.MetricSliceError` applies. A per-thesis ranking
    wearing a slice's clothes is exactly what this refuses."""
    with pytest.raises(SliceKeyError, match="not an algorithm dimension"):
        SliceSpec.parse("thesis_id=t1")


def test_a_mistyped_plural_key_is_refused_rather_than_reading_as_a_null_result():
    """`key1_sources` (plural) is a real field but not a SLICE_KEY; it would match nothing and read like
    'the family does nothing'."""
    with pytest.raises(SliceKeyError):
        SliceSpec.parse("key1_sources=insider")


def test_malformed_slices_are_refused():
    with pytest.raises(SliceKeyError):
        SliceSpec.parse("key1_source=")  # no value
    with pytest.raises(SliceKeyError):
        SliceSpec.parse("key1_source")  # no '='
    with pytest.raises(SliceKeyError):
        SliceSpec.parse("key1_source=insider,confirmation_grade=core,co_arm_bucket=alone")  # > 2
    with pytest.raises(SliceKeyError):
        SliceSpec.parse("key1_source=insider,key1_source=activist")  # repeated key


def test_a_valid_one_and_two_key_slice_parses_and_labels():
    assert SliceSpec.parse("key1_source=insider").pairs == (("key1_source", "insider"),)
    s = SliceSpec.parse("key1_source=insider,confirmation_grade=core")
    assert s.pairs == (("key1_source", "insider"), ("confirmation_grade", "core"))
    assert s.label == "key1_source=insider,confirmation_grade=core"


def test_the_family_match_uses_the_canonical_stringify():
    """`confirmation_grade=core` must match `Grade.CORE`, and a None `key1_source` matches 'none' -- the
    same enum->wire mapping the pooled report groups on (`pooled._slice_key`)."""
    tid = uuid.uuid4()
    ep_core = _ep(tid, uuid.uuid4(), key1="insider", grade=Grade.CORE)
    ep_flip = _ep(tid, uuid.uuid4(), key1="insider", grade=Grade.FLIP)
    assert SliceSpec.parse("confirmation_grade=core").matches(ep_core)
    assert not SliceSpec.parse("confirmation_grade=core").matches(ep_flip)
    ep_none = _ep(tid, uuid.uuid4(), key1="insider").model_copy(update={"key1_source": None})
    assert SliceSpec.parse("key1_source=none").matches(ep_none)


# --- the early refusals (no mirror, no episodes needed) -----------------------------------------------------


def _write_min_manifest(d, *, mirror_hash: str | None) -> None:
    man = mf.BacktestManifest(
        run_id="r1",
        created_at="2026-09-18T00:00:00+00:00",
        window_start=date(2026, 6, 1),
        window_end=date(2026, 7, 1),
        pin="2027-01-01T00:00:00+00:00",
        config_hash="c" * 64,
        config_short="cccccccc",
        config_canonical_json="{}",
        mirror=mf.MirrorInfo(hash=mirror_hash or mf.mirror_hash(d)),
        null_seed="r1",
        null_draws=5,
    )
    (d / mf.MANIFEST_NAME).write_text(man.model_dump_json(indent=2), encoding="utf-8")


def test_a_mirror_that_is_not_the_runs_own_is_refused(tmp_path):
    """Content-addressed: re-drawing over a different tape is a different experiment, checked not trusted."""
    _write_min_manifest(tmp_path, mirror_hash="a" * 64)
    w = analyze_run(tmp_path, tmp_path, SliceSpec.parse("key1_source=insider"))
    assert "not this run's mirror" in w.refused and not w.reproduced


def test_a_run_with_no_pooled_json_is_refused(tmp_path):
    """No stored nulls to verify the re-draw against -> refuse, never re-draw unverified."""
    _write_min_manifest(
        tmp_path, mirror_hash=None
    )  # matches the empty dir's hash, so the mirror check passes
    w = analyze_run(tmp_path, tmp_path, SliceSpec.parse("key1_source=insider"))
    assert "no pooled.json" in w.refused and not w.reproduced


def test_a_pass_of_only_refused_runs_reports_the_refusals_not_an_empty_finding(tmp_path):
    """When emptiness is because runs were REFUSED, the refusals are the story -- not a silent zero and not
    the loud empty-slice error (that is reserved for an absent VALUE across REPRODUCED windows)."""
    _write_min_manifest(tmp_path, mirror_hash="a" * 64)
    rep = analyze_pass([tmp_path], tmp_path, SliceSpec.parse("key1_source=insider"))
    assert rep.n_episodes == 0 and rep.run_ids == []
    assert "r1" in rep.refused


# --- the ORACLE: a real run, both nulls reproduced --------------------------------------------------------

pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from backtest.rebench import TOLERANCE  # noqa: E402
from backtest.run import execute  # noqa: E402
from pipeline.seed import (  # noqa: E402
    seed_hims,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
    seed_nuclear_theme_conviction,
    seed_unh,
)

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2026, 4, 1), date(2026, 6, 30)


def _seed_all(db):
    seed_hims(db)
    seed_unh(db)
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    seed_leu_catalyst(db)
    seed_nuclear_theme_conviction(db)
    db.commit()


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_oracle_reproduces_a_real_runs_nulls_both_of_them_and_slices_faithfully(db, tmp_path):
    """THE PROOF. A real run over a MULTI-NAME thesis (the 4-name nuclear basket arms OKLO with SMR/NNE/LEU
    as peers, so the NAME null is non-empty), re-drawn from its own bytes:

    - the whole-pool re-draw reproduces the stored `pooled.json` vs_timing AND vs_name, count-for-count --
      the proof that BOTH the sessions and the roster reconstruction match the pass;
    - the reconstructed session count matches what the run recorded;
    - a marginal slice is a faithful subset of those verified draws;
    - the reproduction gate REFUSES a tampered pooled.json;
    - an absent slice value raises LOUD.
    """
    _seed_all(db)
    outcome = execute(db, start=_START, end=_END, pin=_PIN, root=tmp_path)
    run_dir = store.run_dir(outcome.manifest.run_id, tmp_path)
    assert run_dir is not None
    man = outcome.manifest

    # the fixture actually DRIVES the name null: a multi-name thesis armed. Member-count verified.
    nuke = next(t for t in man.theses if t.name.startswith("Small-scale nuclear"))
    assert nuke.basket_size == 4, "the name null must draw real peers"

    pooled = json.loads((run_dir / "pooled.json").read_text("utf-8"))
    metric = next(m for m in pooled["metrics"] if m["name"] == "arm_timing_forward_return")
    assert metric["vs_name"]["n"] > 0, "the oracle must exercise a NON-EMPTY name null"
    assert metric["vs_timing"]["n"] > 0

    # ORACLE — the whole pool re-drawn reproduces the stored pool-level nulls, BOTH of them.
    whole = analyze_pass([run_dir], run_dir, None)
    assert whole.refused == {}, whole.refused
    assert whole.windows[0].reproduced is True
    assert whole.vs_timing.median == pytest.approx(metric["vs_timing"]["median"], abs=TOLERANCE)
    assert whole.vs_timing.mean == pytest.approx(metric["vs_timing"]["mean"], abs=TOLERANCE)
    assert whole.vs_timing.n == metric["vs_timing"]["n"]
    assert whole.vs_name.median == pytest.approx(metric["vs_name"]["median"], abs=TOLERANCE)
    assert whole.vs_name.mean == pytest.approx(metric["vs_name"]["mean"], abs=TOLERANCE)
    assert whole.vs_name.n == metric["vs_name"]["n"]  # the NAME null reproduced count-for-count
    # ...and the reconstructed timing sessions match what the run recorded
    assert (
        whole.windows[0].timing_candidate_sessions
        == pooled["diagnostics"]["timing_candidate_sessions"]
    )

    # a MARGINAL insider slice is a faithful, reproduced subset (HIMS/UNH are insider-keyed)
    insider = analyze_pass([run_dir], run_dir, SliceSpec.parse("key1_source=insider"))
    assert insider.windows[0].reproduced is True
    assert 0 < insider.n_episodes <= whole.n_episodes
    assert insider.effective_n <= insider.n_episodes

    # the reproduction GATE refuses a tampered pooled.json (a wrong stored null -> refuse, never pool)
    bad = tmp_path / "tampered"
    shutil.copytree(run_dir, bad)
    blob = json.loads((bad / "pooled.json").read_text("utf-8"))
    tmetric = next(m for m in blob["metrics"] if m["name"] == "arm_timing_forward_return")
    tmetric["vs_timing"]["median"] = (tmetric["vs_timing"]["median"] or 0.0) + 1.0
    (bad / "pooled.json").write_text(json.dumps(blob), encoding="utf-8")
    refused = analyze_run(bad, bad, SliceSpec.parse("key1_source=insider"))
    assert "do not reproduce" in refused.refused and not refused.reproduced

    # an absent slice value across reproduced windows raises LOUD (not silent zeros)
    with pytest.raises(SliceKeyError, match="0 scoreable episodes"):
        analyze_pass([run_dir], run_dir, SliceSpec.parse("key1_source=nonexistent_value"))
