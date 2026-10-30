"""Hot Board (``docs/dashboards/hot-board.md``): the pure rules on hand-built rows (the arm-night walk, the row sets,
the clocks, the since-arm return, the heat and the tape), the router's reads (the heavy read stubbed, and the real
``read_board`` with its DB reads stubbed), and DB-backed reads through the real ``calls`` log and price tape: a card
armed after the as-of has no rows, a member added after the as-of is not a row then, and a price printed after the
as-of is never read.

Every card, company and ticker below is a neutral fixture name.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import hot_board as router_mod
from dashboards.baskets import CardsAsOf, FlaggedCompany
from dashboards.cache import CACHE
from dashboards.hot_board import (
    ARMED_RUN,
    MANAGING_RUN,
    ArmNight,
    Candidate,
    Clocks,
    CompanyTape,
    HeatInput,
    LogRow,
    Recorded,
    Tape,
    arm_night,
    arm_nights,
    assemble,
    candidates,
    card_state_counts,
    clocks,
    closed_ago,
    company_tape,
    earliest_arm_night,
    heat,
    percentiles,
    rs_leading,
    run_states,
    sessions_left,
    sessions_since,
    since_arm,
    trigger_key,
    vs_iwm_cell,
    winning_nights,
)
from dashboards.peer_table import CapCell
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from domain.signal import Provenance
from domain.thesis import BasketMember, Segment, Thesis
from repositories import calls_repo, thesis_repo
from signals.display import (
    insider_flow,
    price_path,
    range52w,
    relative_strength,
    rvol,
    sma,
    trailing_returns,
    volume_regime,
)
from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayHeadline,
    DisplayMetric,
    DisplaySeries,
    DisplaySignal,
)

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "frontend"
    / "src"
    / "dashboards"
    / "hot-board"
    / "__tests__"
    / "heat-fixtures.json"
)

ASOF = date(2026, 10, 28)  # a Wednesday
A, B, C, D = (uuid.uuid4() for _ in range(4))
T1, T2, T3 = (uuid.uuid4() for _ in range(3))
NAMES = {T1: "Card A", T2: "Card B", T3: "Card C"}


def weekdays(start: date, end: date, skip: tuple[date, ...] = ()) -> list[date]:
    """Mon-Fri dates in [start, end], less ``skip`` (a market holiday)."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def key(label: str, turned: bool = False) -> KeyState:
    return KeyState(turned=turned, label=label)


def trig(
    sid: UUID,
    label: str = "8-day breakout",
    kind: Kind = Kind.TECHNICAL_BREAKOUT,
    on: date | None = date(2026, 10, 20),
    **kw,
) -> TriggerRef:
    return TriggerRef(label=label, kind=kind, security_id=sid, event_date=on, **kw)


def mem(sid: UUID, **kw) -> MemberCall:
    return MemberCall(security_id=sid, **kw)


def cc(state: State, *, tid: UUID = T1, asof: date = ASOF, **kw) -> CallCard:
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=key("Conviction"),
        key_confirmation=key("Confirmation"),
        **kw,
    )


def rec(card: CallCard, *, seq: int = 1, reconstructed: bool = False) -> Recorded:
    return Recorded(asof=card.asof, seq=seq, reconstructed=reconstructed, card=card)


def log(
    asof: date,
    state: State = State.ARMED,
    ids: tuple[UUID, ...] = (A,),
    *,
    seq: int = 1,
    reconstructed: bool = False,
) -> LogRow:
    return LogRow(asof, seq, reconstructed, state, frozenset(ids))


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


# ---------------------------------------------------------------------------------------------------------
# 1. the arm night
# ---------------------------------------------------------------------------------------------------------


def test_the_arm_night_is_the_first_night_of_the_unbroken_armed_run():
    rows = [
        log(d(20), State.WARMING, ()),
        log(d(21)),
        log(d(22)),
        log(d(23)),
    ]
    assert arm_night(rows, A) == ArmNight(d(21), False)
    # the input order does not matter: the walk sorts the nights itself
    assert arm_night(list(reversed(rows)), A) == ArmNight(d(21), False)


def test_a_run_broken_by_a_warming_night_starts_over():
    rows = [log(d(19)), log(d(20), State.WARMING, ()), log(d(21)), log(d(22))]
    assert arm_night(rows, A) == ArmNight(d(21), False)


def test_a_same_night_re_run_with_a_higher_seq_is_the_night_that_stood():
    # the 22nd's first row armed, its re-run (seq 2) Warming: the night reads Warming, so the card is not armed
    broken = [log(d(21)), log(d(22), seq=1), log(d(22), State.WARMING, (), seq=2)]
    assert arm_night(broken, A) is None
    # the other way round: Warming first, the re-run Armed, so the 22nd is the run's first night
    healed = [
        log(d(21), State.WARMING, ()),
        log(d(22), State.WARMING, (), seq=1),
        log(d(22), seq=2),
    ]
    assert arm_night(healed, A) == ArmNight(d(22), False)
    assert [(r.asof, r.seq) for r in winning_nights(broken)] == [(d(22), 2), (d(21), 1)]


def test_a_reconstructed_arm_night_is_marked_and_a_later_one_does_not_mark_it():
    rows = [log(d(21), reconstructed=True), log(d(22)), log(d(23), reconstructed=True)]
    assert arm_night(rows, A) == ArmNight(d(21), True)
    assert arm_night([log(d(21)), log(d(22), reconstructed=True)], A) == ArmNight(d(21), False)


def test_a_member_that_joined_the_run_later_has_its_own_later_arm_night():
    rows = [log(d(21), ids=(A,)), log(d(22), ids=(A, B)), log(d(23), ids=(A, B))]
    assert arm_night(rows, A) == ArmNight(d(21), False)
    assert arm_night(rows, B) == ArmNight(d(22), False)


def test_a_member_that_dropped_out_and_came_back_starts_again():
    rows = [log(d(21), ids=(A, B)), log(d(22), ids=(A,)), log(d(23), ids=(A, B))]
    assert arm_night(rows, B) == ArmNight(d(23), False)
    assert arm_night(rows, A) == ArmNight(d(21), False)


def test_a_member_not_listed_on_the_night_in_effect_has_no_arm_night():
    rows = [log(d(21), ids=(A, B)), log(d(22), ids=(A,))]
    assert arm_night(rows, B) is None
    assert arm_night([], A) is None


def test_a_card_not_in_the_run_on_the_night_in_effect_has_no_arm_night():
    assert arm_night([log(d(21)), log(d(22), State.WARMING)], A) is None
    assert arm_night([log(d(21)), log(d(22), State.MANAGING)], A, ARMED_RUN) is None


def test_a_managing_card_walks_back_over_its_armed_nights_and_an_armed_card_does_not_over_managing():
    managing = [log(d(20)), log(d(21)), log(d(22), State.MANAGING), log(d(23), State.MANAGING)]
    assert arm_night(managing, A, MANAGING_RUN) == ArmNight(d(20), False)
    armed_after_managing = [log(d(21), State.MANAGING), log(d(22)), log(d(23))]
    assert arm_night(armed_after_managing, A, ARMED_RUN) == ArmNight(d(22), False)
    assert run_states(State.MANAGING) == MANAGING_RUN and run_states(State.ARMED) == ARMED_RUN


