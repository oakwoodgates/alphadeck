from __future__ import annotations

import uuid
from datetime import date, timedelta

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
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
    return BasketMember(
        ticker=ticker, role="the name", archetype=Archetype.LEADER, security_id=security_id
    )


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
    assert [s["kind"] for s in m["signals"]] == ["sma_position", "range_52w", "volume_regime"]
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
            BasketMember(ticker="GHOST", role="r", archetype=Archetype.LOTTO, security_id=None),
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
    sig = r.json()["members"][0]["signals"][0]
    assert sig["basis"]["window_end"] == _ASOF.isoformat()
    by_key = {mt["key"]: mt for mt in sig["metrics"]}
    assert by_key["close"]["value"] == 15.9  # the asof bar (10.0 + 59*0.1), not the 999 print


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


def test_unknown_thesis_404s_and_missing_asof_422s(client, db):
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
