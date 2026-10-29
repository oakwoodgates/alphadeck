"""The shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7): a hit after a miss, each
part of the generation token clearing it, the LRU bound, single-flight, and never storing a value computed
across a generation. Pure: the token's three reads are stubbed, so no database is needed."""

from __future__ import annotations

import threading
from datetime import date

import pytest

from dashboards import cache as cache_mod
from dashboards.cache import DashboardCache

D = date(2026, 6, 1)
CONN = object()


class Counter:
    def __init__(self, value="v"):
        self.calls = 0
        self.value = value

    def __call__(self):
        self.calls += 1
        return f"{self.value}{self.calls}"


@pytest.fixture
def token_parts(monkeypatch):
    """The three token reads, each a value a test can move."""
    parts = {
        "run": ("2026-06-01T02:30:00+00:00", "finished", "x"),
        "basket": 1,
        "cards": ((1, "A"),),
    }
    monkeypatch.setattr(cache_mod, "latest_run_identity", lambda: parts["run"])
    monkeypatch.setattr(cache_mod, "latest_basket_snapshot", lambda conn: parts["basket"])
    monkeypatch.setattr(cache_mod, "live_cards", lambda conn: parts["cards"])
    return parts


def test_a_miss_computes_then_the_same_key_hits(token_parts):
    c, compute = DashboardCache(), Counter()
    first = c.get(CONN, "rotation-map", (), D, compute)
    second = c.get(CONN, "rotation-map", (), D, compute)
    assert (first.status, first.value) == ("computed", "v1")
    assert (second.status, second.value) == ("hit", "v1")
    assert second.compute_ms == first.compute_ms and compute.calls == 1


def test_the_key_is_slug_params_and_asof(token_parts):
    c, compute = DashboardCache(), Counter()
    c.get(CONN, "rotation-map", (), D, compute)
    assert c.get(CONN, "rotation-map", (True,), D, compute).status == "computed"
    assert c.get(CONN, "rotation-map", (), date(2026, 6, 2), compute).status == "computed"
    assert c.get(CONN, "other", (), D, compute).status == "computed"
    assert compute.calls == 4 and len(c) == 4


@pytest.mark.parametrize(
    "move",
    [
        lambda p: p.update(
            run=("2026-06-02T02:30:00+00:00", "running", None)
        ),  # a new pass started
        lambda p: p.update(run=(p["run"][0], "finished", "y")),  # the same pass finished
        lambda p: p.update(run=None),
        lambda p: p.update(basket=2),  # a roster edit
        lambda p: p.update(cards=((1, "A"), (2, "B"))),  # a card created / restored
        lambda p: p.update(cards=()),  # a card archived
        lambda p: p.update(cards=((1, "A renamed"),)),  # a rename
    ],
)
def test_each_token_part_moving_clears_the_whole_cache(token_parts, move):
    c, compute = DashboardCache(), Counter()
    c.get(CONN, "rotation-map", (), D, compute)
    c.get(CONN, "rotation-map", (), date(2026, 6, 2), compute)
    move(token_parts)
    again = c.get(CONN, "rotation-map", (), D, compute)
    assert again.status == "computed" and again.value == "v3"
    assert len(c) == 1  # the other as-of went with it


def test_the_production_token_reads_its_three_parts(token_parts):
    assert cache_mod.generation_token(CONN) == (
        token_parts["run"],
        token_parts["basket"],
        token_parts["cards"],
    )


def test_the_run_identity_comes_from_the_newest_artifact(monkeypatch):
    seen = {}

    def fake_list(**kw):
        seen.update(kw)
        return [{"started_at": "s", "status": "finished", "finished_at": "f", "theses": []}]

    monkeypatch.setattr(cache_mod, "list_run_logs", fake_list)
    assert cache_mod.latest_run_identity() == ("s", "finished", "f")
    assert seen == {"limit": 1}
    monkeypatch.setattr(cache_mod, "list_run_logs", lambda **kw: [])
    assert cache_mod.latest_run_identity() is None


def test_the_lru_bound(token_parts):
    c, compute = DashboardCache(max_entries=3), Counter()
    for day in (1, 2, 3):
        c.get(CONN, "s", (), date(2026, 6, day), compute)
    c.get(CONN, "s", (), date(2026, 6, 1), compute)  # touch day 1: day 2 is now the oldest
    c.get(CONN, "s", (), date(2026, 6, 4), compute)
    assert len(c) == 3
    assert c.get(CONN, "s", (), date(2026, 6, 1), compute).status == "hit"
    assert c.get(CONN, "s", (), date(2026, 6, 2), compute).status == "computed"


def test_the_default_bound_is_64():
    assert cache_mod.MAX_ENTRIES == 64 and DashboardCache()._max == 64


def test_single_flight_two_cold_requests_compute_once(token_parts):
    c = DashboardCache()
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow():
        calls.append(1)
        started.set()
        assert release.wait(5)
        return "payload"

    results = []
    first = threading.Thread(target=lambda: results.append(c.get(CONN, "s", (), D, slow)))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=lambda: results.append(c.get(CONN, "s", (), D, slow)))
    second.start()
    second.join(0.2)
    assert second.is_alive()  # waiting on the first request's compute, not computing its own
    release.set()
    first.join(5)
    second.join(5)
    assert len(calls) == 1
    assert sorted(r.status for r in results) == ["computed", "hit"]
    assert {r.value for r in results} == {"payload"}


def test_a_failed_compute_is_not_stored_and_the_next_request_retries(token_parts):
    c = DashboardCache()

    def boom():
        raise RuntimeError("db went away")

    with pytest.raises(RuntimeError):
        c.get(CONN, "s", (), D, boom)
    assert len(c) == 0
    assert c.get(CONN, "s", (), D, lambda: "ok").status == "computed"


def test_a_value_computed_across_a_generation_is_returned_but_not_stored(token_parts):
    c = DashboardCache()

    def compute_while_a_run_starts():
        token_parts["run"] = ("2026-06-02T02:30:00+00:00", "running", None)
        return "mid-run"

    got = c.get(CONN, "s", (), D, compute_while_a_run_starts)
    assert (got.status, got.value) == ("computed", "mid-run")
    assert len(c) == 0
    assert c.get(CONN, "s", (), D, lambda: "after").value == "after"


def test_the_shared_instance_and_helper(token_parts):
    cache_mod.CACHE.clear()
    try:
        got = cache_mod.cached(CONN, "s", (), D, lambda: 1)
        assert got.status == "computed"
        assert cache_mod.cached(CONN, "s", (), D, lambda: 2).value == 1
    finally:
        cache_mod.CACHE.clear()
