"""The one in-process sliding-window counter (``app/throttle.py``, security PR 5 step 4).

The login throttle and the OAuth throttles (registration, ``/authorize``) all count with it, so window aging,
the limit and the memory bound are tested once here. The guard below keeps a second deque-based counter from
growing elsewhere under ``app/``.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.throttle import SlidingWindow

_APP = Path(__file__).resolve().parents[2] / "app"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_limit_counts_events_inside_the_window():
    clock = Clock()
    w = SlidingWindow(limit=3, window_s=60, clock=clock)
    for _ in range(3):
        assert w.allowed("a")
        w.record("a")
    assert not w.allowed("a")
    assert w.allowed("b")  # keys are independent
    assert w.count("a") == 3


def test_events_age_out_of_the_window():
    clock = Clock()
    w = SlidingWindow(limit=2, window_s=60, clock=clock)
    w.record("a")
    clock.now += 30
    w.record("a")
    assert not w.allowed("a")
    clock.now += 31  # the first event is now 61 s old
    assert w.allowed("a") and w.count("a") == 1
    clock.now += 60
    assert w.count("a") == 0


def test_record_returns_the_count_inside_the_window():
    clock = Clock()
    w = SlidingWindow(limit=5, window_s=10, clock=clock)
    assert [w.record("a") for _ in range(3)] == [1, 2, 3]
    clock.now += 11
    assert w.record("a") == 1


def test_a_limit_of_zero_means_no_limit():
    w = SlidingWindow(limit=0, window_s=60, clock=Clock())
    for _ in range(1000):
        w.record("a")
    assert w.allowed("a")


def test_the_limit_and_window_can_be_given_per_call():
    clock = Clock()
    w = SlidingWindow(limit=100, window_s=3600, clock=clock)
    w.record("a")
    w.record("a")
    assert not w.allowed("a", limit=2)
    assert w.allowed("a", limit=3)
    clock.now += 11
    assert (
        w.count("a", window_s=10) == 0
    )  # the window in force now decides (a setting read at call time)


def test_memory_stays_bounded_when_keys_age_out():
    clock = Clock()
    w = SlidingWindow(limit=5, window_s=60, clock=clock, max_keys=10_000)
    for i in range(20_000):
        w.record(f"10.0.{i // 256}.{i % 256}")
        clock.now += 0.01  # 200 s in all: most keys age out along the way
    assert len(w) < 10_000


def test_an_emptied_key_is_dropped_when_read():
    clock = Clock()
    w = SlidingWindow(limit=5, window_s=60, clock=clock)
    w.record("a")
    clock.now += 61
    assert w.allowed("a")
    assert len(w) == 0


def test_reset_forgets_everything():
    w = SlidingWindow(limit=1, window_s=60, clock=Clock())
    w.record("a")
    w.reset()
    assert w.allowed("a") and len(w) == 0


# The deque-based structures under app/ that are NOT throttles (job registries keep jobs, not counts).
_EXEMPT = {"throttle.py"}


def test_no_other_module_under_app_grows_its_own_deque_counter():
    offenders = [
        str(p.relative_to(_APP))
        for p in sorted(_APP.rglob("*.py"))
        if p.name not in _EXEMPT and re.search(r"\bdeque\(", p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
