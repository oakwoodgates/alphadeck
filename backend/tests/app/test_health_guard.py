"""Boot-visibility guard + ``/health`` degraded-flag logic — asserted DIRECTLY on the pure functions (no
DB, no ``TestClient``), so this runs even when Postgres is down (the DB-backed app suite otherwise SKIPs).
See ``app/health.py`` and the ``app/main.py`` lifespan.
"""

from __future__ import annotations

import logging

import pytest

from app.health import compute_health, enforce_boot_visibility
from domain.settings import get_settings


# --- /health degraded flag ---------------------------------------------------------------------------
def test_health_ok_when_ua_present():
    h = compute_health("Alpha Deck Research admin you@example.com")
    assert h.status == "ok"
    assert h.degraded == {}


@pytest.mark.parametrize("ua", [None, "", "   "])
def test_health_degraded_when_ua_missing(ua):
    h = compute_health(ua)
    assert h.status == "degraded"
    assert h.degraded == {"user_agent": "missing"}


# --- boot guard: fail-open by default, fail-closed under ALPHADECK_REQUIRE_UA ------------------------
def test_boot_guard_ua_present_never_raises():
    # A present UA returns cleanly whether or not strict mode is on.
    enforce_boot_visibility("Alpha Deck Research admin you@example.com", require_ua=False)
    enforce_boot_visibility("Alpha Deck Research admin you@example.com", require_ua=True)


@pytest.mark.parametrize("ua", [None, "", "   "])
def test_boot_guard_missing_ua_fail_open_logs_but_boots(ua, caplog):
    with caplog.at_level(logging.ERROR, logger="alphadeck.health"):
        enforce_boot_visibility(ua, require_ua=False)  # must NOT raise — fail-open
    assert "ALPHADECK_USER_AGENT is missing" in caplog.text


@pytest.mark.parametrize("ua", [None, "", "   "])
def test_boot_guard_missing_ua_strict_refuses_boot(ua):
    with pytest.raises(RuntimeError, match="refusing to boot"):
        enforce_boot_visibility(ua, require_ua=True)


# --- env wiring: ALPHADECK_REQUIRE_UA -> Settings.require_ua (pure settings, no DB/network) ----------
def test_require_ua_reads_env(monkeypatch):
    monkeypatch.setenv("ALPHADECK_REQUIRE_UA", "true")
    get_settings.cache_clear()
    try:
        assert get_settings().require_ua is True
    finally:
        get_settings.cache_clear()  # never leak the cached singleton into other tests


def test_require_ua_defaults_false(monkeypatch):
    monkeypatch.delenv("ALPHADECK_REQUIRE_UA", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().require_ua is False
    finally:
        get_settings.cache_clear()
