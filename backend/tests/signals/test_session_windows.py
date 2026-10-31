"""F4 — a BAR-DRIVEN window counted in market sessions (the dormant ``<family>_alpha_liveness_unit`` dials).

What is proved here, against the REAL detectors and the real assembler:

* **The unit math.** ``signals.common.window_end`` is the one place a window ends. Under ``"sessions"`` a fire
  gives exactly N sessions to act in whatever weekday it fired on (MEASURED weekday dependence of today's
  calendar windows: a 10-day window gives 8 sessions from a Monday fire and 6 from a Friday one), a window
  never ends on a weekend, and a market holiday inside it is not counted.
* **F3 applies unchanged.** A trigger is live on night N while the first session after N is on or before
  the window end, so the last live night is the night before the window's last session.
* **DORMANT is today, byte for byte.** Under the defaults every window is the pre-F4 expression
  (``fire + days``), and a card built through the new seam with the dials spelled out is identical to the
  production card. (The wider claim is carried by the goldens and the replay sweeps passing unmodified.)
* **The families do what they say.** The volume breakout, the 52-week breakout, the laggard and the flip
  breakdown (which reads the breakout's window, never its own) each honor their family's unit.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from backtest.config_overlay import apply_overlay
from calls.assembler import assemble_call, sessions_left
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade
from domain.trading_days import is_scheduled_day, next_scheduled_day, nth_scheduled_day_after
from ingest.prices.eod_loader import parse_yahoo_chart
from signals import breakdown, breakout_52w, laggard, volume_breakout
from signals.common import (
    BarWindow,
    breakout_52w_window,
    breakout_window,
    entry_signal_is_live,
    laggard_window,
    window_end,
)
from tests.calls.factories import SID, insider_event, make_thesis
from tests.signals.test_laggard import (
    _B,
    _B_LAG,
    _C_UP,
    _LEADER,
    _LEADER_UP,
    _FakePit,
    _leader_breakout,
    _thesis,
)
from tests.signals.test_laggard import _C as _COMOVER


def _sessions_cfg(family: str, n: int):
    return apply_overlay(
        {f"{family}_alpha_liveness_unit": "sessions", f"{family}_alpha_liveness_sessions": n}
    )


def _acted_on(win: BarWindow, fire: date) -> list[date]:
    """The sessions the operator could act in: the next session after every night the trigger is live."""
    nights = [fire + timedelta(days=i) for i in range(0, 120)]
    return sorted({next_scheduled_day(n) for n in nights if win.is_live(fire, n)})


_MON, _FRI = date(2026, 10, 19), date(2026, 10, 23)


# --- the unit math ------------------------------------------------------------------------------------


def test_calendar_days_depend_on_the_fire_weekday_today():
    """The problem F4 answers, on the one calendar: a 10-day window gives 8 sessions from a Monday fire and
    6 from a Friday one (the plan's §4 table)."""
    win = BarWindow(days=10)
    assert len(_acted_on(win, _MON)) == 8
    assert len(_acted_on(win, _FRI)) == 6


@pytest.mark.parametrize("n", [1, 5, 7, 10])
def test_a_session_window_gives_n_sessions_whatever_the_weekday(n):
    win = BarWindow(days=10, sessions=n)
    for fire in (_MON, _FRI, date(2026, 10, 21)):  # Monday, Friday, Wednesday
        acted = _acted_on(win, fire)
        assert len(acted) == n, fire
        # and they are exactly the N sessions after the fire, in order
        assert acted == [nth_scheduled_day_after(fire, k) for k in range(1, n + 1)]


def test_a_session_window_never_ends_on_a_weekend():
    calendar_ends_on_a_weekend = False
    fire = date(2026, 1, 5)
    while fire < date(2026, 12, 18):
        if is_scheduled_day(fire):
            for n in range(1, 13):
                assert is_scheduled_day(BarWindow(days=10, sessions=n).end(fire)), (fire, n)
            calendar_ends_on_a_weekend |= BarWindow(days=10).end(fire).weekday() >= 5
        fire += timedelta(days=1)
    assert (
        calendar_ends_on_a_weekend  # the calendar unit does (Wed + 10 = Sat): the contrast is real
    )


def test_a_holiday_inside_the_window_is_not_counted():
    """Wed 2026-11-25, the day before Thanksgiving: 5 sessions are Fri 27, Mon 30, Tue 1, Wed 2, Thu 3 —
    the closed Thursday is skipped, so the window runs one calendar day longer than it would without it.
    """
    win = BarWindow(days=10, sessions=5)
    fire = date(2026, 11, 25)
    assert win.end(fire) == date(2026, 12, 3)
    assert date(2026, 11, 26) not in _acted_on(win, fire)
    assert len(_acted_on(win, fire)) == 5
    # Juneteenth 2026 is a Friday: from Wed 06-17, 2 sessions are Thu 18 and Mon 22
    assert BarWindow(days=10, sessions=2).end(date(2026, 6, 17)) == date(2026, 6, 22)


def test_the_window_end_has_one_definition():
    fire = _FRI
    assert window_end(fire, 10) == fire + timedelta(days=10)
    assert window_end(fire, 10, sessions=7) == nth_scheduled_day_after(fire, 7)
    win = BarWindow(days=10, sessions=7)
    assert win.end(fire) == window_end(fire, 10, sessions=7)
    assert fire + timedelta(days=win.span_days(fire)) == win.end(fire)


# --- F3, unchanged ------------------------------------------------------------------------------------


def test_f3_the_last_live_night_is_the_night_before_the_last_session():
    """Fri 10-23 + 5 sessions ends Fri 10-30. Live through Thu 10-29 (its next session IS the end); the end
    night itself is not (its next session, Mon 11-02, is outside); the weekend in between is live.
    """
    win = BarWindow(days=10, sessions=5)
    fire = _FRI
    assert win.end(fire) == date(2026, 10, 30)
    assert win.is_live(fire, date(2026, 10, 24))  # Saturday -> next session Mon 10-26
    assert win.is_live(fire, date(2026, 10, 29))
    assert not win.is_live(fire, date(2026, 10, 30))
    # and the test is F3's own function, fed the span
    for night in (date(2026, 10, 29), date(2026, 10, 30)):
        assert win.is_live(fire, night) == entry_signal_is_live(fire, win.span_days(fire), night)


def test_f3_a_one_session_window_is_live_only_until_its_one_session():
    win = BarWindow(days=10, sessions=1)
    assert _acted_on(win, _FRI) == [
        date(2026, 10, 26)
    ]  # Fri fire -> act Monday; the weekend is live
    assert win.is_live(_FRI, date(2026, 10, 25))
    assert not win.is_live(_FRI, date(2026, 10, 26))


def test_entry_window_sessions_left_reads_the_same_end():
    """``sessions_left`` (the "entry window closing" flag) counts to ``arm_until``; on the fire night a
    session window has exactly N left, from any weekday."""
    for fire in (_MON, _FRI):
        win = BarWindow(days=10, sessions=7)
        assert sessions_left(fire, fire + timedelta(days=win.span_days(fire))) == 7


# --- dormant == today ---------------------------------------------------------------------------------


def test_the_calendar_unit_is_the_pre_f4_expression_on_every_night():
    """Under the defaults every family's window is ``fire + days`` and its liveness is F3's pre-F4 test,
    for every fire date and night across a year with holidays in it."""
    for win in (breakout_window(DEFAULT_CONFIG), breakout_52w_window(DEFAULT_CONFIG)) + (
        laggard_window(DEFAULT_CONFIG),
    ):
        assert win.sessions is None
        fire = date(2025, 12, 1)
        while fire < date(2026, 2, 1):
            assert win.span_days(fire) == win.days
            for k in range(0, 60, 3):
                night = fire + timedelta(days=k)
                old = next_scheduled_day(night) <= fire + timedelta(days=win.days)
                assert win.is_live(fire, night) == old
            fire += timedelta(days=1)


_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_HIMS = parse_yahoo_chart(
    json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
)


def _hims_through(d: date) -> list[dict]:
    return [b for b in _HIMS if b["d"] <= d]


def test_a_card_through_the_new_seam_is_the_production_card():
    """The HIMS M3 case (real EOD): insider conviction + the real volume-breakout event, assembled under
    the production config and under the dormant dials spelled out — byte-identical."""
    explicit = DEFAULT_CONFIG.model_copy(
        update={
            "breakout_alpha_liveness_unit": "calendar",
            "breakout_alpha_liveness_sessions": None,
            "breakout_52w_alpha_liveness_unit": "calendar",
            "breakout_52w_alpha_liveness_sessions": None,
            "laggard_alpha_liveness_unit": "calendar",
            "laggard_alpha_liveness_sessions": None,
        }
    )
    for night in (date(2026, 6, 2), date(2026, 6, 4)):
        cards = []
        for cfg in (DEFAULT_CONFIG, explicit):
            ev = volume_breakout.score(_hims_through(night), SID, night, cfg)
            assert ev is not None and ev.alpha_liveness_days == 10
            cards.append(assemble_call(make_thesis(), [insider_event(), ev], night, cfg))
        assert cards[0].model_dump_json() == cards[1].model_dump_json()


# --- the families do what they say --------------------------------------------------------------------


def test_the_volume_breakout_counts_its_window_in_sessions():
    """HIMS's last tape bar, Thu 2026-06-04, is a breakout. Calendar 10 days ends Sun 06-14, so it is dead
    by Fri 06-12's night (next session Mon 06-15); 7 sessions end Mon 06-15, so it is still live then and
    the card's ``arm_until`` is that Monday."""
    night = date(2026, 6, 12)
    assert volume_breakout.score(_hims_through(night), SID, night, DEFAULT_CONFIG) is None
    cfg = _sessions_cfg("breakout", 7)
    ev = volume_breakout.score(_hims_through(night), SID, night, cfg)
    assert ev is not None and ev.asof == date(2026, 6, 4)
    assert ev.alpha_liveness_days == 11  # the session window, as its calendar span
    card = assemble_call(make_thesis(), [insider_event(), ev], night, cfg)
    (member,) = [m for m in card.armed_members if m.security_id == SID]
    assert member.arm_until == date(2026, 6, 15)


def _session_bars(closes: list[float], end: date) -> list[dict]:
    """Ascending bars on SESSIONS only (no weekend or holiday dates), the last one on ``end``."""
    dates: list[date] = []
    d = end
    while len(dates) < len(closes):
        if is_scheduled_day(d):
            dates.append(d)
        d -= timedelta(days=1)
    return [{"d": dd, "close": c} for dd, c in zip(reversed(dates), closes, strict=True)]


def _nights_after(day: date, count: int) -> list[date]:
    out, night = [], day
    for _ in range(count):
        night = next_scheduled_day(night)
        out.append(night)
    return out


def _flip_nights(cfg, breakout_day: date, nights: list[date]) -> list[date]:
    """The nights on which the flip breakdown fires for a base-100 tape that breaks out to 115 on
    ``breakout_day`` and closes at 95 on every session after it."""
    fired = []
    for k, night in enumerate(nights, start=1):
        bars = _session_bars([100.0] * 20 + [115.0] + [95.0] * k, night)
        assert bars[20]["d"] == breakout_day
        if breakdown.flip_score(bars, SID, night, cfg) is not None:
            fired.append(night)
    return fired


def test_the_flip_breakdown_reads_the_breakout_s_window_not_its_own():
    """A Friday breakout (2026-10-09) given back from the next session on. The flip de-arm fires on
    exactly the nights the BREAKOUT's window is live, on either unit: 5 on calendar days (10 days end Mon
    10-19), 6 on the breakout family's 7 sessions (ending Tue 10-20; the fire night itself closed above the
    base, so it is the 6 nights after it). It never counts on a clock of its own."""
    fri = date(2026, 10, 9)
    nights = _nights_after(fri, 15)
    for cfg, expected in ((DEFAULT_CONFIG, 5), (_sessions_cfg("breakout", 7), 6)):
        win = breakout_window(cfg)
        fired = _flip_nights(cfg, fri, nights)
        assert fired == [n for n in nights if win.is_live(fri, n)]
        assert len(fired) == expected


def _tape_52w(night: date, breakout_day: date) -> list[dict]:
    """A flat year on session bars, a heavy-volume year-high on ``breakout_day``, then quiet bars below it
    (never a fresher 52-week high) through ``night``."""
    after = sum(1 for n in _nights_after(breakout_day, 200) if n <= night)
    closes = [100.0 + (i % 7) * 0.1 for i in range(260)] + [130.0] + [125.0] * after
    vols = [1000.0] * 260 + [5000.0] + [1000.0] * after
    bars = [dict(b, volume=v) for b, v in zip(_session_bars(closes, night), vols, strict=True)]
    assert bars[260]["d"] == breakout_day
    return bars


def test_the_52_week_breakout_counts_its_window_in_sessions():
    """Fri 2026-06-05's year-high. 28 sessions end Fri 07-17 (Juneteenth and July 3 are skipped); 45
    calendar days end Mon 07-20. So on 07-17's night the calendar window is still live (next session
    07-20) and the 28-session window is not."""
    fri = date(2026, 6, 5)
    cal = breakout_52w.score(_tape_52w(fri, fri), SID, fri, DEFAULT_CONFIG)
    assert cal is not None and cal.asof == fri and cal.alpha_liveness_days == 45
    cfg = _sessions_cfg("breakout_52w", 28)
    ses = breakout_52w.score(_tape_52w(fri, fri), SID, fri, cfg)
    assert ses is not None and ses.asof == fri and ses.grade is Grade.CORE
    assert nth_scheduled_day_after(fri, 28) == date(2026, 7, 17)
    assert ses.alpha_liveness_days == (date(2026, 7, 17) - fri).days

    night = date(2026, 7, 17)
    late = breakout_52w.score(_tape_52w(night, fri), SID, night, DEFAULT_CONFIG)
    assert late is not None and late.asof == fri
    assert breakout_52w.score(_tape_52w(night, fri), SID, night, cfg) is None


def test_the_laggard_counts_its_window_in_sessions_from_the_leader_s_breakout():
    """The leader broke out Tue 2026-06-02 (the factory's fire date): 10 sessions end Tue 06-16, a 14-day
    span; on calendar days the span is the 15-day dial."""
    pit = _FakePit({_LEADER: _LEADER_UP, _B: _B_LAG, _COMOVER: _C_UP})

    cal = laggard.detect(pit, _thesis(), [_leader_breakout()], date(2026, 6, 2), DEFAULT_CONFIG)
    assert [e.alpha_liveness_days for e in cal] == [15]
    cfg = _sessions_cfg("laggard", 10)
    (ev,) = laggard.detect(pit, _thesis(), [_leader_breakout()], date(2026, 6, 2), cfg)
    assert ev.asof == date(2026, 6, 2)
    assert ev.alpha_liveness_days == 14
    assert ev.asof + timedelta(days=ev.alpha_liveness_days) == date(2026, 6, 16)


def test_a_disclosure_window_is_never_counted_in_sessions():
    """The operator's scope: the insider conviction keeps its calendar window under every F4 dial."""
    cfg = apply_overlay(
        {
            "breakout_alpha_liveness_unit": "sessions",
            "breakout_alpha_liveness_sessions": 5,
            "breakout_52w_alpha_liveness_unit": "sessions",
            "breakout_52w_alpha_liveness_sessions": 20,
            "laggard_alpha_liveness_unit": "sessions",
            "laggard_alpha_liveness_sessions": 5,
        }
    )
    assert cfg.insider_core_alpha_liveness_days == DEFAULT_CONFIG.insider_core_alpha_liveness_days
    assert cfg.insider_flip_alpha_liveness_days == DEFAULT_CONFIG.insider_flip_alpha_liveness_days
    assert cfg.activist_13d_liveness_days == DEFAULT_CONFIG.activist_13d_liveness_days
