"""A page's own per-company result as a piece (``baskets.per_company(piece=..., variant=...)``,
``dashboards/cache.py``): after a basket edit (page entries cleared, pieces kept) or one company's change, a
recompute reads ONLY the companies with no stored result.

The pure tests count the READS: the point-in-time views built (a stand-in ``PointInTimeData`` that records the
companies it holds) and the callback's calls. The token is injected. The end-to-end test runs the REAL token and
the real view against the test database, and counts the callback's calls."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg.types.json import Json

from app.routers.dashboards import cross_radar as radar_router
from dashboards import baskets
from dashboards import cache as cache_mod
from dashboards.baskets import per_company
from dashboards.cache import DashboardCache, Generation
from db.change_stamp import mark_changed
from db.session import DEFAULT_TENANT_ID

D = date(2026, 6, 1)
K = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
CONN = object()
W = DEFAULT_TENANT_ID
V = uuid.UUID("00000000-0000-0000-0000-0000000000b2")  # a second workspace
PIECE = "test.reading"


class Views:
    """A stand-in ``PointInTimeData``: records the companies each view holds; ``price_history`` serves
    ``bars`` (empty by default)."""

    def __init__(self):
        self.baskets: list[list[uuid.UUID]] = []
        self.bars: dict[uuid.UUID, list[dict]] = {}

    def __call__(self, conn, *, asof, known_at, data_tenant, workspace, basket, bounds, clock):
        views = self

        class _Pit:
            def __init__(self):
                self.asof, self.clock = asof, clock

            def price_history(self, sid, lookback_days=None):
                return views.bars.get(sid, [])

        self.baskets.append(list(basket))
        return _Pit()

    @property
    def read(self) -> list[uuid.UUID]:
        return [s for b in self.baskets for s in b]


@pytest.fixture
def views(monkeypatch):
    v = Views()
    monkeypatch.setattr(baskets, "PointInTimeData", v)
    return v


@pytest.fixture
def stamps(monkeypatch):
    """A movable generation (hard, soft, stamps) and the stamp map the cache reads when the stamps move."""
    state: dict = {"hard": 1, "soft": 1, "fp": 1, "map": {}}
    monkeypatch.setattr(cache_mod, "stamp_map", lambda conn: dict(state["map"]))
    monkeypatch.setattr(cache_mod, "benchmark_security_ids", lambda conn: frozenset())
    return state


@pytest.fixture
def cache(stamps):
    return DashboardCache(
        token=lambda conn: Generation(stamps["hard"], stamps["soft"], stamps["fp"])
    )


class Callback:
    """``read(pit, sid)``: counts its calls; the result is a fresh object per call."""

    def __init__(self):
        self.calls: list[uuid.UUID] = []

    def __call__(self, pit, sid):
        self.calls.append(sid)
        return ("r", sid)


def _page(read, sids, *, piece=PIECE, variant=None, workspace=W):
    return lambda: per_company(
        CONN, sids, D, K, "public", read, workspace=workspace, piece=piece, variant=variant
    )


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(n)]


# --- reads ---------------------------------------------------------------------------------------------------


def test_a_second_compute_under_another_slug_reads_no_company(cache, views):
    sids, read = _ids(5), Callback()
    one = cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    assert read.calls == sids and len(views.baskets) == 1
    two = cache.get(CONN, "b", (), D, _page(read, sids), workspace=W)
    assert two.status == "computed"
    assert read.calls == sids and len(views.baskets) == 1  # no view built, no callback called
    assert two.value == one.value
    assert all(two.value[s] is one.value[s] for s in sids)  # the stored results themselves


def test_a_soft_move_recomputes_the_page_reading_no_company(cache, views, stamps):
    sids, read = _ids(3), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    stamps["soft"] = 2  # a basket edit
    again = cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    assert again.status == "computed" and read.calls == sids and len(views.baskets) == 1


def test_one_stale_company_of_250_is_the_only_one_read(cache, views, stamps, monkeypatch):
    sids, read = _ids(250), Callback()
    stamps["map"] = {s: 1 for s in sids}
    stamps["fp"] = 2
    cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    assert [len(b) for b in views.baskets] == [100, 100, 50]
    assert cache.piece_count() == 250
    stale = sids[137]
    stamps["map"] = {**stamps["map"], stale: 2}  # its stamp moved: its pieces drop
    stamps["fp"] = 3
    views.baskets.clear()
    read.calls.clear()
    got = cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    assert views.baskets == [[stale]]  # exactly one view, holding exactly that company
    assert read.calls == [stale]
    assert list(got.value) == sids


def test_a_changed_variant_recomputes_that_company_only(cache, views):
    sids, read = _ids(4), Callback()
    flags = {sids[1]: "a flag"}
    cache.get(CONN, "a", (), D, _page(read, sids, variant=lambda s: flags.get(s)), workspace=W)
    assert read.calls == sids
    flags = {sids[1]: "a flag", sids[3]: "another flag"}  # sids[3] changed; sids[1] did not
    got = cache.get(
        CONN, "b", (), D, _page(read, sids, variant=lambda s: flags.get(s)), workspace=W
    )
    assert read.calls == sids + [sids[3]]
    assert views.baskets[-1] == [sids[3]]
    assert list(got.value) == sids
    # the new variant value was stored with the new result: a third compute is all hits
    cache.get(CONN, "c", (), D, _page(read, sids, variant=lambda s: flags.get(s)), workspace=W)
    assert read.calls == sids + [sids[3]]


def test_a_piece_read_with_a_variant_is_not_a_hit_for_a_read_without_one(cache, views):
    sids, read = _ids(2), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids, variant=lambda s: None), workspace=W)
    cache.get(CONN, "b", (), D, _page(read, sids), workspace=W)
    assert read.calls == sids + sids
    cache.get(CONN, "c", (), D, _page(read, sids), workspace=W)  # both absent: a hit
    assert read.calls == sids + sids


def test_two_piece_names_never_share_a_result(cache, views):
    sids, read = _ids(1), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids, piece="closes.420d"), workspace=W)
    got = cache.get(CONN, "b", (), D, _page(read, sids, piece="closes.130d"), workspace=W)
    assert read.calls == sids + sids  # the second name read the company again
    assert cache.piece_count() == 2
    assert got.value[sids[0]] == ("r", sids[0])


# --- order ---------------------------------------------------------------------------------------------------


def test_the_result_keeps_the_sids_order_all_miss_all_hit_and_mixed(cache, views, monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    sids, read = _ids(6), Callback()
    first = sids[1::2]  # three of them, out of their order in sids
    miss = cache.get(CONN, "a", (), D, _page(read, first), workspace=W)
    assert list(miss.value) == first
    mixed = cache.get(CONN, "b", (), D, _page(read, sids), workspace=W)
    assert list(mixed.value) == sids
    # the misses were read in their own order, CHUNK per view
    assert views.baskets[-2:] == [[sids[0], sids[2]], [sids[4]]]
    hit = cache.get(CONN, "c", (), D, _page(read, list(reversed(sids))), workspace=W)
    assert list(hit.value) == list(reversed(sids))
    assert read.calls == first + [sids[0], sids[2], sids[4]]


# --- nothing shared or stored --------------------------------------------------------------------------------


def test_no_piece_name_reads_as_today_and_stores_nothing(cache, views):
    sids, read = _ids(3), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids, piece=None), workspace=W)
    cache.get(CONN, "b", (), D, _page(read, sids, piece=None), workspace=W)
    assert read.calls == sids + sids and cache.piece_count() == 0


def test_outside_a_page_compute_reads_as_today_and_stores_nothing(cache, views):
    sids, read = _ids(3), Callback()
    got = _page(read, sids)()
    _page(read, sids)()
    assert read.calls == sids + sids and cache.piece_count() == 0
    assert list(got) == sids


def test_another_workspace_reads_as_today_and_stores_nothing(cache, views):
    sids, read = _ids(3), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids, workspace=V), workspace=W)
    cache.get(CONN, "b", (), D, _page(read, sids, workspace=V), workspace=W)
    assert read.calls == sids + sids and cache.piece_count() == 0


def test_two_workspaces_never_share_a_result(cache, views):
    sids, read = _ids(2), Callback()
    cache.get(CONN, "a", (), D, _page(read, sids, workspace=W), workspace=W)
    cache.get(CONN, "a", (), D, _page(read, sids, workspace=V), workspace=V)
    assert read.calls == sids + sids


def test_a_raising_callback_fails_the_compute_and_stores_nothing(cache, views, monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    sids, read = _ids(5), Callback()

    def boom(pit, sid):
        if sid == sids[3]:
            raise RuntimeError("a bad read")
        return read(pit, sid)

    with pytest.raises(RuntimeError, match="a bad read"):
        cache.get(CONN, "a", (), D, _page(boom, sids), workspace=W)
    assert cache.piece_count() == 0 and len(cache) == 0
    read.calls.clear()
    cache.get(CONN, "a", (), D, _page(read, sids), workspace=W)
    assert read.calls == sids  # every company read again: none of the failed compute's was kept


# --- Cross Radar: the timeline's sessions are folded from the stored results -----------------------------------


def _bars(n: int, end: date) -> list[dict]:
    start = end - timedelta(days=n - 1)
    return [{"d": start + timedelta(days=i), "close": 100.0 + i} for i in range(n)]


def test_cross_radar_second_compute_is_all_hits_with_the_same_sessions(cache, views, monkeypatch):
    a, b, c = _ids(3)
    views.bars = {a: _bars(260, D), b: _bars(260, D - timedelta(days=1)), c: []}
    none = lambda pit, sid, asof: None  # noqa: E731 - the two display members: no reading
    monkeypatch.setattr(
        radar_router, "display_members", lambda names, dash: {n: none for n in names}
    )
    read = lambda: radar_router._readings(  # noqa: E731
        CONN, [a, b, c], D, K, "public", {a: "flagged"}, workspace=W
    )
    one = cache.get(CONN, "cross-radar", (), D, read, workspace=W)
    views.baskets.clear()
    two = cache.get(CONN, "other", (), D, read, workspace=W)
    assert views.baskets == []  # every company a hit
    assert two.value == one.value
    readings, seen = two.value
    assert list(readings) == [a, b, c]
    # the flagged company's bars never shape the sessions; the priced one's do; the bare one has none
    assert seen == set(radar_router.recent_dates(views.bars[b]))
    assert readings[a].priced and readings[b].priced and not readings[c].priced


# --- end to end: the REAL token, the real view, the test database ---------------------------------------------


@pytest.fixture
def run(monkeypatch):
    """The newest cron run's identity (a run artifact on disk, not the database): held still."""
    monkeypatch.setattr(
        cache_mod, "latest_run_identity", lambda: ("2026-06-01T02:30:00+00:00", "finished", "x")
    )


