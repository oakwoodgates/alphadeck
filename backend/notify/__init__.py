"""The notify seam (slice C) — the PIPE is built; the DELIVERY is deferred (operator call, 2026-07-11).

``pipeline.daily`` detects MATERIAL TRANSITIONS — the state or verdict changed vs the PRIOR as-of's
call-of-record — and emits each through a ``Notifier``. "Material" is deliberately this compare and
nothing subtler: trigger churn, provenance reorders, and confidence drift all version the calls log
via ``record_if_changed`` without being transitions; a state/verdict move is the thing an operator
would want to be woken for (the calls-log material-change question, answered where it bites).

v1 ships ONE adapter: ``LogNotifier`` — a loud log line + the transitions block in the daily
summary. No email / push / webhook until the operator picks a channel; when they do, it is one new
adapter behind ``get_notifier()`` and zero rework in the cron. Inverse loudness (#7) lives in the
ADAPTER: the log notifier records every transition (a log is a record, not a nag); a future push
adapter filters to the loud ones (→ armed) and stays silent for the quiet states.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from domain.enums import State
from domain.settings import get_settings

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransitionEvent:
    """One thesis's state/verdict move between consecutive calls-of-record."""

    thesis_id: UUID
    thesis_name: str
    asof: date
    from_state: str
    to_state: str
    from_verdict: str
    to_verdict: str

    @property
    def label(self) -> str:
        arrow = f"{self.from_state} → {self.to_state}"
        if self.from_verdict != self.to_verdict:
            arrow += f" ({self.from_verdict} → {self.to_verdict})"
        return f"{self.thesis_name}: {arrow}"


class Notifier(Protocol):
    """The delivery seam — an adapter per channel; the cron never knows which one it's holding."""

    def notify(self, event: TransitionEvent) -> None: ...  # pragma: no cover — a Protocol


class LogNotifier:
    """v1: the transition is RECORDED loudly, delivered nowhere (the deferred-delivery adapter)."""

    def notify(self, event: TransitionEvent) -> None:
        _log.warning("TRANSITION %s (asof %s)", event.label, event.asof)


class SlackNotifier:
    """The loud DELIVERY channel: an incoming-webhook POST, gated by inverse loudness (#7).

    Two responsibilities, in order:

    1. **RECORD every transition** — delegates to a ``LogNotifier`` so the log line still fires for every move,
       exactly as when Slack is off (a log is a record, not a nag; the record stays regardless of delivery).
    2. **PUSH only a → armed move** — a state change INTO ``armed`` is the transition an operator wants NOW;
       every quieter move (warming / watch / lapsing / managing) is a no-op for Slack, still logged by (1).

    FAIL-OPEN BY CONTRACT: ``notify()`` NEVER raises. Its only caller (``pipeline.daily``) runs it *before*
    ``record_if_changed`` + ``commit`` inside a shared ``try/except`` that would ``rollback`` on any exception —
    so a raising notifier would DROP the call-of-record and falsely fail the thesis. The whole POST therefore
    sits inside a broad fail-open ``except`` (delivery is best-effort; the cron's job is the record). A Slack
    outage, bad URL, or network error is logged and swallowed — it can never corrupt the record.
    """

    def __init__(self) -> None:
        self._log_sink = LogNotifier()  # the record — every transition, independent of the push

    def notify(self, event: TransitionEvent) -> None:
        # (1) record every transition, always — before any push decision
        self._log_sink.notify(event)
        # (2) inverse loudness (#7): push ONLY on a → armed move; every quieter move is a no-op for Slack
        if event.to_state != State.ARMED:
            return
        # fail-open: the whole POST is best-effort. notify() must NEVER raise (see the class docstring) — a
        # Slack outage / bad URL / network error is logged and swallowed, it can never corrupt the record.
        try:
            import httpx  # lazy (repo convention) — the package imports without a live HTTP client

            s = get_settings()
            url = s.slack_webhook_url
            if not url:  # defensive: no webhook => no push (the transition is already logged above)
                return
            resp = httpx.post(url, json={"text": self._format(event)}, timeout=s.http_timeout_s)
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort delivery, never breaks the cron/record
            _log.warning(
                "slack notify failed for %s (fail-open, logged only)", event.label, exc_info=True
            )

    @staticmethod
    def _format(event: TransitionEvent) -> str:
        """A glanceable one-liner — thesis name identifies WHAT armed, plus the state move."""
        return f"🔴 {event.thesis_name} — ARMED ({event.from_state} → {event.to_state})"


def get_notifier() -> Notifier:
    """The configured notifier: the Slack adapter when ``SLACK_WEBHOOK_URL`` is set, else the LogNotifier
    fallback (the log record stays regardless). One env-selected branch — a future channel (Telegram, …) slots
    in here as one more same-shape adapter, nothing else changes."""
    if get_settings().slack_webhook_url:
        return SlackNotifier()
    return LogNotifier()
