"""The per-user fan-out (docs/FEED_LOOP.md step 3): ``SlackNotifier(webhook_urls)``, ``notifier_for_workspace`` and
``post_text``.

No DB: the outbound ``httpx.post`` is patched and every call captured. Proven: N URLs -> N POSTs and ONE log
record; a failing first URL still posts to the second; ``SlackNotifier()`` posts to the env URL; the
``notifier_for_workspace`` matrix (urls / none + super admin + env / none + plain user / env unset); duplicates
collapse to one POST.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import uuid4

import httpx
import pytest

from domain.settings import get_settings
from notify import (
    LogNotifier,
    SlackNotifier,
    TransitionEvent,
    notifier_for_workspace,
    post_text,
)
from tests.notify.test_slack_notifier import _freeze

_ENV = "https://hooks.example.test/env-channel"
_U1 = "https://hooks.slack.com/services/TEST/U1/invented-for-tests"
_U2 = "https://hooks.slack.com/services/TEST/U2/invented-for-tests"
_U3 = "https://hooks.slack.com/services/TEST/U3/invented-for-tests"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _env(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _ENV)
    get_settings.cache_clear()


def _capture(monkeypatch, *, failing=(), status=200):
    calls: list[str] = []

    def fake_post(url, **kw):
        calls.append(url)
        if url in failing:
            raise httpx.ConnectError(f"cannot reach {url}")
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def _armed() -> TransitionEvent:
    return TransitionEvent(
        thesis_id=uuid4(),
        thesis_name="Synthetic card",
        asof=date(2026, 6, 5),
        from_state="warming",
        to_state="armed",
        from_verdict="watching",
        to_verdict="starter_entry",
    )


def test_n_urls_get_n_posts_and_one_log_record(monkeypatch, caplog):
    calls = _capture(monkeypatch)
    with caplog.at_level(logging.WARNING):
        SlackNotifier((_U1, _U2, _U3)).notify(_armed())
    assert calls == [_U1, _U2, _U3]
    assert sum("TRANSITION" in r.getMessage() for r in caplog.records) == 1


def test_a_failing_first_url_still_posts_to_the_second_and_never_logs_the_url(monkeypatch, caplog):
    calls = _capture(monkeypatch, failing={_U1})
    with caplog.at_level(logging.WARNING):
        SlackNotifier((_U1, _U2)).notify(_armed())  # never raises
    assert calls == [_U1, _U2]
    assert "destination 1 of 2" in caplog.text and _U1 not in caplog.text


def test_the_health_page_fans_out_too(monkeypatch):
    calls = _capture(monkeypatch, failing={_U1})
    SlackNotifier((_U1, _U2)).notify_health(_freeze())
    assert calls == [_U1, _U2]


def test_no_args_posts_to_the_env_url(monkeypatch):
    _env(monkeypatch)
    calls = _capture(monkeypatch)
    SlackNotifier().notify(_armed())
    assert calls == [_ENV]


def test_no_args_and_no_env_posts_nothing(monkeypatch):
    calls = _capture(monkeypatch)
    SlackNotifier().notify(_armed())
    assert calls == []


def test_given_urls_never_reach_the_env_url(monkeypatch):
    _env(monkeypatch)
    calls = _capture(monkeypatch)
    SlackNotifier((_U1,)).notify(_armed())
    assert calls == [_U1]


# --- notifier_for_workspace ------------------------------------------------------------------------------------


def test_urls_make_one_slack_notifier_over_all_of_them_deduplicated(monkeypatch):
    _env(monkeypatch)
    calls = _capture(monkeypatch)
    n = notifier_for_workspace([_U2, _U1, _U2], has_super_admin=True)
    assert isinstance(n, SlackNotifier)
    n.notify(_armed())
    assert calls == [_U1, _U2]  # sorted, each once; the env channel not added


def test_no_urls_a_super_admin_and_the_env_reach_the_env_channel(monkeypatch):
    _env(monkeypatch)
    calls = _capture(monkeypatch)
    n = notifier_for_workspace((), has_super_admin=True)
    assert isinstance(n, SlackNotifier)
    n.notify(_armed())
    assert calls == [_ENV]


def test_no_urls_and_a_plain_user_log_only(monkeypatch):
    _env(monkeypatch)
    assert isinstance(notifier_for_workspace((), has_super_admin=False), LogNotifier)


def test_no_urls_a_super_admin_and_no_env_log_only():
    assert isinstance(notifier_for_workspace((), has_super_admin=True), LogNotifier)


# --- post_text -------------------------------------------------------------------------------------------------


def test_post_text_reports_delivery_and_never_raises(monkeypatch):
    _capture(monkeypatch)
    assert post_text(_U1, "hi", timeout=1.0) == (True, "Slack answered 200")
    _capture(monkeypatch, status=404)
    assert post_text(_U1, "hi") == (False, "Slack answered 404")
    _capture(monkeypatch, failing={_U1})
    delivered, detail = post_text(_U1, "hi")
    assert delivered is False and detail == "could not reach Slack (ConnectError)"
