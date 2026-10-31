"""Q14 — the ENTRY-WINDOW flag (``MemberCall.entry_window_closing``) and the HOLD-clock flag (``lapsing``).

``lapsing`` follows the conviction's ``exit_by`` (the hold clock, ``headline_lapsing_soon_days``) and was
labeled "entry window closing" in the UI — wrong for 69% of arm-window expiries (MEASURED, 2026-10-16
audit A3). Q14 relabels it "hold clock closing" (meaning unchanged, pinned below) and adds a SEPARATE
flag on the real entry window, ``arm_until``: on once ``entry_window_closing_sessions`` (2) or fewer
TRADING sessions remain after the card's asof, counted on the ONE session calendar — Mon-Fri minus the
NYSE's full-day closures since F5 (``domain.trading_days.is_scheduled_day``, which ``pipeline.schedule``
imports — one definition).

REAL SHAPES. Every event set below is a recorded armed member read off the PROD call-of-record
(read-only, 2026-10-18): the trigger kinds, grades and fire dates are the card's own, the liveness
windows are the live dials, and each test first asserts the reconstruction reproduces the RECORDED
``arm_until`` / ``exit_by`` / ``lapsing`` before it asserts the new flag — so the flag is proven on a
date it genuinely occurred, not on a hand-tuned fixture.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import date, timedelta

import pytest

from calls import assembler
from calls.assembler import assemble_call, sessions_left
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State, Verdict
from domain.signal import Provenance, SignalEvent
from domain.thesis import BasketMember, Position, Thesis
from signals.activist_stake import DETECTOR_NAME as ACTIVIST_DETECTOR
from signals.revenue_acceleration import DETECTOR_NAME as REVENUE_ACCEL_DETECTOR

_CFG = DEFAULT_CONFIG


def _sid(ticker: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"q14:{ticker}")


def _thesis(*tickers: str) -> Thesis:
    return Thesis(
        id=uuid.uuid5(uuid.NAMESPACE_URL, "q14:thesis:" + ",".join(tickers)),
        name="Q14 real-shape thesis",
        narrative="Reconstructed from the recorded call-of-record.",
        basket=[BasketMember(ticker=t, role="member", security_id=_sid(t)) for t in tickers],
    )


def _revenue_accel(ticker: str, fired: date) -> SignalEvent:
    """Key 1 as recorded: the revenue re-acceleration screen (Kind.CATALYST, core, 180d)."""
    return SignalEvent(
        detector=REVENUE_ACCEL_DETECTOR,
        security_id=_sid(ticker),
        role=Role.ENTRY_TRIGGER,
        fired=True,
        kind=Kind.CATALYST,
        grade=Grade.CORE,
        score=0.6,
        label=f"Revenue growth re-accelerated ({ticker})",
        alpha_liveness_days=_CFG.revenue_accel_alpha_liveness_days,
        provenance=[Provenance(source="xbrl", ref=f"revenue:{ticker}")],
        asof=fired,
    )


def _activist(ticker: str, fired: date) -> SignalEvent:
    """Key 1 as recorded: a Schedule 13D activist stake (core, 180d)."""
    return SignalEvent(
        detector=ACTIVIST_DETECTOR,
        security_id=_sid(ticker),
        role=Role.ENTRY_TRIGGER,
        fired=True,
        kind=Kind.ACTIVIST_STAKE,
        grade=Grade.CORE,
        score=0.7,
        label=f"SCHEDULE 13D — activist stake ({ticker})",
        alpha_liveness_days=_CFG.activist_13d_liveness_days,
        provenance=[Provenance(source="edgar", ref=f"13d:{ticker}")],
        asof=fired,
    )


def _breakout(ticker: str, fired: date, grade: Grade = Grade.CORE) -> SignalEvent:
    """Key 2 as recorded: the 8-day volume_breakout (10d entry window)."""
    return SignalEvent(
        detector="volume_breakout",
        security_id=_sid(ticker),
        role=Role.ENTRY_TRIGGER,
        fired=True,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=grade,
        score=0.7 if grade is Grade.CORE else 0.45,
        label=f"breakout ({ticker})",
        alpha_liveness_days=_CFG.breakout_alpha_liveness_days,
        provenance=[Provenance(source="price", ref=f"price:{ticker}:{fired.isoformat()}")],
        asof=fired,
    )


def _member(card, ticker: str):
    (m,) = [m for m in card.armed_members if m.security_id == _sid(ticker)]
    return m


# --- Modern Defense · MTUS (recorded 10-12/10-13/10-15: arm_until Thu 10-15, exit_by 2027-01-31) --------

_MTUS = [_revenue_accel("MTUS", date(2026, 8, 4)), _breakout("MTUS", date(2026, 10, 5))]


def test_mtus_reconstruction_matches_the_record():
    m = _member(assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 13), _CFG), "MTUS")
    assert m.verdict is Verdict.CORE_ENTRY and m.entry_grade is Grade.CORE
    assert m.arm_until == date(2026, 10, 15) and m.exit_by == date(2027, 1, 31)
    assert m.lapsing is False  # as recorded — the hold clock is months out


def test_flag_on_two_trading_days_before_the_window_closes():
    """Tue 10-13 → Wed + Thu remain = 2 sessions → ON (the night the operator gets two sessions' notice)."""
    m = _member(assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 13), _CFG), "MTUS")
    assert sessions_left(date(2026, 10, 13), m.arm_until) == 2
    assert m.entry_window_closing is True