def _company(conn, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, date(2020, 1, 1)),
        )
    conn.commit()
    return sid


def _snapshot(conn) -> None:
    tid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "a card", "a narrative"),
        )
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (gen_random_uuid(), %s, %s, clock_timestamp(), %s, md5(random()::text))",
            (DEFAULT_TENANT_ID, tid, Json([])),
        )
    conn.commit()


def test_end_to_end_a_soft_move_reads_no_company_and_a_stamp_reads_one(db, run):
    sids = [_company(db, t) for t in ("AAA", "BBB", "CCC")]
    calls: list[uuid.UUID] = []

    def read(pit, sid):
        calls.append(sid)
        return len(pit.price_history(sid))

    def page():
        return per_company(db, sids, D, K, "public", read, workspace=W, piece=PIECE)

    cache = DashboardCache()
    assert cache.get(db, "page", (), D, page, workspace=W).status == "computed"
    assert calls == sids
    _snapshot(db)  # a roster edit: the page entry clears, the pieces stay
    again = cache.get(db, "page", (), D, page, workspace=W)
    assert again.status == "computed" and calls == sids
    mark_changed(db, [sids[1]])
    db.commit()
    last = cache.get(db, "page", (), D, page, workspace=W)
    assert last.status == "computed"
    assert calls == sids + [sids[1]]  # exactly the changed company was read again
    assert list(last.value) == sids
