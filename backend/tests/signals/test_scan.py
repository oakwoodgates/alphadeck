from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from signals.scan import rank_candidates

# Real HIMS EOD (the M3 target): security A's bars produce a real breakout at 2026-06-01.
_HIMS = parse_yahoo_chart(
    json.loads(
        (
            Path(__file__).resolve().parent.parent / "fixtures" / "prices" / "HIMS.yahoo.json"
        ).read_text(encoding="utf-8")
    )
)
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _add_security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "2026-01-01"),
        )
    db.commit()
    return sid


def _add_buy(db, sid, name, role, usd, d, accession):
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "insider_name": name,
            "insider_role": role,
            "txn_code": "P",
            "usd": usd,
            "accession": accession,
            "valid_from": d,
        },
    )
    db.commit()


def test_rank_puts_both_keys_first(db):
    # A: both keys — a senior insider cluster + the real HIMS price breakout
    a = _add_security(db, "AAA")
    _add_buy(db, a, "Jane Doe", "Chief Executive Officer", 150_000, date(2026, 5, 20), "acc-a1")
    _add_buy(db, a, "John Roe", "Chief Financial Officer", 120_000, date(2026, 5, 21), "acc-a2")
    ingest_prices(db, a, _HIMS)

    # B: conviction only — a single small buy, no price history (so no breakout)
    b = _add_security(db, "BBB")
    _add_buy(db, b, "Sam Lee", "Chief Executive Officer", 60_000, date(2026, 5, 20), "acc-b1")

    # pass B first to prove the ranking actually reorders
    ranked = rank_candidates(db, [(b, "BBB"), (a, "AAA")], date(2026, 6, 1), known_at=_KNOWN)
    assert [c.ticker for c in ranked] == ["AAA", "BBB"]
    assert ranked[0].both_keys is True
    assert ranked[1].both_keys is False
    assert ranked[1].conviction is not None and ranked[1].confirmation is None
