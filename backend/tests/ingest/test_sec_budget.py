"""X2 — the global SEC budget (``ingest/sec_budget.py``), proved on the real test Postgres.

The headline: ANY two reserved slots are at least ``1/rate`` apart whichever connection — whichever PROCESS —
reserved them. Asserted on the slots the database handed out (exact, not a stopwatch), across two
connections in one process and across a real second Python process. Then the fair share (a lane with few
threads still gets half the budget against a lane with many; the control shows plain FIFO would not), a lane
alone gets the whole budget, a 429 pauses every lane, a missing row heals, and a database fault falls back
to a local half-rate limiter instead of failing the fetch.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from db.session import connect
from domain.market_time import market_now
from ingest import sec_budget as SB
from ingest.edgar import client as client_mod
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.http import polite_get
from ingest.sec_budget import BudgetStore, SecBudget

_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def budget_row(_migrated):
    """An empty budget before and after (a test that reserves far ahead must not slow the next one)."""

    def clear() -> None:
        c = connect()
        try:
            c.execute("DELETE FROM sec_request_budget")
            c.commit()
        finally:
            c.close()

    clear()
    yield
    clear()


def _gaps(slots: list[datetime]) -> list[float]:
    ordered = sorted(slots)
    return [(b - a).total_seconds() for a, b in zip(ordered, ordered[1:], strict=False)]


# --- the cap ---------------------------------------------------------------------------------------------


def test_the_cap_holds_across_two_CONNECTIONS_hammered_by_threads(budget_row):
    """Two budgets on two separate database sessions (what two processes are), eight threads hammering with
    no sleep at all: every reserved slot is at least one interval from every other."""
    rate = 20.0
    stores = [BudgetStore(), BudgetStore()]
    budgets = [
        SecBudget(lane, rate_per_sec=rate, store=s, sleep=lambda _s: None, depth=8)
        for lane, s in zip(("recurring", "interactive"), stores, strict=True)
    ]
    slots: list[datetime] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def hammer(b: SecBudget) -> None:
        barrier.wait()
        for _ in range(25):
            s = b.acquire()
            with lock:
                slots.append(s)

    threads = [threading.Thread(target=hammer, args=(budgets[i % 2],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for s in stores:
        s.close()

    assert len(slots) == 200 and None not in slots
    assert min(_gaps(slots)) >= 1 / rate - 1e-6  # the global spacing — never two slots closer


_CHILD = """
import json, sys
from ingest.sec_budget import BudgetStore, SecBudget
b = SecBudget("interactive", rate_per_sec=float(sys.argv[1]), store=BudgetStore())
b.acquire()  # connected and warm before the go signal
print("ready", flush=True)
sys.stdin.readline()
print(json.dumps([b.acquire().isoformat() for _ in range(int(sys.argv[2]))]), flush=True)
"""


def test_the_cap_holds_across_two_PROCESSES(budget_row):
    """A real second Python process reserving at the same time as this one: the merged slots still never
    come closer than one interval, and the two processes genuinely interleave."""
    rate, n = 25.0, 20
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(rate), str(n)],
        cwd=_BACKEND,
        env={**os.environ},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline().strip() == "ready"
        mine = SecBudget("recurring", rate_per_sec=rate, store=BudgetStore())
        child.stdin.write("go\n")
        child.stdin.flush()
        ours = [mine.acquire() for _ in range(n)]
        theirs = [datetime.fromisoformat(s) for s in json.loads(child.stdout.readline())]
    finally:
        child.kill()
        child.wait(timeout=30)

    assert min(_gaps(ours + theirs)) >= 1 / rate - 1e-6
    # interleaved, not one process after the other: each one's slots straddle the other's
    assert min(theirs) < max(ours) and min(ours) < max(theirs)


# --- the fair share --------------------------------------------------------------------------------------


def _contend(depth: int, rate: float = 40.0, total: int = 60) -> tuple[int, int]:
    """Lane A (8 threads) and lane B (2 threads), each on its own session, each thread taking a slot then
    "sending" for 10 ms, until ``total`` slots were granted. Returns (A's slots, B's slots). Counted by SLOTS,
    not by a stopwatch, so a stalled host only makes it slower, never changes the share."""
    stores = [BudgetStore(), BudgetStore()]
    for (
        s
    ) in stores:  # connect BEFORE contending: a session's first connect is not the budget's doing
        s._run("SELECT 1", {}, fetch=True)
    a = SecBudget("interactive", rate_per_sec=rate, store=stores[0], depth=depth)
    b = SecBudget("recurring", rate_per_sec=rate, store=stores[1], depth=depth)
    counts = {"a": 0, "b": 0}
    lock = threading.Lock()
    start = threading.Barrier(10)
    done = threading.Event()

    def run(budget: SecBudget, key: str) -> None:
        start.wait()
        while not done.is_set():
            budget.acquire()
            with lock:
                if counts["a"] + counts["b"] < total:
                    counts[key] += 1
                else:
                    done.set()
            time.sleep(0.01)

    threads = [threading.Thread(target=run, args=(a, "a")) for _ in range(8)]
    threads += [threading.Thread(target=run, args=(b, "b")) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for s in stores:
        s.close()
    return counts["a"], counts["b"]


def test_the_FAIR_SHARE_a_lane_with_few_threads_still_gets_half(budget_row):
    """Discovery-like lane A (8 threads) against a nightly-like lane B (2 threads): B gets ~half the slots,
    not the ~1/5 its thread count would get from plain FIFO — neither lane starves the other."""
    a, b = _contend(depth=SB.LANE_DEPTH)
    assert b / (a + b) >= 0.4, (a, b)
    assert a / (a + b) >= 0.4, (a, b)


def test_CONTROL_without_the_lane_depth_FIFO_gives_the_many_threaded_lane_most(budget_row):
    """The control that keeps the fair-share test honest: with the per-lane depth effectively removed, the
    same contention gives lane B roughly its thread share — so the depth, not luck, made it fair."""
    a, b = _contend(depth=100)
    assert b / (a + b) < 0.32, (a, b)


def test_a_lane_ALONE_gets_the_whole_budget(budget_row):
    rate = 40.0
    b = SecBudget("recurring", rate_per_sec=rate, store=BudgetStore())
    got: list[datetime] = []
    lock = threading.Lock()
    stop = time.monotonic() + 1.0

    def run() -> None:
        while time.monotonic() < stop:
            s = b.acquire()
            with lock:
                got.append(s)
            time.sleep(0.01)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) >= 0.8 * rate  # ~ the full rate over the second, from one lane
    assert min(_gaps(got)) >= 1 / rate - 1e-6


# --- penalize, self-heal, fail-safe ----------------------------------------------------------------------


def test_a_429_PAUSES_every_lane(budget_row):
    """``penalize`` pushes the ONE global next slot out: the very next reservation — any lane, any process —
    lands after the pause."""
    store = BudgetStore()
    try:
        store.reserve(0.01)
        store.penalize(1.0)
        slot, db_now = store.reserve(0.01)
    finally:
        store.close()
    assert (slot - db_now).total_seconds() >= 0.95


def test_a_missing_row_is_re_created_by_the_reservation(budget_row):
    store = BudgetStore()
    try:
        slot, db_now = store.reserve(0.1)  # the table is empty (the fixture deleted the row)
        assert slot == db_now  # the first slot is now
        slot2, _ = store.reserve(0.1)
        assert (slot2 - slot).total_seconds() == pytest.approx(0.1)
    finally:
        store.close()
    c = connect()
    try:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) n FROM sec_request_budget")
            assert cur.fetchone()["n"] == 1
    finally:
        c.close()


class _BrokenStore:
    def __init__(self) -> None:
        self.calls = 0

    def reserve(self, step_s):
        self.calls += 1
        raise RuntimeError("connection refused")

    def penalize(self, seconds):
        raise RuntimeError("connection refused")


class _GoodStore:
    def __init__(self) -> None:
        self.calls = 0

    def reserve(self, step_s):
        self.calls += 1
        now = market_now()  # any aware instant: a slot that is due at once
        return now, now


def test_a_database_fault_FALLS_BACK_to_a_local_half_rate_limiter_and_recovers(caplog):
    broken = _BrokenStore()
    b = SecBudget("recurring", rate_per_sec=8.0, store=broken)
    assert b.acquire() is None  # paced locally, never raised
    assert b.degraded and broken.calls == 1
    assert b._fallback.max_per_sec == pytest.approx(4.0)  # half the global rate
    assert (
        b.acquire() is None and broken.calls == 1
    )  # the database is not re-asked inside the window
    assert sum("database unavailable" in r.getMessage() for r in caplog.records) == 1  # logged ONCE
    b.penalize(1.0)  # a fault here falls back too — never raises

    good = _GoodStore()
    b._store = good
    b._down_until = 0.0  # the retry window has passed
    assert b.acquire() is not None and not b.degraded and good.calls == 1


# --- the client wiring -----------------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.acquired = 0
        self.penalties: list[float] = []

    def acquire(self):
        self.acquired += 1

    def penalize(self, seconds):
        self.penalties.append(seconds)


def test_every_live_SEC_attempt_draws_from_the_budget_and_a_429_pauses_it(tmp_path, monkeypatch):
    replies = iter(
        [
            httpx.Response(429, request=httpx.Request("GET", "https://data.sec.gov/x")),
            httpx.Response(200, request=httpx.Request("GET", "https://data.sec.gov/x"), text="{}"),
        ]
    )
    monkeypatch.setattr(httpx, "get", lambda url, **kw: next(replies))
    slept: list[float] = []
    monkeypatch.setattr(client_mod, "polite_get", functools.partial(polite_get, sleep=slept.append))
    budget = _Recorder()
    c = EdgarClient(cache_dir=tmp_path, allow_live=True, user_agent="ua", budget=budget)

    assert c.get_text("https://data.sec.gov/x", "misc/x.json") == "{}"
    assert budget.acquired == 2  # the first attempt AND the retry
    assert len(budget.penalties) == 1 and 1.0 <= budget.penalties[0] <= 1.5  # jittered backoff
    assert slept == budget.penalties  # the same pause, for this thread and every other
    assert c.live_fetches == 1 and c.live_completed == 1


def test_the_lane_follows_the_dial(monkeypatch):
    SB.reset_sec_budgets()
    try:
        recurring = EdgarClient(allow_live=True, cache_ttl_s=RECURRING_CACHE_TTL_S)
        interactive = EdgarClient(allow_live=True)
        assert recurring._sec_budget().lane == SB.LANE_RECURRING
        assert interactive._sec_budget().lane == SB.LANE_INTERACTIVE
        assert (
            recurring._sec_budget()
            is EdgarClient(allow_live=True, cache_ttl_s=RECURRING_CACHE_TTL_S)._sec_budget()
        )  # one budget per lane per process, shared by every client
    finally:
        SB.reset_sec_budgets()


@pytest.mark.parametrize("module", ["securities.sec_tickers", "securities.fund_tickers"])
def test_the_universe_pulls_draw_from_the_budget_too(module, monkeypatch):
    import importlib

    mod = importlib.import_module(module)
    budget = _Recorder()
    monkeypatch.setattr(mod, "sec_budget", lambda lane: budget)
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kw: httpx.Response(200, request=httpx.Request("GET", url), json={}),
    )
    mod._fetch_live("ua")
    assert budget.acquired == 1


def test_the_default_rate_keeps_headroom_under_SECs_ten_per_second():
    import math

    from domain.settings import Settings

    rate = Settings().sec_global_rate_per_sec
    assert rate == 8.5
    # slots 1/rate apart: a closed one-second window holds at most floor(rate) + 1 request starts
    assert math.floor(rate) + 1 == 9 < 10
