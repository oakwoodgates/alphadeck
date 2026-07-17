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


@dataclass(frozen=True)
class HealthEvent:
    """The cron's RUN-LEVEL health, emitted once per run — but ONLY when something is wrong (R4). A silent
    daily job is a daily job you don't have: the R1 freeze went 11+ days undetected because the operator was
    the monitoring system. This is the page. It fires on a FREEZE (a live run that made ZERO EDGAR fetches —
    the cache never refreshed), on WITHHELD calls (a --no-live or total-failure run that didn't record), or on
    thesis ERRORS. A healthy run emits nothing (``assess_health`` returns None) — loudness marks the exception.
    """

    asof: date
    theses: int
    withheld: int
    errored: int
    edgar_fetches: int
    frozen: bool  # live run + names present + ZERO edgar fetches = the R1 freeze, pageable

    @property
    def label(self) -> str:
        bits: list[str] = []
        if self.frozen:
            bits.append(
                f"FROZEN — 0 EDGAR fetches across {self.theses} theses (the cache never refreshed)"
            )
        if self.withheld:
            bits.append(f"{self.withheld} call(s) WITHHELD (no-live / total ingest failure)")
        if self.errored:
            bits.append(f"{self.errored} thesis error(s)")
        return f"cron {self.asof}: " + " · ".join(bits)


class Notifier(Protocol):
    """The delivery seam — an adapter per channel; the cron never knows which one it's holding."""

    def notify(self, event: TransitionEvent) -> None: ...  # pragma: no cover — a Protocol

    def notify_health(self, event: HealthEvent) -> None: ...  # pragma: no cover — a Protocol


class LogNotifier:
    """v1: the transition is RECORDED loudly, delivered nowhere (the deferred-delivery adapter)."""

    def notify(self, event: TransitionEvent) -> None:
        _log.warning("TRANSITION %s (asof %s)", event.label, event.asof)

    def notify_health(self, event: HealthEvent) -> None:
        # a run-health page is a RECORD (a bad cron night, logged loud); delivery is the Slack adapter's job
        _log.error("CRON HEALTH %s", event.label)


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

    def notify_health(self, event: HealthEvent) -> None:
        """R4 — the DURABLE page on a bad cron night. Unlike ``notify`` (which pushes only a→armed), EVERY
        health event pushes: it fires only when something is wrong (freeze / withheld / errored), so it is by
        construction the rare exception loudness is for. Records loud first (the log line survives regardless),
        then best-effort pushes. FAIL-OPEN, like ``notify`` — a Slack outage can never break the cron.
        """
        self._log_sink.notify_health(event)  # (1) record — always, before any push
        try:
            import httpx  # lazy (repo convention)

            s = get_settings()
            if not s.slack_webhook_url:
                return
            resp = httpx.post(
                s.slack_webhook_url,
                json={"text": f"🚨 {event.label}"},
                timeout=s.http_timeout_s,
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort delivery, never breaks the cron
            _log.warning(
                "slack health notify failed for '%s' (fail-open, logged only)",
                event.label,
                exc_info=True,
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