def test_flag_off_three_trading_days_before():
    """Mon 10-12 → Tue + Wed + Thu = 3 sessions → OFF (quiet until it matters, #7)."""
    m = _member(assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 12), _CFG), "MTUS")
    assert sessions_left(date(2026, 10, 12), m.arm_until) == 3
    assert m.entry_window_closing is False


def test_flag_stays_on_through_the_last_live_night():
    """F3: Wed 10-14 is the last armed night — its next session, Thu 10-15, is ``arm_until`` — with 1 session
    left → ON. On Thu 10-15 itself no session of the window is left (the call is recorded after the close), so
    the confirmation is no longer live: not armed at all, no flag to carry. (The record, written before F3,
    shows MTUS Armed on 10-15 with 0 sessions left: the arm nobody could act on that F3 removes.)"""
    m = _member(assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 14), _CFG), "MTUS")
    assert m.arm_until == date(2026, 10, 15) and sessions_left(date(2026, 10, 14), m.arm_until) == 1
    assert m.entry_window_closing is True
    last_day = assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 15), _CFG)
    assert last_day.armed_members == [] and last_day.state is State.WARMING


# --- The Everything Exchange · YYAI (recorded 10-07/10-08: arm_until MONDAY 10-12) ---------------------

_YYAI = [_activist("YYAI", date(2026, 8, 7)), _breakout("YYAI", date(2026, 10, 2))]


def test_friday_to_monday_boundary_counts_trading_days_not_calendar_days():
    """Thu 10-08 with a Monday close: Fri + Mon = 2 SESSIONS (4 calendar days) → ON. A calendar-day
    rule would have stayed off; the weekend is not two chances to act. Wed 10-07 = 3 sessions → OFF.
    """
    thu = _member(assemble_call(_thesis("YYAI"), _YYAI, date(2026, 10, 8), _CFG), "YYAI")
    assert thu.arm_until == date(2026, 10, 12) and thu.exit_by == date(2027, 2, 3)  # as recorded
    assert (thu.arm_until - date(2026, 10, 8)).days == 4
    assert thu.entry_window_closing is True
    wed = _member(assemble_call(_thesis("YYAI"), _YYAI, date(2026, 10, 7), _CFG), "YYAI")
    assert wed.entry_window_closing is False


# --- Modern Defense · TPCS (recorded 10-13..10-16: arm_until SUNDAY 10-18) ------------------------------

_TPCS = [_revenue_accel("TPCS", date(2026, 8, 13)), _breakout("TPCS", date(2026, 10, 8))]


@pytest.mark.parametrize(
    "asof,left,flag",
    [
        (date(2026, 10, 13), 3, False),  # Tue: Wed, Thu, Fri
        (date(2026, 10, 14), 2, True),  # Wed: Thu, Fri
        (date(2026, 10, 15), 1, True),  # Thu: Fri, the window's last SESSION, is the next one (F3)
    ],
)
def test_a_weekend_close_counts_only_the_sessions_before_it(asof, left, flag):
    """arm_until can land on a weekend (a calendar-day liveness window). The weekend adds no session,
    so Friday is the last one, and under F3 Thursday is the last armed night."""
    m = _member(assemble_call(_thesis("TPCS"), _TPCS, asof, _CFG), "TPCS")
    assert m.arm_until == date(2026, 10, 18) and m.exit_by == date(2027, 2, 9)  # as recorded
    assert sessions_left(asof, m.arm_until) == left
    assert m.entry_window_closing is flag


def test_a_weekend_close_is_over_on_the_friday_night():
    """TPCS on Fri 10-16 was the ONE armed member flagged in the measured first-pass churn (the 10-16
    record, 0 sessions left). Under F3 the Friday night's next session is Monday 10-19, past ``arm_until``
    (Sun 10-18), so the breakout is no longer live that night: TPCS is not armed, and no flag shows.
    """
    card = assemble_call(_thesis("TPCS"), _TPCS, date(2026, 10, 16), _CFG)
    assert card.armed_members == [] and card.state is State.WARMING


