"""Narrative Index (``docs/dashboards/narrative-index.md``): the equal-weighted index math on hand-computed
numbers, joins and gaps, flat sessions, the window slice rebased to 100, since-creation inside and outside the
window, a missing benchmark, the Context/Bench toggle and the per-card dedupe; the router off one cached read;
and, DB-backed, a price published after a past as-of staying invisible to it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import narrative_index as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.narrative_index import (
    BASE,
    WINDOWS,
    chain,
    creation_base,
    index_inputs,
    narrative_index,
    session_calendar,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo

ASOF = date(2026, 6, 1)
T1, T2 = uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(6)]

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def days(n: int, end: date = ASOF) -> list[date]:
    """``n`` consecutive calendar days ending at ``end`` (the tests' sessions)."""
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


def tape(ds: list[date], closes: list[float | None]) -> dict[date, float]:
    return {d: c for d, c in zip(ds, closes) if c is not None}


def ms(
    tid: UUID, ticker: str, sid: UUID | None, segment: str | None = "Core", card: str = "Card"
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def build(
    memberships,
    closes,
    benchmarks=None,
    *,
    cards=None,
    created=None,
    states=None,
    include_silent=False,
    asof=ASOF,
):
    cards = cards or [(T1, "Card")]
    return index_inputs(
        cards=cards,
        memberships=memberships,
        closes=closes,
        benchmarks=benchmarks or {},
        created=created or {},
        states=states or {},
        asof=asof,
        include_silent=include_silent,
        live_cards=len(cards),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )


def page(inputs, window="1y", asof=ASOF):
    return narrative_index(inputs, asof=asof, window=window)


def card(result, tid: UUID = T1):
    return next(c for c in result.cards if c.thesis_id == tid)


# ---------------------------------------------------------------------------------------------------------
# the index math
# ---------------------------------------------------------------------------------------------------------


def test_the_index_on_hand_computed_numbers_two_members_three_sessions():
    """A: 10 -> 11 -> 12.1 (+10%, +10%). B: 20 -> 19 -> 19 (-5%, 0%).
    Session 1: 100 × (1 + (0.10 − 0.05) / 2) = 102.5. Session 2: 102.5 × (1 + (0.10 + 0) / 2) = 107.625.
    """
    ds = days(3)
    inputs = build(
        [ms(T1, "AAA", S[0]), ms(T1, "BBB", S[1])],
        {S[0]: tape(ds, [10, 11, 12.1]), S[1]: tape(ds, [20, 19, 19])},
    )
    c = card(page(inputs))
    assert c.points == pytest.approx((100.0, 102.5, 107.625))
    assert c.ret == 7.63 or c.ret == 7.62  # round(7.625, 2) in floats
    assert c.ret == pytest.approx(7.625, abs=0.006)
    assert c.names == 2 and c.priced == 2 and c.sessions_flat == 0


def test_the_chain_itself_counts_contributors_and_who_contributed():
    ch = chain([[10, 11, 12.1], [20, None, 19]], 3)
    assert ch.levels == pytest.approx((100.0, 110.0, 121.0))
    assert ch.contributors == (0, 1, 1)
    assert ch.contributed == ((1, 2), ())


def test_a_member_joins_mid_window_once_it_has_two_consecutive_bars():
    """B has no bar until session 2: session 1 is A alone, B joins on session 3 (its 2nd bar)."""
    ds = days(4)
    inputs = build(
        [ms(T1, "AAA", S[0]), ms(T1, "BBB", S[1])],
        {S[0]: tape(ds, [10, 11, 11, 11]), S[1]: tape(ds, [None, None, 50, 60])},
    )
    c = card(page(inputs))
    # s1: A +10% -> 110; s2: A 0% (B has one bar, no return yet) -> 110; s3: mean(0%, +20%) -> 121
    assert c.points == pytest.approx((100.0, 110.0, 110.0, 121.0))
    assert c.priced == 2 and c.sessions_flat == 0


def test_a_members_gap_breaks_only_its_own_contribution():
    """B misses session 2: its returns into AND out of session 2 drop; A's never do."""
    ds = days(4)
    inputs = build(
        [ms(T1, "AAA", S[0]), ms(T1, "BBB", S[1])],
        {S[0]: tape(ds, [10, 11, 12.1, 13.31]), S[1]: tape(ds, [10, 10, None, 10])},
    )
    c = card(page(inputs))
    # s1: mean(+10%, 0%) = 5% -> 105; s2: A alone +10% -> 115.5; s3: A alone +10% -> 127.05
    assert c.points == pytest.approx((100.0, 105.0, 115.5, 127.05))
    assert c.sessions_flat == 0


def test_a_session_no_member_can_contribute_to_carries_flat_and_is_counted():
    """Both miss session 2 (the calendar comes from the benchmark): sessions 2 and 3 carry flat."""
    ds = days(5)
    inputs = build(
        [ms(T1, "AAA", S[0]), ms(T1, "BBB", S[1])],
        {S[0]: tape(ds, [10, 11, None, 11, 12.1]), S[1]: tape(ds, [10, 11, None, 11, 12.1])},
        {"SPY": tape(ds, [100, 100, 100, 100, 100])},
    )
    c = card(page(inputs))
    assert c.points == pytest.approx((100.0, 110.0, 110.0, 110.0, 121.0))
    assert c.sessions_flat == 2


def test_a_card_with_no_priced_name_reads_no_return_never_a_flat_zero():
    ds = days(3)
    inputs = build(
        [ms(T1, "AAA", S[0]), ms(T1, "BBB", None)],
        {S[0]: {}},
        {"SPY": tape(ds, [100, 101, 102])},
    )
    c = card(page(inputs))
    assert c.points == pytest.approx((100.0, 100.0, 100.0))
    assert c.ret is None and c.vs_spy is None and c.priced == 0
    assert c.names == 2 and c.unresolved == 1 and c.sessions_flat == 2


# ---------------------------------------------------------------------------------------------------------
# the windows: slice + rebase
# ---------------------------------------------------------------------------------------------------------


def _long_tape():
    """300 sessions; AAA rises 1% a session, so a rebased window's end is 100 × 1.01^N."""
    ds = days(300)
    closes = [10.0 * 1.01**i for i in range(300)]
    return ds, build(
        [ms(T1, "AAA", S[0])], {S[0]: tape(ds, closes)}, {"SPY": tape(ds, [100.0] * 300)}
    )


@pytest.mark.parametrize("window", ["1y", "90d", "30d"])
def test_a_window_is_the_tail_of_the_1y_series_rebased_to_100(window):
    ds, inputs = _long_tape()
    n = WINDOWS[window]
    result = page(inputs, window)
    c = card(result)
    assert result.sessions == n and len(result.dates) == n + 1 and result.dates[-1] == ASOF
    assert result.dates[0] == ds[-(n + 1)]
    assert c.points[0] == BASE and len(c.points) == n + 1
    assert c.points[-1] == pytest.approx(BASE * 1.01**n)
    assert c.ret == pytest.approx((1.01**n - 1) * 100, abs=0.006)


def test_the_1y_calendar_is_the_last_253_sessions():
    _, inputs = _long_tape()
    assert len(inputs.sessions) == WINDOWS["1y"] + 1 and inputs.sessions[-1] == ASOF


def test_a_short_tape_shortens_the_window_and_says_so():
    ds = days(40)
    inputs = build([ms(T1, "AAA", S[0])], {S[0]: tape(ds, [10.0 + i for i in range(40)])})
    result = page(inputs, "90d")
    assert result.sessions == 39 and len(card(result).points) == 40


# ---------------------------------------------------------------------------------------------------------
# since creation
# ---------------------------------------------------------------------------------------------------------


def test_since_creation_inside_the_window_runs_from_the_last_session_on_or_before_it():
    _, inputs = _long_tape()
    created = ASOF - timedelta(days=10)
    inputs = build(
        [ms(T1, "AAA", S[0])],
        {S[0]: tape(days(300), [10.0 * 1.01**i for i in range(300)])},
        created={T1: created},
    )
    c = card(page(inputs, "30d"))
    assert c.created_in_window and c.created_base == created and c.created == created
    assert c.since_created == pytest.approx((1.01**10 - 1) * 100, abs=0.006)


def test_since_creation_outside_the_window_is_created_before_the_window():
    inputs = build(
        [ms(T1, "AAA", S[0])],
        {S[0]: tape(days(300), [10.0 * 1.01**i for i in range(300)])},
        created={T1: ASOF - timedelta(days=60)},
    )
    c30 = card(page(inputs, "30d"))
    assert not c30.created_in_window and c30.since_created is None and c30.created_base is None
    c90 = card(page(inputs, "90d"))  # the same card, a window that reaches back past its creation
    assert c90.created_in_window and c90.since_created == pytest.approx(
        (1.01**60 - 1) * 100, abs=0.006
    )


def test_an_unknown_creation_day_shows_no_since_creation():
    _, inputs = _long_tape()
    c = card(page(inputs, "30d"))
    assert c.created is None and not c.created_in_window and c.since_created is None


def test_a_creation_day_between_sessions_falls_back_to_the_session_before():
    ds = [date(2026, 5, 28), date(2026, 5, 29), date(2026, 6, 1)]  # Thu, Fri, Mon
    assert creation_base(date(2026, 5, 30), ds) == 1  # a Saturday -> Friday's close
    assert creation_base(date(2026, 5, 27), ds) is None  # before the window
    assert creation_base(date(2026, 6, 1), ds) == 2


# ---------------------------------------------------------------------------------------------------------
# the benchmarks
# ---------------------------------------------------------------------------------------------------------


def test_benchmarks_ride_the_same_sessions_and_set_vs_spy():
    ds = days(3)
    inputs = build(
        [ms(T1, "AAA", S[0])],
        {S[0]: tape(ds, [10, 11, 12.1])},
        {"SPY": tape(ds, [100, 102, 104.04]), "IWM": tape(ds, [50, 50, 49])},
    )
    result = page(inputs)
    spy, iwm = result.benchmarks
    assert spy.symbol == "SPY" and spy.available and spy.ret == pytest.approx(4.04)
    assert spy.points == pytest.approx((100.0, 102.0, 104.04))
    assert iwm.ret == pytest.approx(-2.0)
    assert card(result).vs_spy == pytest.approx(21.0 - 4.04, abs=0.006)
    assert result.session_source == "benchmarks"


def test_a_missing_benchmark_is_absent_with_its_reason_and_no_vs_spy():
    ds = days(3)
    inputs = build(
        [ms(T1, "AAA", S[0])],
        {S[0]: tape(ds, [10, 11, 12.1])},
        {"IWM": tape(ds, [50, 51, 52])},
    )
    result = page(inputs)
    spy, iwm = result.benchmarks
    assert not spy.available and spy.points is None and spy.ret is None
    assert spy.reason == "no SPY tape at this as-of"
    assert iwm.available
    assert card(result).vs_spy is None and card(result).ret == pytest.approx(21.0)


def test_no_benchmark_tape_at_all_takes_the_sessions_from_the_companies():
    ds = days(3)
    inputs = build([ms(T1, "AAA", S[0])], {S[0]: tape(ds, [10, 11, 12.1])})
    result = page(inputs)
    assert result.session_source == "companies" and len(result.dates) == 3
    assert all(not b.available for b in result.benchmarks)


def test_a_late_benchmark_never_cuts_the_lines_short():
    cal, src = session_calendar(
        [date(2026, 5, 28), date(2026, 5, 29)],
        [date(2026, 5, 27), date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2)],
        ASOF,
    )
    # the benchmarks' days, plus company days after their last bar, never past the as-of
    assert cal == (date(2026, 5, 28), date(2026, 5, 29), date(2026, 6, 1)) and src == "benchmarks"


# ---------------------------------------------------------------------------------------------------------
# who counts: the silent toggle, the dedupe
# ---------------------------------------------------------------------------------------------------------


def test_context_and_bench_are_left_out_by_default_and_included_on_the_toggle():
    ds = days(3)
    rows = [ms(T1, "AAA", S[0]), ms(T1, "CTX", S[1], segment=CONTEXT_LABEL)]
    closes = {S[0]: tape(ds, [10, 11, 12.1]), S[1]: tape(ds, [10, 5, 2.5])}
    off = build(rows, closes)
    c = card(page(off))
    assert c.names == 1 and c.silent_left_out == 1 and c.ret == pytest.approx(21.0)
    assert off.silent_rows_left_out == 1 and off.companies == 1

    on = build(rows, closes, include_silent=True)
    c = card(page(on))
    assert c.names == 2 and c.silent_left_out == 0
    # s1: mean(+10%, −50%) = −20% -> 80; s2: mean(+10%, −50%) -> 64
    assert c.points == pytest.approx((100.0, 80.0, 64.0))
    assert page(on).include_silent is True and on.companies == 2


def test_a_company_in_two_links_of_a_card_counts_once():
    ds = days(3)
    rows = [
        ms(T1, "AAA", S[0], segment="Turbines"),
        ms(T1, "AAA", S[0], segment="Cables"),
        ms(T1, "BBB", S[1]),
        ms(T1, "ZZZ", None),
        ms(T1, "ZZZ", None, segment="Cables"),
    ]
    closes = {S[0]: tape(ds, [10, 11, 12.1]), S[1]: tape(ds, [10, 10, 10])}
    inputs = build(rows, closes)
    c = card(page(inputs))
    # AAA once (not double-weighted): mean(+10%, 0%) each session
    assert c.points == pytest.approx((100.0, 105.0, 110.25))
    assert c.names == 3 and c.unresolved == 1 and c.priced == 2
    assert inputs.unresolved_rows == 1


def test_cards_sort_by_name_and_carry_their_state():
    ds = days(3)
    inputs = build(
        [ms(T1, "AAA", S[0], card="Zinc"), ms(T2, "BBB", S[1], card="alpha")],
        {S[0]: tape(ds, [10, 11, 12]), S[1]: tape(ds, [10, 9, 8])},
        cards=[(T1, "Zinc"), (T2, "alpha")],
        states={T1: "armed"},
    )
    result = page(inputs)
    assert [c.thesis_name for c in result.cards] == ["alpha", "Zinc"]
    assert card(result, T1).state == "armed" and card(result, T2).state is None


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    ds = days(3)

    def fake_read(conn, asof, known_at, clock, include_silent):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock, include_silent)
        return build(
            [ms(T1, "AAA", S[0]), ms(T1, "BBB", None)],
            {S[0]: tape(ds, [10, 11, 12.1])},
            {"SPY": tape(ds, [100, 101, 102])},
            created={T1: ds[1]},
            states={T1: "warming"},
            include_silent=include_silent,
        )

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_every_window_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/narrative-index", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["window"] == "1y"
    assert body["include_silent"] is False and body["window_sessions"] == WINDOWS
    asof, known_at, clock, silent = seen["view"]
    assert asof == ASOF and clock == "public" and known_at is not None and silent is False
    (c,) = body["cards"]
    assert c["state"] == "warming" and c["names"] == 2 and c["priced"] == 1
    assert c["created_in_window"] is True and c["since_created"] == pytest.approx(10.0)
    assert [b["symbol"] for b in body["benchmarks"]] == ["SPY", "IWM"]
    assert body["benchmarks"][1]["available"] is False

    for window in ("90d", "30d"):
        again = client.get(
            "/dashboards/narrative-index", params={"asof": "2026-06-01", "window": window}
        ).json()
        assert again["cache"] == "hit" and again["window"] == window
    detail = client.get(f"/dashboards/narrative-index/{T1}", params={"asof": "2026-06-01"}).json()
    assert detail["cache"] == "hit" and seen["reads"] == 1
    assert detail["card"]["thesis_id"] == str(T1) and detail["contributors"] == [0, 1, 1]
    assert [(m["ticker"], m["sessions"]) for m in detail["members"]] == [("AAA", 2), ("BBB", 0)]

    client.get(
        "/dashboards/narrative-index", params={"asof": "2026-06-01", "include_silent": "true"}
    )
    assert seen["reads"] == 2 and seen["view"][3] is True  # the toggle is part of the key

    client.get("/dashboards/narrative-index", params={"asof": market_today().isoformat()})
    assert seen["view"][1:3] == (None, "record") and seen["reads"] == 3


