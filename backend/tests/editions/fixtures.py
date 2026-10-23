"""Shared seeding for the editions engine tests: one company's tape and one big open-market insider buy whose
SEC acceptance and our ingest are far apart — so a view judged on the PUBLIC clock (when SEC accepted it) and
one judged on when THIS system stored it would give visibly different answers on the same nights."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID

TAPE_START = date(2026, 6, 1)
TAPE_END = date(2026, 7, 10)
BUY_DAY = date(2026, 6, 15)
# 17:00 ET on 06-17 — public that night
ACCEPTED = datetime(2026, 6, 17, 21, 0, tzinfo=timezone.utc)
INGESTED = datetime(2026, 7, 20, 2, 30, tzinfo=timezone.utc)  # we only stored it on 07-19's pass


def weekdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def evening(d: date) -> datetime:
    """23:00 UTC = 19:00 ET on ``d`` — after the session settles, before the market day ends."""
    return datetime(d.year, d.month, d.day, 23, 0, tzinfo=timezone.utc)


def seed_bar(db, sid: UUID, d: date, *, close: float = 10.0, recorded_at: datetime | None = None):
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "open": close,
            "high": close * 1.05,
            "low": close * 0.95,
            "close": close,
            "volume": 100_000,
            "valid_from": d,
            "recorded_at": recorded_at or evening(d),
        },
    )


def seed_tape(db, sid: UUID, *, start: date = TAPE_START, end: date = TAPE_END) -> None:
    """A flat weekday tape, each bar stored the evening it printed (its own night's pass)."""
    for d in weekdays(start, end):
        seed_bar(db, sid, d)


def seed_big_buy(
    db,
    sid: UUID,
    *,
    accession: str = "0000000000-26-000001",
    valid_from: date = BUY_DAY,
    accepted: datetime | None = ACCEPTED,
    recorded_at: datetime = INGESTED,
    usd: float = 600_000.0,
) -> None:
    """A $600k CEO open-market buy (above the strong-single threshold): a Key-1 conviction on its own."""
    row: dict[str, Any] = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "insider_name": "Jane CEO",
        "insider_role": "CEO",
        "txn_code": "P",
        "shares": usd / 10.0,
        "price": 10.0,
        "usd": usd,
        "accession": accession,
        "txn_seq": 0,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }
    if accepted is not None:
        row["accepted"] = accepted
    append_fact(db, "fact_insider_txn", row)


def member_rows(*sids: UUID) -> tuple[dict[str, Any], ...]:
    """``basket_snapshot.members`` rows (the shape ``repositories.mappers._row_to_basket_member`` reads)."""
    return tuple(
        {
            "ordinal": i,
            "ticker": f"T{i}",
            "role": "core",
            "security_id": str(s),
            "detail": None,
            "segment": None,
            "thesis_fit": None,
            "conviction": None,
            "surfaced_terms": [],
            "authored_by": "system_drafted",
            "signed_off": False,
        }
        for i, s in enumerate(sids)
    )
