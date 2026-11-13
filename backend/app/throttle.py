"""The one in-process sliding-window counter every throttle in the API uses (security PR 5).

``SlidingWindow`` counts events per key inside a moving window: the login throttle (failed sign-ins per client
address, ``app/auth.py``), the registration throttle and the ``/authorize`` per-address limit
(``app/oauth/server.py``, ``app/oauth/provider.py``). In-process like the job registries: the API runs one worker
by design (``backend/Dockerfile``), and a restart forgets every count (a throttle, not a record).

Memory is bounded: a key whose events have all aged out is dropped when it is next read, and when the map passes
``max_keys`` every aged-out key is swept. Keys still inside their window are kept (they are live callers).

A limit or window of 0 means no limit. Both may be passed per call, so a throttle reads its setting when it runs
(tests change settings between calls). The clock is injectable (tests).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

DEFAULT_MAX_KEYS = 10_000


class SlidingWindow:
    """Events per key inside the last ``window_s`` seconds; ``allowed`` while fewer than ``limit``."""

    def __init__(
        self,
        *,
        limit: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}

    def _trim(self, key: str, now: float, window_s: float) -> deque[float] | None:
        q = self._events.get(key)
        if q is None:
            return None
        while q and now - q[0] > window_s:
            q.popleft()
        if not q:
            del self._events[key]
            return None
        return q

    def _sweep(self, now: float, window_s: float) -> None:
        for key in [k for k, q in self._events.items() if now - q[-1] > window_s]:
            del self._events[key]

    def count(self, key: str, *, window_s: float | None = None, now: float | None = None) -> int:
        """How many events ``key`` has inside the window."""
        now = self.clock() if now is None else now
        with self._lock:
            q = self._trim(key, now, self.window_s if window_s is None else window_s)
            return len(q) if q is not None else 0

    def allowed(
        self,
        key: str,
        *,
        limit: int | None = None,
        window_s: float | None = None,
        now: float | None = None,
    ) -> bool:
        """True while ``key`` has fewer than ``limit`` events inside the window (always True for limit 0)."""
        limit = self.limit if limit is None else limit
        if limit <= 0:
            return True
        return self.count(key, window_s=window_s, now=now) < limit

    def record(self, key: str, *, window_s: float | None = None, now: float | None = None) -> int:
        """Count one event for ``key``; returns its count inside the window, this one included."""
        now = self.clock() if now is None else now
        window_s = self.window_s if window_s is None else window_s
        with self._lock:
            q = self._trim(key, now, window_s)
            if q is None:
                if len(self._events) >= self.max_keys:
                    self._sweep(now, window_s)
                q = self._events[key] = deque()
            q.append(now)
            return len(q)

    def forget(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        """Forget every count (tests)."""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


__all__ = ["DEFAULT_MAX_KEYS", "SlidingWindow"]
