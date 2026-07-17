"""SlackNotifier — the loud DELIVERY channel (inverse loudness #7) + its fail-open contract.

No DB: these build a TransitionEvent, patch the outbound ``httpx.post``, and assert on the captured call. The
env override follows the repo recipe — ``setenv`` + ``get_settings.cache_clear()`` under an autouse fixture that
scrubs the var and clears the cache on both sides.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import httpx
import pytest

from domain.settings import get_settings
from notify import HealthEvent, LogNotifier, SlackNotifier, TransitionEvent, get_notifier

_WEBHOOK = "https://hooks.slack.example/T000/B000/xxxx: the webhook"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Isolate SLACK_WEBHOOK_URL from the real env and re-read the cached singleton both sides."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _event(*, to_state: str, from_state: str = "warming") -> TransitionEvent:
    return TransitionEvent(
        thesis_id=uuid4(),
        thesis_name="AI Memory & Storage Supercycle",
        asof=date(2026, 6, 5),
        from_state=from_state,
        to_state=to_state,
        from_verdict="watching",
        to_verdict="starter_entry",
    )


def _capture_post(monkeypatch, *, raises: BaseException | None = None):
    """Patch httpx.post to record (url, json) — or raise, to exercise the fail-open path."""
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append({"url": url, "json": kw.get("json"), "timeout": kw.get("timeout")})
        if raises is not None:
            raise raises
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _enable_slack(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    get_settings.cache_clear()


# --- (1) posts on a → armed move ---------------------------------------------------------------------------


def test_posts_on_armed_transition(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify(_event(to_state="armed"))

    assert len(calls) == 1
    assert calls[0]["url"] == _WEBHOOK
    body = calls[0]["json"]
    assert set(body) == {"text"}
    text = body["text"]
    assert (
        "AI Memory & Storage Supercycle" in text
    )  # thesis-name identity (operator-chosen; no ticker)
    assert "ARMED" in text
    assert "warming → armed" in text  # the state move is glanceable


# --- (2) no-op on a quiet (non-armed) move -----------------------------------------------------------------


def test_does_not_post_on_non_armed_transition(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    # a move INTO warming (not armed) — logged by the record sink, but never pushed
    SlackNotifier().notify(_event(from_state="incubating", to_state="warming"))

    assert calls == []


# --- (3) FAIL-OPEN: a raising POST must NOT propagate out of notify() ---------------------------------------
# This is the load-bearing test: pipeline.daily calls notify() BEFORE record_if_changed + commit inside a
# try/except that rolls back on any exception. If a Slack outage could raise out of notify(), it would DROP the
# call-of-record. So a POST that throws must be swallowed — notify() returns normally.


def test_post_exception_does_not_propagate(monkeypatch):
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))

    # must NOT raise — a Slack outage can never corrupt the record
    SlackNotifier().notify(_event(to_state="armed"))

    assert len(calls) == 1  # the POST was attempted...
    # ...and its exception was swallowed (control reached here without propagating)


def test_raise_for_status_error_does_not_propagate(monkeypatch):
    """A 4xx/5xx from Slack (raise_for_status) is also swallowed."""
    _enable_slack(monkeypatch)
    calls: list[str] = []

    def fake_post(url, **kw):
        calls.append(url)
        return httpx.Response(
            500, request=httpx.Request("POST", url)
        )  # .raise_for_status() -> HTTPStatusError

    monkeypatch.setattr(httpx, "post", fake_post)

    SlackNotifier().notify(_event(to_state="armed"))  # must not raise
    assert calls == [_WEBHOOK]


# --- R4: notify_health — the durable page on a bad cron night (freeze / withheld / errored) ---


def _freeze() -> HealthEvent:
    return HealthEvent(
        asof=date(2026, 7, 17), theses=6, withheld=0, errored=0, edgar_fetches=0, frozen=True
    )


def test_health_ALWAYS_pushes_it_only_fires_when_wrong(monkeypatch):
    """Unlike notify (armed-only), EVERY health event pushes — it exists only for the exception (freeze /
    withheld / errored), so it IS the rare loud one. A freeze is exactly the page R1 lacked."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch)

    SlackNotifier().notify_health(_freeze())

    assert len(calls) == 1 and calls[0]["url"] == _WEBHOOK
    text = calls[0]["json"]["text"]
    assert "FROZEN" in text and "0 EDGAR fetches" in text  # the freeze, glanceable


def test_health_push_is_FAIL_OPEN(monkeypatch):
    """Same load-bearing contract as notify: a Slack outage must NEVER raise out of the cron."""
    _enable_slack(monkeypatch)
    calls = _capture_post(monkeypatch, raises=httpx.ConnectError("slack is down"))
    SlackNotifier().notify_health(_freeze())  # must not raise
    assert len(calls) == 1  # attempted + swallowed


def test_health_records_loud_even_with_no_webhook(monkeypatch, caplog):
    """No Slack configured → the LogNotifier fallback still RECORDS the bad night loudly (a log is a record)."""
    import logging

    calls = _capture_post(monkeypatch)  # webhook unset (autouse fixture) → no push
    with caplog.at_level(logging.ERROR):
        LogNotifier().notify_health(_freeze())
    assert calls == []  # nothing pushed (no webhook)
    assert any("CRON HEALTH" in r.message and "FROZEN" in r.message for r in caplog.records)


# --- (4) get_notifier() env selection ----------------------------------------------------------------------


def test_get_notifier_returns_slack_when_webhook_set(monkeypatch):
    _enable_slack(monkeypatch)
    assert isinstance(get_notifier(), SlackNotifier)


def test_get_notifier_falls_back_to_log_when_webhook_unset(monkeypatch):
    # autouse fixture already delenv'd + cache-cleared — the var is unset
    assert isinstance(get_notifier(), LogNotifier)
