"""Entry Windows (``docs/dashboards/entry-windows.md``): the current Armed run on hand-built log rows (a same-night
re-run with a higher ``seq`` wins, a reconstructed night marked, a run broken by a Warming night starts over, a
Managing card), a member's own arm night, sessions counted on the IWM bar calendar (a weekend and a holiday do not
count), the since-arm return per name and the median with its n (an unpriced and a flagged name counted, not
averaged), the catalysts inside the window (hollow when passed), a card with no clock, the axis, the sparkline; the
router off one cached read; and, DB-backed, a card armed after the as-of is not a row, a night recorded after the
as-of is not read, and a price published after the as-of stays invisible (skips without Postgres).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import entry_windows as router_mod
from dashboards.cache import CACHE
from dashboards.entry_windows import (
    AXIS_LEAD_FALLBACK_DAYS,
    AXIS_LEAD_SESSIONS,
    NightRow,
    armed_run,
    axis_bounds,
    catalysts_in_window,
    entry_clock,
    entry_windows,
    lookback_days,
    member_arm_nights,
    member_clocks,
    night_winners,
    row_since,
    sessions_between,
    since_arm,
    spark,
    weekdays_between,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Grade, Kind, State, Verdict
from domain.market_time import market_today
from domain.signal import Provenance
from domain.thesis import BasketMember, Catalyst, Segment, Thesis
from repositories import calls_repo, thesis_repo
from securities.benchmarks import seed_benchmarks

TID, TID2 = uuid.uuid4(), uuid.uuid4()
A, B, C = (uuid.uuid4() for _ in range(3))
ASOF = date(2026, 10, 27)  # a Tuesday

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def nr(asof: date, state: State, seq: int = 1, reconstructed: bool = False) -> NightRow:
    return NightRow(asof, seq, state.value, reconstructed)


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


def weekdays(start: date, end: date) -> list[date]:
    """Mon-Fri days, start..end: a stand-in for IWM's bar calendar (no holidays unless a test drops one)."""
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def tape(days: list[date], closes: list[float]) -> dict[date, float]:
    return dict(zip(days, closes))


def trig(sid: UUID, label: str = "8-day breakout", **kw) -> TriggerRef:
    kw.setdefault("kind", Kind.TECHNICAL_BREAKOUT)
    kw.setdefault("event_date", d(20))
    return TriggerRef(label=label, security_id=sid, **kw)


def mem(sid: UUID, **kw) -> MemberCall:
    return MemberCall(security_id=sid, **kw)


def card(
    state: State = State.ARMED,
    *,
    tid: UUID = TID,
    asof: date = ASOF,
    members: tuple[UUID, ...] = (A,),
    **kw,
) -> CallCard:
    kw.setdefault("armed_members", [mem(s) for s in members])
    return CallCard(
        thesis_id=tid,
        asof=asof,
        state=state,
        verdict=Verdict.NOT_YET,
        expression="",
        key_conviction=KeyState(turned=True, label="Conviction"),
        key_confirmation=KeyState(turned=True, label="Confirmation"),
        **kw,
    )


TICKERS = {A: "AAA", B: "BBB", C: "CCC"}


# ---------------------------------------------------------------------------------------------------------
# the current run
# ---------------------------------------------------------------------------------------------------------


def test_the_arm_night_is_the_first_night_of_the_current_armed_run():
    rows = [
        nr(d(5), State.WARMING),
        nr(d(8), State.ARMED),
        nr(d(12), State.ARMED),
        nr(d(20), State.ARMED),
    ]
    run = armed_run(rows)
    assert run is not None and run.state == "armed"
    assert run.first == d(8) and run.last == d(20)
    assert [n.asof for n in run.nights] == [d(8), d(12), d(20)]


def test_a_same_night_rerun_with_a_higher_seq_wins_the_night():
    # the night's last row is Armed: the night is Armed
    up = armed_run([nr(d(8), State.WARMING, seq=1), nr(d(8), State.ARMED, seq=2)])
    assert up is not None and up.first == d(8) and up.nights[0].seq == 2
    # the night's last row is Warming: the run is broken and the night is not armed
    down = [
        nr(d(7), State.ARMED, seq=1),
        nr(d(8), State.ARMED, seq=2),
        nr(d(8), State.WARMING, seq=3),
    ]
    assert armed_run(down) is None
    # rows arrive in any order; the highest seq still wins
    assert [r.seq for r in night_winners(reversed(down))] == [1, 3]


def test_a_run_broken_by_a_warming_night_starts_over():
    rows = [
        nr(d(1), State.ARMED),
        nr(d(5), State.WARMING),
        nr(d(9), State.ARMED),
        nr(d(14), State.ARMED),
    ]
    run = armed_run(rows)
    assert run is not None and run.first == d(9)  # not d(1): the earlier run ended on d(5)


def test_a_reconstructed_night_counts_as_a_night_and_is_marked():
    rows = [
        nr(d(8), State.ARMED, reconstructed=True),
        nr(d(9), State.ARMED, reconstructed=True),
        nr(d(12), State.ARMED),
    ]
    run = armed_run(rows)
    assert run is not None and run.first == d(8) and len(run.nights) == 3
    assert [n.reconstructed for n in run.nights] == [True, True, False]


def test_a_card_not_armed_or_managing_in_effect_has_no_run():
    assert armed_run([]) is None
    assert armed_run([nr(d(8), State.ARMED), nr(d(12), State.WARMING)]) is None
    assert armed_run([nr(d(8), State.INCUBATING)]) is None


