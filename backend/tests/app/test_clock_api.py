"""``GET /clock`` (F1, 2026-10-30): the server's current night, action day and today — the app's default as-of.

No database: the route reads one clock (``domain.market_time.market_clock``). The clock is pinned through
``market_now`` (the seam ``tests/conftest.py``'s ``pinned_today`` uses), so each case is an exact instant.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import app
from domain import market_time
from domain.settings import get_settings

_ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _default_schedule(monkeypatch):
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _at(monkeypatch, d: date, hh: int, mm: int = 0) -> None:
    instant = datetime.combine(d, time(hh, mm), _ET)
    monkeypatch.setattr(
        market_time, "market_now", lambda tz=None: instant.astimezone(tz or market_time.market_tz())
    )


@pytest.mark.parametrize(
    "day, hh, mm, want",
    [
        # Tuesday morning: Monday's night, acted on today
        (date(2026, 10, 27), 9, 0, ("2026-10-26", "2026-10-27", "2026-10-27")),
        # Tuesday after the 22:30 pass: tonight, acted on Wednesday
        (date(2026, 10, 27), 22, 30, ("2026-10-27", "2026-10-28", "2026-10-27")),
        # a Saturday: Friday's night, acted on Monday
        (date(2026, 10, 31), 12, 0, ("2026-10-30", "2026-11-02", "2026-10-31")),
    ],
)
def test_the_clock_serves_the_current_night_the_action_day_and_today(
    monkeypatch, day, hh, mm, want
):
    _at(monkeypatch, day, hh, mm)
    r = TestClient(app).get("/clock")
    assert r.status_code == 200
    body = r.json()
    assert (body["current_night"], body["action_day"], body["market_today"]) == want
    assert body["market_tz"] == "America/New_York"


def test_a_malformed_pass_time_is_a_loud_500(monkeypatch):
    monkeypatch.setenv("ALPHADECK_CRON_AT", "half past ten")
    get_settings.cache_clear()
    r = TestClient(app).get("/clock")
    assert r.status_code == 500 and "ALPHADECK_CRON_AT" in r.json()["detail"]
