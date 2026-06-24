from __future__ import annotations

import threading
import time

import httpx
import pytest

from ingest import http as http_mod
from ingest.http import RateLimiter, polite_get


class _FakeResp:
    """A stand-in httpx Response: only the bits polite_get touches."""

    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _seq(monkeypatch, responses):
    """Make httpx.get return (or RAISE, for an exception item) the given responses in order; record call URLs."""
    it = iter(responses)
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        x = next(it)
        if isinstance(x, BaseException):
            raise x
        return x

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_returns_immediately_on_200(monkeypatch):
    calls = _seq(monkeypatch, [_FakeResp(200)])
    slept: list[float] = []
    resp = polite_get("http://x", sleep=lambda s: slept.append(s))
    assert resp.status_code == 200
    assert len(calls) == 1 and slept == []  # no retry, no backoff


def test_retries_429_then_succeeds(monkeypatch):
    calls = _seq(monkeypatch, [_FakeResp(429), _FakeResp(200)])
    slept: list[float] = []
    resp = polite_get("http://x", sleep=lambda s: slept.append(s))
    assert resp.status_code == 200
    assert len(calls) == 2 and len(slept) == 1  # backed off once before the retry


def test_retries_transient_5xx(monkeypatch):
    calls = _seq(monkeypatch, [_FakeResp(503), _FakeResp(200)])
    slept: list[float] = []
    resp = polite_get("http://x", sleep=lambda s: slept.append(s))
    assert resp.status_code == 200 and len(calls) == 2 and len(slept) == 1


def test_honors_numeric_retry_after(monkeypatch):
    _seq(monkeypatch, [_FakeResp(429, {"Retry-After": "5"}), _FakeResp(200)])
    slept: list[float] = []
    polite_get("http://x", sleep=lambda s: slept.append(s))
    assert slept == [5.0]  # the server's Retry-After wins over exponential backoff


def test_gives_up_after_max_retries(monkeypatch):
    calls = _seq(monkeypatch, [_FakeResp(429)] * 5)
    slept: list[float] = []
    with pytest.raises(RuntimeError):
        polite_get("http://x", max_retries=2, sleep=lambda s: slept.append(s))
    assert len(calls) == 3 and len(slept) == 2  # initial + 2 retries, slept before each retry


def test_does_not_retry_a_404(monkeypatch):
    calls = _seq(monkeypatch, [_FakeResp(404), _FakeResp(200)])
    slept: list[float] = []
    with pytest.raises(RuntimeError):
        polite_get("http://x", sleep=lambda s: slept.append(s))
    assert len(calls) == 1 and slept == []  # a non-transient 4xx raises immediately


def test_pre_hook_runs_before_every_attempt(monkeypatch):
    _seq(monkeypatch, [_FakeResp(429), _FakeResp(200)])
    pres: list[int] = []
    polite_get("http://x", pre=lambda: pres.append(1), sleep=lambda s: None)
    assert len(pres) == 2  # the throttle runs before the retry too, not just the first try


def test_retries_transient_network_blip_then_succeeds(monkeypatch):
    """The class of failure that silently broke the parallel discovery fan-out: a transient httpx transport
    error (connect / read-timeout / protocol). It is now retried with backoff like a 5xx — and the SHARED rate
    limiter (``pre``) still runs before the retry, never bypassed."""
    calls = _seq(monkeypatch, [httpx.ConnectError("conn reset"), _FakeResp(200)])
    slept: list[float] = []
    pres: list[int] = []
    resp = polite_get("http://x", pre=lambda: pres.append(1), sleep=lambda s: slept.append(s))
    assert resp.status_code == 200
    assert len(calls) == 2 and len(slept) == 1  # retried once after the blip
    assert (
        len(pres) == 2
    )  # rate-limited on the retry too (shared throttle, not a per-call/bypassed limiter)


def test_gives_up_after_network_retries_exhausted(monkeypatch):
    """A PERSISTENT transport error raises after the budget is spent — discover()'s per-page guard then
    skips/threshold-fails it, never silently empties the universe."""
    calls = _seq(monkeypatch, [httpx.ReadTimeout("slow")] * 5)
    with pytest.raises(httpx.TransportError):
        polite_get("http://x", max_retries=2, sleep=lambda s: None)
    assert len(calls) == 3  # initial + 2 retries


# --- RateLimiter: the SHARED, thread-safe throttle behind the parallel EFTS fan-out ---


def test_rate_limiter_reserves_evenly_spaced_slots(monkeypatch):
    """Deterministic (no real time): each ``acquire`` RESERVES the next slot one interval past the last, so
    consecutive callers sleep exactly one interval. The reservation arithmetic is what keeps concurrent callers
    from colliding on the same slot."""
    clock = [0.0]
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock[0] += s  # the sleeping thread advances to its reserved slot

    monkeypatch.setattr(http_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_mod.time, "sleep", fake_sleep)

    rl = RateLimiter(max_per_sec=10)  # interval 0.1s
    for _ in range(4):
        rl.acquire()
    # first slot is free (now); each subsequent slot is exactly one interval later
    assert sleeps == pytest.approx([0.1, 0.1, 0.1])


def test_rate_limiter_is_the_shared_throttle_under_threads():
    """The load-bearing concurrency guarantee: N threads hammering ONE shared limiter at once get N DISTINCT
    slots spaced by >= the interval — so the discovery thread-pool can fan out EFTS pages without exceeding the
    SEC budget. The lower bound (n-1)*interval is enforced by the algorithm regardless of machine speed (a slow
    box only makes it longer), so this is not timing-flaky."""
    n, rate = 12, 50.0  # interval 20ms
    interval = 1.0 / rate
    rl = RateLimiter(max_per_sec=rate)
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()  # release all threads to contend simultaneously
        rl.acquire()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0
    # n requests took >= (n-1)*interval -> the global rate never exceeded max_per_sec (concurrency didn't
    # collapse the slots). 0.8 leaves margin for sleep granularity; it can only run LONGER, never shorter.
    assert elapsed >= (n - 1) * interval * 0.8
