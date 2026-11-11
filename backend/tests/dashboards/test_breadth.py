"""The per-card theme breadth the dashboards share (``dashboards/breadth.py``, ``docs/DASHBOARDS.md`` rule 7): each
card's reading is exactly what ``theme_breadth.breadth_for`` returns over a view of just that card's names (the
read the Theme Leaderboard and Forming Quietly made per card before), while each company's closes are read ONCE
however many cards hold it, and kept as a piece.

The pure tests COUNT the reads: the point-in-time views built and the companies each holds (a stand-in
``PointInTimeData`` whose ``price_history`` runs the real ``window_prices``) and the bar reads per company. The
token is injected. The DB-backed test runs the REAL token and the real view against the test database and counts
the companies whose closes were read."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg.types.json import Json

from dashboards import baskets
from dashboards import cache as cache_mod
from dashboards.baskets import per_company
from dashboards.breadth import CLOSES_PIECE, read_breadths
from dashboards.cache import DashboardCache, Generation
from db.change_stamp import mark_changed
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof
from domain.scopes import DATA_TENANT_ID
from ingest.prices.eod_loader import ingest_prices
from signals.base import PointInTimeData, window_prices
from signals.display import theme_breadth
from signals.horizons import display_bounds

D = date(2026, 6, 1)
K = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
CONN = object()
W = DEFAULT_TENANT_ID
V = uuid.UUID("00000000-0000-0000-0000-0000000000b2")  # a second workspace

FULL, DOWN, THIN, BARE, HOLE, OLD = (
    uuid.UUID(f"00000000-0000-0000-0000-0000000000d{i}") for i in range(1, 7)
)
T1, T2, T3, T4 = (uuid.UUID(f"00000000-0000-0000-0000-0000000000e{i}") for i in range(1, 5))


def _bars(n: int, *, end: date = D, start: float = 100.0, step: float = 1.0) -> list[dict]:
    first = end - timedelta(days=n - 1)
    return [{"d": first + timedelta(days=i), "close": start + step * i} for i in range(n)]


def _tape() -> dict[uuid.UUID, list[dict]]:
    """Six companies: a full rising history, a full falling one, a thin one (30 bars: shown, not counted), one
    with no bars, one whose history has bars with no close (left out of its series), and one with a long history
    most of which is older than the breadth's lookback."""
    hole = _bars(100)
    for i in (10, 55, 99):  # the newest bar among them
        hole[i] = {**hole[i], "close": None}
    return {
        FULL: _bars(120),
        DOWN: _bars(120, start=300.0, step=-1.0),
        THIN: _bars(30),
        BARE: [],
        HOLE: hole,
        OLD: _bars(400),
    }


# the cards' names, in the order each reading counts them; FULL is on three cards
CARDS: dict[uuid.UUID, list[uuid.UUID]] = {
    T1: [FULL, THIN, BARE, HOLE],
    T2: [DOWN, FULL, OLD],
    T3: [],  # no unflagged counting name: no reading
    T4: [BARE, FULL],
}
COMPANIES = [FULL, THIN, BARE, HOLE, DOWN, OLD]  # each once, in first-seen order


class Views:
    """A stand-in ``PointInTimeData``: records the companies each view holds and every bar read it serves (the
    company and the lookback asked for); ``price_history`` is the real ``window_prices`` over ``bars``.
    """

    def __init__(self, bars: dict[uuid.UUID, list[dict]]):
        self.baskets: list[list[uuid.UUID]] = []
        self.reads: list[tuple[uuid.UUID, int | None]] = []
        self.bars = bars

    def pit(self, asof: date = D, clock: str = "public"):
        views = self

        class _Pit:
            def __init__(self):
                self.asof, self.clock = asof, clock

            def price_history(self, sid, lookback_days=None):
                views.reads.append((sid, lookback_days))
                return window_prices(list(views.bars.get(sid, [])), asof, lookback_days)

        return _Pit()

    def __call__(self, conn, *, asof, known_at, data_tenant, workspace, basket, bounds, clock):
        self.baskets.append(list(basket))
        return self.pit(asof, clock)

    @property
    def read(self) -> list[uuid.UUID]:
        return [s for b in self.baskets for s in b]