def test_a_managing_card_takes_the_first_armed_night_of_its_span():
    rows = [
        nr(d(1), State.WARMING),
        nr(d(3), State.MANAGING),  # before the Armed night: not part of the run
        nr(d(6), State.ARMED),
        nr(d(9), State.ARMED),
        nr(d(14), State.MANAGING),
        nr(d(20), State.MANAGING),
    ]
    run = armed_run(rows)
    assert run is not None and run.state == "managing"
    assert run.first == d(6) and run.last == d(20)
    assert [n.asof for n in run.nights] == [d(6), d(9), d(14), d(20)]


def test_a_managing_card_with_no_armed_night_in_its_span_has_no_run():
    assert armed_run([nr(d(1), State.WARMING), nr(d(5), State.MANAGING)]) is None


def test_a_member_that_joins_an_armed_card_later_gets_its_own_arm_night():
    got = member_arm_nights(
        [
            (d(12), [A, B]),
            (d(8), [A]),  # out of order on purpose
            (d(20), [A, B, C]),
        ]
    )
    assert got == {A: d(8), B: d(12), C: d(20)}


def test_a_member_that_drops_out_and_returns_keeps_its_first_night():
    got = member_arm_nights([(d(8), [A, B]), (d(9), [A]), (d(12), [A, B])])
    assert got[B] == d(8)


# ---------------------------------------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------------------------------------

# Labor Day 2026 is Mon 2026-09-07: IWM has no bar that day, nor on the weekend
LABOR = [x for x in weekdays(date(2026, 8, 31), date(2026, 9, 15)) if x != date(2026, 9, 7)]


def test_sessions_are_counted_on_the_bar_calendar_a_weekend_and_a_holiday_do_not_count():
    # Thu 09-03 -> Tue 09-08: Fri 09-04 and Tue 09-08 (Sat, Sun and Labor Day have no bar)
    assert sessions_between(LABOR, date(2026, 9, 3), date(2026, 9, 8)) == 2
    # the earlier date is excluded, the later included
    assert sessions_between(LABOR, date(2026, 9, 4), date(2026, 9, 8)) == 1
    assert sessions_between(LABOR, date(2026, 9, 8), date(2026, 9, 8)) == 0
    # an arm night that is itself a weekend: the next session is the first one in
    assert sessions_between(LABOR, date(2026, 9, 5), date(2026, 9, 9)) == 2
    assert sessions_between(LABOR, date(2026, 9, 9), date(2026, 9, 3)) == 0


def test_sessions_ahead_are_the_mon_fri_days_after_the_asof_through_arm_until():
    # Fri 10-23 -> Wed 10-28: Mon, Tue, Wed
    assert weekdays_between(d(23), d(28)) == 3
    assert weekdays_between(d(28), d(28)) == 0
    assert weekdays_between(d(28), d(23)) == 0


def test_the_entry_clock_is_sessions_left_while_open_and_sessions_since_it_closed_after():
    cal = weekdays(d(1), d(27))
    assert entry_clock(cal, ASOF, d(30)) == (3, None)  # Wed, Thu, Fri
    assert entry_clock(cal, ASOF, d(28)) == (1, None)
    assert entry_clock(cal, ASOF, ASOF) == (None, 0)  # its last day: closed after the session
    assert entry_clock(cal, ASOF, d(22)) == (None, 3)  # the bars of 10-23, 10-26 and 10-27
    assert entry_clock(cal, ASOF, None) == (None, None)


# ---------------------------------------------------------------------------------------------------------
# since the arm night
# ---------------------------------------------------------------------------------------------------------


