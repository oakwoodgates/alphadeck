"""D2 — the settle guard: no in-progress session bar is ever stored as a close.

Pure (no DB). Every bar here is REAL vendor data in the vendor's own chart shape
(``tests/fixtures/prices/benchmark_tape_2026_08.json`` — its ``_provenance`` says where each cut came
from), parsed by the production ``parse_yahoo_chart``; the in-progress value is the one a 2026-08-25
market-hours pass actually stored for SPY (765.27, against a final 765.91). Every pass time is a FIXED,
timezone-aware instant — the guard's clock is a parameter, never the wall clock.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from domain.settings import Settings, get_settings
from ingest.prices.eod_loader import parse_yahoo_chart
from ingest.prices.settle import is_settled, settle_instant, withhold_unsettled

ET = ZoneInfo("America/New_York")
_FIX = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "benchmark_tape_2026_08.json"
    ).read_text(encoding="utf-8")
)


def _bars(ticker: str, cut: str, *, through: date | None = None) -> list[dict]:
    rows = parse_yahoo_chart(_FIX[ticker][cut])
    return [r for r in rows if through is None or r["d"] <= through]


def _et(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def _dates(rows: list[dict]) -> list[date]:
    return [r["d"] for r in rows]


# --- the daytime pass: today's bar is withheld, everything before it is stored -----------------------


def test_a_daytime_pass_withholds_todays_in_progress_bar():
    """The 2026-08-25 shape: a pass at 12:07 ET (the measured daytime boot-catch-up hour) pulls a payload
    whose last element is the session SO FAR. It must not be stored; the prior sessions are."""
    fresh = _bars("SPY", "midsession_2026_08_25")
    assert fresh[-1]["d"] == date(2026, 8, 25) and fresh[-1]["close"] == pytest.approx(765.27)

    settled, withheld = withhold_unsettled(fresh, now=_et(2026, 8, 25, 12, 7))

    assert withheld == 1
    assert _dates(settled) == [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)]
    assert all(
        r["close"] != pytest.approx(765.27) for r in settled
    )  # the frozen value never passes


def test_the_settle_boundary_is_the_configured_time():
    """One second before 18:00 ET the bar is still the session-in-progress; at 18:00 it is a close."""
    fresh = _bars("SPY", "final", through=date(2026, 8, 25))

    _, before = withhold_unsettled(fresh, now=_et(2026, 8, 25, 17, 59, 59))
    settled, at = withhold_unsettled(fresh, now=_et(2026, 8, 25, 18, 0, 0))

    assert before == 1 and at == 0
    assert settled[-1]["d"] == date(2026, 8, 25)
    assert settled[-1]["close"] == pytest.approx(765.909973)


def test_the_scheduled_22_30_pass_always_stores_its_own_night():
    """The cron's RUN_AT (22:30 ET) is 4.5 h past the settle time: the night's bar is never withheld."""
    fresh = _bars("SPY", "final")  # through 2026-08-28 (a Friday)

    settled, withheld = withhold_unsettled(fresh, now=_et(2026, 8, 28, 22, 30))

    assert withheld == 0 and _dates(settled) == _dates(fresh)


def test_a_post_midnight_catch_up_still_stores_the_previous_session():
    """A late wake / boot catch-up at 00:30 ET: ``market_today`` is ALREADY the next day (pre-open), and
    the bar it is catching up for — yesterday's — is a prior day's bar, so it is settled."""
    fresh = _bars("SPY", "final", through=date(2026, 8, 27))

    settled, withheld = withhold_unsettled(fresh, now=_et(2026, 8, 28, 0, 30))

    assert withheld == 0
    assert settled[-1]["d"] == date(2026, 8, 27)
    assert settled[-1]["close"] == pytest.approx(771.099976)


def test_a_future_dated_bar_is_never_a_close():
    """A bar dated after ``now``'s market day can never be final, at any hour."""
    fresh = _bars("SPY", "final", through=date(2026, 8, 28))

    settled, withheld = withhold_unsettled(fresh, now=_et(2026, 8, 27, 23, 59))

    assert withheld == 1 and settled[-1]["d"] == date(2026, 8, 27)


# --- holidays and half-days: calendar-free, conservative, never a loss -------------------------------


def test_a_market_holiday_has_nothing_to_withhold():
    """Thanksgiving 2025-11-27: the vendor serves NO bar for the holiday (the real payload skips it), so a
    daytime pass that day withholds nothing and stores the 11-26 close."""
    fresh = _bars("SPY", "thanksgiving_2025", through=date(2025, 11, 27))
    assert date(2025, 11, 27) not in _dates(_bars("SPY", "thanksgiving_2025"))

    settled, withheld = withhold_unsettled(fresh, now=_et(2025, 11, 27, 12, 0))

    assert withheld == 0 and settled[-1]["d"] == date(2025, 11, 26)


def test_a_half_day_bar_is_held_until_the_settle_time_then_stored():
    """2025-11-28 closed at 13:00 ET. A 14:00 pass holds its (already final) bar — conservative by design,
    there is no exchange calendar — and the evening pass stores it. Held, never lost."""
    fresh = _bars("SPY", "thanksgiving_2025", through=date(2025, 11, 28))

    held, n_held = withhold_unsettled(fresh, now=_et(2025, 11, 28, 14, 0))
    stored, n_stored = withhold_unsettled(fresh, now=_et(2025, 11, 28, 18, 30))

    assert n_held == 1 and held[-1]["d"] == date(2025, 11, 26)
    assert n_stored == 0 and stored[-1]["d"] == date(2025, 11, 28)


# --- the clock contract ------------------------------------------------------------------------------


def test_the_settle_instant_is_market_time_across_the_dst_change():
    """18:00 in MARKET time, not UTC: 22:00 UTC on an EDT day, 23:00 UTC on an EST day."""
    edt = settle_instant(date(2026, 10, 30)).astimezone(timezone.utc)
    est = settle_instant(date(2026, 11, 2)).astimezone(timezone.utc)

    assert edt == datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)
    assert est == datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc)
    # and a UTC-expressed `now` is judged by the same instant (17:30 EST unsettled, 18:00 EST settled)
    assert not is_settled(date(2026, 11, 2), now=datetime(2026, 11, 2, 22, 30, tzinfo=timezone.utc))
    assert is_settled(date(2026, 11, 2), now=datetime(2026, 11, 2, 23, 0, tzinfo=timezone.utc))


def test_a_naive_now_is_refused_not_guessed():
    with pytest.raises(ValueError, match="timezone-AWARE"):
        is_settled(date(2026, 8, 25), now=datetime(2026, 8, 25, 22, 30))


def test_the_default_settle_time_is_18_00_market_time(monkeypatch):
    monkeypatch.delenv("ALPHADECK_PRICE_SETTLE_TIME", raising=False)
    assert Settings().price_settle_time == time(18, 0)


def test_the_settle_time_is_env_overridable_and_malformed_fails_loud(monkeypatch):
    monkeypatch.setenv("ALPHADECK_PRICE_SETTLE_TIME", "16:30")
    get_settings.cache_clear()
    try:
        assert is_settled(date(2026, 8, 25), now=_et(2026, 8, 25, 16, 30))
        assert not is_settled(date(2026, 8, 25), now=_et(2026, 8, 25, 16, 29))
    finally:
        get_settings.cache_clear()
    monkeypatch.setenv("ALPHADECK_PRICE_SETTLE_TIME", "after the close")
    with pytest.raises(ValidationError):
        Settings()
