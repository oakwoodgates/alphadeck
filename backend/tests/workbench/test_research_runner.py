"""The research cost-safety wrapper (``workbench.research_runner``): the in-flight guard (ATOMIC claim +
``finally``-release) and the TTL cache. Pure in-process — no DB, no network, no key.

These guard the $8-and-nothing failure mode: a re-fire must NEVER launch a parallel (expensive) Opus call, and a
failed/timed-out run must NEVER strand a thesis permanently in-flight.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest

from workbench import research_runner
from workbench.research_runner import ResearchInFlight, run_research


@pytest.fixture(autouse=True)
def _reset_state():
    """The registry + cache are module-level (single-process), so they persist across tests — reset around each."""
    research_runner.reset_state()
    yield
    research_runner.reset_state()


def test_runs_and_returns_the_synthesis():
    assert (
        run_research(uuid4(), "a narrative", ttl_s=0, run=lambda: "Oklo (OKLO).") == "Oklo (OKLO)."
    )


def test_sequential_second_draft_after_completion_succeeds():
    tid = uuid4()
    assert run_research(tid, "n", ttl_s=0, run=lambda: "first") == "first"
    # the first finished + freed the key -> a second draft runs (not blocked)
    assert run_research(tid, "n", ttl_s=0, run=lambda: "second") == "second"


def test_concurrent_second_draft_raises_inflight_and_run_fires_once():
    """ATOMIC claim under concurrency: while a pass for a thesis is running (held via an Event), a concurrent
    second draft for the SAME thesis raises ResearchInFlight — and ``run`` fires exactly once. (Two threads must
    not both pass the check before either claims, which would launch two Opus calls.)"""
    tid = uuid4()
    in_run = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    results: dict[str, object] = {}

    def slow_run():
        calls.append(1)
        in_run.set()  # the first pass has claimed + entered run()
        release.wait(timeout=5)  # hold the in-flight state
        return "synthesis"

    def worker(name, run):
        try:
            results[name] = run_research(tid, "n", ttl_s=0, run=run)
        except ResearchInFlight:
            results[name] = "INFLIGHT"

    t1 = threading.Thread(target=worker, args=("a", slow_run))
    t1.start()
    assert in_run.wait(timeout=5)  # t1 now holds the key inside run()
    t2 = threading.Thread(target=worker, args=("b", lambda: "should-not-run"))
    t2.start()
    t2.join(timeout=5)
    assert results["b"] == "INFLIGHT"  # the concurrent second draft was rejected
    release.set()
    t1.join(timeout=5)
    assert results["a"] == "synthesis"
    assert calls == [1]  # run fired EXACTLY once (no parallel Opus call)


def test_failed_run_frees_the_key_so_a_retry_succeeds():
    """``finally``-release: a raising run frees the key — a failed/timed-out draft must NOT brick the thesis
    (every future draft 409ing forever). The exception still propagates."""
    tid = uuid4()

    def boom():
        raise RuntimeError("research blew up / timed out")

    with pytest.raises(RuntimeError):
        run_research(tid, "n", ttl_s=0, run=boom)
    # the key was freed in `finally` -> a second draft for the same thesis SUCCEEDS
    assert run_research(tid, "n", ttl_s=0, run=lambda: "recovered") == "recovered"


def test_cache_hit_within_ttl_returns_stored_without_calling_run():
    tid = uuid4()
    assert run_research(tid, "n", ttl_s=3600, run=lambda: "stored") == "stored"

    def must_not_run():
        raise AssertionError("run() called on a cache hit — re-spend!")

    assert run_research(tid, "n", ttl_s=3600, run=must_not_run) == "stored"


def test_ttl_zero_disables_the_cache_always_fresh():
    """ttl=0 = the convergence gate-2 mode: every run is fresh, so a cache hit can't mask convergence."""
    tid = uuid4()
    assert run_research(tid, "n", ttl_s=0, run=lambda: "run1") == "run1"
    assert run_research(tid, "n", ttl_s=0, run=lambda: "run2") == "run2"


def test_none_result_is_not_cached():
    """A failed/empty research (None) is never cached — the next draft retries rather than being stranded on
    recall-only for the whole TTL."""
    tid = uuid4()
    assert run_research(tid, "n", ttl_s=3600, run=lambda: None) is None
    assert run_research(tid, "n", ttl_s=3600, run=lambda: "now-it-works") == "now-it-works"


def test_distinct_narrative_is_a_distinct_cache_key():
    tid = uuid4()
    assert run_research(tid, "narrative one", ttl_s=3600, run=lambda: "one") == "one"
    # a DIFFERENT narrative for the same thesis is a cache MISS -> fresh research
    assert run_research(tid, "narrative two", ttl_s=3600, run=lambda: "two") == "two"


def test_cache_expires_after_ttl(monkeypatch):
    """Past the TTL, a re-draft re-runs — bounding staleness so the next rebrand isn't re-stranded."""
    tid = uuid4()
    clock = {"t": 1000.0}
    monkeypatch.setattr(research_runner, "monotonic", lambda: clock["t"])
    assert run_research(tid, "n", ttl_s=60, run=lambda: "fresh") == "fresh"
    clock["t"] += 61  # past the TTL
    assert run_research(tid, "n", ttl_s=60, run=lambda: "refreshed") == "refreshed"