def test_since_arm_runs_from_the_first_close_on_or_after_the_arm_night_to_the_last_on_or_before_the_asof():
    ds = weekdays(d(19), d(27))  # Mon 10-19 .. Tue 10-27
    closes = tape(ds, [9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    got = since_arm(closes, d(21), ASOF)  # the arm night is a session: its own close is the entry
    assert got is not None
    assert (got.entry_on, got.entry_close, got.last_on, got.last_close) == (
        d(21),
        11.0,
        d(27),
        15.0,
    )
    assert got.ret == pytest.approx((15 / 11 - 1) * 100) and got.above is True
    # a weekend arm night enters on the next bar
    sat = since_arm(closes, d(24), ASOF)
    assert sat is not None and sat.entry_on == d(26)
    # a bar after the as-of is never read
    assert since_arm({**closes, d(28): 99.0}, d(21), ASOF).last_close == 15.0
    # a close at the arm-night close is not above it
    flat = since_arm({d(26): 10.0, d(27): 10.0}, d(26), ASOF)
    assert flat is not None and flat.ret == 0.0 and flat.above is False


def test_since_arm_is_none_with_no_bar_on_or_after_the_arm_night_and_skips_a_bad_close():
    assert since_arm({d(20): 10.0}, d(21), ASOF) is None
    assert since_arm({}, d(21), ASOF) is None
    got = since_arm(
        {d(21): 0.0, d(22): 8.0, d(23): 6.0}, d(21), ASOF
    )  # a non-positive close is not a bar
    assert got is not None and got.entry_on == d(22) and got.ret == pytest.approx(-25.0)


def test_the_median_has_its_n_and_an_unpriced_and_a_flagged_name_are_counted_not_averaged():
    ds = weekdays(d(19), d(27))
    closes = {
        A: tape(ds, [10, 10, 10, 10, 10, 10, 11]),  # +10%
        B: tape(ds, [10, 10, 10, 10, 10, 10, 9]),  # -10%
        C: {d(2): 5.0},  # no bar on or after the arm night
        uuid.UUID(int=4): tape(ds, [10, 10, 10, 10, 10, 10, 99]),  # flagged: never read
    }
    flagged_sid = uuid.UUID(int=4)
    bench = {"IWM": tape(ds, [100] * 6 + [101]), "SPY": tape(ds, [100] * 6 + [98])}
    s = row_since(
        [A, B, C, flagged_sid],
        closes,
        {flagged_sid: "mixed-scale price history"},
        bench,
        d(19),
        ASOF,
    )
    assert (s.names, s.priced, s.flagged, s.no_bar, s.above) == (4, 2, 1, 1, 1)
    assert s.median == pytest.approx(0.0)  # median(+10, -10), not the mean with the flagged 890%
    assert (s.iwm, s.spy) == (pytest.approx(1.0), pytest.approx(-2.0))


def test_a_row_with_no_priced_name_has_no_median_never_zero():
    s = row_since([A], {}, {}, {}, d(19), ASOF)
    assert (s.names, s.priced, s.no_bar) == (1, 0, 1)
    assert s.median is None and s.iwm is None and s.spy is None


# ---------------------------------------------------------------------------------------------------------
# catalysts, the axis, the sparkline
# ---------------------------------------------------------------------------------------------------------


def cat(
    label: str, when: date | None, kind: str | None = "earnings", cid: UUID | None = None
) -> Catalyst:
    return Catalyst(id=cid or uuid.uuid4(), label=label, kind=kind, when_date=when)


def test_catalysts_inside_the_window_are_ticks_hollow_when_before_the_asof():
    start, end = d(15), d(30)
    ticks = catalysts_in_window(
        [
            cat("before the arm night", d(14)),
            cat("passed", d(20)),
            cat("the as-of day", ASOF),
            cat("ahead", d(29)),
            cat("on the hold end", d(30)),
            cat("after the hold end", d(31)),
            cat("undated", None),
        ],
        start,
        end,
        ASOF,
    )
    assert [(t.label, t.past) for t in ticks] == [
        ("passed", True),
        ("the as-of day", False),
        ("ahead", False),
        ("on the hold end", False),
    ]


def test_a_catalyst_is_hollow_when_dated_before_the_countdown_origin():
    """F2: the night is Tuesday 10-27 and a live view counts from Wednesday 10-28, the action day: a catalyst dated
    the night itself has passed by then (hollow), one dated the action day is still ahead (it reads 'today').
    """
    cs = [cat("the night", ASOF), cat("the action day", d(28)), cat("later", d(29))]
    live = catalysts_in_window(cs, d(15), d(30), d(28))
    assert [(t.label, t.past) for t in live] == [
        ("the night", True),
        ("the action day", False),
        ("later", False),
    ]
    past = catalysts_in_window(cs, d(15), d(30), ASOF)  # a past view counts from its own as-of
    assert [t.past for t in past] == [False, False, False]


def test_the_hold_days_count_from_the_origin_and_the_sessions_stay_on_the_asof():
    """F2: the hold horizon (Grid: 11-14) is 18 days from the night 10-27 and 17 from the action day 10-28; the entry
    window's session counts are the call path's own and do not move."""
    base, _ = page()
    live, _ = page(origin=d(28))
    g0 = next(r for r in base.rows if r.thesis_name == "Grid")
    g1 = next(r for r in live.rows if r.thesis_name == "Grid")
    assert (g0.hold_days, g1.hold_days) == (18, 17)
    assert (g1.sessions_into, g1.sessions_left, g1.closed_ago) == (5, 3, None)
    assert (g1.sessions_into, g1.sessions_left) == (g0.sessions_into, g0.sessions_left)
    assert live.asof == ASOF  # the page's own as-of is the night


def test_a_catalyst_dated_after_a_past_asof_is_a_solid_tick_still_ahead_then():
    c = cat("Oct 29 readout", d(29))
    now = catalysts_in_window([c], d(15), d(30), date(2026, 10, 31))
    then = catalysts_in_window([c], d(15), d(30), ASOF)
    assert [t.past for t in now] == [True] and [t.past for t in then] == [False]


def test_a_catalyst_listed_twice_is_one_tick_and_a_card_with_no_clock_places_none():
    cid = uuid.uuid4()
    twice = [cat("x", d(20), cid=cid), cat("x", d(20), cid=cid)]
    assert len(catalysts_in_window(twice, d(15), d(30), ASOF)) == 1
    assert catalysts_in_window(twice, d(15), None, ASOF) == ()


def test_the_axis_starts_five_sessions_before_the_earliest_arm_night_and_ends_at_the_latest_clock():
    cal = weekdays(d(1), d(27))
    start, end = axis_bounds(cal, [d(14), d(20)], [d(30), None, d(26)], ASOF) or (None, None)
    assert start == cal[cal.index(d(14)) - AXIS_LEAD_SESSIONS] == d(7)
    assert end == d(30)
    # all the clocks are behind the as-of: the as-of is the right edge
    assert axis_bounds(cal, [d(14)], [d(20), None], ASOF) == (d(7), ASOF)
    # a calendar that does not reach back that far: a week before the arm night
    short = weekdays(d(12), d(27))
    assert axis_bounds(short, [d(14)], [], ASOF)[0] == d(14) - timedelta(
        days=AXIS_LEAD_FALLBACK_DAYS
    )
    assert axis_bounds(cal, [], [d(30)], ASOF) is None


def test_the_bar_read_reaches_back_past_the_earliest_arm_night():
    assert lookback_days([d(14), d(20)], ASOF) == 13 + 14
    assert lookback_days([], ASOF) == 14


def test_the_sparkline_runs_from_the_arm_night_to_the_asof_with_iwm_from_the_same_base():
    ds = weekdays(d(19), d(27))
    names = [tape(ds, [10, 10, 11, 11, 12, 12, 12]), tape(ds, [20, 20, 20, 22, 22, 22, 22])]
    iwm = tape(ds, [100, 100, 100, 100, 100, 101, 102])
    got = spark(ds, d(21), names, iwm)
    assert got is not None
    assert got.dates == tuple(ds[2:]) and got.dates[-1] == ASOF
    assert got.line[0] == 100.0 and got.iwm is not None and got.iwm[0] == 100.0
    # 10-22: B steps +10% and A is flat, so the first step is the mean, +5%
    assert got.line[1] == pytest.approx(100 * (1 + (0.0 + 0.1) / 2))
    assert got.iwm[-1] == pytest.approx(102.0)


def test_the_sparkline_is_none_with_no_priced_name_or_fewer_than_two_sessions():
    ds = weekdays(d(19), d(27))
    assert spark(ds, d(21), [], tape(ds, [100] * 7)) is None
    assert (
        spark(ds, ASOF, [tape(ds, [10] * 7)], tape(ds, [100] * 7)) is None
    )  # the arm night is the last session
    # a name with no two consecutive bars on the sessions never steps
    gappy = {d(21): 10.0, d(23): 11.0, ASOF: 12.0}
    assert spark(ds, d(21), [gappy], tape(ds, [100] * 7)) is None


def test_a_flat_name_draws_a_flat_line_not_nothing():
    ds = weekdays(d(19), d(27))
    got = spark(ds, d(21), [tape(ds, [10] * 7)], tape(ds, [100] * 7))
    assert got is not None and got.line == (100.0,) * 5


# ---------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------


def page(**over):
    """Card "Grid" Armed since 10-20 (A from 10-20, B joined 10-23), card "Beta" Warming, "Cobalt" Managing
    from an Armed run, "Delta" Managing with no Armed night, "Echo" with no call yet."""
    ds = weekdays(d(12), ASOF)
    t_grid, t_beta, t_cobalt, t_delta, t_echo = (uuid.uuid4() for _ in range(5))
    cards = [
        (t_grid, "Grid"),
        (t_beta, "beta"),
        (t_cobalt, "Cobalt"),
        (t_delta, "Delta"),
        (t_echo, "Echo"),
    ]
    nights = {
        t_grid: [nr(d(8), State.WARMING), nr(d(20), State.ARMED), nr(d(23), State.ARMED)],
        t_beta: [nr(d(8), State.WARMING)],
        t_cobalt: [nr(d(14), State.ARMED), nr(d(22), State.MANAGING)],
        t_delta: [nr(d(8), State.WARMING), nr(d(22), State.MANAGING)],
    }
    form4 = Provenance(source="form4", ref="0001234567-26-000123")
    px = Provenance(source="price", ref="price:AAA:2026-10-19")
    recorded = {
        (t_grid, d(20)): card(tid=t_grid, asof=d(20), members=(A,)),
        (t_grid, d(23)): card(
            tid=t_grid,
            asof=d(23),
            arm_until=d(30),
            exit_by=d(14, 11),
            confidence=0.62,
            catalyst_surface=[cat("readout", d(29)), cat("passed", d(21))],
            armed_members=[
                mem(
                    A,
                    entry_window_closing=True,
                    triggers=[trig(A, sources=[form4, px], grade=Grade.CORE)],
                ),
                mem(B),
            ],
        ),
        (t_cobalt, d(14)): card(tid=t_cobalt, asof=d(14), members=(C,)),
        (t_cobalt, d(22)): card(
            State.MANAGING, tid=t_cobalt, asof=d(22), members=(C,), exit_by=d(14, 11)
        ),
    }
    args = dict(
        cards=cards,
        nights=nights,
        recorded=recorded,
        tickers=TICKERS,
        ciks={A: "0000123456"},
        closes={
            A: tape(ds, [10.0] * 6 + [11.0] * 4 + [12.0, 13.0]),  # 11.0 from the arm night (10-20)
            B: tape(ds, [20.0] * 12),
            C: tape(ds, [5.0] * 12),
        },
        flagged={},
        bench={"IWM": tape(ds, [100.0] * 11 + [103.0]), "SPY": tape(ds, [400.0] * 11 + [404.0])},
        asof=ASOF,
        live_cards=6,
        not_yet_created=1,
    )
    args.update(over)
    return entry_windows(**args), (t_grid, t_cobalt)


def test_only_armed_and_managing_cards_are_rows_and_every_other_card_is_counted():
    got, (t_grid, t_cobalt) = page()
    assert [(r.thesis_name, r.state) for r in got.rows] == [
        ("Cobalt", "managing"),
        ("Grid", "armed"),
    ]
    c = got.counts
    assert (c.live_cards, c.cards_counted, c.cards_not_yet_created) == (6, 5, 1)
    assert (c.armed, c.managing, c.managing_without_arm_night) == (1, 1, 1)
    assert (c.warming, c.incubating, c.no_call) == (1, 0, 1)
    # nothing is dropped: the states account for every counted card
    assert (
        c.armed + c.managing + c.managing_without_arm_night + c.warming + c.incubating + c.no_call
        == c.cards_counted
    )


def test_a_row_carries_the_arm_night_the_clocks_the_sessions_and_the_flags():
    got, (t_grid, _) = page()
    row = next(r for r in got.rows if r.thesis_id == t_grid)
    assert (row.arm_night, row.in_effect, row.run_nights) == (d(20), d(23), 2)
    assert (row.arm_until, row.exit_by, row.confidence) == (d(30), d(14, 11), 0.62)
    # IWM bars after Tue 10-20 through Tue 10-27: 21, 22, 23, 26, 27
    assert row.sessions_into == 5
    # Wed 10-28, Thu, Fri 10-30 (Mon-Fri days after the as-of)
    assert (row.sessions_left, row.closed_ago) == (3, None)
    assert row.hold_days == 18
    assert (row.entry_window_closing, row.lapsing, row.reconstructed) == (True, False, False)
    assert [(t.label, t.past) for t in row.catalysts] == [("passed", True), ("readout", False)]


def test_a_rows_flags_are_true_when_any_armed_member_s_flag_is_true():
    """Operator decision 2026-10-30: the second name's window closing marks the card, not only the headline's."""
    ds = weekdays(d(12), ASOF)

    def one(*members: MemberCall):
        t = uuid.uuid4()
        got, _ = page(
            cards=[(t, "Pair")],
            nights={t: [nr(d(20), State.ARMED)]},
            recorded={(t, d(20)): card(tid=t, asof=d(20), armed_members=list(members))},
            closes={A: tape(ds, [10.0] * 12), B: tape(ds, [20.0] * 12)},
        )
        (row,) = got.rows
        return row

    # the headline (A) is quiet; B alone closing marks the card
    row = one(mem(A), mem(B, entry_window_closing=True))
    assert (row.entry_window_closing, row.lapsing) == (True, False)
    # B alone lapsing (the hold clock) marks the other flag the same way
    row = one(mem(A), mem(B, lapsing=True))
    assert (row.entry_window_closing, row.lapsing) == (False, True)
    # both flags, on different members
    row = one(mem(A, lapsing=True), mem(B, entry_window_closing=True))
    assert (row.entry_window_closing, row.lapsing) == (True, True)
    # no armed member carries either flag: neither is set
    row = one(mem(A), mem(B))
    assert (row.entry_window_closing, row.lapsing) == (False, False)


def test_a_marked_row_carries_each_flagged_members_own_clock_counted_to_its_own_arm_until():
    """The card's figure counts to the headline name's clock; a second name's window is its own."""
    ds = weekdays(d(12), ASOF)
    t = uuid.uuid4()

    def one(*members: MemberCall, arm_until=d(30)):
        got, _ = page(
            cards=[(t, "Pair")],
            nights={t: [nr(d(20), State.ARMED)]},
            recorded={
                (t, d(20)): card(
                    tid=t, asof=d(20), arm_until=arm_until, armed_members=list(members)
                )
            },
            closes={A: tape(ds, [10.0] * 12), B: tape(ds, [20.0] * 12)},
        )
        (row,) = got.rows
        return row

    # the headline (A, to Fri 10-30) is quiet; B's own window ends Wed 10-28: one session left for B, three for the card
    row = one(mem(A, arm_until=d(30)), mem(B, arm_until=d(28), entry_window_closing=True))
    assert (row.sessions_left, row.entry_window_closing) == (3, True)
    (clock,) = row.member_clocks
    assert (clock.security_id, clock.ticker, clock.headline) == (B, "BBB", False)
    assert (clock.entry_window_closing, clock.lapsing) == (True, False)
    assert (clock.arm_until, clock.sessions_left, clock.closed_ago) == (d(28), 1, None)
    # no member closing or lapsing: the row carries no member clock
    assert one(mem(A), mem(B)).member_clocks == ()


def test_member_clocks_list_the_flagged_members_in_ranked_order_each_name_once():
    cal = weekdays(d(12), ASOF)  # the as-of is Tue 10-27
    members = [
        mem(A, arm_until=d(28), entry_window_closing=True),  # the headline: one session left
        mem(B),  # no flag: not listed
        mem(C, arm_until=d(30), lapsing=True),  # lapsing only: no entry figure
        mem(B, arm_until=d(29), entry_window_closing=True),  # B's first FLAGGED entry
        mem(B, arm_until=d(26), entry_window_closing=True, lapsing=True),  # a repeat of B: ignored
    ]
    got = member_clocks(members, TICKERS, cal, ASOF)
    assert [(c.ticker, c.headline) for c in got] == [("AAA", True), ("CCC", False), ("BBB", False)]
    a, c, b = got
    assert (a.entry_window_closing, a.lapsing, a.arm_until, a.sessions_left) == (
        True,
        False,
        d(28),
        1,
    )
    # a lapsing-only name states no entry clock of its own
    assert (c.entry_window_closing, c.lapsing) == (False, True)
    assert (c.arm_until, c.sessions_left, c.closed_ago) == (None, None, None)
    # B: Wed 10-28 and Thu 10-29 are the Mon-Fri days after the as-of through its own arm_until
    assert (b.arm_until, b.sessions_left, b.closed_ago, b.lapsing) == (d(29), 2, None, False)
    assert member_clocks([], TICKERS, cal, ASOF) == ()


def test_a_member_clock_follows_the_card_rule_on_the_last_day_and_after_the_window_closed():
    cal = weekdays(d(12), ASOF)
    last = member_clocks([mem(A, arm_until=ASOF, entry_window_closing=True)], TICKERS, cal, ASOF)
    assert (last[0].sessions_left, last[0].closed_ago) == (None, 0)  # the window's last day
    shut = member_clocks([mem(A, arm_until=d(23), entry_window_closing=True)], TICKERS, cal, ASOF)
    # IWM sessions after Fri 10-23 through the as-of: Mon 10-26 and Tue 10-27
    assert (shut[0].sessions_left, shut[0].closed_ago) == (None, 2)
    no_clock = member_clocks([mem(A, entry_window_closing=True)], TICKERS, cal, ASOF)
    assert (no_clock[0].arm_until, no_clock[0].sessions_left, no_clock[0].closed_ago) == (None,) * 3
    # a name without a ticker is still listed
    anon = member_clocks([mem(UUID(int=7), lapsing=True)], TICKERS, cal, ASOF)
    assert (anon[0].ticker, anon[0].lapsing) == (None, True)


def test_each_armed_member_has_its_own_arm_night_its_triggers_and_its_own_return():
    got, (t_grid, _) = page()
    row = next(r for r in got.rows if r.thesis_id == t_grid)
    a, b = row.members
    assert (a.ticker, a.arm_night, b.ticker, b.arm_night) == ("AAA", d(20), "BBB", d(23))
    (t,) = a.triggers
    assert (t.label, t.kind, t.grade, t.event_date) == (
        "8-day breakout",
        Kind.TECHNICAL_BREAKOUT,
        Grade.CORE,
        d(20),
    )
    # a Form 4 accession links through the issuer CIK; a price reference stays text
    assert [(s.source, s.url) for s in t.sources] == [
        ("form4", "https://www.sec.gov/Archives/edgar/data/123456/000123456726000123/"),
        ("price", None),
    ]
    # AAA: first close on or after 10-20 is 11.0 (10-20 is the 7th bar: 10 flat to 10-19, 11 from 10-20)
    assert a.entry_on == d(20) and a.since == pytest.approx((13 / 11 - 1) * 100) and a.above is True
    assert b.entry_on == d(23) and b.since == pytest.approx(0.0) and b.above is False


def test_the_row_median_uses_the_card_arm_night_and_states_its_n():
    got, (t_grid, _) = page()
    row = next(r for r in got.rows if r.thesis_id == t_grid)
    s = row.since
    assert (s.names, s.priced, s.flagged, s.no_bar, s.above) == (2, 2, 0, 0, 1)
    assert s.median == pytest.approx(((13 / 11 - 1) * 100 + 0.0) / 2)
    assert s.iwm == pytest.approx(3.0) and s.spy == pytest.approx(1.0)
    assert row.spark is not None and row.spark.dates[-1] == ASOF


def test_a_flagged_name_is_out_of_every_number_named_and_counted():
    got, (t_grid, _) = page(flagged={B: "mixed-scale price history"})
    row = next(r for r in got.rows if r.thesis_id == t_grid)
    assert (row.since.priced, row.since.flagged) == (1, 1)
    flagged_member = next(m for m in row.members if m.security_id == B)
    assert flagged_member.since is None and flagged_member.flag == "mixed-scale price history"
    assert [(f.ticker, f.reason) for f in got.flagged] == [("BBB", "mixed-scale price history")]
    assert (
        got.counts.armed_names == 3
        and got.counts.priced_names == 2
        and got.counts.unpriced_names == 1
    )


def test_a_card_with_no_arm_until_has_no_entry_clock_and_one_with_no_exit_by_has_no_hold_clock():
    got, _ = page()
    cobalt = next(r for r in got.rows if r.thesis_name == "Cobalt")
    assert cobalt.arm_until is None
    assert (cobalt.sessions_left, cobalt.closed_ago) == (None, None)
    assert cobalt.exit_by == d(14, 11) and cobalt.hold_days == 18
    t = uuid.uuid4()
    open_ended, _ = page(
        cards=[(t, "Open")],
        nights={t: [nr(d(20), State.ARMED)]},
        recorded={
            (t, d(20)): card(tid=t, asof=d(20), arm_until=d(30), catalyst_surface=[cat("x", d(29))])
        },
        closes={A: tape(weekdays(d(12), ASOF), [10.0] * 12)},
    )
    (row,) = open_ended.rows
    assert (row.exit_by, row.hold_days) == (None, None) and row.arm_until == d(30)
    assert row.sessions_left == 3
    # with no hold clock the entry window bounds the catalysts
    assert [c.label for c in row.catalysts] == ["x"]


def test_a_closed_entry_window_counts_the_sessions_since_it_closed():
    t = uuid.uuid4()
    closed, _ = page(
        cards=[(t, "Shut")],
        nights={t: [nr(d(14), State.ARMED)]},
        recorded={(t, d(14)): card(tid=t, asof=d(14), arm_until=d(22))},
        closes={A: tape(weekdays(d(12), ASOF), [10.0] * 12)},
    )
    (row,) = closed.rows
    # IWM bars after Thu 10-22 through Tue 10-27: 23, 26, 27
    assert (row.sessions_left, row.closed_ago) == (None, 3)


def test_a_reconstructed_arm_night_is_marked_and_counted():
    got, _ = page()
    assert got.counts.reconstructed_arm_nights == 0
    ds = weekdays(d(12), ASOF)
    t = uuid.uuid4()
    one, _ = page(
        cards=[(t, "Rebuilt")],
        nights={t: [nr(d(20), State.ARMED, reconstructed=True), nr(d(23), State.ARMED)]},
        recorded={(t, d(20)): card(tid=t, asof=d(20)), (t, d(23)): card(tid=t, asof=d(23))},
        closes={A: tape(ds, [10.0] * 12)},
    )
    (row,) = one.rows
    assert (row.reconstructed, row.reconstructed_nights, row.run_nights) == (True, 1, 2)
    assert one.counts.reconstructed_arm_nights == 1


def test_an_armed_card_with_no_armed_name_recorded_is_a_row_with_no_chips():
    t = uuid.uuid4()
    got, _ = page(
        cards=[(t, "Quiet")],
        nights={t: [nr(d(20), State.ARMED)]},
        recorded={(t, d(20)): card(tid=t, asof=d(20), armed_members=[])},
        closes={},
    )
    (row,) = got.rows
    assert (
        row.members == ()
        and row.since.names == 0
        and row.since.median is None
        and row.spark is None
    )
    assert (row.entry_window_closing, row.lapsing) == (False, False)


def test_no_rows_is_an_honest_empty_page():
    got = entry_windows(
        [(TID, "Only")],
        {TID: [nr(d(8), State.INCUBATING)]},
        {},
        {},
        {},
        {},
        {},
        {},
        asof=ASOF,
        live_cards=1,
    )
    assert got.rows == () and got.axis_start is None and got.axis_end is None
    assert (
        got.counts.incubating == 1 and got.session_source == "none" and got.calendar_sessions == 0
    )


def test_the_calendar_is_iwm_bars_and_the_page_says_where_it_came_from():
    got, _ = page()
    assert got.session_source == "benchmarks" and got.calendar_last == ASOF
    no_iwm, _ = page(bench={})
    assert no_iwm.session_source == "companies"
    assert no_iwm.rows[0].since.iwm is None


def test_the_axis_spans_five_sessions_before_the_earliest_arm_night_to_the_latest_clock():
    got, _ = page()
    ds = weekdays(d(12), ASOF)
    # the earliest arm night is Cobalt's, 10-14 (Wed): five sessions earlier is Wed 10-07, which is before the
    # calendar's first bar (10-12), so a week before the arm night
    assert ds[0] == d(12)
    assert got.axis_start == d(14) - timedelta(days=AXIS_LEAD_FALLBACK_DAYS)
    assert got.axis_end == d(14, 11)


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, asof, known_at, clock, origin=None):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        seen["origin"] = origin
        got, _ = page(asof=asof)
        return got

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_page", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_the_page_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/entry-windows", params={"asof": ASOF.isoformat()}).json()
    asof, known_at, clock = seen["view"]
    assert asof == ASOF and clock == "public" and known_at is not None
    assert (
        body["cache"] == "computed" and body["asof"] == "2026-10-27" and body["clock"] == "public"
    )
    assert [(r["thesis_name"], r["state"]) for r in body["rows"]] == [
        ("Cobalt", "managing"),
        ("Grid", "armed"),
    ]
    grid = body["rows"][1]
    assert grid["arm_night"] == "2026-10-20" and grid["sessions_left"] == 3
    assert grid["members"][0]["triggers"][0]["kind"] == "technical_breakout"
    assert body["counts"]["no_call"] == 1 and body["session_source"] == "benchmarks"

    again = client.get("/dashboards/entry-windows", params={"asof": ASOF.isoformat()}).json()
    assert again["cache"] == "hit" and seen["reads"] == 1
    # another as-of is its own read
    client.get("/dashboards/entry-windows", params={"asof": "2026-10-26"})
    assert seen["reads"] == 2