# ---------------------------------------------------------------------------------------------------------
# 2. who is a row
# ---------------------------------------------------------------------------------------------------------


def _by_card(cands: list[Candidate]) -> list[tuple]:
    return [(c.thesis_name, c.kind, c.security_id) for c in cands]


def test_an_armed_cards_armed_members_are_rows_and_its_watch_members_a_second_kind():
    card = cc(
        State.ARMED,
        armed_members=[mem(A), mem(B), mem(A)],  # a repeat is one row
        watch_members=[mem(C), mem(B)],  # B is armed: it is not also a watch row
    )
    got = candidates({T1: "Card A"}, {T1: rec(card)})
    assert _by_card(got) == [
        ("Card A", "armed", A),
        ("Card A", "armed", B),
        ("Card A", "watch", C),
    ]


def test_a_warming_cards_names_with_a_live_entry_trigger_are_rows_that_say_which_key():
    card = cc(
        State.WARMING,
        triggers_fired=[
            trig(A, "Insider cluster buy", Kind.INSIDER),
            trig(A, "8-day breakout"),  # A has both keys' triggers
            trig(B, "8-day breakout"),
            trig(C, "Theme conviction", Kind.THEME_CONVICTION),
        ],
        risk_signals=[trig(B, "Convertible overhang", Kind.DILUTION_RISK)],
        armed_members=[mem(D)],  # a Warming card has none; if one were listed it is not a row
    )
    got = candidates({T1: "Card A"}, {T1: rec(card)})
    assert _by_card(got) == [
        ("Card A", "warming", A),
        ("Card A", "warming", B),
        ("Card A", "warming", C),
    ]
    by_sid = {c.security_id: c for c in got}
    assert by_sid[A].keys == ("conviction", "confirmation")
    assert by_sid[B].keys == ("confirmation",) and by_sid[C].keys == ("conviction",)
    assert [t.label for t in by_sid[A].triggers] == ["Insider cluster buy", "8-day breakout"]
    assert [t.label for t in by_sid[B].risks] == ["Convertible overhang"] and by_sid[A].risks == ()
    assert by_sid[A].member is None


def test_a_managing_cards_held_and_armed_members_are_rows():
    held = mem(A, verdict=Verdict.MANAGING)
    card = cc(State.MANAGING, armed_members=[held, mem(B)], watch_members=[mem(C)])
    got = candidates({T1: "Card A"}, {T1: rec(card)})
    # a Managing card's watch members are not rows (only an Armed card's are)
    assert _by_card(got) == [("Card A", "managing", A), ("Card A", "managing", B)]
    assert got[0].member is held


def test_an_incubating_card_and_a_card_with_no_recorded_call_have_no_rows_and_are_counted():
    names = {T1: "Card A", T2: "Card B", T3: "Card C"}
    recorded = {
        T1: rec(cc(State.INCUBATING, tid=T1, armed_members=[mem(A)])),  # ignored: not Armed
        T2: rec(cc(State.ARMED, tid=T2, armed_members=[mem(B)])),
    }
    assert _by_card(candidates(names, recorded)) == [("Card B", "armed", B)]
    assert card_state_counts(names, recorded) == {
        "armed": 1,
        "warming": 0,
        "managing": 0,
        "incubating": 1,
        "no_call": 1,
    }
    # a card outside the counted names (archived, or not yet created) is never read, whatever the log holds
    assert _by_card(candidates({T2: "Card B"}, recorded)) == [("Card B", "armed", B)]
    assert candidates({T3: "Card C"}, recorded) == []


def test_cards_come_by_name_and_a_member_keeps_its_recorded_order():
    recorded = {
        T1: rec(cc(State.ARMED, tid=T1, armed_members=[mem(B), mem(A)])),
        T2: rec(cc(State.ARMED, tid=T2, armed_members=[mem(C)])),
    }
    got = candidates({T2: "alpha card", T1: "Beta card"}, recorded)
    assert [(c.thesis_name, c.security_id) for c in got] == [
        ("alpha card", C),
        ("Beta card", B),
        ("Beta card", A),
    ]


def test_a_members_clocks_and_flags_come_from_its_own_call_and_fall_back_to_the_cards():
    card = cc(
        State.ARMED,
        arm_until=d(30),
        exit_by=date(2026, 12, 1),
        armed_members=[
            mem(A, arm_until=d(29), exit_by=date(2026, 11, 15), entry_window_closing=True),
            mem(B),
        ],
    )
    a, b = candidates({T1: "Card A"}, {T1: rec(card)})
    assert (a.arm_until, a.exit_by) == (d(29), date(2026, 11, 15))
    assert (b.arm_until, b.exit_by) == (d(30), date(2026, 12, 1))


def test_the_key_a_trigger_turns_is_the_call_configs_own_kind_set():
    assert trigger_key(Kind.INSIDER) == "conviction"
    assert trigger_key(Kind.CATALYST) == "conviction"
    assert trigger_key(Kind.TECHNICAL_BREAKOUT) == "confirmation"
    assert trigger_key(Kind.LAGGARD) == "confirmation"
    assert trigger_key(Kind.SQUEEZE) is None


def test_arm_nights_are_read_for_armed_and_managing_rows_only():
    card_armed = cc(State.ARMED, tid=T1, armed_members=[mem(A)], watch_members=[mem(B)])
    card_managing = cc(State.MANAGING, tid=T2, armed_members=[mem(C)])
    card_warming = cc(State.WARMING, tid=T3, triggers_fired=[trig(D)])
    names = {T1: "Card A", T2: "Card B", T3: "Card C"}
    cands = candidates(names, {T1: rec(card_armed), T2: rec(card_managing), T3: rec(card_warming)})
    runs = {
        T1: [log(d(27), ids=(A,)), log(d(28), ids=(A,))][::-1],
        T2: [log(d(20), ids=(C,)), log(d(21), State.MANAGING, ids=(C,))][::-1],
    }
    got = arm_nights(cands, runs)
    assert got == {
        (T1, A): ArmNight(d(27), False),
        (T2, C): ArmNight(d(20), False),
    }
    # the earliest arm night of each security across its rows
    assert earliest_arm_night(
        {(T1, A): ArmNight(d(27), False), (T2, A): ArmNight(d(21), True), (T3, B): None}
    ) == {A: d(21)}


# ---------------------------------------------------------------------------------------------------------
# 3. the clocks
# ---------------------------------------------------------------------------------------------------------

HOLIDAY = d(22)  # a Thursday the market is shut (a fixture)
CAL = weekdays(d(1), ASOF, skip=(HOLIDAY,))


def test_sessions_since_counts_the_benchmarks_bars_after_the_arm_night_through_the_as_of():
    # Mon 10-19 .. Wed 10-28: Tue 20, Wed 21, [Thu 22 shut], Fri 23, Mon 26, Tue 27, Wed 28 = 6
    assert sessions_since(CAL, d(19), ASOF) == 6
    # the weekend between Fri 23 and Mon 26 does not count either
    assert sessions_since(CAL, d(23), ASOF) == 3
    # armed tonight: none since; a later arm night than the as-of counts none
    assert sessions_since(CAL, ASOF, ASOF) == 0
    assert sessions_since(CAL, None, ASOF) is None
    assert sessions_since([], d(19), ASOF) is None


