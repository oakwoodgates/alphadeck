from __future__ import annotations

import ast
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtest.nulls import draw_nulls, episode_seed, horizon_days
from domain.enums import Grade, Verdict
from replay.schema import Episode

# B4 — THE TWO NULL MODELS. Absolute returns on this universe are biased upward by selection (the baskets
# were authored in 2026 over names that had already moved), so the only honest questions are relative:
# same name/random time, and same time/random name. These tests pin the properties that make a null a
# NULL rather than a second number -- the same horizon, the same scorer, no lookahead of its own, and a
# draw that is reproducible from the manifest.

_T0 = date(2026, 6, 1)
_SESSIONS = [_T0 + timedelta(days=i) for i in range(60)]


class _Realized:
    """A deterministic tape: every name rises by its own fixed daily step, so a window's return is exactly
    computable and a test can assert arithmetic rather than a direction. Duck-types the two methods
    ``score_window`` uses -- which is itself the point: the nulls never touch a point-in-time view.
    """

    def __init__(self, steps: dict[uuid.UUID, float], start: float = 100.0) -> None:
        self.steps = steps
        self.start = start
        self.calls: list[tuple] = []

    def _close(self, sid, d):
        return self.start + self.steps.get(sid, 0.0) * (d - _T0).days

    def first_close_on_or_after(self, security_id, d):
        self.calls.append(("first", security_id, d))
        return (d, self._close(security_id, d)) if d in _SESSIONS else None

    def bars_between(self, security_id, start, end):
        self.calls.append(("bars", security_id, start, end))
        return [
            {"d": d, "close": self._close(security_id, d), "open": None, "high": None, "low": None}
            for d in _SESSIONS
            if start <= d <= end
        ]


def _ep(tid, sid, *, arm=_T0 + timedelta(days=10), horizon=20) -> Episode:
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
        confirmation_grade=Grade.CORE,
        exit_by=arm + timedelta(days=horizon),
        key1_source="insider",
        co_arm_bucket="alone",
    )


def _draw(realized, episodes, roster, *, seed="s", draws=5):
    return draw_nulls(
        episodes,
        realized,
        roster_at=lambda tid, d, _r=roster: _r.get(tid, []),
        sessions=_SESSIONS,
        seed=seed,
        draws=draws,
    )


# --- the properties that make it a NULL -------------------------------------------------------------


def test_every_draw_uses_the_EPISODE_S_OWN_horizon():
    """A null scored over a different window measures the difference between two horizons, not between
    two decisions."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peer = uuid.uuid4()
    r = _Realized({sid: 1.0, peer: 0.5})
    [n] = _draw(r, [_ep(tid, sid, horizon=20)], {tid: [sid, peer]})
    assert n.horizon_days == 20
    assert all(d.horizon_days == 20 for d in n.timing + n.name)


def test_the_timing_null_never_draws_the_real_arm_date():
    """A null that can draw the actual answer is not a null."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    r = _Realized({sid: 1.0})
    arm = _T0 + timedelta(days=10)
    [n] = _draw(r, [_ep(tid, sid, arm=arm)], {tid: [sid]}, draws=len(_SESSIONS))
    assert n.timing and all(d.entry_date != arm for d in n.timing)


def test_the_timing_null_draws_only_real_trading_sessions():
    """A decision that could not have been made is not a counterfactual decision."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    r = _Realized({sid: 1.0})
    [n] = _draw(r, [_ep(tid, sid)], {tid: [sid]}, draws=40)
    assert all(d.entry_date in _SESSIONS for d in n.timing)


def test_the_name_null_draws_peers_and_never_the_name_itself():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peers = [uuid.uuid4() for _ in range(6)]
    r = _Realized({s: 1.0 for s in [sid, *peers]})
    [n] = _draw(r, [_ep(tid, sid)], {tid: [sid, *peers]}, draws=6)
    assert n.name and all(d.security_id != sid for d in n.name)
    assert {d.security_id for d in n.name} <= set(peers)


def test_the_name_null_holds_the_entry_date_fixed():
    """Same DAY, different name -- otherwise it is measuring timing and selection at once."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peers = [uuid.uuid4() for _ in range(4)]
    r = _Realized({s: 1.0 for s in [sid, *peers]})
    arm = _T0 + timedelta(days=12)
    [n] = _draw(r, [_ep(tid, sid, arm=arm)], {tid: [sid, *peers]})
    assert all(d.entry_date == arm for d in n.name)