# --- Rainbow Rush · TSNDF (recorded 09-02/09-03: arm_until = LABOR DAY Mon 2026-09-07) -----------------

_TSNDF = [
    _revenue_accel("TSNDF", date(2026, 3, 12)),
    _breakout("TSNDF", date(2026, 8, 28), grade=Grade.FLIP),
]


def test_a_market_holiday_is_not_a_session_so_the_flag_is_on_time():
    """F5 (2026-10-31, "market holidays are not sessions"): ``is_scheduled_day`` skips the NYSE's full-day
    closures, so Labor Day (Mon 09-07, TSNDF's ``arm_until``) is not counted. Wed 09-02 reads 2 (Thu, Fri)
    → ON, two real sessions before the window ends; Tue 09-01 reads 3 → OFF. Before F5 the calendar counted
    the holiday: Wed read 3 (Thu, Fri, Mon-the-holiday) → OFF and the flag came on one session late.
    """
    wed = _member(assemble_call(_thesis("TSNDF"), _TSNDF, date(2026, 9, 2), _CFG), "TSNDF")
    assert wed.arm_until == date(2026, 9, 7) and wed.exit_by == date(2026, 9, 8)  # as recorded
    assert sessions_left(date(2026, 9, 2), date(2026, 9, 7)) == 2
    assert wed.entry_window_closing is True
    tue = _member(assemble_call(_thesis("TSNDF"), _TSNDF, date(2026, 9, 1), _CFG), "TSNDF")
    assert tue.arm_until == date(2026, 9, 7)  # the same window, one session earlier (computed)
    assert sessions_left(date(2026, 9, 1), date(2026, 9, 7)) == 3
    assert tue.entry_window_closing is False


def test_hold_clock_and_entry_window_are_independent_flags():
    """TSNDF is the mislabel in one row: the record carried ``lapsing=true`` on 09-02 (the HOLD clock —
    exit_by 09-08 is 6 days out). Under the old label the UI said "entry window closing" for the wrong
    clock; now each flag names its own. Since F5 (Labor Day is not a session) its entry window IS in its
    last two sessions on 09-02, so the independence is shown a session earlier: on Tue 09-01 (computed from
    the same recorded event set) the hold clock is closing while the entry window has 3 sessions left.
    """
    rec = _member(assemble_call(_thesis("TSNDF"), _TSNDF, date(2026, 9, 2), _CFG), "TSNDF")
    assert rec.lapsing is True  # as recorded
    m = _member(assemble_call(_thesis("TSNDF"), _TSNDF, date(2026, 9, 1), _CFG), "TSNDF")
    assert m.lapsing is True
    assert m.entry_window_closing is False
    mtus = _member(assemble_call(_thesis("MTUS"), _MTUS, date(2026, 10, 13), _CFG), "MTUS")
    assert mtus.lapsing is False and mtus.entry_window_closing is True  # the converse


# --- `lapsing` keeps its meaning --------------------------------------------------------------------------


@pytest.mark.parametrize("runway,expected", [(44, True), (45, False), (46, False)])
def test_lapsing_meaning_is_unchanged_the_hold_clock_band(runway, expected):
    """``lapsing`` is still exactly ``exit_by - asof < headline_lapsing_soon_days`` on an armed member —
    the hold clock — and does not move with the new dial."""
    asof = date(2026, 10, 13)
    fired = asof + timedelta(days=runway) - timedelta(days=_CFG.revenue_accel_alpha_liveness_days)
    events = [_revenue_accel("MTUS", fired), _breakout("MTUS", date(2026, 10, 5))]
    for dial in (0, 2, 1000):
        cfg = _CFG.model_copy(update={"entry_window_closing_sessions": dial})
        m = _member(assemble_call(_thesis("MTUS"), events, asof, cfg), "MTUS")
        assert (m.exit_by - asof).days == runway
        assert m.lapsing is expected


# --- display only (#4) and quiet outside Armed (#7) -------------------------------------------------------