def test_sessions_left_counts_the_mon_fri_sessions_after_the_as_of_through_arm_until():
    assert sessions_left(ASOF, d(30)) == 2  # Thu 29, Fri 30
    assert sessions_left(ASOF, date(2026, 10, 31)) == 2  # Saturday adds none
    assert sessions_left(ASOF, date(2026, 11, 2)) == 3  # Thu, Fri, Mon
    assert sessions_left(ASOF, ASOF) == 0 and sessions_left(ASOF, d(27)) == 0


def test_closed_ago_counts_the_calendars_sessions_after_arm_until():
    assert closed_ago(CAL, d(26), ASOF) == 2  # Tue 27, Wed 28
    assert closed_ago(CAL, d(21), ASOF) == 4  # Fri 23, Mon 26, Tue 27, Wed 28 (Thu 22 shut)


def test_the_clocks_of_a_row():
    arm = ArmNight(d(19), True)
    got = clocks(arm, d(30), date(2026, 12, 1), CAL, ASOF)
    assert got == Clocks(
        arm_night=d(19),
        arm_reconstructed=True,
        sessions_since=6,
        arm_until=d(30),
        sessions_left=2,
        closed_ago=None,
        exit_by=date(2026, 12, 1),
        days_to_exit=34,
    )
    closed = clocks(arm, d(26), d(20), CAL, ASOF)
    assert (closed.sessions_left, closed.closed_ago) == (None, 2)
    assert closed.days_to_exit == -8  # the hold horizon passed eight days ago
    # a window ending on the as-of is open with none left, not closed
    last = clocks(arm, ASOF, None, CAL, ASOF)
    assert (last.sessions_left, last.closed_ago, last.days_to_exit) == (0, None, None)


def test_a_card_with_no_entry_clock_has_no_session_counts_and_a_name_with_no_arm_night_no_age():
    got = clocks(ArmNight(d(19), False), None, None, CAL, ASOF)
    assert (got.arm_until, got.sessions_left, got.closed_ago, got.exit_by, got.days_to_exit) == (
        None,
        None,
        None,
        None,
        None,
    )
    none = clocks(None, None, None, CAL, ASOF)
    assert (none.arm_night, none.arm_reconstructed, none.sessions_since) == (None, False, None)


def test_an_arm_night_before_the_price_window_gets_no_session_count():
    # the calendar stops at the window's first bar, so a count from an earlier night would fall short
    got = clocks(ArmNight(d(5), False), None, None, CAL, ASOF, window_start=d(10))
    assert got.arm_night == d(5) and got.sessions_since is None
    # an arm night inside the window counts as always: Mon 12 .. Wed 28, less the shut Thursday 22
    inside = clocks(ArmNight(d(12), False), None, None, CAL, ASOF, window_start=d(10))
    assert inside.sessions_since == 11


# ---------------------------------------------------------------------------------------------------------
# 4. since the arm night
# ---------------------------------------------------------------------------------------------------------

BARS = [(d(19), 10.0), (d(20), 10.5), (d(21), 11.0), (d(26), 12.0), (ASOF, 12.5)]
IWM = [(d(19), 200.0), (d(20), 201.0), (d(21), 203.0), (d(26), 204.0), (ASOF, 206.0)]


def test_the_return_runs_from_the_first_close_on_or_after_the_arm_night_to_the_last_close():
    got = since_arm(BARS, IWM, d(20))
    assert (got.entry_date, got.entry_close, got.last_date, got.last_close) == (
        d(20),
        10.5,
        ASOF,
        12.5,
    )
    assert got.ret_pct == round((12.5 / 10.5 - 1) * 100, 2) == 19.05
    assert got.iwm_pct == round((206.0 / 201.0 - 1) * 100, 2) == 2.49
    assert got.gap is None


def test_an_arm_night_with_no_bar_of_its_own_enters_at_the_next_close():
    # Saturday the 24th: no bar, the first close on or after it is Monday the 26th
    got = since_arm(BARS, IWM, date(2026, 10, 24))
    assert (got.entry_date, got.entry_close, got.ret_pct) == (
        d(26),
        12.0,
        round((12.5 / 12.0 - 1) * 100, 2),
    )
    assert got.iwm_pct == round((206.0 / 204.0 - 1) * 100, 2)


def test_armed_on_the_as_of_is_a_stated_zero_and_a_missing_benchmark_is_blank_not_zero():
    got = since_arm(BARS, [], ASOF)
    assert (got.ret_pct, got.iwm_pct, got.entry_date) == (0.0, None, ASOF)


def test_each_reason_a_return_is_blank():
    assert since_arm(BARS, IWM, None).gap == "no_arm_night"
    assert since_arm(BARS, IWM, d(20), flag="mixed-scale price history").gap == "flagged"
    assert since_arm(BARS, IWM, d(5), window_start=d(10)).gap == "before_window"
    assert since_arm(BARS, IWM, date(2026, 10, 29)).gap == "no_bar"
    assert since_arm([], IWM, d(20)).gap == "no_bar"
    for g in (since_arm(BARS, IWM, None), since_arm([], IWM, d(20))):
        assert g.ret_pct is None and g.iwm_pct is None


# ---------------------------------------------------------------------------------------------------------
# 5. heat
# ---------------------------------------------------------------------------------------------------------


def _cases():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["cases"]


def test_the_shared_fixture_file_is_there_and_has_cases():
    assert len(_cases()) >= 6


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_heat_matches_the_fixtures_the_page_is_tested_against(case):
    rows = [HeatInput(**r) for r in case["rows"]]
    got = heat(rows)
    assert len(got) == len(rows)
    for row, h in zip(rows, got):
        want = case["expected"][row.key]
        for part in ("fresh", "loud", "leading", "unextended", "score"):
            value = getattr(h, part)
            if want[part] is None:
                assert value is None, (row.key, part)
            else:
                assert value == pytest.approx(want[part], abs=1e-9), (row.key, part)


def test_percentile_is_the_mid_rank_over_n_minus_1_and_a_blank_stays_blank():
    assert percentiles([10.0, 30.0, 20.0, None]) == [0.0, 1.0, 0.5, None]
    assert percentiles([1.0, 1.0, 3.0]) == [0.25, 0.25, 1.0]
    assert percentiles([5.0]) == [0.5]
    assert percentiles([None, None]) == [None, None]
    assert percentiles([]) == []


