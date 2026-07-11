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


def get_notifier() -> Notifier:
    """The configured notifier — LogNotifier until a delivery channel is chosen (then: an env-selected
    adapter here, nothing else changes)."""
    return LogNotifier()