def test_the_router_counts_a_live_view_from_the_action_day_and_a_past_one_from_its_asof(
    stub_client, pin_market_clock
):
    """F2: Tuesday 09:00 is Monday's night. The default view of that night is live, so the hold days and a catalyst's
    passed flag count from Tuesday, the action day; a past view counts from itself. The origin is part of the key.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    pin_market_clock(datetime(2026, 10, 27, 9, 0, tzinfo=ZoneInfo("America/New_York")))
    client, seen = stub_client
    client.get("/dashboards/entry-windows", params={"asof": "2026-10-26"})
    assert seen["view"][1:] == (None, "record") and seen["origin"] == date(2026, 10, 27)
    client.get("/dashboards/entry-windows", params={"asof": "2026-10-23"})
    assert seen["view"][2] == "public" and seen["origin"] == date(2026, 10, 23)
    keys = {k for k in CACHE._entries}
    assert ("entry-windows", ("record", date(2026, 10, 27)), date(2026, 10, 26)) in keys
    assert ("entry-windows", ("public", date(2026, 10, 23)), date(2026, 10, 23)) in keys


def test_the_router_reads_a_live_asof_on_the_record_clock(stub_client):
    client, seen = stub_client
    client.get("/dashboards/entry-windows", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record")


def test_the_router_needs_an_asof(stub_client):
    client, _ = stub_client
    assert client.get("/dashboards/entry-windows").status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real calls log, cards and tape (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _security(db, ticker: str, cik: str = "0000123456") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    return sid


def _bar(db, sid: UUID, day: date, close: float, recorded_at: datetime | None = None) -> None:
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
            "recorded_at": recorded_at or datetime.combine(day, time.min, tzinfo=timezone.utc),
        },
    )


def _tape(db, sid: UUID, closes: dict[date, float]) -> None:
    for day, close in closes.items():
        _bar(db, sid, day, close)


def _seed_card(db, name: str, sid: UUID, ticker: str) -> UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker=ticker, role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with (
        db.cursor() as cur
    ):  # the card existed long before the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    return thesis.id


def _record(db, tid: UUID, asof: date, state: State, sid: UUID, **kw) -> None:
    members = [
        mem(
            sid,
            triggers=[trig(sid, sources=[Provenance(source="form4", ref="0001234567-26-000123")])],
        )
    ]
    recon = kw.pop("reconstructed", False)
    calls_repo.append(
        db,
        card(
            state, tid=tid, asof=asof, armed_members=members if state != State.WARMING else [], **kw
        ),
        DEFAULT_TENANT_ID,
        reconstructed=recon,
    )


def _get(db, asof: date) -> dict:
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        r = TestClient(app).get("/dashboards/entry-windows", params={"asof": asof.isoformat()})
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert r.status_code == 200, r.text
    return r.json()


def _world(db):
    """Card "Grid" with one name (ticker WLD1): Warming 10-05, Armed 10-12 (re-run the same night with a later
    arm_until), Armed 10-15 (a reconstructed night, a later arm_until again), then a Warming night 10-26. IWM, SPY
    and the name trade every weekday 10-05 .. 10-28, each stepping up a point a day from 200, 600 and 10.
    """
    sid = _security(db, "WLD1")
    ids = seed_benchmarks(db)
    days = weekdays(date(2026, 10, 5), date(2026, 10, 28))
    _tape(db, sid, {x: 10.0 + i for i, x in enumerate(days)})
    for symbol, base in (("IWM", 200.0), ("SPY", 600.0)):
        _tape(db, ids[symbol], {x: base + i for i, x in enumerate(days)})
    tid = _seed_card(db, "Grid", sid, "WLD1")
    _record(db, tid, d(5), State.WARMING, sid)
    _record(db, tid, d(12), State.ARMED, sid, arm_until=d(21), exit_by=d(6, 11))
    _record(
        db, tid, d(12), State.ARMED, sid, arm_until=d(22), exit_by=d(6, 11)
    )  # a re-run: the higher seq wins
    _record(db, tid, d(15), State.ARMED, sid, arm_until=d(23), exit_by=d(6, 11), reconstructed=True)
    _record(db, tid, d(26), State.WARMING, sid)
    db.commit()
    return tid, sid


def test_db_a_card_armed_after_the_asof_is_not_a_row(db):
    tid, _ = _world(db)
    before = _get(db, d(8))  # Warming then; the Armed night 10-12 is after it
    assert before["rows"] == []
    assert (before["counts"]["warming"], before["counts"]["armed"]) == (1, 0)
    on = _get(db, d(12))
    (row,) = on["rows"]
    assert row["thesis_id"] == str(tid) and row["arm_night"] == "2026-10-12"
    assert row["sessions_into"] == 0 and row["in_effect"] == "2026-10-12"


def test_db_a_night_recorded_after_the_asof_is_not_read(db):
    _world(db)
    early = _get(db, d(14))
    (row,) = early["rows"]
    # 10-12's last row is read (the re-run, arm_until 10-22), not the 10-15 row recorded after the as-of
    assert row["in_effect"] == "2026-10-12" and row["arm_until"] == "2026-10-22"
    assert row["reconstructed"] is False and row["run_nights"] == 1
    later = _get(db, d(19))
    (row,) = later["rows"]
    assert row["in_effect"] == "2026-10-15" and row["arm_until"] == "2026-10-23"
    assert (row["reconstructed"], row["reconstructed_nights"], row["run_nights"]) == (False, 1, 2)
    assert row["arm_night"] == "2026-10-12"  # a same-night re-run does not move the arm night
    # the Warming night 10-26 is after both as-ofs; read at 10-27 it ends the run
    after = _get(db, d(27))
    assert after["rows"] == [] and after["counts"]["warming"] == 1


def test_db_the_tape_the_sessions_and_the_links_come_off_the_real_reads(db):
    _world(db)
    body = _get(db, d(19))
    (row,) = body["rows"]
    # IWM trades every weekday: bars after Mon 10-12 through Mon 10-19 are 13, 14, 15, 16, 19
    assert row["sessions_into"] == 5
    assert (row["sessions_left"], row["closed_ago"]) == (4, None)  # Tue 20 .. Fri 23
    assert body["session_source"] == "benchmarks" and body["calendar_last"] == "2026-10-19"
    # WLD1 closes 10 + n for the n-th weekday from 10-05: 10-12 is the 6th (15), 10-19 the 11th (20)
    (m,) = row["members"]
    assert (
        m["ticker"] == "WLD1" and m["arm_night"] == "2026-10-12" and m["entry_on"] == "2026-10-12"
    )
    assert m["since"] == pytest.approx((20 / 15 - 1) * 100) and m["above"] is True
    assert row["since"]["median"] == pytest.approx((20 / 15 - 1) * 100)
    assert row["since"]["iwm"] == pytest.approx((210 / 205 - 1) * 100)  # 200 + 5 .. 200 + 10
    assert row["since"]["spy"] == pytest.approx((610 / 605 - 1) * 100)
    (t,) = m["triggers"]
    assert (
        t["sources"][0]["url"]
        == "https://www.sec.gov/Archives/edgar/data/123456/000123456726000123/"
    )
    assert row["spark"]["dates"][0] == "2026-10-12" and row["spark"]["dates"][-1] == "2026-10-19"
    assert (row["hold_days"], body["axis_end"]) == (18, "2026-11-06")
    assert body["counts"]["cards_counted"] == 1 and body["flagged"] == []


def test_db_a_price_published_after_the_asof_stays_invisible(db):
    """The as-of's own bar is re-versioned a week later at double the close: the past view's return still uses the
    ORIGINAL close (a re-version counts from its own recorded time, so the correction was not public by the end of
    that day)."""
    _, sid = _world(db)
    _bar(
        db, sid, d(19), 40.0, recorded_at=datetime(2026, 10, 26, tzinfo=timezone.utc)
    )  # corrected later
    db.commit()
    (row,) = _get(db, d(19))["rows"]
    (m,) = row["members"]
    assert m["since"] == pytest.approx((20 / 15 - 1) * 100)  # not 40/15
    assert row["spark"]["dates"][-1] == "2026-10-19"


def test_db_the_view_is_read_only_the_log_is_unchanged(db):
    _world(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        before = cur.fetchone()["n"]
    _get(db, d(19))
    _get(db, d(12))
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        assert cur.fetchone()["n"] == before