def test_each_part_ranks_the_direction_the_plan_states():
    rows = [
        HeatInput("old", sessions_since=9, rvol20=1.0, vs_iwm=0.0, since_arm_pct=0.0),
        HeatInput("new", sessions_since=0, rvol20=1.0, vs_iwm=0.0, since_arm_pct=0.0),
    ]
    old, new = heat(rows)
    assert (new.fresh, old.fresh) == (1.0, 0.0)  # fewer sessions since the arm night ranks higher
    rows = [
        HeatInput("quiet", sessions_since=1, rvol20=0.8, vs_iwm=0.0, since_arm_pct=0.0),
        HeatInput("loud", sessions_since=1, rvol20=3.1, vs_iwm=0.0, since_arm_pct=0.0),
    ]
    quiet, loud = heat(rows)
    assert (loud.loud, quiet.loud) == (1.0, 0.0)  # a higher RVOL ranks higher
    rows = [
        HeatInput("lagging", sessions_since=1, rvol20=1.0, vs_iwm=-4.0, since_arm_pct=0.0),
        HeatInput("leading", sessions_since=1, rvol20=1.0, vs_iwm=6.0, since_arm_pct=0.0),
    ]
    lagging, leading = heat(rows)
    assert (leading.leading, lagging.leading) == (
        1.0,
        0.0,
    )  # a higher return minus IWM's ranks higher
    rows = [
        HeatInput("far", sessions_since=1, rvol20=1.0, vs_iwm=0.0, since_arm_pct=40.0),
        HeatInput("near", sessions_since=1, rvol20=1.0, vs_iwm=0.0, since_arm_pct=2.0),
    ]
    far, near = heat(rows)
    assert (near.unextended, far.unextended) == (
        1.0,
        0.0,
    )  # a smaller return since the arm night ranks higher


def test_a_row_missing_any_part_has_no_heat_and_every_part_is_still_reported():
    rows = [
        HeatInput("full", 1, 2.0, 3.0, 4.0),
        HeatInput("no-bar", 1, 2.0, 3.0, None),
        HeatInput("no-tape", None, None, None, None),
        HeatInput("no-rvol", 1, None, 3.0, 4.0),
    ]
    got = {r.key: h for r, h in zip(rows, heat(rows))}
    assert got["full"].score is not None
    assert all(got[k].score is None for k in ("no-bar", "no-tape", "no-rvol"))
    assert got["no-tape"] == replace(
        got["no-tape"], fresh=None, loud=None, leading=None, unextended=None
    )
    assert heat([]) == []


def test_heat_is_recomputed_on_the_rows_shown():
    rows = [
        HeatInput("A", 0, 2.0, 8.0, 1.0),
        HeatInput("B", 2, 1.0, 3.0, 4.0),
        HeatInput("C", 5, 3.0, -2.0, -1.0),
    ]
    whole = {r.key: h.score for r, h in zip(rows, heat(rows))}
    subset = {r.key: h.score for r, h in zip(rows[:2], heat(rows[:2]))}
    # A beats B on all four readings, so among the two of them it is the top of every part; with C beside it, not
    assert subset == {"A": pytest.approx(100.0), "B": pytest.approx(0.0)}
    assert whole["A"] == pytest.approx(75.0)


# ---------------------------------------------------------------------------------------------------------
# 6. the tape
# ---------------------------------------------------------------------------------------------------------


def _sig(kind, metrics=(), events=(), headline=None, bars_used=None, window_end=None, series=()):
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=list(metrics),
        events=list(events),
        headline=headline,
        series=list(series),
        basis=DisplayBasis(source="fact_price_eod", bars_used=bars_used, window_end=window_end),
    )


def _metric(k, v=None, note=None):
    return DisplayMetric(key=k, label=k, value=v, note=note)


def _trailing(ret30, end=ASOF, ret7=1.0):
    return _sig(
        trailing_returns.MEMBER_NAME,
        [
            _metric("ret_7d", ret7),
            _metric("ret_30d", ret30, None if ret30 is not None else "n/a: 20/31 bars"),
        ],
        window_end=end,
    )


NOCAP = CapCell(gap="no_shares")


def test_a_tape_is_read_off_the_members_own_keys():
    signals = {
        rvol.MEMBER_NAME: _sig("rvol", [_metric("rvol", 1.1), _metric("rvol20", 1.8)]),
        sma.MEMBER_NAME: _sig("sma", [_metric("pct_vs_fast", -0.5)]),
        range52w.MEMBER_NAME: _sig("r", [_metric("pct_off_52w_high", -3.25)]),
        relative_strength.MEMBER_NAME: _sig(
            "rs",
            events=[DisplayEvent(key="rs_high_iwm", label="x", date=ASOF)],
            headline=DisplayHeadline(key="leading", label="x"),
            bars_used=relative_strength.MIN_BARS,
        ),
        trailing_returns.MEMBER_NAME: _trailing(9.5, ret7=-2.0),
        insider_flow.MEMBER_NAME: _sig(
            "ins", [_metric("buy_count_30d", 3.0), _metric("distinct_buyers_30d", 2.0)]
        ),
        volume_regime.MEMBER_NAME: _sig("v", [_metric("adv_usd_20d", 2_500_000.0)]),
        price_path.MEMBER_NAME: _sig(
            "p", series=[DisplaySeries(key="close", label="close", values=[None, 1.234567, 2.0])]
        ),
    }
    iwm = _trailing(4.0)
    t = company_tape(signals, iwm, NOCAP, BARS).tape
    assert (t.rvol20.value, t.vs_50d.value, t.off_high.value) == (1.8, -0.5, -3.25)
    assert t.vs_iwm.value == 5.5  # 9.5 - 4.0 percentage points
    assert (t.rs_leading, t.rs_leading_vs) == (True, ("IWM",))
    assert (t.ret_7d.value, t.ret_30d.value) == (-2.0, 9.5)
    assert (t.buys_30d, t.buyers_30d, t.adv_usd_20d.value) == (3, 2, 2_500_000.0)
    assert t.spark == (None, 1.2346, 2.0)
    assert t.flag is None


def test_a_blank_cell_keeps_the_members_note_and_a_missing_member_says_no_price_bars():
    signals = {
        rvol.MEMBER_NAME: _sig("rvol", [_metric("rvol20", None, "n/a: 12/21 bars")]),
        trailing_returns.MEMBER_NAME: _trailing(None),
    }
    t = company_tape(signals, _trailing(4.0), NOCAP).tape
    assert (t.rvol20.value, t.rvol20.note) == (None, "n/a: 12/21 bars")
    assert t.vs_iwm.note == "n/a: 20/31 bars"
    assert (t.vs_50d.value, t.vs_50d.note) == (None, "no price bars")
    assert (t.buys_30d, t.buyers_30d) == (None, None)  # no Form 4 ingested: a blank, never a zero
    assert t.spark == ()


def test_a_quiet_insider_tape_is_a_stated_zero_and_no_form_4_at_all_is_a_blank():
    # off the member's own pure compute: a buy 60 days back is in the 90-day window but not the 30-day one
    quiet = insider_flow.compute(
        [{"valid_from": ASOF - timedelta(days=60), "txn_code": "P", "insider_name": "X"}], ASOF
    )
    t = company_tape({insider_flow.MEMBER_NAME: quiet}, None, NOCAP).tape
    assert (t.buys_30d, t.buyers_30d) == (0, 0)
    # no Form 4 ingested: the member gives nothing, and the cell is a blank, never a zero
    none = company_tape(
        {insider_flow.MEMBER_NAME: insider_flow.compute([], ASOF)}, None, NOCAP
    ).tape
    assert (none.buys_30d, none.buyers_30d) == (None, None)
    busy = insider_flow.compute(
        [
            {"valid_from": ASOF - timedelta(days=3), "txn_code": "P", "insider_name": "X"},
            {"valid_from": ASOF - timedelta(days=5), "txn_code": "P", "insider_name": "Y"},
        ],
        ASOF,
    )
    got = company_tape({insider_flow.MEMBER_NAME: busy}, None, NOCAP).tape
    assert (got.buys_30d, got.buyers_30d) == (2, 2)


