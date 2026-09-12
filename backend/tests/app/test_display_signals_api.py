from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.routers import theses as theses_router
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_today
from domain.thesis import BasketMember, Thesis
from repositories import thesis_repo
from signals.base import PointInTimeData

_ASOF = date(2026, 6, 1)
# The measured serve-path leak's shape: bars dated in the past, INGESTED long after (the 2026-09-01 thaw
# stamped TPCS's August bars). Any instant after _ASOF's day-end works; this is the real one.
_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)


def _knowable_on(d: date) -> datetime:
    """A bar's honest ``recorded_at``: its own date (the ``ingest_prices_backfill`` contract — an EOD bar
    is knowable that day). Leaving the column at the DB default ``now()`` would make every June bar
    "learned today", and a 06-01 read must NOT see that — the serve-path leak the router now closes.
    """
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _price(
    db,
    security_id,
    d: date,
    close: float,
    volume: float | None = None,
    recorded_at: datetime | None = None,
) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": d,
            "close": close,
            "volume": volume,
            "valid_from": d,
            "recorded_at": recorded_at or _knowable_on(d),
        },
    )


def _seed_bars(
    db, security_id, n: int, end: date = _ASOF, recorded_at: datetime | None = None
) -> None:
    """n consecutive-day bars ending at ``end``: closes 10.0, 10.1, … + a flat volume (ascending,
    deterministic — enough for every price-fed member to compute). Each bar is knowable on its own date
    unless ``recorded_at`` stamps them all at one instant (the leak test's thaw shape)."""
    start = end - timedelta(days=n - 1)
    for i in range(n):
        _price(
            db,
            security_id,
            start + timedelta(days=i),
            10.0 + i * 0.1,
            volume=1000.0,
            recorded_at=recorded_at,
        )
    db.commit()


def _member(security_id, ticker: str = "DEVCO") -> BasketMember:
    return BasketMember(ticker=ticker, role="the name", security_id=security_id)


def _seed_thesis(db, members: list[BasketMember]) -> uuid.UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="display-signals fixture",
        narrative="tape context for the panel",
        ticker=members[0].ticker if members else None,
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


def _master_row(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0009876543", "2026-01-01"),
        )
    db.commit()
    return sid


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()["count"]


