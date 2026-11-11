"""Pieces (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7): one company's result shared by every page of
a cache generation. ``read_price_flags`` and the display members ``display_members`` hands out read a company once
per generation, view and workspace; outside a page compute they behave exactly as before. Pure: the generation
token is injected, the flags reader and the members are stand-ins that COUNT their calls, so no database is
needed."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from dashboards import baskets
from dashboards import cache as cache_mod
from dashboards.baskets import SharedMember, per_company, read_price_flags
from dashboards.cache import DashboardCache
from db.session import DEFAULT_TENANT_ID
from domain.data_flags import MIXED_SCALE, DataFlag, MemberFlags
from signals.display.base import DisplayBasis, DisplaySignal

D = date(2026, 6, 1)
K = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
CONN = object()
W = DEFAULT_TENANT_ID
V = uuid.UUID("00000000-0000-0000-0000-0000000000b2")  # a second workspace

# a flag that reaches a price cell (``ret_1y`` is one of ``PRICE_CELLS``)
PRICED = DataFlag(kind=MIXED_SCALE, level="warn", reason="r", clears="c", columns=("ret_1y",))


@pytest.fixture
def token():
    """A generation token a test can move."""
    return {"v": 1}


@pytest.fixture
def cache(token):
    return DashboardCache(token=lambda conn: token["v"])


@pytest.fixture
def flags(monkeypatch):
    """A stand-in ``read_flags``: every company in ``flagged`` carries a price-reaching flag; each call's asked
    ids are recorded."""
    state: dict = {"calls": [], "flagged": set(), "run_reads": 0, "tickers": 0}

    def read_flags(conn, **kw):
        state["calls"].append(list(kw["security_ids"]))
        return {
            sid: MemberFlags(
                security_id=sid,
                ticker="X",
                flags=(PRICED,) if sid in state["flagged"] else (),
            )
            for sid in kw["security_ids"]
        }

    def run_reads():
        state["run_reads"] += 1
        return ({}, {})

    def tickers_for(conn, ids, tenant_id):
        state["tickers"] += 1
        return {s: "X" for s in ids}

    monkeypatch.setattr(baskets.flags_reader, "read_flags", read_flags)
    monkeypatch.setattr(baskets.flags_reader, "run_reads", run_reads)
    monkeypatch.setattr(baskets.master, "tickers_for", tickers_for)
    return state


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


def _flags_page(ids, *, asof=D, known_at=K, clock="public"):
    return lambda: read_price_flags(CONN, ids, asof, known_at=known_at, clock=clock)


# --- read_price_flags ------------------------------------------------------------------------------------------


def test_a_second_page_reads_no_company_the_first_one_read(cache, flags):
    ids = _ids(3)
    flags["flagged"] = {ids[0], ids[2]}
    a = cache.get(CONN, "page-a", (), D, _flags_page(ids), workspace=W)
    b = cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W)
    assert flags["calls"] == [ids]  # the first page only
    assert a.value == b.value and set(a.value) == {ids[0], ids[2]}
    # every company hit: the nightly readings, the tickers and the settings are not read either
    assert (flags["run_reads"], flags["tickers"]) == (1, 1)


def test_extra_companies_are_the_only_ones_read(cache, flags, monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    ids = _ids(5)
    cache.get(CONN, "page-a", (), D, _flags_page(ids[:2]), workspace=W)
    cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W)
    # the misses, in their asked order, CHUNK per call
    assert flags["calls"] == [ids[:2], ids[2:4], ids[4:]]


def test_a_stored_none_is_a_hit(cache, flags):
    ids = _ids(2)  # neither flagged: both pieces are None
    assert cache.get(CONN, "page-a", (), D, _flags_page(ids), workspace=W).value == {}
    assert cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W).value == {}
    assert flags["calls"] == [ids] and cache.piece_count() == 2


def test_a_generation_move_clears_the_pieces(cache, flags, token):
    ids = _ids(2)
    cache.get(CONN, "page-a", (), D, _flags_page(ids), workspace=W)
    token["v"] = 2
    cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W)
    assert flags["calls"] == [ids, ids]


def test_a_compute_the_generation_moved_under_stores_no_piece(cache, flags, token):
    ids = _ids(2)

    def moving():
        got = read_price_flags(CONN, ids, D, known_at=K, clock="public")
        token["v"] = 2
        return got

    flags["flagged"] = {ids[0]}
    got = cache.get(CONN, "page-a", (), D, moving, workspace=W)
    assert set(got.value) == {ids[0]}
    assert cache.piece_count() == 0 and len(cache) == 0
    cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W)
    assert flags["calls"] == [ids, ids]


def test_a_compute_that_raises_stores_no_piece(cache, flags):
    ids = _ids(2)

    def boom():
        read_price_flags(CONN, ids, D, known_at=K, clock="public")
        raise RuntimeError("db went away")

    with pytest.raises(RuntimeError):
        cache.get(CONN, "page-a", (), D, boom, workspace=W)
    assert cache.piece_count() == 0
    cache.get(CONN, "page-b", (), D, _flags_page(ids), workspace=W)
    assert flags["calls"] == [ids, ids]


def test_outside_a_page_compute_nothing_is_shared_or_stored(cache, flags):
    ids = _ids(2)
    flags["flagged"] = {ids[1]}
    first = read_price_flags(CONN, ids, D, known_at=K, clock="public")
    second = read_price_flags(CONN, ids, D, known_at=K, clock="public")
    assert first == second and set(first) == {ids[1]}
    assert flags["calls"] == [ids, ids] and cache.piece_count() == 0
    assert cache_mod.CACHE.piece_count() == 0


def test_two_workspaces_never_share_a_piece(cache, flags):
    ids = _ids(2)
    cache.get(CONN, "page-a", (), D, _flags_page(ids), workspace=W)
    cache.get(CONN, "page-a", (), D, _flags_page(ids), workspace=V)
    assert flags["calls"] == [ids, ids]


def test_a_different_asof_clock_or_known_at_does_not_share(cache, flags):
    ids = _ids(1)
    cache.get(CONN, "p", (), D, _flags_page(ids), workspace=W)
    cache.get(CONN, "p", (1,), D, _flags_page(ids, clock="record"), workspace=W)
    cache.get(CONN, "p", (2,), D, _flags_page(ids, known_at=None), workspace=W)
    cache.get(CONN, "p", (), date(2026, 6, 2), _flags_page(ids, asof=date(2026, 6, 2)), workspace=W)
    assert len(flags["calls"]) == 4


def test_a_fourth_group_evicts_the_least_recently_used(cache, flags):
    assert cache_mod.MAX_PIECE_GROUPS == 3
    ids = _ids(1)
    days = [date(2026, 6, d) for d in (1, 2, 3, 4)]
    for d in days[:3]:
        cache.get(CONN, "p", (), d, _flags_page(ids, asof=d), workspace=W)
    # touch day 1 from another page: a hit, and day 2 is now the oldest group
    cache.get(CONN, "q", (), days[0], _flags_page(ids, asof=days[0]), workspace=W)
    assert len(flags["calls"]) == 3
    cache.get(CONN, "p", (), days[3], _flags_page(ids, asof=days[3]), workspace=W)
    assert cache.piece_count() == 3
    cache.get(CONN, "r", (), days[0], _flags_page(ids, asof=days[0]), workspace=W)  # still there
    assert len(flags["calls"]) == 4
    cache.get(
        CONN, "r", (), days[1], _flags_page(ids, asof=days[1]), workspace=W
    )  # evicted: re-read
    assert len(flags["calls"]) == 5


def test_the_result_keeps_the_asked_order(cache, flags, monkeypatch):
    """Today's order is ``read_flags``' own per chunk, which is the asked order: pinned for a cold read, a warm
    one and one mixing hits and misses."""
    monkeypatch.setattr(baskets, "CHUNK", 2)
    ids = _ids(5)
    flags["flagged"] = set(ids)
    asked = [
        ids[3],
        ids[0],
        ids[4],
        ids[1],
        ids[2],
        ids[0],
    ]  # a repeat counts once, where it first came
    expected = [ids[3], ids[0], ids[4], ids[1], ids[2]]
    assert list(read_price_flags(CONN, asked, D, known_at=K, clock="public")) == expected
    cache.get(CONN, "warm-some", (), D, _flags_page([ids[4], ids[0]]), workspace=W)
    mixed = cache.get(CONN, "mixed", (), D, _flags_page(asked), workspace=W).value
    warm = cache.get(CONN, "warm", (), D, _flags_page(asked), workspace=W).value
    assert list(mixed) == expected and list(warm) == expected


def test_a_nested_page_compute_works_and_hands_the_outer_session_back(cache, flags):
    a, b = _ids(2)
    seen = {}

    def outer():
        read_price_flags(CONN, [a], D, known_at=K, clock="public")
        inner = cache.get(
            CONN,
            "inner",
            (),
            D,
            lambda: (
                cache_mod.piece_workspace(),
                read_price_flags(CONN, [a, b], D, known_at=K, clock="public"),
            ),
            workspace=V,
        )
        seen["inner_ws"] = inner.value[0]
        seen["outer_ws"] = cache_mod.piece_workspace()
        return read_price_flags(CONN, [a, b], D, known_at=K, clock="public")

    cache.get(CONN, "outer", (), D, outer, workspace=W)
    assert (seen["inner_ws"], seen["outer_ws"]) == (V, W)
    assert cache_mod.piece_workspace() is None
    # outer: a; inner (workspace V, its own pieces): a and b; outer again: b only (a is staged)
    assert flags["calls"] == [[a], [a, b], [b]]
    cache.get(CONN, "again", (), D, _flags_page([a, b]), workspace=W)
    assert len(flags["calls"]) == 3  # the outer page's pieces were stored


# --- display members shared across pages ------------------------------------------------------------------------


class Members:
    """Stand-in display members that count their calls per (name, company)."""

    def __init__(self):
        self.calls: list[tuple[str, uuid.UUID]] = []

    def member(self, name: str, *, stamp: str | None = None) -> SharedMember:
        def compute(pit, sid, asof):
            self.calls.append((name, sid))
            return DisplaySignal(kind=stamp or name, label=name, basis=DisplayBasis(source="test"))

        return SharedMember(name=name, compute=compute, horizons={})


def _page(members, sids, *, asof=D, known_at=K, clock="public", workspace=W):
    def read(pit, sid):
        return tuple(m(pit, sid, asof) for m in members)

    return lambda: per_company(CONN, sids, asof, known_at, clock, read, workspace=workspace)


def test_two_pages_call_each_member_once_per_company(cache, monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    m = Members()
    sids = _ids(3)
    one = cache.get(CONN, "a", (), D, _page([m.member("x"), m.member("y")], sids), workspace=W)
    two = cache.get(CONN, "b", (), D, _page([m.member("y"), m.member("z")], sids), workspace=W)
    assert sorted(m.calls, key=str) == sorted(
        [(n, s) for n in ("x", "y", "z") for s in sids], key=str
    )
    assert [one.value[s][1] for s in sids] == [two.value[s][0] for s in sids]  # the same y reading


def test_a_member_called_outside_per_company_is_never_shared(cache):
    m = Members()
    member = m.member("x")
    sids = _ids(1)
    pit_holder = {}

    def page():
        def read(pit, sid):
            pit_holder["pit"] = pit
            return member(pit, sid, D)

        per_company(CONN, sids, D, K, "public", read, workspace=W)
        # the same view, but called after per_company returned: computed, never shared
        member(pit_holder["pit"], sids[0], D)
        member(pit_holder["pit"], sids[0], D)
        return None

    cache.get(CONN, "a", (), D, page, workspace=W)
    assert m.calls == [("x", sids[0])] * 3


def test_a_member_called_with_another_view_or_asof_is_not_shared(cache):
    m = Members()
    member = m.member("x")
    sids = _ids(1)

    def page():
        def read(pit, sid):
            other = baskets.PointInTimeData(
                CONN, asof=D, known_at=K, workspace=W, basket=[sid], clock="public"
            )
            member(other, sid, D)  # another view
            member(pit, sid, date(2026, 5, 29))  # another as-of
            return member(pit, sid, D)  # the view itself: shared

        per_company(CONN, sids, D, K, "public", read, workspace=W)
        per_company(CONN, sids, D, K, "public", read, workspace=W)
        return None

    cache.get(CONN, "a", (), D, page, workspace=W)
    assert len(m.calls) == 5  # 2 unshared per run + the shared reading once


def test_a_different_asof_clock_or_known_at_does_not_share_a_reading(cache):
    m = Members()
    member = m.member("x")
    sids = _ids(1)
    cache.get(CONN, "a", (), D, _page([member], sids), workspace=W)
    cache.get(CONN, "a", (1,), D, _page([member], sids, clock="record"), workspace=W)
    later = datetime(2026, 6, 3, 3, 0, tzinfo=timezone.utc)
    cache.get(CONN, "a", (2,), D, _page([member], sids, known_at=later), workspace=W)
    d2 = date(2026, 6, 2)
    cache.get(CONN, "a", (), d2, _page([member], sids, asof=d2), workspace=W)
    assert len(m.calls) == 4


def test_two_workspaces_never_share_a_reading(cache):
    m = Members()
    member = m.member("x")
    sids = _ids(1)
    cache.get(CONN, "a", (), D, _page([member], sids, workspace=W), workspace=W)
    cache.get(CONN, "a", (), D, _page([member], sids, workspace=V), workspace=V)
    # per_company's view workspace differs from the page's: computed, never shared
    cache.get(CONN, "b", (), D, _page([member], sids, workspace=V), workspace=W)
    cache.get(CONN, "c", (), D, _page([member], sids, workspace=V), workspace=W)
    assert len(m.calls) == 4


def test_without_a_page_compute_a_member_reads_as_before(cache):
    m = Members()
    member = m.member("x")
    sids = _ids(2)
    _page([member], sids)()
    _page([member], sids)()
    assert len(m.calls) == 4 and cache.piece_count() == 0


def test_a_wrongly_stamped_signal_still_fails_on_a_miss(cache):
    m = Members()
    bad = m.member("x", stamp="not-x")
    with pytest.raises(ValueError, match="stamped"):
        cache.get(CONN, "a", (), D, _page([bad], _ids(1)), workspace=W)
    assert cache.piece_count() == 0


def test_display_members_hands_out_shared_members_that_read_like_the_registered_ones():
    from signals.display import registered_display_members

    got = baskets.display_members(("sma_position",), "Test")["sma_position"]
    reg = next(m for m in registered_display_members() if m.name == "sma_position")
    assert isinstance(got, SharedMember)
    assert (got.name, got.compute, got.horizons) == (reg.name, reg.compute, reg.horizons)


def test_clear_drops_the_pieces(cache, flags):
    cache.get(CONN, "a", (), D, _flags_page(_ids(2)), workspace=W)
    assert cache.piece_count() == 2
    cache.clear()
    assert cache.piece_count() == 0
