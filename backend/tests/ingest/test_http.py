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


# --- X7: jitter, the pool-wide pause on a 429, and the refusals counted by status ---


def test_jittered_backoff_is_never_SHORTER_than_the_plain_backoff_and_is_spread(monkeypatch):
    _seq(monkeypatch, [_FakeResp(429), _FakeResp(503), _FakeResp(429), _FakeResp(200)])
    draws = iter([0.0, 1.0, 0.5])  # random.random() -> the jitter factor 1 + 0.5 * draw
    monkeypatch.setattr(http_mod.random, "random", lambda: next(draws))
    slept: list[float] = []
    polite_get("http://x", jitter=True, sleep=slept.append)
    assert slept == [1.0, 3.0, 5.0]  # 1x1.0, 2x1.5, 4x1.25 — each within [base, 1.5 * base]


def test_jitter_samples_stay_inside_their_band():
    samples = [http_mod._jittered(2.0) for _ in range(200)]
    assert all(2.0 <= s <= 3.0 for s in samples)
    assert len({round(s, 6) for s in samples}) > 1  # actually spread, not a constant


def test_a_numeric_retry_after_is_honored_EXACTLY_even_with_jitter(monkeypatch):
    _seq(monkeypatch, [_FakeResp(429, {"Retry-After": "7"}), _FakeResp(200)])
    slept: list[float] = []
    polite_get("http://x", jitter=True, sleep=slept.append)
    assert slept == [7.0]  # the server named the instant; jitter applies to OUR guesses only


def test_on_throttle_fires_for_429_and_503_but_not_for_500(monkeypatch):
    _seq(monkeypatch, [_FakeResp(429), _FakeResp(500), _FakeResp(503), _FakeResp(200)])
    throttled: list[float] = []
    polite_get("http://x", max_retries=3, sleep=lambda s: None, on_throttle=throttled.append)
    assert throttled == [
        1.0,
        4.0,
    ]  # the 429 (attempt 0) and the 503 (attempt 2); the 500 is a fault


def test_penalize_makes_EVERY_waiter_back_off(monkeypatch):
    """One thread is told 429; the shared limiter pushes its next slot out, so the NEXT caller — any thread —
    waits out the pause too (deterministic clock, no real time)."""
    clock = [100.0]
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock[0] += s

    monkeypatch.setattr(http_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http_mod.time, "sleep", fake_sleep)
    rl = RateLimiter(max_per_sec=10)
    rl.acquire()  # slot at 100.0
    rl.penalize(5.0)  # told "too many" at 100.0
    rl.acquire()
    assert sleeps == pytest.approx([5.0])
    rl.penalize(0.0)  # a zero / negative pause never shortens anything
    rl.acquire()
    assert sleeps == pytest.approx([5.0, 0.1])


def test_the_yahoo_gate_is_one_process_wide_limiter_read_from_settings(monkeypatch):
    from domain.settings import get_settings

    monkeypatch.setenv("ALPHADECK_YAHOO_RATE_PER_SEC", "2.5")
    get_settings.cache_clear()
    http_mod.reset_yahoo_limiter()
    try:
        gate = http_mod.yahoo_limiter()
        assert gate is http_mod.yahoo_limiter()  # one gate for every puller in the process
        assert gate.max_per_sec == pytest.approx(2.5)
    finally:
        http_mod.reset_yahoo_limiter()
        get_settings.cache_clear()


def test_the_yahoo_default_is_the_conservative_four_per_second():
    from domain.settings import Settings

    assert Settings().yahoo_rate_per_sec == 4.0


def test_a_YAHOO_429_backs_off_with_jitter_pauses_the_pool_and_is_COUNTED(monkeypatch, tmp_path):
    """The price fetch end to end with only the HTTP layer stubbed: two 429s then the series. The pull is
    retried after a jittered backoff, the shared Yahoo gate is pushed back by each pause (every other
    puller waits it out too), each refusal is counted on the pass meter, and the series lands in the cache
    in one piece."""
    import functools
    import json

    from ingest.instrument import ACTIVE
    from ingest.prices import eod_loader
    from pipeline import pass_timings

    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1759363200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [1.0],
                                "high": [1.0],
                                "low": [1.0],
                                "close": [1.0],
                                "volume": [9],
                            }
                        ]
                    },
                }
            ]
        }
    }

    body = json.dumps(payload)

    class _Resp(_FakeResp):
        text = body

        def json(self):
            return payload

    calls = _seq(monkeypatch, [_Resp(429), _Resp(429), _Resp(200)])
    slept: list[float] = []
    monkeypatch.setattr(eod_loader, "polite_get", functools.partial(polite_get, sleep=slept.append))
    http_mod.reset_yahoo_limiter()
    timer = pass_timings.start()
    token = ACTIVE.set(timer)
    try:
        gate = http_mod.yahoo_limiter()
        before = time.monotonic()
        bars = pass_timings.timed("price")(eod_loader.fetch_eod)(
            "ABC", cache_dir=tmp_path, allow_live=True, force_refresh=True
        )
        snap = timer.snapshot()
    finally:
        ACTIVE.reset(token)
        http_mod.reset_yahoo_limiter()

    assert len(calls) == 3 and len(bars) == 1
    assert 1.0 <= slept[0] <= 1.5 and 2.0 <= slept[1] <= 3.0  # jittered exponential backoff
    assert gate._next >= before + slept[1]  # the gate was pushed back: the whole pool pauses
    counts = snap["counts"]["price"]
    assert counts["http_429:query1.finance.yahoo.com"] == 2  # the 429 rate, on the artifact
    assert counts["http:query1.finance.yahoo.com"] == 3
    assert json.loads((tmp_path / "ABC.yahoo.json").read_text(encoding="utf-8")) == payload
    assert pass_timings.summary_line(snap).endswith("HTTP 429s: query1.finance.yahoo.com 2")


def test_every_refusal_is_counted_by_status_and_host(monkeypatch):
    from ingest.instrument import ACTIVE
    from pipeline import pass_timings

    _seq(monkeypatch, [_FakeResp(404)])
    timer = pass_timings.start()
    token = ACTIVE.set(timer)
    try:
        with pytest.raises(RuntimeError):
            polite_get(
                "https://query1.finance.yahoo.com/v8/finance/chart/GONE", sleep=lambda s: None
            )
        snap = timer.snapshot()
    finally:
        ACTIVE.reset(token)
    assert snap["counts"]["other"] == {
        "http:query1.finance.yahoo.com": 1,
        "http_404:query1.finance.yahoo.com": 1,
    }
