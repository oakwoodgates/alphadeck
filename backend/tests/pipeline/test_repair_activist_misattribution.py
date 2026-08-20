"""Band 03 S5 — the one-time self-filed mis-attribution repair (DB, count-the-table).

Real cited shapes (test-honesty — no fabricated instances):
  - SELF-FILED (deleted): UEC's ``SCHEDULE 13D`` 0001437749-26-024641 stored under UEC ITSELF
    (``filer_cik`` == the subject's cik 0001334933) — a company is never its own 13D subject.
  - CORRECT sub-5% (KEPT): GameStop's ``SCHEDULE 13D`` 0001193125-26-202465 correctly on eBay's tape
    (filer GameStop 0001326380 != subject eBay 0001065088, pct 0.01) — a real filing on the RIGHT
    subject that the FIRE screen drops on the pct rule; the TAPE keeps it (the repair must not touch it).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from db.bitemporal import append_fact, as_of
from db.session import DEFAULT_TENANT_ID
from pipeline.repair_activist_misattribution import run_repair

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _master(db, cik, ticker) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _stake(db, sid, *, accession, form="SCHEDULE 13D", filer_cik, pct, recorded_at=None) -> None:
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": sid,
        "form": form,
        "filer_cik": filer_cik,
        "filer_name": None,
        "pct_owned": pct,
        "accession": accession,
        "filed": date(2026, 7, 28),
        "source_ref": f"https://www.sec.gov/x/{accession}-index.htm",
        "valid_from": date(2026, 7, 28),
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    append_fact(db, "fact_activist_stake", values)


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_activist_stake WHERE tenant_id = %s",
            (DEFAULT_TENANT_ID,),
        )
        return cur.fetchone()["n"]


def _read(db, sid):
    return as_of(
        db,
        "fact_activist_stake",
        security_id=sid,
        asof=date(2026, 12, 31),
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )


def test_dry_run_lists_self_filed_and_writes_nothing(db):
    """The default DRY-RUN counts + lists the self-filed filing but touches nothing (count the table)."""
    uec = _master(db, "0001334933", "UEC")
    ebay = _master(db, "0001065088", "EBAY")
    _stake(db, uec, accession="0001437749-26-024641", filer_cik="0001334933", pct=7.7)  # self-filed
    _stake(db, ebay, accession="0001193125-26-202465", filer_cik="0001326380", pct=0.01)  # correct
    db.commit()
    before = _count(db)
    res = run_repair(db, apply=False)
    assert (res.filings_targeted, res.rows_deleted) == (1, 1)
    assert res.groups[0]["subject_ticker"] == "UEC"
    assert res.groups[0]["accession"] == "0001437749-26-024641"
    assert _count(db) == before  # a dry-run wrote nothing


def test_apply_deletes_self_filed_keeps_correct_subject(db):
    """--apply deletes the self-filed UEC row and KEEPS the correctly-attributed sub-5% eBay row (a
    real filing on the right subject — the fire screen drops it on pct, the tape must not)."""
    uec = _master(db, "0001334933", "UEC")
    ebay = _master(db, "0001065088", "EBAY")
    _stake(db, uec, accession="0001437749-26-024641", filer_cik="0001334933", pct=7.7)
    _stake(db, ebay, accession="0001193125-26-202465", filer_cik="0001326380", pct=0.01)
    db.commit()
    res = run_repair(db, apply=True)
    assert (res.filings_targeted, res.rows_deleted) == (1, 1)
    assert _read(db, uec) == []  # the self-filed row is gone
    (kept,) = _read(db, ebay)  # the correct sub-5% row survives
    assert kept["filer_cik"] == "0001326380" and float(kept["pct_owned"]) == 0.01


def test_apply_deletes_all_versions_of_a_self_filed_filing(db):
    """A self-filed filing may carry an earlier NULL-identity version (an ``identity_skipped`` pass)
    THEN a resolved filer==subject version. The whole logical fact is mis-attributed, so BOTH versions
    are removed — no lingering wrong-subject row for the fire screen to keep (#9)."""
    uec = _master(db, "0001334933", "UEC")
    acc = "0001437749-26-024641"
    _stake(
        db,
        uec,
        accession=acc,
        filer_cik=None,
        pct=None,
        recorded_at=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
    )  # v1: NULL identity
    _stake(
        db,
        uec,
        accession=acc,
        filer_cik="0001334933",
        pct=7.7,
        recorded_at=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
    )  # v2: resolved self-filed
    db.commit()
    assert _count(db) == 2
    res = run_repair(db, apply=True)
    assert (res.filings_targeted, res.rows_deleted) == (1, 2)  # both versions deleted
    assert _count(db) == 0


def test_apply_is_idempotent(db):
    """After a repair no self-filed group remains, so a second --apply deletes zero (count-the-table)."""
    uec = _master(db, "0001334933", "UEC")
    _stake(db, uec, accession="0001437749-26-024641", filer_cik="0001334933", pct=7.7)
    db.commit()
    run_repair(db, apply=True)
    res2 = run_repair(db, apply=True)
    assert (res2.filings_targeted, res2.rows_deleted) == (0, 0)
    assert _count(db) == 0


def test_null_subject_cik_is_never_self_filed_recall_safe(db):
    """NULL-safe (#9): a subject whose master carries no cik can never match 'self-filed' — the row is
    KEPT (an absent cik is never asserted equal to a filer)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "NOCIK", None, date(2026, 1, 1)),
        )
    _stake(db, sid, accession="ACC-X", filer_cik="0001334933", pct=7.7)
    db.commit()
    res = run_repair(db, apply=False)
    assert res.filings_targeted == 0