@pytest.fixture
def views(monkeypatch):
    v = Views(_tape())
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


def _page(cards=CARDS, *, workspace=W):
    return lambda: read_breadths(CONN, D, K, "public", cards, workspace=workspace)


def _per_card(views: Views, cards=CARDS):
    """What the two pages computed before: ``breadth_for`` over a view of just each card's names (``None`` for a
    card with none). Its reads are left out of the counts."""
    before = list(views.reads)
    out = {
        tid: theme_breadth.breadth_for(views.pit(), list(names)) if names else None
        for tid, names in cards.items()
    }
    views.reads[:] = before
    return out


def _metric(sig, key: str):
    return next(m.value for m in sig.metrics if m.key == key)


# --- the same reading as breadth_for, per card ----------------------------------------------------------------


def test_each_cards_reading_is_breadth_for_over_just_that_cards_names(views):
    expected = _per_card(views)
    got = _page()()  # outside a page compute: pieces off
    assert got == expected and list(got) == list(CARDS)
    assert got[T3] is None  # no names: no reading
    # the fixture's cases reach the reading: a thin member and one with no bars are shown, never counted, and a
    # bar with no close is left out of its member's series (the member still counts)
    assert (_metric(got[T1], "members_counted"), _metric(got[T1], "members_thin")) == (2.0, 2.0)
    assert got[T1].basis.params["members_resolved"] == 4
    assert (_metric(got[T2], "members_counted"), _metric(got[T2], "breadth")) == (3.0, 66.67)
    assert (_metric(got[T4], "members_counted"), _metric(got[T4], "members_thin")) == (1.0, 1.0)
    # a card of one member with no bars: a reading that says so, never None and never a made-up "below"
    alone = read_breadths(CONN, D, K, "public", {T1: [BARE]}, workspace=W)[T1]
    assert alone == theme_breadth.compute([[]]) and alone.headline.key == "unknown"


def test_a_members_series_is_the_one_breadth_for_builds(views):
    """``member_closes`` is the one definition: the lookback asked for, the None closes dropped, the order kept."""
    pit = views.pit()
    lookback = theme_breadth.LOOKBACK_DAYS
    series = theme_breadth.member_closes(pit, HOLE)
    assert views.reads == [(HOLE, lookback)]
    in_view = window_prices(
        list(views.bars[HOLE]), D, lookback
    )  # the view's own rule (holidays left out)
    assert series == [float(b["close"]) for b in in_view if b["close"] is not None]
    assert (
        len(in_view) - len(series) == 3
    )  # the three bars with no close, and nothing else, were left out
    assert theme_breadth.member_closes(pit, BARE) == []
    # the lookback trims: 400 calendar-day bars, only those inside the breadth's window are in the series
    old = theme_breadth.member_closes(pit, OLD)
    assert len(old) == len(window_prices(list(views.bars[OLD]), D, lookback))
    assert 200 < len(old) <= lookback + 1
    assert theme_breadth.breadth_for(pit, [HOLE, BARE, OLD]) == theme_breadth.compute(
        [series, [], old]
    )


def test_the_order_and_a_repeated_name_are_kept_as_given(views):
    cards = {T1: [THIN, FULL, FULL, DOWN], T2: [DOWN, FULL, THIN]}
    got = _page(cards)()
    assert got == _per_card(views, cards)
    assert (
        got[T1].basis.params["members_resolved"] == 4
    )  # the repeat counts as breadth_for counts it


# --- reads ---------------------------------------------------------------------------------------------------


def test_a_company_on_several_cards_is_read_once(cache, views):
    expected = _per_card(views)
    got = cache.get(CONN, "a", (), D, _page(), workspace=W)
    assert got.value == expected
    assert views.baskets == [COMPANIES]  # one view, each company once, in first-seen order
    assert views.reads == [(sid, theme_breadth.LOOKBACK_DAYS) for sid in COMPANIES]


def test_outside_a_page_compute_a_company_is_still_read_once_and_nothing_is_stored(cache, views):
    one = _page()()
    two = _page()()
    assert one == two == _per_card(views)
    assert views.baskets == [COMPANIES, COMPANIES]
    assert cache.piece_count() == 0 and cache_mod.CACHE.piece_count() == 0