def _bars(n: int, close=lambda i: 10.0 + i * 0.1):
    return [
        {"d": ASOF - timedelta(days=n - 1 - i), "close": close(i), "volume": 1000.0 + i}
        for i in range(n)
    ]


def test_the_real_members_keys_parse_so_a_renamed_key_fails_here():
    """Off the eight members' own pure computes over one synthetic tape (no stub of a key name)."""
    bars = _bars(260)
    iwm_bars = _bars(260, close=lambda i: 200.0)
    signals = {
        rvol.MEMBER_NAME: rvol.compute(bars, ASOF),
        sma.MEMBER_NAME: sma.compute(bars, ASOF),
        range52w.MEMBER_NAME: range52w.compute(bars, ASOF),
        relative_strength.MEMBER_NAME: relative_strength.compute(
            bars, {"SPY": iwm_bars, "IWM": iwm_bars}, ASOF
        ),
        trailing_returns.MEMBER_NAME: trailing_returns.compute(bars, ASOF),
        insider_flow.MEMBER_NAME: insider_flow.compute([], ASOF),
        volume_regime.MEMBER_NAME: volume_regime.compute(bars, ASOF),
        price_path.MEMBER_NAME: price_path.compute(bars, ASOF),
    }
    t = company_tape(signals, trailing_returns.compute(iwm_bars, ASOF), NOCAP).tape
    for cell in (t.rvol20, t.vs_50d, t.off_high, t.vs_iwm, t.ret_7d, t.ret_30d, t.adv_usd_20d):
        assert cell.value is not None, cell
    assert t.off_high.value == 0.0  # a steady climb prints its high on the as-of bar
    assert t.rs_leading is True and t.rs_leading_vs == ("SPY", "IWM")
    assert len(t.spark) == price_path.BARS and t.spark[-1] == round(bars[-1]["close"], 4)
    assert t.ret_30d.value == round((bars[-1]["close"] / bars[-31]["close"] - 1) * 100, 2)
    assert t.vs_iwm.value == round(t.ret_30d.value - 0.0, 2)  # IWM is flat


def test_the_return_minus_iwms_needs_the_same_last_close_and_both_windows():
    own = _trailing(10.0)
    assert vs_iwm_cell(own, _trailing(3.0)).value == 7.0
    assert vs_iwm_cell(None, _trailing(3.0)).note == "no price bars"
    assert vs_iwm_cell(own, None).note == "no IWM bars"
    other_day = vs_iwm_cell(own, _trailing(3.0, end=d(27)))
    assert other_day.value is None and "is not IWM's" in (other_day.note or "")
    assert vs_iwm_cell(own, _trailing(None)).note == "n/a: 20/31 bars"


def test_a_thin_rs_has_no_verdict_and_a_quiet_one_is_not_leading():
    thin = _sig(
        "rs",
        headline=DisplayHeadline(key="inline", label="x"),
        bars_used=relative_strength.MIN_BARS - 1,
    )
    assert rs_leading(thin) == (None, ())
    assert rs_leading(None) == (None, ())
    quiet = _sig(
        "rs",
        headline=DisplayHeadline(key="inline", label="x"),
        bars_used=relative_strength.MIN_BARS,
    )
    assert rs_leading(quiet) == (False, ())


def test_a_flagged_company_has_every_price_cell_blank_with_the_reason_and_keeps_its_insider_counts():
    signals = {
        insider_flow.MEMBER_NAME: _sig(
            "ins", [_metric("buy_count_30d", 2.0), _metric("distinct_buyers_30d", 1.0)]
        )
    }
    got = company_tape(
        signals, _trailing(1.0), CapCell(gap="flagged", note="mixed-scale"), flag="mixed-scale"
    )
    t = got.tape
    assert t.flag == "mixed-scale"
    for cell in (t.rvol20, t.vs_50d, t.off_high, t.vs_iwm, t.ret_7d, t.ret_30d, t.adv_usd_20d):
        assert (cell.value, cell.note) == (None, "flagged: mixed-scale")
    assert (t.buys_30d, t.buyers_30d) == (2, 1)
    assert got.bars == () and got.cap.gap == "flagged"


# ---------------------------------------------------------------------------------------------------------
# 7. the payload
# ---------------------------------------------------------------------------------------------------------


def _url(ref: str, cik: str | None) -> str | None:
    return f"https://example.test/{cik}/{ref}" if cik else None


def _board(cands, **kw):
    base = dict(
        asof=ASOF,
        clock="public",
        arms={},
        tapes={},
        roster_tickers={},
        master_tickers={},
        company_names={},
        ciks={},
        iwm_bars=IWM,
        calendar=CAL,
        calendar_source="benchmarks",
        window_start=None,
        url_of=_url,
        counts={"armed": 1, "warming": 0, "managing": 0, "incubating": 0, "no_call": 0},
        live_cards=3,
        cards_not_yet_created=0,
        roster_fallback_cards=0,
        cards_counted=1,
    )
    return assemble(cands, **{**base, **kw})


def _ok_tape(**over) -> CompanyTape:
    t = Tape(
        rvol20=replace(Tape().rvol20, value=1.5),
        vs_iwm=replace(Tape().vs_iwm, value=2.0),
        buys_30d=0,
        buyers_30d=0,
    )
    return CompanyTape(replace(t, **over), CapCell(value=5e8, gap=None), tuple(BARS))


