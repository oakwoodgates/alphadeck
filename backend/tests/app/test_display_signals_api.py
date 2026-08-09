from __future__ import annotations

import uuid
from datetime import date, timedelta

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.thesis import BasketMember, Thesis
from repositories import thesis_repo

_ASOF = date(2026, 6, 1)


def _price(db, security_id, d: date, close: float, volume: float | None = None) -> None:
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
        },
    )


def _seed_bars(db, security_id, n: int, end: date = _ASOF) -> None:
    """n consecutive-day bars ending at ``end``: closes 10.0, 10.1, … + a flat volume (ascending,
    deterministic — enough for every price-fed member to compute)."""
    start = end - timedelta(days=n - 1)
    for i in range(n):
        _price(db, security_id, start + timedelta(days=i), 10.0 + i * 0.1, volume=1000.0)
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
    # registry render order; insider_flow_90d is honestly ABSENT (no Form 4 ingested), not zeroed
    assert [s["kind"] for s in m["signals"]] == [
        "sma_position",
        "trailing_returns",
        "range_52w",
        "volume_regime",
        "rvol",
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
    _seed_bars(db, security_id, 60)
    _price(
        db, security_id, _ASOF + timedelta(days=1), 999.0
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
