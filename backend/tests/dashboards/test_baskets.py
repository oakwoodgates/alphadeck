"""The shared basket read (``dashboards/baskets.py``): the counted cards and their rosters at an as-of, who counts,
the four groupings, the per-group company fold and the chunked point-in-time read. Pure: the repository reads
and the point-in-time view are stubbed. The Rotation Map's and the Cross Radar's own tests cover these rules
through each page; this pins the shared pieces once."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from dashboards import baskets
from dashboards.baskets import (
    Membership,
    buckets,
    cards_as_of,
    companies_in,
    counted_rows,
    display_members,
    display_ticker,
    flagged_in_view,
    group_of,
    link_label,
    memberships_of,
    per_company,
    price_flags,
    price_reason,
    reaches_price,
    resolved_ids,
)
from db.session import DEFAULT_TENANT_ID
from domain.data_flags import (
    CELLS_BY_GROUP,
    IN_DOUBT,
    INSIDER,
    INSIDER_CELLS,
    MARKET_CAP_CELLS,
    MIXED_SCALE,
    PRICE,
    PRICE_CELLS,
    VOLUME,
    VOLUME_CELLS,
    DataFlag,
    MemberFlags,
    sort_flags,
)
from domain.settings import get_settings
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from pipeline import data_flags as data_flags_reader
from pipeline.fundamentals_lag import FundamentalsLag
from pipeline.split_basis import ISLAND, Finding

ASOF = date(2026, 6, 1)
T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B = uuid.uuid4(), uuid.uuid4()


def ms(tid, card, ticker, sid, segment="Core") -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def test_memberships_mark_the_silent_rows_and_keep_every_row():
    t = Thesis(
        id=T1,
        name="Grid",
        narrative="x",
        segments=[Segment(label="Turbines")],
        basket=[
            BasketMember(ticker="AAA", role="r", security_id=A, segment="Turbines"),
            BasketMember(ticker="CTX", role="r", security_id=B, segment=CONTEXT_LABEL),
            BasketMember(ticker="NEWCO", role="r", segment=None),
        ],
    )
    rows = memberships_of([t])
    assert [(m.ticker, m.segment, m.silent) for m in rows] == [
        ("AAA", "Turbines", False),
        ("CTX", CONTEXT_LABEL, True),
        ("NEWCO", None, False),
    ]
    assert resolved_ids(rows) == sorted([A, B], key=str)


def test_counted_rows_filters_silent_and_sets_unresolved_aside():
    rows = [
        ms(T1, "Grid", "AAA", A),
        ms(T1, "Grid", "CTX", B, CONTEXT_LABEL),
        ms(T1, "Grid", "NEWCO", None),
        ms(T1, "Grid", "QUIET", None, BENCH_LABEL),
    ]
    off = counted_rows(rows, include_silent=False)
    assert (
        [m.ticker for m in off.rows],
        off.basket_rows,
        off.silent_rows_left_out,
        off.unresolved_rows,
    ) == (
        ["AAA"],
        4,
        2,
        1,
    )
    on = counted_rows(rows, include_silent=True)
    assert ([m.ticker for m in on.rows], on.silent_rows_left_out, on.unresolved_rows) == (
        ["AAA", "CTX"],
        0,
        2,
    )


def test_the_four_groupings_key_label_and_meta():
    m = ms(T1, "Grid", "AAA", A, "Turbines")
    ident = {A: {"business_type": "utilities", "business_supersector": "energy_utilities"}}
    assert group_of(m, "link", ident) == (
        f"link:{T1}:Turbines",
        "Turbines · Grid",
        {"link": "Turbines", "thesis_id": T1, "thesis_name": "Grid"},
    )
    assert group_of(m, "card", ident) == (
        f"card:{T1}",
        "Grid",
        {"thesis_id": T1, "thesis_name": "Grid"},
    )
    assert group_of(m, "business_type", ident) == (
        "type:utilities",
        "utilities",
        {"business_type": "utilities"},
    )
    assert group_of(m, "sector", ident) == (
        "sector:energy_utilities",
        "energy_utilities",
        {"supersector": "energy_utilities"},
    )
    unseg = ms(T1, "Grid", "BBB", B, None)
    assert group_of(unseg, "link", {})[:2] == (f"link:{T1}:", "Unsegmented · Grid")
    assert group_of(unseg, "business_type", {})[:2] == ("type:", "Unclassified")
    assert group_of(unseg, "sector", {})[:2] == ("sector:", "Unclassified")
    assert (link_label(None), link_label("Fuel")) == ("Unsegmented", "Fuel")


def test_the_link_view_counts_a_two_link_company_in_both_and_the_others_fold_it_once():
    rows = [
        ms(T1, "Grid", "AAA", A, "Turbines"),
        ms(T1, "Grid", "AAA", A, "Cables"),
        ms(T2, "Nuclear", "AAA.X", A, "Fuel"),
        ms(T1, "Grid", "BBB", B, "Turbines"),
    ]
    by_link = buckets(rows, "link", {})
    assert list(by_link) == [f"link:{T1}:Turbines", f"link:{T1}:Cables", f"link:{T2}:Fuel"]
    assert [m.ticker for m in by_link[f"link:{T1}:Turbines"][0]] == ["AAA", "BBB"]

    card_rows, _, _ = buckets(rows, "card", {})[f"card:{T1}"]
    folded = companies_in(card_rows)
    assert [sid for sid, _ in folded] == [A, B]
    assert [(c.thesis_name, c.links) for c in folded[0][1]] == [("Grid", ("Turbines", "Cables"))]

    sector_rows, _, _ = buckets(rows, "sector", {})["sector:"]
    aaa, _bbb = companies_in(sector_rows)
    assert [(c.thesis_name, c.ticker, c.links) for c in aaa[1]] == [
        ("Grid", "AAA", ("Turbines", "Cables")),
        ("Nuclear", "AAA.X", ("Fuel",)),
    ]
    # the shown ticker: the one most of its cards use, ties by ticker
    assert display_ticker(aaa[1]) == "AAA"


@pytest.fixture
def repo(monkeypatch):
    old = Thesis(id=T1, name="Old", narrative="x")
    new = Thesis(id=T2, name="New", narrative="x")
    snap_basket = [BasketMember(ticker="NNE", role="r")]
    calls: dict = {"snapshots_asof_for": []}
    monkeypatch.setattr(baskets.thesis_repo, "list_all", lambda conn, **_: [old, new])
    monkeypatch.setattr(
        baskets.thesis_repo,
        "created_at_for",
        lambda conn, ids, **_: {
            T1: datetime(2026, 1, 5, tzinfo=timezone.utc),
            T2: datetime(2026, 7, 1, tzinfo=timezone.utc),
        },
    )

    def snapshots_asof_for(conn, ids, known_at):
        # one batched read: T1 has a roster snapshot by then, nothing else does
        calls["snapshots_asof_for"].append((list(ids), known_at))
        return {T1: snap_basket} if T1 in ids else {}

    monkeypatch.setattr(baskets.thesis_repo, "snapshots_asof_for", snapshots_asof_for)
    return calls


def test_cards_as_of_leaves_out_later_cards_and_counts_a_roster_fallback(repo):
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    got = cards_as_of(object(), ASOF, known_at, workspace=DEFAULT_TENANT_ID)
    assert (
        got.live_count,
        [t.name for t in got.cards],
        got.not_yet_created,
        got.roster_fallback,
    ) == (
        2,
        ["Old"],
        1,
        0,
    )
    assert [m.ticker for m in got.cards[0].basket] == ["NNE"]  # T1 reads its snapshot roster
    assert repo["snapshots_asof_for"] == [([T1], known_at)]  # ONE batched read, the kept cards only
    # a card with no snapshot by then keeps today's roster and is counted as a fallback
    repo["snapshots_asof_for"].clear()
    later = cards_as_of(object(), date(2026, 10, 1), known_at, workspace=DEFAULT_TENANT_ID)
    assert [(t.name, [m.ticker for m in t.basket]) for t in later.cards] == [
        ("Old", ["NNE"]),
        ("New", []),
    ]
    assert later.roster_fallback == 1  # T2 has no snapshot: today's roster stands in
    assert repo["snapshots_asof_for"] == [([T1, T2], known_at)]
    # the live view: today's roster, no snapshot read
    repo["snapshots_asof_for"].clear()
    live = cards_as_of(object(), date(2026, 10, 1), None, workspace=DEFAULT_TENANT_ID)
    assert ([t.name for t in live.cards], live.roster_fallback) == (["Old", "New"], 0)
    assert repo["snapshots_asof_for"] == []


def test_per_company_reads_each_company_once_through_one_view_per_chunk(monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    sids = [uuid.uuid4() for _ in range(5)]
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    seen: list = []

    def read(pit, sid):
        # hold the view itself: per_company frees each chunk's view as it moves on, and CPython hands a
        # freed object's id() to the next one, so an id alone can merge two chunks' views
        seen.append((pit, sid, pit._basket, pit.asof, pit.known_at, pit.clock))
        return sid.hex

    out = per_company(object(), sids, ASOF, known_at, "public", read, workspace=DEFAULT_TENANT_ID)
    assert out == {sid: sid.hex for sid in sids}
    assert [s for _, s, *_ in seen] == sids
    views = {}
    for pit, sid, basket, *view in seen:
        views.setdefault(id(pit), (basket, set()))[1].add(sid)
        assert view == [ASOF, known_at, "public"]
    assert [b == frozenset(got) for b, got in views.values()] == [True, True, True]  # 2 + 2 + 1


def test_display_members_fail_loud_on_a_renamed_member():
    got = display_members(("sma_position", "relative_strength"), "Cross Radar")
    assert list(got) == ["sma_position", "relative_strength"]
    with pytest.raises(RuntimeError, match="Cross Radar: display members not registered"):
        display_members(("sma_position", "no_such_member"), "Cross Radar")


# --- the data flags that reach a price reading (``price_flags``) ---------------------------------------------


def _weekdays(n: int, end: date = ASOF) -> list[date]:
    """The last ``n`` weekdays through ``end`` (a stand-in tape's bar dates)."""
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def test_price_flags_maps_only_the_flags_that_reach_a_price_cell(monkeypatch):
    """The reader's own output, built with the real flag constructors: a mixed-scale island inside the 1Y
    window and a stopped feed reach the price cells (mapped, by label); the same island 300 bars back (history
    only), a fundamentals lag and a withheld market cap reach none (not mapped). The reader gets the view's
    pair, the nightly's readings and the master's tickers, ``CHUNK`` companies per call."""
    bars = _weekdays(400)
    island = data_flags_reader.mixed_scale_flag([Finding(ISLAND, (bars[-10],), 90.0)], bars, ASOF)
    history_only = data_flags_reader.mixed_scale_flag(
        [Finding(ISLAND, (bars[-300],), 90.0)], bars, ASOF
    )
    stopped = data_flags_reader.stopped_flag(
        bars[-30], asof=ASOF, stale_days=5, reading=None, ticker="STOP"
    )
    lag = data_flags_reader.fundamentals_flag(
        FundamentalsLag(
            cik="0000000001",
            form="10-Q",
            accession="0000000001-26-000001",
            filed=date(2026, 5, 15),
            period=date(2026, 3, 31),
            last_period=date(2025, 12, 31),
        )
    )
    cap = data_flags_reader.market_cap_flag(
        {"valid_from": date(2026, 3, 1), "ads_ratio_status": "unread"}, ASOF, [], asof=ASOF
    )
    assert island is not None and history_only is not None and stopped is not None
    assert lag is not None and cap is not None
    # what the real constructors produced: the rule reads ``columns``, never a list of kinds
    assert {"ret_30d", "ret_90d", "ret_1y", "sma", "path"} <= set(island.columns)
    assert history_only.columns == () and lag.columns == () and cap.columns == ("mktcap",)

    s_island, s_hist, s_lag, s_cap, s_both, s_clean = (uuid.uuid4() for _ in range(6))
    member = {
        s_island: (island, lag),
        s_hist: (history_only,),
        s_lag: (lag,),
        s_cap: (cap,),
        s_both: (island, stopped, cap),
        s_clean: (),
    }
    sids = list(member)
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    calls: list[dict] = []

    def read_flags(conn, **kw):
        calls.append(kw)
        return {
            sid: MemberFlags(security_id=sid, ticker="X", flags=tuple(sort_flags(member[sid])))
            for sid in kw["security_ids"]
        }

    monkeypatch.setattr(baskets, "CHUNK", 4)
    monkeypatch.setattr(
        baskets, "live_card_ids", lambda conn, **_: set(sids)
    )  # every company is on a card
    monkeypatch.setattr(baskets.flags_reader, "read_flags", read_flags)
    monkeypatch.setattr(baskets.flags_reader, "run_reads", lambda: ("READINGS", "CORRECTIONS"))
    monkeypatch.setattr(
        baskets.master, "tickers_for", lambda conn, ids, tenant_id: {s: "X" for s in ids}
    )

    got = price_flags(
        object(),
        sids + [s_island],
        ASOF,
        known_at=known_at,
        clock="public",
        workspace=DEFAULT_TENANT_ID,
    )
    assert got == {
        s_island: "mixed-scale price history",
        s_both: "price feed stopped; mixed-scale price history",  # a known-wrong number first
    }
    assert [c["security_ids"] for c in calls] == [sids[:4], sids[4:]]  # deduped, CHUNK per call
    for c in calls:
        assert (c["asof"], c["known_at"], c["clock"]) == (ASOF, known_at, "public")
        assert (c["readings"], c["corrections"]) == ("READINGS", "CORRECTIONS")
        assert (
            c["price_only"] is True
        )  # only price-reaching flags are kept: the other reads are skipped
        assert c["stale_days"] == get_settings().tape_stale_days
        assert c["tenant_id"] == DEFAULT_TENANT_ID and set(c["tickers"]) == set(sids)
    assert (
        price_flags(object(), [], ASOF, known_at=None, clock="record", workspace=DEFAULT_TENANT_ID)
        == {}
    )
    assert len(calls) == 2  # no companies: no read


def test_reaches_price_is_the_price_group_cells_and_nothing_else():
    """The rule on hand-set columns: any price cell reaches; volume / insider / market-cap cells alone, or
    none, do not (``affects`` = what a kind CAN reach, never read)."""

    def flag(columns, affects=(PRICE, VOLUME, INSIDER)):
        return DataFlag(
            kind=MIXED_SCALE,
            level=IN_DOUBT,
            reason="r",
            clears="c",
            affects=affects,
            columns=columns,
        )

    assert PRICE_CELLS == CELLS_BY_GROUP[PRICE]
    for cell in PRICE_CELLS:
        assert reaches_price(flag((cell,)))
    assert not reaches_price(flag(VOLUME_CELLS + INSIDER_CELLS + MARKET_CAP_CELLS))
    assert not reaches_price(
        flag(())
    )  # history only: the kind could reach price, this flag does not
    assert price_reason([flag(()), flag(("rvol8",))]) is None
    assert price_reason([flag(("ret_1y",)), flag(("ret_90d",))]) == "mixed-scale price history"


def test_flagged_in_view_names_each_flagged_company_once_by_ticker_under_its_display_ticker():
    """A company on two cards is named once, under the ticker most of its cards use; a flagged company out of
    view (not on the rows) is not named; no flags = nothing."""
    out_of_view = uuid.uuid4()
    rows = [
        ms(T1, "Grid", "ZZZ", A),
        ms(T2, "Nuclear", "ZZZ", A, "Fuel"),
        ms(T2, "Nuclear", "AAA", B, "Fuel"),
    ]
    flagged = {A: "mixed-scale price history", B: "price feed stopped", out_of_view: "x"}
    got = flagged_in_view(rows, flagged)
    assert [(f.security_id, f.ticker, f.reason) for f in got] == [
        (B, "AAA", "price feed stopped"),
        (A, "ZZZ", "mixed-scale price history"),
    ]
    assert flagged_in_view(rows, {}) == ()


# --- the price + split rows ``read_price_flags`` shares with the ``per_company`` after it -------------------


class _Conn:
    """A weakly-referenceable stand-in connection (the reads are stubbed; nothing is ever opened)."""


def test_per_company_is_seeded_with_the_price_and_split_rows_read_price_flags_loaded(monkeypatch):
    from domain.trading_days import closure_name
    from signals import base as signals_base
    from signals.base import PointInTimeData
    from signals.horizons import display_bounds

    sids = [uuid.uuid4() for _ in range(3)]
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    tape = _weekdays(700)
    price_calls: list[dict] = []
    split_calls: list[list] = []
    split_row = {"split_date": date(2026, 1, 5), "ratio": 2}

    def as_of_many(conn, table, *, security_ids, valid_from_lower, **_):
        price_calls.append({"table": table, "ids": list(security_ids), "lower": valid_from_lower})
        return {
            sid: [
                {"d": d, "valid_from": d, "close": 10.0}
                for d in tape
                if valid_from_lower is None or d >= valid_from_lower
            ]
            for sid in security_ids
        }

    def split_rows(conn, *, security_ids, **_):
        split_calls.append(list(security_ids))
        return {sid: [split_row] for sid in security_ids}

    monkeypatch.setattr(signals_base, "as_of_many", as_of_many)
    monkeypatch.setattr(signals_base.split_repo, "as_of_rows", split_rows)

    def read_flags(conn, **kw):
        # the real reader's shape: ONE unbounded view over the chunk, its rows read, then the hook
        pit = PointInTimeData(
            conn,
            asof=kw["asof"],
            known_at=kw["known_at"],
            workspace=kw["tenant_id"],
            basket=kw["security_ids"],
            bounds=None,
            clock=kw["clock"],
        )
        for sid in kw["security_ids"]:
            pit.price_history(sid)
            pit.split_events(sid)
        hook = baskets.flags_reader._ON_PIT.get()
        assert hook is not None
        hook(pit, list(kw["security_ids"]))
        return {}

    monkeypatch.setattr(baskets.flags_reader, "read_flags", read_flags)
    monkeypatch.setattr(baskets.flags_reader, "run_reads", lambda: ({}, {}))
    monkeypatch.setattr(
        baskets.master, "tickers_for", lambda conn, ids, tenant_id: {s: "X" for s in ids}
    )
    monkeypatch.setattr(baskets, "get_piece", lambda *a, **k: (False, None))
    monkeypatch.setattr(baskets, "put_piece", lambda *a, **k: None)
    baskets._TAPE_STASH.clear()

    conn = _Conn()
    baskets.read_price_flags(conn, sids, ASOF, known_at=known_at, clock="record")
    assert [c["lower"] for c in price_calls] == [None] and len(split_calls) == 1  # the flags' read

    seen: dict = {}

    def read(pit, sid):
        seen[sid] = pit.price_history(sid)
        assert pit.split_events(sid) == [split_row]
        return len(seen[sid])

    price_calls.clear()
    split_calls.clear()
    out = per_company(conn, sids, ASOF, known_at, "record", read, workspace=DEFAULT_TENANT_ID)
    days = display_bounds()["fact_price_eod"]
    lower = None if days is None else ASOF - timedelta(days=days)
    want = [d for d in tape if (lower is None or d >= lower) and closure_name(d) is None]
    assert price_calls == [] and split_calls == []  # no second read of either table
    assert all([r["d"] for r in seen[sid]] == want for sid in sids)
    assert set(out.values()) == {len(want)}

    # the rows are consumed, and another connection never sees them
    price_calls.clear()
    per_company(_Conn(), sids, ASOF, known_at, "record", read, workspace=DEFAULT_TENANT_ID)
    assert len(price_calls) == 1 and len(split_calls) == 1
    baskets._TAPE_STASH.clear()
