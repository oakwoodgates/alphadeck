"""A polite HTTP GET shared by the live ingest fetchers (EDGAR + Yahoo).

Adds REACTIVE backoff on top of any proactive throttle: retries on 429 (rate-limit) and transient 5xx with
capped exponential backoff, honoring a numeric ``Retry-After`` when the server sends one. On exhaustion it
raises the last HTTP error, so callers stay fail-visible (the per-name ingest records the failure; data is
never silently dropped). ``httpx`` is imported lazily so the package imports without it (mirroring the other
clients); ``sleep`` is injectable so tests never actually wait.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class RateLimiter:
    """A minimal token-bucket gate: at most ``max_per_sec`` requests/second — the proactive politeness
    throttle the cache-first clients put in front of every live fetch (pair with ``polite_get``'s ``pre``).

    THREAD-SAFE and the SHARED throttle across concurrent callers: the EDGAR-first discovery fans out the
    per-keyword EFTS pages over a thread pool, but they all funnel through the ONE ``RateLimiter`` on the ONE
    ``EdgarClient`` — the SEC fair-access limit is a GLOBAL budget, not a per-request one. ``acquire`` RESERVES
    the next slot under a lock (advancing ``_next`` by the interval) and then sleeps OUTSIDE the lock, so N
    threads get N distinct slots spaced by ``_min_interval`` (global rate <= ``max_per_sec``) without
    serializing their sleeps. Single-threaded callers behave exactly as before (each slot is ``last + interval``).
    """

    def __init__(self, max_per_sec: float = 8.0) -> None:
        self._min_interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._next = 0.0  # earliest monotonic time the next request may fire

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)  # this caller's reserved slot
            self._next = (
                start + self._min_interval
            )  # the following caller waits a full interval past it
            wait = start - now
        if (
            wait > 0
        ):  # sleep OUTSIDE the lock — the slot is already reserved, so others don't block on it
            time.sleep(wait)


# Transient statuses worth a retry: rate-limit (429) + the standard transient 5xx. A 4xx other than 429
# (e.g. 404) is NOT retried — it won't fix itself, so it raises straight through to the fail-visible caller.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _retry_after_seconds(resp: Any) -> float | None:
    """A numeric ``Retry-After`` (seconds) when present, else ``None`` (we fall back to exponential backoff;
    the rare HTTP-date form is ignored rather than mis-parsed)."""
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def polite_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    backoff_cap: float = 30.0,
    pre: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
):
    """GET ``url`` politely, returning the httpx ``Response`` (2xx; ``raise_for_status`` passed).

    Retries on 429 / transient 5xx with capped exponential backoff (a numeric ``Retry-After`` wins when
    given). ``pre`` runs before EACH attempt — pass a rate-limiter's ``acquire`` to keep the proactive
    throttle in front of every try. Raises on a non-retryable status, or re-raises the final status after the
    last retry is spent. ``sleep`` is injectable (tests pass a no-op)."""
    import httpx

    attempt = 0
    while True:
        if pre is not None:
            pre()
        resp = httpx.get(url, headers=headers, timeout=timeout)
        if resp.status_code in _RETRY_STATUS and attempt < max_retries:
            delay = _retry_after_seconds(resp)
            if delay is None:
                delay = min(backoff_base * (2**attempt), backoff_cap)
            sleep(delay)
            attempt += 1
            continue
        resp.raise_for_status()
        return resp