def test_a_second_compute_reads_no_company(cache, views, stamps):
    one = cache.get(CONN, "theme-leaderboard", (), D, _page(), workspace=W)
    # another page (Forming Quietly reads fewer cards), then a basket edit and the same page again
    fewer = {T2: CARDS[T2], T4: [FULL]}
    two = cache.get(CONN, "forming-quietly", (), D, _page(fewer), workspace=W)
    stamps["soft"] = 2
    again = cache.get(CONN, "theme-leaderboard", (), D, _page(), workspace=W)
    assert again.status == "computed"
    assert views.baskets == [COMPANIES] and len(views.reads) == len(COMPANIES)
    assert again.value == one.value == _per_card(views)
    assert two.value == _per_card(views, fewer)
    assert cache.piece_count() == len(COMPANIES)


def test_one_stamped_company_is_the_only_one_read_again(cache, views, stamps):
    stamps["map"] = {s: 1 for s in COMPANIES}
    stamps["fp"] = 2
    cache.get(CONN, "a", (), D, _page(), workspace=W)
    # FULL's tape turns down and its stamp moves: its pieces drop, nobody else's
    views.bars[FULL] = _bars(120, start=300.0, step=-1.0)
    stamps["map"] = {**stamps["map"], FULL: 2}
    stamps["fp"] = 3
    got = cache.get(CONN, "a", (), D, _page(), workspace=W)
    assert views.baskets == [
        COMPANIES,
        [FULL],
    ]  # exactly one more view, holding exactly that company
    assert got.value == _per_card(views)  # every card that holds it reads the new tape
    assert _metric(got.value[T2], "breadth") == 33.33


def test_a_new_name_on_a_card_is_the_only_one_read(cache, views):
    cache.get(CONN, "a", (), D, _page({T1: [FULL, THIN]}), workspace=W)
    got = cache.get(CONN, "b", (), D, _page({T1: [FULL, DOWN, THIN], T2: [DOWN]}), workspace=W)
    assert views.baskets == [[FULL, THIN], [DOWN]]
    assert got.value == _per_card(views, {T1: [FULL, DOWN, THIN], T2: [DOWN]})


# --- nothing shared that should not be ------------------------------------------------------------------------


def test_the_piece_is_the_breadths_own_and_never_a_day_keyed_closes_piece(cache, views):
    assert CLOSES_PIECE == f"theme-breadth.closes.{theme_breadth.LOOKBACK_DAYS}d"
    day_keyed = (
        f"closes.{theme_breadth.LOOKBACK_DAYS}d"  # the name a ``{day: close}`` reader would use
    )

    def page():
        closes = per_company(
            CONN,
            [FULL],
            D,
            K,
            "public",
            lambda pit, sid: {
                b["d"]: b["close"]
                for b in pit.price_history(sid, lookback_days=theme_breadth.LOOKBACK_DAYS)
            },
            workspace=W,
            piece=day_keyed,
        )
        return closes, read_breadths(CONN, D, K, "public", {T1: [FULL]}, workspace=W)

    closes, breadths = cache.get(CONN, "a", (), D, page, workspace=W).value
    assert isinstance(closes[FULL], dict)  # the other reader got its own shape back
    assert breadths == _per_card(views, {T1: [FULL]})
    assert (
        views.baskets == [[FULL], [FULL]] and cache.piece_count() == 2
    )  # two pieces, never one shared


def test_the_stored_series_is_a_tuple_and_each_card_is_handed_its_own_list(
    cache, views, monkeypatch
):
    handed: list[list[float]] = []
    real = theme_breadth.compute

    def compute(member_closes):
        handed.extend(member_closes)
        return real(member_closes)

    monkeypatch.setattr(theme_breadth, "compute", compute)
    cache.get(CONN, "a", (), D, _page({T1: [FULL], T2: [FULL]}), workspace=W)
    assert len(handed) == 2 and handed[0] == handed[1] and handed[0] is not handed[1]

    def never(pit, sid):
        raise AssertionError("stored: not read again")

    stored = cache.get(
        CONN,
        "b",
        (),
        D,
        lambda: per_company(CONN, [FULL], D, K, "public", never, workspace=W, piece=CLOSES_PIECE),
        workspace=W,
    ).value[FULL]
    assert (
        isinstance(stored, tuple) and list(stored) == handed[0]
    )  # shared between pages: immutable