def test_a_row_carries_the_calls_fields_the_clocks_the_return_and_the_tape():
    trigger = trig(
        A,
        "Insider cluster buy",
        Kind.INSIDER,
        d(20),
        grade=Grade.CORE,
        sources=[
            Provenance(source="form4", ref="0001234567-26-000001"),
            Provenance(source="price", ref="price:AAA:2026-10-20"),
        ],
    )
    member = mem(
        A,
        verdict=Verdict.CORE_ENTRY,
        entry_grade=Grade.CORE,
        confidence=0.62,
        arm_until=d(30),
        exit_by=date(2026, 12, 1),
        entry_window_closing=True,
        lapsing=False,
        triggers=[trigger],
    )
    card = cc(
        State.ARMED,
        armed_members=[member],
        risk_signals=[
            trig(A, "Close below the 200d", Kind.BREAKDOWN),
            trig(B, "x", Kind.DILUTION_RISK),
        ],
    )
    cands = candidates({T1: "Card A"}, {T1: rec(card)})
    board = _board(
        cands,
        arms={(T1, A): ArmNight(d(20), False)},
        tapes={A: _ok_tape()},
        roster_tickers={(T1, A): "AAA"},
        company_names={A: "Alpha Co"},
        ciks={A: "0009999999"},
    )
    (row,) = board.rows
    assert (row.kind, row.ticker, row.name, row.key) == ("armed", "AAA", "Alpha Co", f"{T1}:{A}")
    assert (row.verdict, row.entry_grade, row.confidence) == (Verdict.CORE_ENTRY, Grade.CORE, 0.62)
    assert (row.entry_window_closing, row.lapsing) == (True, False)
    assert row.risk_kinds == (Kind.BREAKDOWN,)  # only the risk signal that names this security
    (t,) = row.triggers
    assert (t.label, t.kind, t.grade, t.event_date) == (
        "Insider cluster buy",
        Kind.INSIDER,
        Grade.CORE,
        d(20),
    )
    # an accession links through the issuer's CIK; a price reference carries the link rule's answer for it
    assert [(s.source, s.ref) for s in t.sources] == [
        ("form4", "0001234567-26-000001"),
        ("price", "price:AAA:2026-10-20"),
    ]
    assert t.sources[0].url == "https://example.test/0009999999/0001234567-26-000001"
    assert (
        row.clocks.arm_night == d(20)
        and row.clocks.sessions_since == 5
        and row.clocks.sessions_left == 2
    )
    assert row.since_arm.ret_pct == 19.05 and row.since_arm.iwm_pct == 2.49
    assert row.tape.rvol20.value == 1.5 and row.cap.value == 5e8
    assert (board.calendar_sessions, board.calendar_first, board.calendar_last) == (
        len(CAL),
        CAL[0],
        ASOF,
    )
    assert board.no_ticker_rows == 0 and board.flagged == ()


def test_a_triggers_source_links_through_the_triggers_own_issuer_cik():
    # a trigger can sit on another security than the row's (a theme conviction names the basket's own name)
    other = trig(
        B,
        "Insider cluster buy",
        Kind.INSIDER,
        sources=[Provenance(source="form4", ref="0001234567-26-000002")],
    )
    card = cc(State.ARMED, armed_members=[mem(A, triggers=[other])])
    cands = candidates({T1: "Card A"}, {T1: rec(card)})
    board = _board(cands, ciks={A: "0001111111", B: "0002222222"})
    (source,) = board.rows[0].triggers[0].sources
    assert source.url == "https://example.test/0002222222/0001234567-26-000002"


def test_a_name_on_two_cards_is_two_rows_each_pointing_at_the_other():
    recorded = {
        T1: rec(cc(State.ARMED, tid=T1, armed_members=[mem(A, verdict=Verdict.CORE_ENTRY)])),
        T2: rec(cc(State.ARMED, tid=T2, armed_members=[mem(A, verdict=Verdict.FLIP_ONLY), mem(B)])),
        T3: rec(
            cc(State.WARMING, tid=T3, triggers_fired=[trig(A, "Insider cluster buy", Kind.INSIDER)])
        ),
    }
    cands = candidates(NAMES, recorded)
    board = _board(cands, arms={(T1, A): ArmNight(d(20), False), (T2, A): ArmNight(d(26), False)})
    rows = {(r.thesis_name, r.kind, r.security_id): r for r in board.rows}
    assert [x.thesis_name for x in rows[("Card A", "armed", A)].also_armed] == ["Card B"]
    assert [x.thesis_name for x in rows[("Card B", "armed", A)].also_armed] == ["Card A"]
    assert rows[("Card B", "armed", B)].also_armed == ()
    # a Warming row of a name armed elsewhere points at both cards that arm it
    assert [x.thesis_name for x in rows[("Card C", "warming", A)].also_armed] == [
        "Card A",
        "Card B",
    ]
    # the call fields differ per card
    assert rows[("Card A", "armed", A)].verdict == Verdict.CORE_ENTRY
    assert rows[("Card B", "armed", A)].verdict == Verdict.FLIP_ONLY


def test_a_name_with_no_roster_ticker_keeps_its_row_under_the_masters_or_its_id():
    cands = candidates(
        {T1: "Card A"}, {T1: rec(cc(State.ARMED, armed_members=[mem(A), mem(B), mem(C)]))}
    )
    board = _board(cands, roster_tickers={(T1, A): "AAA"}, master_tickers={B: "BBB", C: None})
    assert [r.ticker for r in board.rows] == ["AAA", "BBB", str(C)]
    assert board.no_ticker_rows == 1


def test_a_roster_ticker_wins_over_the_masters_because_it_is_the_cockpits_name_key():
    cands = candidates({T1: "Card A"}, {T1: rec(cc(State.ARMED, armed_members=[mem(A)]))})
    board = _board(cands, roster_tickers={(T1, A): "AAA.B"}, master_tickers={A: "AAA"})
    assert board.rows[0].ticker == "AAA.B"


def test_a_flagged_name_keeps_its_row_with_a_grayed_tape_no_return_and_is_named():
    cands = candidates({T1: "Card A"}, {T1: rec(cc(State.ARMED, armed_members=[mem(A), mem(B)]))})
    flagged = company_tape(
        {},
        None,
        CapCell(gap="flagged", note="mixed-scale price history"),
        flag="mixed-scale price history",
    )
    board = _board(
        cands,
        arms={(T1, A): ArmNight(d(20), False), (T1, B): ArmNight(d(20), False)},
        tapes={A: flagged, B: _ok_tape()},
        roster_tickers={(T1, A): "AAA", (T1, B): "BBB"},
    )
    a, b = board.rows
    assert a.tape.flag == "mixed-scale price history" and a.since_arm.gap == "flagged"
    assert a.tape.rvol20.note == "flagged: mixed-scale price history" and a.cap.gap == "flagged"
    assert b.tape.flag is None and b.since_arm.gap is None
    assert board.flagged == (FlaggedCompany(A, "AAA", "mixed-scale price history"),)


def test_a_company_with_no_tape_read_keeps_its_row_with_blank_cells():
    cands = candidates({T1: "Card A"}, {T1: rec(cc(State.ARMED, armed_members=[mem(A)]))})
    (row,) = _board(cands, arms={(T1, A): ArmNight(d(20), False)}).rows
    assert (
        row.since_arm.gap == "no_bar"
        and row.tape.rvol20.value is None
        and row.cap.gap == "no_price"
    )


def test_watch_and_warming_rows_have_no_arm_night_and_so_no_age_or_return():
    recorded = {
        T1: rec(cc(State.ARMED, tid=T1, armed_members=[mem(A)], watch_members=[mem(B)])),
        T2: rec(cc(State.WARMING, tid=T2, triggers_fired=[trig(C, "8-day breakout")])),
    }
    cands = candidates(NAMES, recorded)
    board = _board(
        cands,
        arms={(T1, A): ArmNight(d(20), False)},
        tapes={A: _ok_tape(), B: _ok_tape(), C: _ok_tape()},
    )
    by = {r.kind: r for r in board.rows}
    for kind in ("watch", "warming"):
        assert by[kind].clocks.arm_night is None and by[kind].clocks.sessions_since is None
        assert by[kind].since_arm.gap == "no_arm_night"
    assert by["armed"].clocks.sessions_since == 5
    assert by["warming"].keys == ("confirmation",)


