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
from notify import LogNotifier, SlackNotifier, TransitionEvent, get_notifier

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


# --- (4) get_notifier() env selection ----------------------------------------------------------------------


def test_get_notifier_returns_slack_when_webhook_set(monkeypatch):
    _enable_slack(monkeypatch)
    assert isinstance(get_notifier(), SlackNotifier)


def test_get_notifier_falls_back_to_log_when_webhook_unset(monkeypatch):
    # autouse fixture already delenv'd + cache-cleared — the var is unset
    assert isinstance(get_notifier(), LogNotifier)