def test_display_signals_happy_path(client, db, security_id):
    _seed_bars(db, security_id, 220)
    tid = _seed_thesis(db, [_member(security_id)])
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert body["asof"] == _ASOF.isoformat()
    assert len(body["members"]) == 1
    m = body["members"][0]
    assert m["security_id"] == str(security_id)
    assert m["ticker"] == "DEVCO"  # resolved from the master, not echoed from the basket
    # registry render order; insider_flow_90d is honestly ABSENT (no Form 4 ingested), not zeroed.
    # vcp (§3.2) computes on the ascending fixture but is NOT coiling (no loud headline) — the quiet
    # contraction metrics still ride the panel, so it appears in the emitted list.
    assert [s["kind"] for s in m["signals"]] == [
        "sma_position",
        "trailing_returns",
        "range_52w",
        "volume_regime",
        "rvol",
        "vcp",
        "price_path",
    ]
    sig = m["signals"][0]
    assert sig["basis"]["bars_used"] == 220
    assert sig["basis"]["window_end"] == _ASOF.isoformat()
    by_key = {mt["key"]: mt for mt in sig["metrics"]}
    assert by_key["close"]["value"] == 31.9  # 10.0 + 219*0.1
    assert by_key["ma_slow"]["value"] is not None  # 220 bars -> the 200d line is real
    assert by_key["ma_slow"]["note"] is None
    # the posture chip rides the wire: ascending fixture = the strongest quadrant
    assert sig["headline"]["key"] == "above_rising"
    assert sig["headline"]["glyph"] == "up"
    # trailing returns ride the SAME generic wire (zero schema change): the ascending fixture is up
    # across every window, tone=pos, unit=pct — each an EOD trading-day return (1d = prior close)
    ret = next(s for s in m["signals"] if s["kind"] == "trailing_returns")
    ret_by_key = {mt["key"]: mt for mt in ret["metrics"]}
    assert [mt["key"] for mt in ret["metrics"]] == [
        "ret_1d",
        "ret_7d",
        "ret_30d",
        "ret_90d",
        "ret_1y",
    ]
    assert ret_by_key["ret_1d"]["value"] == 0.31  # 31.9 / 31.8 - 1
    # the four reachable windows are all up on the ascending fixture (tone=pos, unit=pct)
    for k in ("ret_1d", "ret_7d", "ret_30d", "ret_90d"):
        assert ret_by_key[k]["tone"] == "pos" and ret_by_key[k]["unit"] == "pct"
    # 1Y needs 253 bars; a 220-bar name honestly BLANKS it (value None + the why), never a fake number
    assert ret_by_key["ret_1y"]["value"] is None
    assert ret_by_key["ret_1y"]["note"] == "n/a: 220/253 bars"
    assert ret["basis"]["params"]["windows_trading_days"] == [1, 7, 30, 90, 252]
    # rvol rides the SAME generic wire (zero schema change): TWO windows off one member — the 8-bar
    # (call-matched) rvol and the 20-bar (trader-convention) rvol20, each a quiet 1.0x on the
    # flat-volume fixture (below the 1.5x loud thresholds the FE reads off basis.params)
    rv = next(s for s in m["signals"] if s["kind"] == "rvol")
    assert [mt["key"] for mt in rv["metrics"]] == ["rvol", "rvol20"]
    rv_by_key = {mt["key"]: mt for mt in rv["metrics"]}
    assert rv_by_key["rvol"]["value"] == 1.0 and rv_by_key["rvol"]["unit"] == "ratio"
    assert rv_by_key["rvol20"]["value"] == 1.0 and rv_by_key["rvol20"]["unit"] == "ratio"
    assert rv["basis"]["params"]["loud_mult"] == 1.5
    assert (
        rv["basis"]["params"]["loud_mult_20"] == 1.5
        and rv["basis"]["params"]["baseline_bars_20"] == 20
    )
    # the price path rides the wire as the ONE fixed-slot `series` (the widening that let a SHAPE
    # onto the payload): the 220-bar fixture fills all 90 slots (no gaps), newest last = the asof
    # close, first = 89 bars back; a shape only — no metric the sort or the call could read (#4)
    path = next(s for s in m["signals"] if s["kind"] == "price_path")
    assert [s["key"] for s in path["series"]] == ["close"]
    vals = path["series"][0]["values"]
    assert len(vals) == 90 and None not in vals
    assert vals[-1] == pytest.approx(31.9)  # 10.0 + 219*0.1
    assert vals[0] == pytest.approx(23.0)  # 10.0 + (219-89)*0.1
    assert path["series"][0]["unit"] == "price"
    assert path["metrics"] == [] and path["headline"] is None
    assert path["basis"]["bars_used"] == 90 and path["basis"]["note"] is None
    assert path["basis"]["params"] == {"bars": 90, "lookback_days": 150}


def test_theme_breadth_rides_the_response(client, db, security_id):
    """§1.1 — the THESIS-LEVEL breadth thrust rides the SAME response as a top-level ``breadth`` field
    (DISPLAY-only). Two ascending members (each 80 bars) sit above their 50d SMA at both points, so
    breadth is 100% with a flat delta -> the quiet (non-thrust) state, computed over the 2 counted
    members. Proves the field is wired end-to-end; the thrust thresholds are unit-tested in
    tests/signals/display/test_theme_breadth.py."""
    other = _master_row(db, "COMVR")
    _seed_bars(db, security_id, 80)
    _seed_bars(db, other, 80)
    tid = _seed_thesis(db, [_member(security_id), _member(other, ticker="COMVR")])
    body = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}).json()
    breadth = body["breadth"]
    assert breadth is not None and breadth["kind"] == "theme_breadth"
    by_key = {mt["key"]: mt for mt in breadth["metrics"]}
    assert by_key["breadth"]["value"] == 100.0  # both members above their 50d SMA
    assert by_key["breadth_delta"]["value"] == 0.0
    assert by_key["members_counted"]["value"] == 2.0
    assert breadth["headline"]["key"] == "quiet"  # majority holds but no +25pt surge -> not loud


def test_theme_breadth_is_none_for_a_basketless_thesis(client, db):
    """No resolved members -> no breadth reading (None), never a fabricated 0%."""
    tid = _seed_thesis(db, [])
    body = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}).json()
    assert body["breadth"] is None
    assert body["sector_rs"] is None  # §1.3 — no members, no rollup