# ---------------------------------------------------------------------------------------------------------
# 8. the router (the heavy read stubbed; then the real read_board with its DB reads stubbed)
# ---------------------------------------------------------------------------------------------------------


def _hand_board():
    recorded = {
        T1: rec(
            cc(
                State.ARMED,
                armed_members=[
                    mem(A, verdict=Verdict.STARTER_ENTRY, entry_grade=Grade.FLIP, confidence=0.4)
                ],
            )
        )
    }
    return _board(
        candidates({T1: "Card A"}, recorded),
        arms={(T1, A): ArmNight(d(27), True)},
        tapes={A: _ok_tape()},
        roster_tickers={(T1, A): "AAA"},
        company_names={A: "Alpha Co"},
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return _hand_board()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_board", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_serve_view_pair_and_serves_a_second_request_from_the_cache(
    stub_client,
):
    client, seen = stub_client
    first = client.get("/dashboards/hot-board", params={"asof": "2026-06-01"}).json()
    again = client.get("/dashboards/hot-board", params={"asof": "2026-06-01"}).json()
    assert (first["cache"], again["cache"]) == ("computed", "hit") and seen["reads"] == 1
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    from domain.market_time import market_today

    client.get("/dashboards/hot-board", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2
    # a different as-of is its own read
    client.get("/dashboards/hot-board", params={"asof": "2026-06-02"})
    assert seen["reads"] == 3


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/hot-board", params={"asof": "2026-06-01"}).json()
    assert body["benchmark"] == "IWM" and body["clock"] == "public"
    assert (body["cards_armed"], body["cards_no_call"], body["calendar_source"]) == (
        1,
        0,
        "benchmarks",
    )
    (row,) = body["rows"]
    assert row["ticker"] == "AAA" and row["kind"] == "armed" and row["verdict"] == "starter_entry"
    assert row["entry_grade"] == "flip" and row["confidence"] == 0.4
    assert row["clocks"]["arm_night"] == "2026-10-27" and row["clocks"]["arm_reconstructed"] is True
    assert row["since_arm"]["gap"] is None and row["since_arm"]["ret_pct"] is not None
    assert row["tape"]["rvol20"] == {"value": 1.5, "note": None}
    assert row["cap"]["value"] == 5e8 and "heat" not in row  # the page ranks the rows it shows
    assert body["flagged"] == [] and isinstance(body["compute_ms"], int)


def test_the_router_requires_an_asof(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/hot-board").status_code == 422


def test_read_board_threads_one_view_into_every_read_and_never_reads_the_live_call(monkeypatch):
    """``read_board`` itself (the DB reads stubbed): the roster, the flags, the benchmark's bars and every company's
    view all get the request's one ``(known_at, clock)``; the log is read once for the cards, once for the armed
    ones' runs."""
    card = cc(
        State.ARMED,
        armed_members=[mem(A, arm_until=d(30)), mem(B)],
        watch_members=[mem(C)],
    )
    seen: dict = {"roster": [], "recorded": [], "runs": [], "flags": [], "company": [], "bench": []}
    thesis = Thesis(
        id=T1,
        name="Card A",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="AAA", role="x", security_id=A, segment="Core")],
    )

    def fake_cards(conn, asof, known_at):
        seen["roster"].append((asof, known_at))
        return CardsAsOf(2, [thesis], 1, 1)

    def fake_recorded(conn, ids, asof):
        seen["recorded"].append((ids, asof))
        return {T1: rec(card)}

    def fake_runs(conn, ids, asof):
        seen["runs"].append((ids, asof))
        return {T1: [log(d(28), ids=(A, B)), log(d(27), ids=(A,))]}

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flags"].append((set(ids), asof, known_at, clock))
        return {B: "mixed-scale price history"}

    class FakePit:
        def benchmark_prices(self, symbol, lookback_days=None):
            seen["bench"].append((symbol, lookback_days))
            return [{"d": x, "close": 200.0 + i} for i, x in enumerate(weekdays(d(19), ASOF))]

    def fake_pit(conn, asof, known_at, clock):
        seen["pit_view"] = (asof, known_at, clock)
        return FakePit()

    def fake_per_company(conn, sids, asof, known_at, clock, read):
        seen["company"].append((list(sids), asof, known_at, clock))
        return {
            sid: CompanyTape(
                Tape(flag="mixed-scale price history") if sid == B else Tape(),
                CapCell(gap="no_price"),
                tuple(b for b in BARS) if sid == A else (),
            )
            for sid in sids
        }

    CACHE.clear()
    monkeypatch.setattr(router_mod, "cards_as_of", fake_cards)
    monkeypatch.setattr(router_mod, "read_recorded", fake_recorded)
    monkeypatch.setattr(router_mod, "read_runs", fake_runs)
    monkeypatch.setattr(router_mod, "_benchmark_pit", fake_pit)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod, "per_company", fake_per_company)
    monkeypatch.setattr(router_mod, "display_members", lambda names, who: {})
    monkeypatch.setattr(
        router_mod.master, "tickers_for", lambda conn, ids, **kw: {A: "AAA", B: "BBB", C: "CCC"}
    )
    monkeypatch.setattr(
        router_mod.master, "identity_for", lambda conn, ids, **kw: {A: {"name": "Alpha Co"}}
    )
    monkeypatch.setattr(router_mod.master, "ciks_for", lambda conn, ids, **kw: {})
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        body = TestClient(app).get("/dashboards/hot-board", params={"asof": "2026-10-28"}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    asof, known_at = seen["roster"][0]
    assert asof == ASOF and known_at is not None
    assert seen["pit_view"] == (ASOF, known_at, "public")
    assert seen["flags"][0][1:] == (ASOF, known_at, "public") and seen["flags"][0][0] == {A, B, C}
    assert seen["company"][0][1:] == (ASOF, known_at, "public") and seen["company"][0][0] == sorted(
        [A, B, C], key=str
    )
    assert seen["recorded"] == [([T1], ASOF)] and seen["runs"] == [([T1], ASOF)]
    assert {s for s, _ in seen["bench"]} == {"IWM"}

    rows = {r["ticker"]: r for r in body["rows"]}
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert (
        rows["AAA"]["clocks"]["arm_night"] == "2026-10-27"
        and rows["BBB"]["clocks"]["arm_night"] == "2026-10-28"
    )
    assert rows["CCC"]["kind"] == "watch" and rows["CCC"]["clocks"]["arm_night"] is None
    assert rows["BBB"]["tape"]["flag"] == "mixed-scale price history"
    assert body["flagged"] == [
        {"security_id": str(B), "ticker": "BBB", "reason": "mixed-scale price history"}
    ]
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (2, 1, 1)
    assert body["roster_fallback_cards"] == 1 and body["cards_armed"] == 1
    # the calendar is IWM's bars (its last bar is the as-of), the sessions since the arm night count from it
    assert body["calendar_source"] == "benchmarks" and body["calendar_last"] == "2026-10-28"
    assert rows["AAA"]["clocks"]["sessions_since"] == 1


# ---------------------------------------------------------------------------------------------------------
# 9. DB-backed: the real calls log and the real price tape (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_DB_DAY0 = date(2026, 9, 1)


def _security(db, ticker: str, *, etf: bool = False) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                ticker,
                "0009876543",
                "2026-01-01",
                "etf" if etf else "equity",
            ),
        )
    return sid


