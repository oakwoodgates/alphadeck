"""The in-band failure page (H3) — ``python -m notify.page``: DB-free, never raises, always exits 0.

No DB and no real Slack: the seam is exercised with a fake notifier, the Slack path with a patched
``httpx.post`` (the test_slack_notifier recipe), and the true CLI in a subprocess pointed at a dead database
port, which is exactly the shape it is for (the shell pages when the night failed, often BECAUSE of the DB).
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

import httpx
import pytest

from domain.settings import get_settings
from notify import HealthEvent, LogNotifier, page

_BACKEND = Path(__file__).resolve().parents[2]
_WEBHOOK = "https://hooks.slack.example/T000/B000/xxxx"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Isolate SLACK_WEBHOOK_URL from the real env and re-read the cached singleton both sides."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _RecordingNotifier:
    """A fake notifier: records every health event it is handed (the page's one delivery path)."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.health: list[HealthEvent] = []
        self._raises = raises

    def notify(self, event) -> None:  # pragma: no cover — the page never sends a transition
        raise AssertionError("a failure page must ride notify_health, never notify")

    def notify_spac_status(self, event) -> None:  # pragma: no cover — same
        raise AssertionError("a failure page must ride notify_health")

    def notify_health(self, event: HealthEvent) -> None:
        if self._raises is not None:
            raise self._raises
        self.health.append(event)


def _subprocess_env(**extra: str) -> dict[str, str]:
    """The current env minus anything that could deliver for real, plus a DEAD database URL."""
    env = {k: v for k, v in os.environ.items() if k != "SLACK_WEBHOOK_URL"}
    env["DATABASE_URL"] = "postgresql://nobody:nothing@127.0.0.1:1/no_such_db"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


# --- needs no database -----------------------------------------------------------------------------------


def test_importing_the_page_loads_no_database_module():
    """Structural: the CLI's whole import graph (``notify`` included, which ``-m notify.page`` imports first)
    loads neither ``db`` nor ``psycopg`` nor any spine-write path. Fresh interpreter, so this test file's
    own imports cannot mask a leak."""
    probe = (
        "import sys, notify.page\n"
        "bad = sorted(m for m in sys.modules if m == 'db' or m.startswith(('db.', 'psycopg', "
        "'repositories', 'pipeline', 'calls')))\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=_BACKEND,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_cli_pages_and_exits_0_with_the_database_down():
    """End to end, the way the shell calls it: a dead DATABASE_URL, no Slack webhook. It exits 0 and the
    LogNotifier's loud CRON HEALTH line carries the night and the reason."""
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "notify.page",
            "--asof",
            "2026-10-12",
            "--reason",
            "the scheduled run and its ONE retry both FAILED (last exit 124)",
        ],
        cwd=_BACKEND,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert (
        "CRON HEALTH cron 2026-10-12: FAILED — the scheduled run and its ONE retry both FAILED "
        "(last exit 124)" in r.stderr
    )


def test_the_cli_still_exits_0_when_settings_cannot_even_parse():
    """A malformed env value makes ``get_settings()`` raise (pydantic cannot parse ``abc`` as a float): the
    same fault that would fail the pass must not kill its page. The CLI falls back, logs the page, exits 0.
    """
    r = subprocess.run(
        [sys.executable, "-m", "notify.page", "--asof", "2026-10-12", "--reason", "boot failed"],
        cwd=_BACKEND,
        env=_subprocess_env(ALPHADECK_EDGAR_RATE_PER_SEC="abc"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    assert "the notify seam failed" in r.stderr
    assert "CRON HEALTH cron 2026-10-12: FAILED — boot failed" in r.stderr
    assert "no SLACK_WEBHOOK_URL" in r.stderr


# --- the seam: one HealthEvent through notify_health -------------------------------------------------------


def test_send_page_hands_one_health_event_to_the_notifier():
    sink = _RecordingNotifier()

    assert page.send_page(date(2026, 10, 12), "the boot catch-up FAILED", notifier=sink) is True

    [event] = sink.health
    assert isinstance(event, HealthEvent)  # rides notify_health on every adapter, unchanged
    assert event.label == "cron 2026-10-12: FAILED — the boot catch-up FAILED"
    assert event.asof == date(2026, 10, 12)
    # no pass reported counts, so the event claims none
    assert (event.theses, event.errored, event.withheld, event.edgar_fetches) == (0, 0, 0, 0)
    assert event.frozen is False


def test_the_label_is_only_the_inputs_so_no_thesis_name_can_ride_it():
    """Structural "no thesis names": the page is built from the as-of and the shell's reason ONLY; there is
    no thesis input to leak, and the label is exactly those two."""
    event = page.failure_event(date(2026, 10, 1), "the nightly DB backup FAILED (exit 1)")
    assert event.label == "cron 2026-10-01: FAILED — the nightly DB backup FAILED (exit 1)"


def test_an_unparseable_asof_still_pages():
    sink = _RecordingNotifier()
    page.send_page(None, "x", notifier=sink)
    assert sink.health[0].label == "cron (asof unknown): FAILED — x"


def test_the_reason_is_collapsed_to_one_line_and_capped():
    event = page.failure_event(date(2026, 10, 1), "line one\n  line two\t\tthree " + "z" * 1000)
    assert "\n" not in event.label and "\t" not in event.label
    assert event.reason.startswith("line one line two three z")
    assert len(event.reason) == page._MAX_REASON and event.reason.endswith("…")
    assert page.failure_event(None, "   ").reason == "unspecified failure"


def test_the_slack_adapter_pushes_the_page(monkeypatch):
    """Through the REAL get_notifier() -> SlackNotifier with a patched httpx.post (no real Slack)."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    get_settings.cache_clear()
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append({"url": url, "json": kw.get("json")})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    assert page.send_page(date(2026, 10, 12), "the boot catch-up FAILED") is True

    assert calls == [
        {"url": _WEBHOOK, "json": {"text": "🚨 cron 2026-10-12: FAILED — the boot catch-up FAILED"}}
    ]


def test_a_slack_outage_is_logged_never_raised(monkeypatch, caplog):
    """The adapter's own fail-open contract carries the page: a delivery fault is a logged warning."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    get_settings.cache_clear()

    def boom(url, **kw):
        raise httpx.ConnectError("slack is down")

    monkeypatch.setattr(httpx, "post", boom)

    assert (
        page.send_page(date(2026, 10, 12), "x") is True
    )  # the seam took it; delivery failed loudly
    assert "slack health notify failed" in caplog.text


def test_without_a_webhook_the_page_is_the_log_notifier_line(caplog):
    caplog.set_level("ERROR")
    assert page.send_page(date(2026, 10, 12), "x") is True
    assert "CRON HEALTH cron 2026-10-12: FAILED — x" in caplog.text


# --- never raises: a broken seam falls back ---------------------------------------------------------------


def test_a_raising_notifier_falls_back_and_never_raises(monkeypatch):
    posted: list[str] = []
    monkeypatch.setattr(page, "_fallback_post", posted.append)

    sink = _RecordingNotifier(raises=RuntimeError("adapter exploded"))
    assert page.send_page(date(2026, 10, 12), "x", notifier=sink) is False

    assert posted == ["🚨 cron 2026-10-12: FAILED — x"]


def test_a_seam_that_cannot_be_built_falls_back(monkeypatch):
    """``get_notifier()`` raising (a Settings parse error) lands in the fallback, not in a traceback."""

    def broken() -> LogNotifier:
        raise ValueError("Settings could not parse ALPHADECK_EDGAR_RATE_PER_SEC")

    posted: list[str] = []
    monkeypatch.setattr(page, "get_notifier", broken)
    monkeypatch.setattr(page, "_fallback_post", posted.append)

    assert page.send_page(date(2026, 10, 12), "x") is False
    assert posted == ["🚨 cron 2026-10-12: FAILED — x"]


def test_the_fallback_posts_the_raw_webhook_and_swallows_a_network_error(monkeypatch, caplog):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _WEBHOOK)
    seen: list[tuple[str, bytes]] = []

    def fake_urlopen(req, timeout):
        seen.append((req.full_url, req.data))
        raise OSError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    page._fallback_post("🚨 cron 2026-10-12: FAILED — x")  # must not raise

    assert seen and seen[0][0] == _WEBHOOK
    assert b"FAILED" in seen[0][1]
    assert "could not deliver (OSError)" in caplog.text
    assert _WEBHOOK not in caplog.text  # the webhook URL is a credential: never logged


def test_the_fallback_without_a_webhook_only_logs(monkeypatch, caplog):
    def no_network(req, timeout):  # pragma: no cover — must not be reached
        raise AssertionError("no webhook configured: nothing may be sent")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    page._fallback_post("🚨 x")
    assert "no SLACK_WEBHOOK_URL" in caplog.text


# --- the CLI: always exits 0 -------------------------------------------------------------------------------


def _record_send(monkeypatch) -> list[tuple[date | None, str | None]]:
    sent: list[tuple[date | None, str | None]] = []
    monkeypatch.setattr(page, "send_page", lambda asof, reason: sent.append((asof, reason)))
    return sent


def test_main_parses_and_pages(monkeypatch):
    sent = _record_send(monkeypatch)
    assert page.main(["--asof", "2026-10-12", "--reason", "the boot catch-up FAILED"]) == 0
    assert sent == [(date(2026, 10, 12), "the boot catch-up FAILED")]


def test_main_with_a_malformed_call_still_pages_and_exits_0(monkeypatch):
    """The shell only calls this when something already failed, so a usage error must not swallow the page
    (argparse would otherwise exit 2 and send nothing)."""
    sent = _record_send(monkeypatch)
    assert page.main(["--no-such-flag"]) == 0
    assert sent == [(None, "the failure page was called with unparseable arguments")]


def test_main_with_a_bad_asof_pages_with_the_night_unknown(monkeypatch):
    sent = _record_send(monkeypatch)
    assert page.main(["--asof", "2026-13-45", "--reason", "x"]) == 0
    assert sent == [(None, "x")]


def test_main_help_exits_0_and_sends_nothing(monkeypatch, capsys):
    sent = _record_send(monkeypatch)
    assert page.main(["--help"]) == 0
    assert sent == []
    assert "--asof" in capsys.readouterr().out


def test_main_survives_a_send_that_raises_anyway(monkeypatch):
    """Belt-and-suspenders for the exit-0 contract: even a send_page that broke its own promise."""

    def boom(asof, reason):
        raise RuntimeError("send_page broke its contract")

    monkeypatch.setattr(page, "send_page", boom)
    assert page.main(["--asof", "2026-10-12", "--reason", "x"]) == 0