def _seed_flat(db, security_id, n: int, close: float = 50.0, end: date = _ASOF) -> None:
    """n consecutive-day bars ending at ``end`` at a CONSTANT close — a flat benchmark, so a rising
    member's RS climbs to a fresh high (the ``_seed_bars`` member vs this benchmark = leading)."""
    start = end - timedelta(days=n - 1)
    for i in range(n):
        _price(db, security_id, start + timedelta(days=i), close, volume=1000.0)
    db.commit()


def test_relative_strength_and_sector_rs_ride_the_response(client, db, security_id):
    """§2.1 the per-name RS column + §1.3 the supersector rollup, end-to-end on the SAME generic wire
    (zero call-path touch). A rising member vs a flat SPY/IWM prints a fresh 13-week RS high -> the
    per-name column is ``leading`` and the (unclassified) supersector rollup reports one leader."""
    from securities.benchmarks import seed_benchmarks

    ids = seed_benchmarks(db)
    _seed_bars(db, security_id, 65)  # member: rising closes
    _seed_flat(db, ids["SPY"], 65)  # benchmarks: flat -> RS rises to a fresh high
    _seed_flat(db, ids["IWM"], 65)
    tid = _seed_thesis(db, [_member(security_id)])

    body = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}).json()

    m = body["members"][0]
    rs = next(s for s in m["signals"] if s["kind"] == "relative_strength")
    assert rs["headline"]["key"] == "leading"
    by = {mt["key"]: mt for mt in rs["metrics"]}
    assert by["rs_spy"]["value"] is not None and by["rs_iwm"]["value"] is not None
    assert {e["key"] for e in rs["events"]} == {"rs_high_spy", "rs_high_iwm"}
    # §1.3 — the rollup rides a top-level field; DEVCO has no enriched sector -> the unclassified group
    sr = body["sector_rs"]
    assert sr is not None and sr["kind"] == "sector_rs"
    sr_by = {mt["key"]: mt for mt in sr["metrics"]}
    assert sr_by["rs_lead_unclassified"]["value"] == 1.0
    assert sr["headline"]["key"] == "leading"


def test_relative_strength_absent_without_a_benchmark(client, db, security_id):
    """No benchmark tape ingested -> the RS column is honestly ABSENT (like etf_flow with no samples),
    never a fabricated ratio, and the sector rollup is None."""
    _seed_bars(db, security_id, 220)
    tid = _seed_thesis(db, [_member(security_id)])
    body = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}).json()
    m = body["members"][0]
    assert all(s["kind"] != "relative_strength" for s in m["signals"])
    assert body["sector_rs"] is None


def test_member_with_no_bars_shows_with_empty_signals(client, db, security_id):
    _seed_bars(db, security_id, 60)
    bare_sid = _master_row(db, "BARECO")
    tid = _seed_thesis(db, [_member(security_id), _member(bare_sid, ticker="BARECO")])
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    assert r.status_code == 200
    rows = {m["ticker"]: m for m in r.json()["members"]}
    assert rows["DEVCO"]["signals"]  # bars -> a reading
    assert rows["BARECO"]["signals"] == []  # no bars -> an honest empty, the member still shows


def test_unresolved_member_is_omitted_and_dupes_collapse(client, db, security_id):
    _seed_bars(db, security_id, 60)
    tid = _seed_thesis(
        db,
        [
            _member(security_id),
            _member(security_id),  # same security twice in the basket -> one row
            BasketMember(ticker="GHOST", role="r", security_id=None),
        ],
    )
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    body = r.json()
    assert [m["security_id"] for m in body["members"]] == [str(security_id)]


def test_no_lookahead_a_post_asof_bar_is_invisible(client, db, security_id):
    """The VALID axis alone: the future bar is stamped as recorded BEFORE the as-of (a bar recorded early,
    dated late — the price-window suite's idiom), so only ``valid_from <= asof`` can hide it. Stamping it
    on its own date would hide it on BOTH axes and prove nothing about this one."""
    _seed_bars(db, security_id, 60)
    _price(
        db, security_id, _ASOF + timedelta(days=1), 999.0, recorded_at=_knowable_on(_ASOF)
    )  # the future bar a backtest must not see
    db.commit()
    tid = _seed_thesis(db, [_member(security_id)])
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    signals = r.json()["members"][0]["signals"]
    sig = signals[0]
    assert sig["basis"]["window_end"] == _ASOF.isoformat()
    by_key = {mt["key"]: mt for mt in sig["metrics"]}
    assert by_key["close"]["value"] == 15.9  # the asof bar (10.0 + 59*0.1), not the 999 print
    # and the trailing return is 15.9/15.8-1, computed from the asof close vs the prior close — the
    # 999 future bar is invisible, so the 1d return is a quiet +0.63%, never a lookahead-blown spike
    ret = next(s for s in signals if s["kind"] == "trailing_returns")
    assert {mt["key"]: mt["value"] for mt in ret["metrics"]}["ret_1d"] == 0.63
    # …and the price path's last slot is the asof close too — the 999 print never enters the shape
    path = next(s for s in signals if s["kind"] == "price_path")
    assert path["series"][0]["values"][-1] == pytest.approx(15.9)
    assert path["basis"]["window_end"] == _ASOF.isoformat()


