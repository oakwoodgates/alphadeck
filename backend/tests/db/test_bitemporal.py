from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest

from db.bitemporal import append_fact, as_of, as_of_thesis
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


def _catalyst(security_id, *, source_ref, grade, valid_from, recorded_at):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "catalyst_type": "contract",
        "grade": grade,
        "label": "power-offtake agreement",
        "source": "ratified",
        "source_ref": source_ref,
        "ratified_by": "operator",
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def test_fact_catalyst_is_append_only(db, security_id):
    """The catalyst conviction fact is append-only too — an UPDATE raises (a re-grade is a new row)."""
    t = datetime(2026, 6, 3, tzinfo=timezone.utc)
    fid = append_fact(
        db,
        "fact_catalyst",
        _catalyst(
            security_id,
            source_ref="cat-1",
            grade="flip",
            valid_from=date(2026, 5, 1),
            recorded_at=t,
        ),
    )
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE fact_catalyst SET grade = 'core' WHERE id = %s", (fid,))
    db.rollback()


def test_fact_catalyst_correction_is_bitemporal(db, security_id):
    """A re-grade (provisional -> binding once the deal is signed) is a NEW row; the as-of read returns
    the version known at each transaction time — no lookahead on the correction."""
    t1 = datetime(2026, 6, 3, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 10, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_catalyst",
        _catalyst(
            security_id,
            source_ref="cat-X",
            grade="flip",
            valid_from=date(2026, 5, 1),
            recorded_at=t1,
        ),
    )
    append_fact(
        db,
        "fact_catalyst",
        _catalyst(
            security_id,
            source_ref="cat-X",
            grade="core",
            valid_from=date(2026, 5, 1),
            recorded_at=t2,
        ),
    )
    db.commit()
    asof = date(2026, 6, 30)
    common = dict(security_id=security_id, asof=asof, tenant_id=DEFAULT_TENANT_ID)
    at_t1 = as_of(db, "fact_catalyst", known_at=t1, **common)
    at_t2 = as_of(db, "fact_catalyst", known_at=t2, **common)
    assert len(at_t1) == 1 and at_t1[0]["grade"] == "flip"  # the upgrade isn't known yet at t1
    assert len(at_t2) == 1 and at_t2[0]["grade"] == "core"  # by t2 it's binding


def _make_thesis_row(db) -> uuid.UUID:
    """A bare thesis row so a thesis-scoped fact (fact_theme_conviction) has something to reference."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Small-scale nuclear", "a theme"),
        )
    db.commit()
    return tid


def _theme(thesis_id, *, source_ref, valid_from, recorded_at, grade="flip"):
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "thesis_id": thesis_id,
        "grade": grade,
        "label": "small-scale-nuclear theme conviction",
        "source": "ratified",
        "source_ref": source_ref,
        "ratified_by": "operator",
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def test_fact_theme_conviction_is_append_only(db):
    """The theme-conviction fact is append-only too — an UPDATE raises (a re-ratification is a NEW row)."""
    tid = _make_thesis_row(db)
    t = datetime(2026, 1, 20, tzinfo=timezone.utc)
    fid = append_fact(
        db,
        "fact_theme_conviction",
        _theme(tid, source_ref="th-1", valid_from=date(2026, 1, 15), recorded_at=t),
    )
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE fact_theme_conviction SET grade = 'core' WHERE id = %s", (fid,))
    db.rollback()


def test_theme_conviction_as_of_thesis_is_thesis_scoped_and_bitemporal(db):
    """``as_of_thesis`` reads a THESIS-scoped fact (a theme conviction is basket-level, not co-located on
    a security) and honors transaction time: a re-ratification (a NEW row) is invisible until known.
    """
    tid = _make_thesis_row(db)
    t1 = datetime(2026, 1, 20, tzinfo=timezone.utc)
    t2 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    append_fact(
        db,
        "fact_theme_conviction",
        _theme(tid, source_ref="th-X", valid_from=date(2026, 1, 15), recorded_at=t1),
    )
    # a re-ratification (same source_ref, a fresher event date) learned at t2
    append_fact(
        db,
        "fact_theme_conviction",
        _theme(tid, source_ref="th-X", valid_from=date(2026, 2, 1), recorded_at=t2),
    )
    db.commit()
    common = dict(thesis_id=tid, asof=date(2026, 6, 5), tenant_id=DEFAULT_TENANT_ID)
    at_t1 = as_of_thesis(db, "fact_theme_conviction", known_at=t1, **common)
    at_t2 = as_of_thesis(db, "fact_theme_conviction", known_at=t2, **common)
    assert len(at_t1) == 1 and at_t1[0]["valid_from"] == date(
        2026, 1, 15
    )  # re-ratification not yet known
    assert len(at_t2) == 1 and at_t2[0]["valid_from"] == date(
        2026, 2, 1
    )  # by t2 the newer one is live
