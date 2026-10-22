"""A polite HTTP GET shared by the live ingest fetchers (EDGAR + Yahoo).

Adds REACTIVE backoff on top of any proactive throttle: retries on 429 (rate-limit) and transient 5xx with
capped exponential backoff, honoring a numeric ``Retry-After`` when the server sends one. On exhaustion it
raises the last HTTP error, so callers stay fail-visible (the per-name ingest records the failure; data is
never silently dropped). ``httpx`` is imported lazily so the package imports without it (mirroring the other
clients); ``sleep`` is injectable so tests never actually wait.

X7 (2026-10-21) — the concurrent fetch-ahead (``pipeline/fetch_ahead.py``) put several threads behind ONE
vendor, so the reactive half grew two things a single serial caller never needed: JITTER on the exponential
backoff (``jitter=True`` — N threads that hit the same 429 must not retry in lock-step) and ``on_throttle``
(a 429 / 503 PUSHES BACK the shared limiter, so every thread pauses, not only the one that was told to). Every
non-2xx status is counted per host (``http_<status>:<host>``, X5), so the run artifact carries the 429 rate.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ingest.instrument import note


class RateLimiter:
    """A minimal token-bucket gate: at most ``max_per_sec`` requests/second — the proactive politeness
    throttle the cache-first clients put in front of every live fetch (pair with ``polite_get``'s ``pre``).

    THREAD-SAFE and the SHARED throttle across concurrent callers: the EDGAR-first discovery fans out the
    per-keyword EFTS pages over a thread pool, but they all funnel through the ONE ``RateLimiter`` on the ONE
    ``EdgarClient``. ``acquire`` RESERVES the next slot under a lock (advancing ``_next`` by the interval) and
    then sleeps OUTSIDE the lock, so N threads get N distinct slots spaced by ``_min_interval`` (rate <=
    ``max_per_sec``) without serializing their sleeps. Single-threaded callers behave exactly as before (each
    slot is ``last + interval``). PROCESS-LOCAL: the SEC's 10/s is an egress-wide limit, which only the
    Postgres-backed ``ingest.sec_budget`` enforces across processes (X2); this class is the per-client cap and
    the Yahoo gate (X7).
    """

    def __init__(self, max_per_sec: float = 8.0) -> None:
        self._min_interval = 1.0 / max_per_sec
        self._lock = threading.Lock()
        self._next = 0.0  # earliest monotonic time the next request may fire

    @property
    def max_per_sec(self) -> float:
        return 1.0 / self._min_interval

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
            note("rate_wait_s", wait)  # X5 — the proactive throttle's share of the leg's time
            time.sleep(wait)

    def penalize(self, seconds: float) -> None:
        """X7 — the vendor said "too many" (a 429 / 503): no slot is handed out for ``seconds`` from now, so
        EVERY thread sharing this limiter backs off together — not just the one that received the answer
        (the others would otherwise keep firing into the same wall). Never shortens a wait already queued.
        """
        if seconds <= 0:
            return
        with self._lock:
            self._next = max(self._next, time.monotonic() + seconds)


# X7 — THE YAHOO GATE. One process-wide limiter in front of every Yahoo chart / search request (the price
# leg's ``fetch_eod``, the benchmark leg, the rename probe, the symbol resolver), created lazily so the
# ``ALPHADECK_YAHOO_RATE_PER_SEC`` dial is read at first use. The default and why it is conservative:
# ``domain.settings.Settings.yahoo_rate_per_sec``. Process-local on purpose: Yahoo is not a fair-access
# correctness requirement the way SEC's limit is, the nightly pass is the one process that pulls hundreds of
# bars, and a 429 is answered by ``polite_get``'s backoff plus ``penalize`` (the whole pool pauses).
_YAHOO: RateLimiter | None = None
_YAHOO_LOCK = threading.Lock()


def yahoo_limiter() -> RateLimiter:
    """The process-wide Yahoo request gate (``Settings.yahoo_rate_per_sec``)."""
    global _YAHOO
    with _YAHOO_LOCK:
        if _YAHOO is None:
            from domain.settings import get_settings  # lazy: this module imports nothing app-level

            _YAHOO = RateLimiter(get_settings().yahoo_rate_per_sec)
        return _YAHOO


def reset_yahoo_limiter() -> None:
    """Forget the process-wide Yahoo gate (the next ``yahoo_limiter()`` re-reads the setting). Tests only."""
    global _YAHOO
    with _YAHOO_LOCK:
        _YAHOO = None


# Transient statuses worth a retry: rate-limit (429) + the standard transient 5xx. A 4xx other than 429
# (e.g. 404) is NOT retried — it won't fix itself, so it raises straight through to the fail-visible caller.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# The statuses that mean "you are sending too much" (429 Too Many Requests, 503 Service Unavailable): the
# shared limiter is pushed back (``on_throttle``) so the whole pool pauses, not just the caller that got it.
_THROTTLE_STATUS = frozenset({429, 503})


def _jittered(delay: float) -> float:
    """X7 — the exponential backoff with JITTER: ``delay`` scaled by a uniform factor in [1.0, 1.5]. Never
    SHORTER than the plain backoff (politeness only ever errs toward waiting), and spread so threads that hit
    the same 429 at the same moment do not all retry at the same moment."""
    return delay * (1.0 + 0.5 * random.random())


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


def _host(url: str) -> str:
    """The URL's host for the X5 request counters (never the path or query — no ticker, no CIK)."""
    try:
        return urlsplit(url).hostname or "unknown"
    except Exception:  # noqa: BLE001 — a counter label never costs the request
        return "unknown"


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
    jitter: bool = False,
    on_throttle: Callable[[float], None] | None = None,
):
    """GET ``url`` politely, returning the httpx ``Response`` (2xx; ``raise_for_status`` passed).

    Retries on 429 / transient 5xx with capped exponential backoff (a numeric ``Retry-After`` wins when
    given). ``pre`` runs before EACH attempt — pass a rate-limiter's ``acquire`` to keep the proactive
    throttle in front of every try. Raises on a non-retryable status, or re-raises the final status after the
    last retry is spent. ``sleep`` is injectable (tests pass a no-op).

    X7 — ``jitter`` spreads the EXPONENTIAL backoff (``_jittered``: never shorter, up to 1.5x); a numeric
    ``Retry-After`` is honored exactly (the server named the instant). ``on_throttle(delay)`` runs when a 429 /
    503 is answered, before the sleep — the shared limiter's ``penalize``, so every thread behind it pauses.
    Every non-2xx answer is counted by status and host (``http_<status>:<host>``)."""
    import httpx

    host = _host(url)
    attempt = 0
    while True:
        if attempt:
            note(f"http_retry:{host}")  # X5 — a retry after a 429 / 5xx / transport blip
        if pre is not None:
            pre()  # the shared rate-limiter runs before EVERY attempt (incl. retries) — never per-call/bypassed
        note(
            f"http:{host}"
        )  # X5 — every request that goes out, by host (`pipeline/pass_timings.py`)
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout)
        except httpx.TransportError:
            # A transient NETWORK blip (connect / read-timeout / protocol) — the class that, unretried, made the
            # parallel discovery fan-out fail wholesale on a single flaky request. Retry it with backoff like a
            # 5xx; re-raise only after the budget is spent (then discover()'s per-page guard skips/threshold it).
            if attempt < max_retries:
                backoff = min(backoff_base * (2**attempt), backoff_cap)
                if jitter:
                    backoff = _jittered(backoff)
                note("backoff_s", backoff)  # X5 — time lost to a retry's backoff
                sleep(backoff)
                attempt += 1
                continue
            raise
        status = resp.status_code
        if status >= 400:
            note(f"http_{status}:{host}")  # X5/X7 — the vendor's refusals by status (the 429 rate)
        if status in _RETRY_STATUS and attempt < max_retries:
            delay = _retry_after_seconds(resp)
            if delay is None:
                delay = min(backoff_base * (2**attempt), backoff_cap)
                if jitter:
                    delay = _jittered(delay)
            if on_throttle is not None and status in _THROTTLE_STATUS:
                on_throttle(delay)  # the whole pool pauses with this caller
            note("backoff_s", delay)  # X5 — time lost to a retry's backoff
            sleep(delay)
            attempt += 1
            continue
        resp.raise_for_status()
        return resp