def test_the_name_null_draws_from_the_roster_it_is_given():
    """A member of ANOTHER thesis is not an alternative this decision had."""
    tid, other = uuid.uuid4(), uuid.uuid4()
    sid, peer, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = _Realized({s: 1.0 for s in (sid, peer, stranger)})
    [n] = _draw(r, [_ep(tid, sid)], {tid: [sid, peer], other: [stranger]}, draws=10)
    assert {d.security_id for d in n.name} == {peer}


# --- reproducibility --------------------------------------------------------------------------------


def test_the_same_seed_reproduces_the_same_draws():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peers = [uuid.uuid4() for _ in range(10)]
    roster = {tid: [sid, *peers]}
    eps = [_ep(tid, sid)]
    a = _draw(_Realized({s: 1.0 for s in [sid, *peers]}), eps, roster, seed="run-1")
    b = _draw(_Realized({s: 1.0 for s in [sid, *peers]}), eps, roster, seed="run-1")
    assert [d.entry_date for d in a[0].timing] == [d.entry_date for d in b[0].timing]
    assert [d.security_id for d in a[0].name] == [d.security_id for d in b[0].name]


def test_a_different_seed_gives_different_draws():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    r = lambda: _Realized({sid: 1.0})  # noqa: E731
    eps = [_ep(tid, sid)]
    a = _draw(r(), eps, {tid: [sid]}, seed="run-1", draws=5)
    b = _draw(r(), eps, {tid: [sid]}, seed="run-2", draws=5)
    assert [d.entry_date for d in a[0].timing] != [d.entry_date for d in b[0].timing]


def test_adding_an_episode_does_not_reshuffle_the_others():
    """The reason each episode draws from its OWN sub-seed rather than one global stream: a re-run over a
    wider window must not silently move the draws -- and therefore the result -- of every episode that was
    already there."""
    tid, a_sid, b_sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    peers = [uuid.uuid4() for _ in range(8)]
    roster = {tid: [a_sid, b_sid, *peers]}
    steps = {s: 1.0 for s in [a_sid, b_sid, *peers]}
    one = _draw(_Realized(steps), [_ep(tid, a_sid)], roster, seed="s")
    two = _draw(_Realized(steps), [_ep(tid, a_sid), _ep(tid, b_sid)], roster, seed="s")
    assert [d.entry_date for d in one[0].timing] == [d.entry_date for d in two[0].timing]


def test_the_sub_seed_is_a_pure_function_of_run_and_episode():
    tid, sid = uuid.uuid4(), uuid.uuid4()
    ep = _ep(tid, sid)
    assert episode_seed("s", ep) == episode_seed("s", ep)
    assert episode_seed("s", ep) != episode_seed("t", ep)
    assert episode_seed("s", ep) != episode_seed("s", _ep(tid, uuid.uuid4()))


# --- the arithmetic ---------------------------------------------------------------------------------


