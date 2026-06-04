from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg
import pytest

from db.bitemporal import append_fact, as_of
from db.session import DEFAULT_TENANT_ID


def _insider(security_id, *, accession, insider_name, valid_from, usd, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "insider_name": insider_name,
        "txn_code": "P",
        "usd": usd,
        "accession": accession,
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def test_valid_time_read_has_no_lookahead(db, security_id):
    """A fact whose event date is after `asof` is invisible to the read."""
    t = datetime(2026, 6, 3, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-1",
            insider_name="CEO",
            valid_from=date(2026, 6, 1),
            usd=1_000_000,
            recorded_at=t,
        ),
    )
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="a-2",
            insider_name="CEO",
            valid_from=date(2026, 6, 5),
            usd=2_000_000,
            recorded_at=t,
        ),
    )
    db.commit()

    rows = as_of(
        db,
        "fact_insider_txn",
        security_id=security_id,
        asof=date(2026, 6, 2),
        known_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert [r["accession"] for r in rows] == [
        "a-1"
    ]  # the 2026-06-05 txn is in the future as of 06-02


def test_transaction_time_correction_does_not_leak_backward(db, security_id):
    """The required honest bitemporal test: a later correction must not change an earlier as-of read."""
    t1 = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    # original value $2.1M, learned at t1
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="acc-X",
            insider_name="CEO",
            valid_from=date(2026, 6, 1),
            usd=2_100_000,
            recorded_at=t1,
        ),
    )
    # correction (same natural key) learned at t2: it was actually $0.9M
    append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="acc-X",
            insider_name="CEO",
            valid_from=date(2026, 6, 1),
            usd=900_000,
            recorded_at=t2,
        ),
    )
    db.commit()

    asof = date(2026, 6, 30)
    at_t1 = as_of(
        db,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=t1,
        tenant_id=DEFAULT_TENANT_ID,
    )
    at_t2 = as_of(
        db,
        "fact_insider_txn",
        security_id=security_id,
        asof=asof,
        known_at=t2,
        tenant_id=DEFAULT_TENANT_ID,
    )

    assert len(at_t1) == 1 and float(at_t1[0]["usd"]) == 2_100_000  # correction not yet known at t1
    assert len(at_t2) == 1 and float(at_t2[0]["usd"]) == 900_000  # correction applied by t2


def test_fact_tables_are_append_only(db, security_id):
    """Append-only is enforced by a DB trigger, not convention: an UPDATE raises."""
    t = datetime(2026, 6, 3, tzinfo=timezone.utc)
    fid = append_fact(
        db,
        "fact_insider_txn",
        _insider(
            security_id,
            accession="acc-Y",
            insider_name="CFO",
            valid_from=date(2026, 6, 1),
            usd=500_000,
            recorded_at=t,
        ),
    )
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE fact_insider_txn SET usd = 1 WHERE id = %s", (fid,))
    db.rollback()
