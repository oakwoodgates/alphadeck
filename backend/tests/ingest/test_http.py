from __future__ import annotations

import httpx
import pytest

from ingest.http import polite_get


class _FakeResp:
    """A stand-in httpx Response: only the bits polite_get touches."""

    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _seq(monkeypatch, responses):
    """Make httpx.get return the given responses in order; record the call URLs."""
    it = iter(responses)
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return next(it)

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