def test_display_get_writes_nothing(client, db, security_id):
    _seed_bars(db, security_id, 60)
    tid = _seed_thesis(db, [_member(security_id)])
    before = (_count(db, "calls"), _count(db, "fact_price_eod"))
    for _ in range(2):  # a refetch / as-of scrub is a pure read
        assert (
            client.get(
                f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}
            ).status_code
            == 200
        )
    assert (_count(db, "calls"), _count(db, "fact_price_eod")) == before


def _fund_sample(db, security_id, d: date, shares: float) -> None:
    append_fact(
        db,
        "fact_fund_shares",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": security_id,
            "d": d,
            "shares_out": shares,
            "source": "globalx",
            "source_ref": "https://www.globalxetfs.com/funds/ura",
            "valid_from": d,
            "recorded_at": _knowable_on(d),  # a sampled shares-out print is knowable on its date
        },
    )


def test_etf_flow_rides_the_wire_for_a_sampled_sleeve_only(client, db, security_id):
    """The etf_flow member serves through the SAME generic endpoint with ZERO wire change: a sampled
    sleeve gets the flow signal (headline + windows + provenance); an unsampled equity member does not
    (an honest absence, exactly like insider_flow with nothing ingested)."""
    _seed_bars(db, security_id, 60)
    sleeve_sid = _master_row(db, "URA")
    _seed_bars(db, sleeve_sid, 60)
    # a 40-day flat-then-creation series: baseline flat, +1000 shares five days before asof
    for i in range(40, 4, -1):
        _fund_sample(db, sleeve_sid, _ASOF - timedelta(days=i), 10_000.0)
    _fund_sample(db, sleeve_sid, _ASOF - timedelta(days=4), 11_000.0)
    db.commit()
    tid = _seed_thesis(db, [_member(security_id), _member(sleeve_sid, ticker="URA")])

    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    assert r.status_code == 200
    rows = {m["ticker"]: m for m in r.json()["members"]}
    flow = next((s for s in rows["URA"]["signals"] if s["kind"] == "etf_flow"), None)
    assert flow is not None
    assert flow["headline"]["key"] == "net_inflow" and flow["headline"]["glyph"] == "up"
    by_key = {mt["key"]: mt for mt in flow["metrics"]}
    assert by_key["flow_1m_pct_of_shares"]["value"] == 10.0  # +1000 on a 10,000 baseline
    assert by_key["flow_1w_usd"]["value"] is not None
    assert flow["basis"]["source"] == "fact_fund_shares"
    assert flow["basis"]["params"]["source_ref"].startswith("https://")  # the sampled page (#6)
    # the equity member carries NO etf_flow — no samples, honestly absent (never a zeroed block)
    assert all(s["kind"] != "etf_flow" for s in rows["DEVCO"]["signals"])
    r = client.get(f"/theses/{uuid.uuid4()}/display-signals", params={"asof": "2026-06-01"})
    assert r.status_code == 404
    tid = _seed_thesis(db, [])
    assert client.get(f"/theses/{tid}/display-signals").status_code == 422


def test_call_response_is_unchanged_by_the_display_feature(client, db, security_id):
    """Belt-and-braces for the cron-idempotency bound: indicators never ride the CallCard wire (the
    real guard is structural — nothing in the display package can reach the call path)."""
    _seed_bars(db, security_id, 60)
    tid = _seed_thesis(db, [_member(security_id)])
    r = client.get(f"/theses/{tid}/call", params={"asof": _ASOF.isoformat()})
    assert r.status_code == 200
    assert not [k for k in r.json() if "display" in k or "indicator" in k]


