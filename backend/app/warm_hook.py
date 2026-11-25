"""Dependency-inversion hook for triggering background dashboard cache warm-ups.

Core application modules (auth, lifespan, mutations, admin daily job) call
``request_warm(...)`` without importing ``dashboards`` or ``app.routers.dashboards``,
preserving the Leaf Rule enforced by ``tests/dashboards/test_isolation.py``.
``app.routers.dashboards.warm`` registers the real handler when it is imported.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date
from uuid import UUID

_log = logging.getLogger("alphadeck.warm_hook")

WarmHandler = Callable[[UUID | None, date | None, str], bool]

_lock = threading.Lock()
_handler: WarmHandler | None = None


def register(fn: WarmHandler | None) -> None:
    """Register (or clear, when ``None``) the process-wide dashboard warm handler."""
    global _handler
    with _lock:
        _handler = fn


def request_warm(
    workspace: UUID | None = None,
    asof: date | None = None,
    *,
    reason: str = "auto",
) -> bool:
    """Request a background dashboard cache warm-up via the registered handler.

    Returns ``False`` when no handler is registered or if the handler raises
    (fail-open, logged as a warning).
    """
    with _lock:
        fn = _handler
    if fn is None:
        return False
    try:
        return bool(fn(workspace, asof, reason))
    except Exception:  # noqa: BLE001 — warm-up triggers are best-effort and fail-open
        _log.warning("dashboard warm hook failed (reason=%s)", reason, exc_info=True)
        return False
