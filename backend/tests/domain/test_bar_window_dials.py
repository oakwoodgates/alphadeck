"""F4 — the DORMANT session-window dials on ``CallConfig`` (operator decision 2026-10-30).

Three claims, each pinned here:

* **DORMANT.** Every bar-driven family's unit defaults to ``"calendar"`` with no session count, so the
  production config is today's (``tests/signals/test_session_windows.py`` proves the card is byte-identical).
* **BOUNDED at config time** (the policy-bounds pattern): a count below 1, a count past the family's cap, a
  ``"sessions"`` unit with no count and a count under ``"calendar"`` all fail at construction or at
  ``apply_overlay`` — never inside a detector on a cron night.
* **THE CAPS ARE HONEST.** At each scanning family's cap, the whole session window still fits inside the
  detector's own default price pull with the bars it needs before a candidate, on the WORST-CASE night of the
  covered calendar. A window longer than the pull would be silently truncated — the failure a sweep must never
  measure — so the cap is checked against the calendar, not asserted.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from backtest.config_overlay import OverlayError, apply_overlay
from domain.config import BAR_WINDOW_FAMILIES, DEFAULT_CONFIG, CallConfig
from domain.trading_days import (
    COVERAGE_END,
    COVERAGE_START,
    is_scheduled_day,
    next_scheduled_day,
    nth_scheduled_day_after,
)
from signals.common import BarWindow

_UNIT = "{}_alpha_liveness_unit"
_SESSIONS = "{}_alpha_liveness_sessions"


def test_the_bar_driven_families_are_exactly_the_operator_s_three():
    """Insider, 13D, 8-K and catalyst windows stay calendar days (the operator's ruling): no pair for them."""
    assert BAR_WINDOW_FAMILIES == ("breakout", "breakout_52w", "laggard")
    for kind in ("insider_core", "insider_flip", "activist_13d", "catalyst", "revenue_accel"):
        assert _UNIT.format(kind) not in CallConfig.model_fields
        assert _SESSIONS.format(kind) not in CallConfig.model_fields


@pytest.mark.parametrize("family", BAR_WINDOW_FAMILIES)
def test_every_family_defaults_to_calendar_days_with_no_count(family):
    assert getattr(DEFAULT_CONFIG, _UNIT.format(family)) == "calendar"
    assert getattr(DEFAULT_CONFIG, _SESSIONS.format(family)) is None


def test_the_calendar_dials_themselves_are_untouched():
    assert DEFAULT_CONFIG.breakout_alpha_liveness_days == 10
    assert DEFAULT_CONFIG.breakout_52w_alpha_liveness_days == 45
    assert DEFAULT_CONFIG.laggard_alpha_liveness_days == 15


def test_spelling_out_the_dormant_defaults_is_the_production_config():
    from domain.config import config_hash

    explicit = apply_overlay(
        {_UNIT.format(f): "calendar" for f in BAR_WINDOW_FAMILIES}
        | {_SESSIONS.format(f): None for f in BAR_WINDOW_FAMILIES}
    )
    assert config_hash(explicit) == config_hash(DEFAULT_CONFIG)


# --- bounds + pairing ---------------------------------------------------------------------------------

_CAPS = {"breakout": 60, "breakout_52w": 45, "laggard": 60}


@pytest.mark.parametrize("family", BAR_WINDOW_FAMILIES)
def test_a_session_count_is_bounded_on_the_model(family):
    unit, n = _UNIT.format(family), _SESSIONS.format(family)
    for bad in (0, -3, _CAPS[family] + 1):
        with pytest.raises(ValidationError):
            CallConfig(**{unit: "sessions", n: bad})
        with pytest.raises(OverlayError):
            apply_overlay({unit: "sessions", n: bad})
    for good in (1, _CAPS[family]):
        assert getattr(apply_overlay({unit: "sessions", n: good}), n) == good


@pytest.mark.parametrize("family", BAR_WINDOW_FAMILIES)
def test_sessions_without_a_count_is_refused(family):
    with pytest.raises(OverlayError, match=_SESSIONS.format(family)):
        apply_overlay({_UNIT.format(family): "sessions"})


@pytest.mark.parametrize("family", BAR_WINDOW_FAMILIES)
def test_a_count_under_calendar_is_refused_because_it_would_change_nothing(family):
    with pytest.raises(OverlayError, match="would change nothing"):
        apply_overlay({_SESSIONS.format(family): 7})


def test_an_unknown_unit_is_refused():
    with pytest.raises(OverlayError):
        apply_overlay(
            {"breakout_alpha_liveness_unit": "weeks", "breakout_alpha_liveness_sessions": 2}
        )


def test_the_family_resolver_refuses_a_lone_unit_that_skipped_validation():
    """``model_copy(update=...)`` skips the model validator; the resolver must still fail loud rather than
    quietly fall back to calendar days."""
    from signals.common import breakout_window

    cfg = DEFAULT_CONFIG.model_copy(update={"breakout_alpha_liveness_unit": "sessions"})
    with pytest.raises(ValueError, match="no session count"):
        breakout_window(cfg)


# --- the session helper -------------------------------------------------------------------------------


def test_nth_session_after_counts_sessions_only():
    assert nth_scheduled_day_after(date(2026, 10, 26), 1) == next_scheduled_day(date(2026, 10, 26))
    # Friday + 1 session = Monday; + 5 = the next Friday
    assert nth_scheduled_day_after(date(2026, 10, 30), 1) == date(2026, 11, 2)
    assert nth_scheduled_day_after(date(2026, 10, 30), 5) == date(2026, 11, 6)
    # from a Saturday (not a session itself): Monday is the first
    assert nth_scheduled_day_after(date(2026, 10, 31), 1) == date(2026, 11, 2)
    # across Thanksgiving 2026 (Thu 11-26 closed): Wed + 1 = Fri, + 2 = Mon
    assert nth_scheduled_day_after(date(2026, 11, 25), 1) == date(2026, 11, 27)
    assert nth_scheduled_day_after(date(2026, 11, 25), 2) == date(2026, 11, 30)


def test_nth_session_after_refuses_a_count_below_one():
    with pytest.raises(ValueError):
        nth_scheduled_day_after(date(2026, 10, 30), 0)


# --- the caps fit the detectors' own pulls ------------------------------------------------------------

_SESSION_LIST = [
    COVERAGE_START + timedelta(days=i)
    for i in range((COVERAGE_END - COVERAGE_START).days + 1)
    if is_scheduled_day(COVERAGE_START + timedelta(days=i))
]


def _worst_case_headroom(n: int, lookback_days: int, bars_before: int) -> int:
    """min over every night of (index of the OLDEST live fire inside the pulled bars) - ``bars_before``.

    Index arithmetic on the session list, so it runs in milliseconds: with sessions ``S`` and the first
    session after the night at ``S[k]``, a fire on ``S[j]`` with an ``n``-session window is live iff
    ``S[k] <= S[j + n]`` (F3), i.e. the oldest live fire is ``S[k - n]``. It must sit at index
    ``>= bars_before`` within the pull ``[night - lookback_days, night]`` (``signals.base.window_prices``).
    ``test_the_index_arithmetic_matches_the_real_liveness_test`` checks this against ``BarWindow`` itself.
    """
    worst = None
    night = _SESSION_LIST[0] + timedelta(days=lookback_days + 1)
    last = _SESSION_LIST[-1] - timedelta(
        days=2 * n
    )  # leave room for the window's end inside coverage
    while night <= last:
        k = bisect_right(_SESSION_LIST, night)  # S[k] = the first session after the night
        p = bisect_left(_SESSION_LIST, night - timedelta(days=lookback_days))  # first pulled bar
        headroom = (k - n) - p - bars_before
        worst = headroom if worst is None else min(worst, headroom)
        night += timedelta(days=1)
    assert worst is not None
    return worst


def test_the_index_arithmetic_matches_the_real_liveness_test():
    for night in (date(2025, 12, 24), date(2026, 4, 4), date(2026, 11, 27), date(2027, 3, 26)):
        for n in (1, 7, 30):
            k = bisect_right(_SESSION_LIST, night)
            oldest = _SESSION_LIST[k - n]
            win = BarWindow(days=10, sessions=n)
            assert win.is_live(oldest, night), (night, n)
            assert not win.is_live(_SESSION_LIST[k - n - 1], night), (night, n)


def test_the_volume_breakout_cap_fits_its_pull_on_the_worst_night():
    cfg = DEFAULT_CONFIG
    bars_before = max(cfg.breakout_base_window, cfg.breakout_return_days)
    assert _worst_case_headroom(_CAPS["breakout"], cfg.breakout_lookback_days, bars_before) >= 0


def test_the_52_week_breakout_cap_fits_its_pull_on_the_worst_night():
    cfg = DEFAULT_CONFIG
    bars_before = cfg.breakout_52w_min_base_bars
    assert (
        _worst_case_headroom(_CAPS["breakout_52w"], cfg.breakout_52w_lookback_days, bars_before)
        >= 0
    )


def test_the_caps_are_the_model_s_own():
    for family, cap in _CAPS.items():
        field = CallConfig.model_fields[_SESSIONS.format(family)]
        assert any(getattr(m, "le", None) == cap for m in field.metadata), family
        assert any(getattr(m, "ge", None) == 1 for m in field.metadata), family