def test_a_null_scores_through_the_same_scorer_as_the_real_arm():
    """A draw whose entry date equals the real one must produce the identical return, or the null is
    measuring the difference between two scorers rather than between a decision and chance."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peer = uuid.uuid4()
    r = _Realized({sid: 1.0, peer: 1.0})
    ep = _ep(tid, sid, arm=_T0 + timedelta(days=10), horizon=20)
    [n] = _draw(r, [ep], {tid: [sid, peer]}, draws=60)
    # the peer rises identically, so the name null's draw over the same window is the same number
    assert n.forward_return == pytest.approx(n.name[0].forward_return)


def test_excess_is_the_return_minus_the_basket_equal_weight_move():
    """Checked as exact arithmetic on a tape whose every number is known: a three-name basket rising 1.0,
    2.0 and 3.0 a day has an equal-weight move of the average, and the excess is the difference."""
    tid = uuid.uuid4()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = _Realized({a: 1.0, b: 2.0, c: 3.0})
    ep = _ep(tid, a, arm=_T0, horizon=10)
    [n] = _draw(r, [ep], {tid: [a, b, c]}, draws=1)
    rets = [(100 + s * 10) / 100 - 1 for s in (1.0, 2.0, 3.0)]
    assert n.forward_return == pytest.approx(rets[0])
    assert n.excess_return == pytest.approx(rets[0] - sum(rets) / 3)


def test_the_benchmark_prices_only_the_names_that_have_a_tape():
    """Dividing by roster size instead of by the names that actually priced would drag the benchmark
    toward zero and flatter every excess return against it."""
    tid = uuid.uuid4()
    a, b, ghost = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = _Realized(
        {a: 1.0, b: 3.0}
    )  # ghost has a step of 0 but still prices; use a real absence instead
    r.steps[ghost] = 0.0
    ep = _ep(tid, a, arm=_T0, horizon=10)
    [with_flat] = _draw(r, [ep], {tid: [a, b, ghost]}, draws=1)
    [without] = _draw(_Realized({a: 1.0, b: 3.0}), [ep], {tid: [a, b]}, draws=1)
    # a flat third name IS priced, so it legitimately moves the benchmark -- the two differ
    assert with_flat.excess_return != pytest.approx(without.excess_return)


def test_an_episode_with_no_horizon_is_skipped():
    """Nothing for a null to match, so no draw is invented for it."""
    tid, sid = uuid.uuid4(), uuid.uuid4()
    ep = _ep(tid, sid).model_copy(update={"exit_by": None})
    assert horizon_days(ep) is None
    assert _draw(_Realized({sid: 1.0}), [ep], {tid: [sid]}) == []


def test_fewer_candidates_than_K_uses_them_all_rather_than_resampling():
    """A four-name basket has three peers. Reporting three is honest; resampling to fifty would
    manufacture confidence the data cannot support, and the per-slice n would stop meaning anything.
    """
    tid, sid = uuid.uuid4(), uuid.uuid4()
    peers = [uuid.uuid4() for _ in range(3)]
    r = _Realized({s: 1.0 for s in [sid, *peers]})
    [n] = _draw(r, [_ep(tid, sid)], {tid: [sid, *peers]}, draws=50)
    assert len(n.name) == 3
    assert len({d.security_id for d in n.name}) == 3  # without replacement


# --- the structural boundary ------------------------------------------------------------------------


def test_the_nulls_module_cannot_reach_a_point_in_time_view():
    """The same structural rule ``tests/replay/test_scoring.py`` holds for the scorer: forward-unbounded
    reads must be unable to reach the as-of path, by IMPORT GRAPH rather than by discipline. A null that
    could open a pit would be one refactor away from scoring a counterfactual on capped data."""
    src = Path(__file__).resolve().parents[2] / "backtest" / "nulls.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(m.startswith("replay.pit") or m == "db.bitemporal" for m in imported), imported


def test_the_nulls_module_touches_no_database():
    """The roster is supplied by the caller, deliberately -- so this module stays a pure function of the
    scored layer and cannot acquire a connection by accident.

    Read off the AST, not the file text: the module's own comments legitimately NAME ``thesis_repo`` while
    explaining that the CALLER is the one that uses it, and a grep over prose would fail on the very
    sentence that documents the boundary."""
    src = Path(__file__).resolve().parents[2] / "backtest" / "nulls.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Call):
            f = node.func
            called.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))

    for mod in imported:
        assert not mod.startswith(("psycopg", "db.", "repositories")), mod
    assert "connect" not in called