def test_the_router_rejects_an_unknown_window_and_404s_an_uncounted_card(stub_client):
    client, _ = stub_client
    r = client.get("/dashboards/narrative-index", params={"asof": "2026-06-01", "window": "5y"})
    assert r.status_code == 422
    r = client.get(f"/dashboards/narrative-index/{T2}", params={"asof": "2026-06-01"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a price published after a past as-of stays invisible to it (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)


def _price(db, sid, d: date, close: float, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": close,
            "volume": 1000.0,
            "valid_from": d,
            "recorded_at": recorded_at,
        },
    )


def _security(db, ticker: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0009876544", "2026-01-01"),
        )
    return sid


def test_a_price_published_after_a_past_asof_stays_invisible(db):
    """60 daily bars ending at the as-of, each public at its own session. The as-of's own bar is then
    re-versioned a week after the thaw at double the close: the past view's index still steps on the
    ORIGINAL close (the correction was not public by the end of that day), a live view steps on the
    correction."""
    sid = _security(db, "NIDX")
    start = ASOF - timedelta(days=59)
    for i in range(60):
        d = start + timedelta(days=i)
        _price(db, sid, d, 10.0 + i * 0.1, datetime.combine(d, time.min, tzinfo=timezone.utc))
    _price(db, sid, ASOF, 31.8, _THAW + timedelta(days=7))  # the as-of bar (15.9), corrected later
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Narrative fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="NIDX", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        params = {"asof": ASOF.isoformat(), "window": "30d"}
        past = client.get("/dashboards/narrative-index", params=params).json()
        live = client.get(
            "/dashboards/narrative-index",
            params={"asof": market_today().isoformat(), "window": "30d"},
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    (c,) = [x for x in past["cards"] if x["thesis_id"] == str(thesis.id)]
    assert past["dates"][-1] == ASOF.isoformat() and past["sessions"] == 30
    pts = c["points"]
    assert pts[-1] / pts[-2] == pytest.approx(15.9 / 15.8, rel=1e-4)  # not the 31.8 correction
    assert c["ret"] == pytest.approx((15.9 / 12.9 - 1) * 100, abs=0.006)
    # created 2026-01-02: long before the 30-session window
    assert c["created_in_window"] is False and c["since_created"] is None
    assert past["cards_counted"] >= 1 and past["roster_fallback_cards"] >= 1
    (lc,) = [x for x in live["cards"] if x["thesis_id"] == str(thesis.id)]
    assert lc["points"][-1] / lc["points"][-2] == pytest.approx(31.8 / 15.8, rel=1e-4)