def test_two_workspaces_never_share_a_series(cache, views):
    cache.get(CONN, "a", (), D, _page(workspace=W), workspace=W)
    cache.get(CONN, "a", (), D, _page(workspace=V), workspace=V)
    assert views.baskets == [COMPANIES, COMPANIES]
    stored = cache.piece_count()
    cache.get(CONN, "b", (), D, _page(workspace=V), workspace=W)  # not the page's own workspace
    assert views.baskets == [COMPANIES] * 3 and cache.piece_count() == stored


# --- end to end: the REAL token, the real view, the test database ---------------------------------------------


@pytest.fixture
def run(monkeypatch):
    """The newest cron run's identity (a run artifact on disk, not the database): held still."""
    monkeypatch.setattr(
        cache_mod, "latest_run_identity", lambda: ("2026-06-01T02:30:00+00:00", "finished", "x")
    )


@pytest.fixture
def closes_read(monkeypatch):
    """The companies whose closes were read (``member_closes`` calls; the real one still runs)."""
    seen: list[uuid.UUID] = []
    real = theme_breadth.member_closes

    def member_closes(pit, security_id):
        seen.append(security_id)
        return real(pit, security_id)

    monkeypatch.setattr(theme_breadth, "member_closes", member_closes)
    return seen


def _company(conn, ticker: str, bars: list[dict]) -> uuid.UUID:
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, date(2020, 1, 1)),
        )
    ingest_prices(
        conn,
        sid,
        [
            {
                "d": b["d"],
                "open": b["close"],
                "high": b["close"],
                "low": b["close"],
                **b,
                "volume": 1000,
            }
            for b in bars
        ],
    )
    conn.commit()
    return sid


def _snapshot(conn) -> None:
    """A roster edit: a card and a basket snapshot (the soft generation moves)."""
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


def test_end_to_end_the_real_view_reads_each_company_once_then_only_the_stamped_one(
    db, run, closes_read
):
    up = _company(db, "AAA", _bars(120))
    down = _company(db, "BBB", _bars(120, start=300.0, step=-1.0))
    thin = _company(db, "CCC", _bars(30))
    bare = _company(db, "DDD", [])
    cards = {T1: [up, thin, bare], T2: [down, up], T3: []}
    companies = [up, thin, bare, down]
    known_at = known_at_for_asof(D)

    def per_card():
        """The read the two pages made before: one view per card, ``breadth_for`` over its names."""
        out = {}
        for tid, names in cards.items():
            view = PointInTimeData(
                db,
                asof=D,
                known_at=known_at,
                data_tenant=DATA_TENANT_ID,
                basket=names,
                bounds=display_bounds(),
                clock="public",
                workspace=W,
            )
            out[tid] = theme_breadth.breadth_for(view, names) if names else None
        return out

    def page():
        return read_breadths(db, D, known_at, "public", cards, workspace=W)

    expected = per_card()
    assert closes_read == [
        up,
        thin,
        bare,
        down,
        up,
    ]  # the old read: a company on two cards read twice
    assert (_metric(expected[T1], "members_counted"), _metric(expected[T1], "members_thin")) == (
        1.0,
        2.0,
    )
    assert (_metric(expected[T2], "members_counted"), _metric(expected[T2], "breadth")) == (
        2.0,
        50.0,
    )

    closes_read.clear()
    cache = DashboardCache()
    one = cache.get(db, "page", (), D, page, workspace=W)
    assert one.value == expected and closes_read == companies  # each company once

    _snapshot(db)  # a roster edit: the page entry clears, the pieces stay
    again = cache.get(db, "page", (), D, page, workspace=W)
    assert again.status == "computed" and again.value == expected and closes_read == companies

    mark_changed(db, [up])
    db.commit()
    last = cache.get(db, "page", (), D, page, workspace=W)
    assert last.status == "computed" and last.value == expected
    assert closes_read == companies + [up]  # exactly the stamped company was read again