def test_the_flag_is_display_only_never_a_call_input():
    """Move the dial from never-on (-1) to always-on (1000): the WHOLE card is identical except the flag
    itself — state, verdict, grades, ranking, setup strength, expression, missing, counter-case."""
    thesis = _thesis("MTUS", "TPCS", "YYAI")
    events = _MTUS + _TPCS + _YYAI
    asof = date(2026, 10, 8)
    never = assemble_call(
        thesis, events, asof, _CFG.model_copy(update={"entry_window_closing_sessions": -1})
    )
    always = assemble_call(
        thesis, events, asof, _CFG.model_copy(update={"entry_window_closing_sessions": 1000})
    )
    assert [m.entry_window_closing for m in never.armed_members] == [False] * 3
    assert [m.entry_window_closing for m in always.armed_members] == [True] * 3

    def strip(card):
        d = card.model_dump(mode="json")
        for m in d["armed_members"] + d["watch_members"]:
            m.pop("entry_window_closing")
        return d

    assert strip(never) == strip(always)


def test_a_watch_member_never_carries_the_flag():
    """Confirmation without conviction is a DECAY, not an entry the system endorses — no flag, even on
    its last live night (inverse loudness, #7). Under F3 that is Wed 10-14, the night whose next session
    is its ``arm_until``."""
    card = assemble_call(_thesis("MTUS"), [_MTUS[1]], date(2026, 10, 14), _CFG)
    (w,) = card.watch_members
    assert w.arm_until == date(2026, 10, 15)
    assert w.entry_window_closing is False and w.lapsing is False


def test_a_managing_held_member_never_carries_the_flag():
    """The held name is past its entry: the Managing override clears the entry-window flag (the hold
    clock, which monitoring still reads, is left as computed)."""
    thesis = _thesis("MTUS").model_copy(
        update={
            "position": Position(
                entry_price=20.0, opened_on=date(2026, 10, 6), security_id=_sid("MTUS")
            )
        }
    )
    card = assemble_call(thesis, _MTUS, date(2026, 10, 13), _CFG)
    assert card.state is State.MANAGING
    held = card.armed_members[0]
    assert held.verdict is Verdict.MANAGING and held.entry_window_closing is False


# --- F3: an armed name always has a session left to act in ------------------------------------------------


def test_an_armed_member_always_has_a_session_left_in_its_window():
    """F3 (rule log ``2026-11-entry-window-next-session``): on every night, every armed member's ``arm_until``
    is on or after the first session after the night, so ``sessions_left`` is at least 1 — the "entry window
    lapsed" next to Armed (``arm_until`` == the night, surfaced by F2) cannot be produced. Swept across the
    four recorded shapes above, a weekend end (TPCS), a Monday end (YYAI) and Labor Day (TSNDF) included.
    """
    from domain.trading_days import next_scheduled_day

    thesis = _thesis("MTUS", "TPCS", "YYAI", "TSNDF")
    events = _MTUS + _TPCS + _YYAI + _TSNDF
    armed_nights = 0
    for i in range(80):
        asof = date(2026, 8, 25) + timedelta(days=i)
        card = assemble_call(thesis, events, asof, _CFG)
        for m in card.armed_members + card.watch_members:
            assert m.arm_until is not None and m.arm_until >= next_scheduled_day(asof), (asof, m)
            assert sessions_left(asof, m.arm_until) >= 1, (asof, m)
        if card.state is State.ARMED:
            assert card.arm_until is not None and card.arm_until >= next_scheduled_day(asof)
            armed_nights += 1
    assert armed_nights > 0  # the sweep reached Armed nights


# --- no lookahead: a pure function of the card's own fields ----------------------------------------------


def test_the_flag_is_recomputable_from_the_cards_own_asof_and_window():
    """For every armed member across two weeks of as-ofs, the flag equals
    ``sessions_left(card.asof, member.arm_until) <= dial`` — read off the CARD alone. Nothing else
    (no wall clock, no later data) can enter it."""
    thesis = _thesis("MTUS", "TPCS", "YYAI")
    events = _MTUS + _TPCS + _YYAI
    seen = set()
    for i in range(14):
        asof = date(2026, 10, 5) + timedelta(days=i)
        card = assemble_call(thesis, events, asof, _CFG)
        for m in card.armed_members:
            expected = sessions_left(card.asof, m.arm_until) <= _CFG.entry_window_closing_sessions
            assert m.entry_window_closing is expected
            seen.add(expected)
    assert seen == {True, False}  # the sweep exercised both sides


def test_no_ambient_clock_on_the_flag_path():
    """Structural guard: neither the assembler nor the calendar predicate it borrows reads a clock."""
    from domain import trading_days
    from pipeline import schedule

    for src in (inspect.getsource(assembler), inspect.getsource(trading_days)):
        for needle in ("date.today(", "datetime.now(", "market_today(", "market_now("):
            assert needle not in src, needle
    # ONE definition: the cron schedule reads the very same predicate the call path counts with
    assert schedule.is_scheduled_day is trading_days.is_scheduled_day
    assert assembler.is_scheduled_day is trading_days.is_scheduled_day