# --- invariant #1 on the SERVE path: the tape beside a past card is the tape as knowable THEN ---
#
# The measured leak (docs/temp/serve-path-lookahead-audit-2026-09-09.md) was on /call, but this route
# builds the SAME PointInTimeData with the SAME default pin (known_at = now), so a Cockpit scrub-back
# showed an SMA posture computed from bars ingested after the as-of. Same cap, same seam, own test.


def test_display_scrub_back_hides_bars_recorded_after_the_asof(client, db, security_id):
    """THE LEAK, on the measured shape (TPCS: bars dated August, ingested 09-01): 220 bars ending _ASOF,
    every one stamped at the thaw. A _ASOF read — the Cockpit scrub-back — sees NONE of them: the member
    still shows, with ``signals: []`` (the honest empty), never a posture computed with hindsight. The
    same bars ARE knowable to a LIVE read (the thaw is in the past relative to today), so asof = today
    holds them — hidden by the as-of's clock, not gone. Before the fix the _ASOF read computed the full
    ``sma_position`` … ``price_path`` list from bars that were not on file that night."""
    _seed_bars(db, security_id, 220, recorded_at=_THAW)
    tid = _seed_thesis(db, [_member(security_id)])
    past = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()}).json()
    (m,) = past["members"]
    assert (
        m["ticker"] == "DEVCO" and m["signals"] == []
    )  # the row stays; its tape wasn't on file yet
    # the thesis-level breadth reads the member as THIN (no knowable bars), so its headline is the honest
    # "n/a" — never a fabricated 0% and never the ascending fixture's 100% computed with hindsight
    breadth = past["breadth"]
    assert breadth["headline"]["key"] == "unknown"
    by_key = {mt["key"]: mt["value"] for mt in breadth["metrics"]}
    assert by_key["members_thin"] == 1.0 and by_key["members_counted"] == 0.0
    live = client.get(
        f"/theses/{tid}/display-signals", params={"asof": market_today().isoformat()}
    ).json()
    (lm,) = live["members"]
    kinds = [s["kind"] for s in lm["signals"]]
    assert "sma_position" in kinds  # the live read holds the (now stale-but-knowable) tape
    assert lm["signals"][0]["basis"]["window_end"] == _ASOF.isoformat()  # the tape's last bar


def test_display_threads_the_asof_cap_for_a_past_view_and_None_for_the_live_one(
    client, db, security_id, monkeypatch
):
    """The per-site TEMPLATE (a future serve site that forgets ``known_at`` fails a copy of this): the
    display PIT is built with ``known_at = known_at_for_asof(asof)`` — the end of that MARKET day — for a
    past asof and ``None`` for a live one (the unchanged live read; None, not a host-clock now — the
    one-clock rule in ``decisions_repo``). The same seam the call and the scored view thread."""
    _seed_bars(db, security_id, 60)
    tid = _seed_thesis(db, [_member(security_id)])
    seen: list[datetime | None] = []

    class SpyPIT(PointInTimeData):
        def __init__(self, conn, **kw):
            seen.append(kw.get("known_at"))
            super().__init__(conn, **kw)

    monkeypatch.setattr(theses_router, "PointInTimeData", SpyPIT)
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": _ASOF.isoformat()})
    assert r.status_code == 200
    today = market_today()
    r = client.get(f"/theses/{tid}/display-signals", params={"asof": today.isoformat()})
    assert r.status_code == 200
    assert seen == [known_at_for_asof(_ASOF), None]
    assert seen[0] == datetime(
        2026, 6, 2, 3, 59, 59, 999999, tzinfo=timezone.utc
    )  # 06-01 23:59 EDT


def test_display_live_view_holds_a_bar_recorded_a_moment_ago(client, db, security_id):
    """The live guard: bars ingested just now (the live ingest's ``now()``-class stamp) at asof = today are
    as visible as before — the fix capped the PAST view only. The live PIT is the pre-fix one byte for
    byte (``known_at=None``, the spy test above), so nothing here can differ from the old read."""
    today = market_today()
    _seed_bars(db, security_id, 60, end=today, recorded_at=datetime.now(timezone.utc))
    tid = _seed_thesis(db, [_member(security_id)])
    body = client.get(f"/theses/{tid}/display-signals", params={"asof": today.isoformat()}).json()
    (m,) = body["members"]
    assert m["signals"] and m["signals"][0]["basis"]["window_end"] == today.isoformat()