def _bar(db, sid: UUID, day: date, close: float) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": day,
            "close": close,
            "volume": 1000.0,
            "valid_from": day,
            "recorded_at": datetime.combine(day, time.min, tzinfo=timezone.utc),
        },
    )


def _tape(db, sid: UUID, base: float) -> None:
    """A close of ``base + days since 2026-09-01`` on every weekday through 2026-10-28."""
    for day in weekdays(_DB_DAY0, ASOF):
        _bar(db, sid, day, base + (day - _DB_DAY0).days)


def _card(db, name: str, members: list[tuple[str, UUID]]) -> UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker=t, role="the name", security_id=s, segment="Core")
            for t, s in members
        ],
    )
    thesis_repo.upsert(db, thesis)
    with (
        db.cursor() as cur
    ):  # the card existed on every as-of below (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    return thesis.id


def _record(db, tid: UUID, night: date, state: State, armed: list[UUID], **kw) -> None:
    card = cc(
        state,
        tid=tid,
        asof=night,
        armed_members=[
            mem(s, verdict=Verdict.CORE_ENTRY, entry_grade=Grade.CORE, confidence=0.5)
            for s in armed
        ],
    )
    calls_repo.append(db, card, DEFAULT_TENANT_ID, **kw)


def _get(db, asof: date) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/hot-board", params={"asof": asof.isoformat()})
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _shape(body: dict) -> list[tuple]:
    return [
        (r["thesis_name"], r["ticker"], r["kind"], r["clocks"]["arm_night"]) for r in body["rows"]
    ]


@pytest.fixture
def seeded(db):
    """Card A: armed on 09-14 and 09-15 with AAA, BBB joining on 09-16, Warming from 09-22. Card B: first armed
    (CCC) on 09-20. A tape for all three and an IWM tape, running past every as-of below."""
    aaa, bbb, ccc = _security(db, "AAA"), _security(db, "BBB"), _security(db, "CCC")
    iwm = _security(db, "IWM", etf=True)
    for sid, base in ((aaa, 20.0), (bbb, 40.0), (ccc, 60.0), (iwm, 200.0)):
        _tape(db, sid, base)
    card_a = _card(db, "Card A", [("AAA", aaa), ("BBB", bbb)])
    card_b = _card(db, "Card B", [("CCC", ccc)])
    _record(db, card_a, date(2026, 9, 14), State.ARMED, [aaa])
    _record(db, card_a, date(2026, 9, 15), State.ARMED, [aaa])
    _record(db, card_a, date(2026, 9, 16), State.ARMED, [aaa, bbb])
    _record(db, card_a, date(2026, 9, 22), State.WARMING, [])
    _record(db, card_b, date(2026, 9, 20), State.ARMED, [ccc])
    db.commit()
    return {"aaa": aaa, "bbb": bbb, "ccc": ccc, "a": card_a, "b": card_b}


def test_db_a_card_armed_after_the_as_of_has_no_rows_and_is_counted_as_no_call_yet(db, seeded):
    body = _get(db, date(2026, 9, 15))
    assert _shape(body) == [("Card A", "AAA", "armed", "2026-09-14")]
    assert (body["cards_counted"], body["cards_armed"], body["cards_no_call"]) == (2, 1, 1)
    # once its first night is on or before the as-of, the card is a row source
    later = _get(db, date(2026, 9, 21))
    assert [(r[0], r[1]) for r in _shape(later)] == [
        ("Card A", "AAA"),
        ("Card A", "BBB"),
        ("Card B", "CCC"),
    ]
    assert later["cards_no_call"] == 0 and later["cards_armed"] == 2


def test_db_a_member_added_after_the_as_of_is_not_a_row_then_and_has_its_own_arm_night_after(
    db, seeded
):
    on_the_15th = _get(db, date(2026, 9, 15))
    assert {r["ticker"] for r in on_the_15th["rows"]} == {"AAA"}
    on_the_17th = _get(db, date(2026, 9, 17))
    arms = {
        r["ticker"]: r["clocks"]["arm_night"]
        for r in on_the_17th["rows"]
        if r["thesis_name"] == "Card A"
    }
    assert arms == {
        "AAA": "2026-09-14",
        "BBB": "2026-09-16",
    }  # BBB joined on the 16th: its own, later arm night


def test_db_a_night_recorded_after_the_as_of_is_not_read(db, seeded):
    # the 22nd's Warming row has no armed members; read before it, Card A is still armed
    assert {(r["thesis_name"], r["ticker"]) for r in _get(db, date(2026, 9, 21))["rows"]} >= {
        ("Card A", "AAA"),
        ("Card A", "BBB"),
    }
    after = _get(db, date(2026, 9, 23))
    assert [r["thesis_name"] for r in after["rows"]] == [
        "Card B"
    ]  # Card A is Warming now, with no trigger
    assert after["cards_warming"] == 1


def test_db_a_price_printed_after_the_as_of_is_never_read(db, seeded):
    body = _get(db, date(2026, 9, 15))
    (row,) = body["rows"]
    s = row["since_arm"]
    # AAA closes 20 + days since 09-01: the 14th is 33.0, the 15th 34.0; the tape runs on to 10-28
    assert (s["entry_date"], s["entry_close"], s["last_date"], s["last_close"]) == (
        "2026-09-14",
        33.0,
        "2026-09-15",
        34.0,
    )
    assert s["ret_pct"] == round((34.0 / 33.0 - 1) * 100, 2)
    assert s["iwm_pct"] == round((214.0 / 213.0 - 1) * 100, 2)
    assert row["clocks"]["sessions_since"] == 1  # IWM's bars after the 14th through the 15th
    assert body["calendar_last"] == "2026-09-15" and body["calendar_source"] == "benchmarks"


def test_db_a_reconstructed_arm_night_is_marked(db):
    aaa = _security(db, "AAA")
    _tape(db, aaa, 20.0)
    card = _card(db, "Card A", [("AAA", aaa)])
    _record(db, card, date(2026, 9, 14), State.ARMED, [aaa], reconstructed=True)
    _record(db, card, date(2026, 9, 15), State.ARMED, [aaa])
    db.commit()
    (row,) = _get(db, date(2026, 9, 16))["rows"]
    assert row["clocks"]["arm_night"] == "2026-09-14" and row["clocks"]["arm_reconstructed"] is True


def test_db_an_archived_card_and_an_empty_log_give_an_honest_empty_page(db):
    card = _card(db, "Card A", [])
    thesis_repo.set_archived(db, card, True)
    _card(db, "Card B", [])
    db.commit()
    body = _get(db, date(2026, 9, 16))
    assert body["rows"] == [] and body["cards_counted"] == 1 and body["cards_no_call"] == 1
    assert body["calendar_source"] == "none" and body["calendar_sessions"] == 0
