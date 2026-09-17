"""``pipeline.repair_impossible_txn_dates`` — delete ``fact_insider_txn`` rows whose transaction date
cannot be true, and NOTHING else.

The rows under test are the real measured shapes (a leading-zero year, a year after the filing) and the real
measured FALSE-POSITIVE guard (a 1990s transaction legitimately reported on a 2006 filing — 42 such rows
exist in the corpus, and a naive "predates EDGAR's 2003 mandate" floor would have deleted every one).

Every count is asserted against the raw TABLE COUNT, never against a read: ``fact_insider_txn``'s as-of read
dedups on the natural key, so a repair that deleted the wrong version — or none at all — would still produce
a plausible-looking read while the table said otherwise.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from pipeline import repair_impossible_txn_dates as repair

_UTC = timezone.utc


def _txn(
    db,
    security_id,
    *,
    accession: str,
    valid_from: date,
    txn_seq: int = 0,
    insider: str = "Peffer Julie",
    txn_code: str = "A",
    recorded_at: datetime | None = None,
    supersedes: uuid.UUID | None = None,
) -> uuid.UUID:
    """Append one ``fact_insider_txn`` row DIRECTLY (bypassing ``ingest_form4``, whose P1 bound now refuses
    to write these at all) — the repair's job is the history already on disk."""
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "insider_name": insider,
        "txn_code": txn_code,
        "shares": 100.0,
        "price": 2.0,
        "usd": 200.0,
        "accession": accession,
        "valid_from": valid_from,
        "txn_seq": txn_seq,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    if supersedes is not None:
        values["supersedes"] = supersedes
    return append_fact(db, "fact_insider_txn", values)


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        return cur.fetchone()["n"]


def _ids(db) -> set[uuid.UUID]:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM fact_insider_txn")
        return {r["id"] for r in cur.fetchall()}


@pytest.fixture
def seeded(db, security_id):
    """Four impossible rows (both measured shapes) + three legitimate ones that must survive."""
    bad = {
        # leading-zero year — the real BBAI filing's value, verbatim
        "bbai": _txn(db, security_id, accession="0001628280-24-010030", valid_from=date(23, 3, 23)),
        # a year after the filing — the real LSCC filing
        "lscc": _txn(
            db,
            security_id,
            accession="0001437749-24-004874",
            valid_from=date(2027, 2, 17),
            txn_seq=1,
            txn_code="F",
        ),
        # only ONE year ahead — the shape a "future vs today" scan misses entirely
        "crdo": _txn(
            db,
            security_id,
            accession="0001628280-23-035012",
            valid_from=date(2024, 10, 24),
            txn_seq=1,
            txn_code="S",
        ),
    }
    good = {
        # a 1993 transaction legitimately reported on a 2006 Form 4 — the false-positive guard
        "old": _txn(
            db, security_id, accession="0001181431-06-033946", valid_from=date(1993, 5, 11)
        ),
        # a correctly-dated row from the SAME BBAI filing as the bad one
        "same_filing": _txn(
            db,
            security_id,
            accession="0001628280-24-010030",
            valid_from=date(2024, 3, 5),
            txn_seq=2,
        ),
        # a seed/test-style accession: no filing year, so the upper bound abstains and the row is kept
        "no_year": _txn(db, security_id, accession="acc-seed", valid_from=date(2026, 6, 1)),
    }
    db.commit()
    return bad, good


def test_finds_every_impossible_row_and_nothing_else(db, seeded):
    bad, good = seeded
    found = {r.row_id for r in repair.find_impossible_rows(db)}
    assert found == set(bad.values())
    assert found.isdisjoint(good.values())


def test_reasons_name_the_anchor_that_rejected_the_row(db, seeded):
    by_id = {r.row_id: r.reason for r in repair.find_impossible_rows(db)}
    bad, _ = seeded
    assert "1934" in by_id[bad["bbai"]]  # the Section 16 epoch anchor
    assert "filing year 2024" in by_id[bad["lscc"]]  # the accession's own year
    assert "filing year 2023" in by_id[bad["crdo"]]


def test_deletes_every_version_of_an_impossible_key_including_its_supersedes_chain(db, security_id):
    """An impossible date is IN the natural key, so every VERSION of that key carries it and all of them
    must go — a surviving older version would still be returned by an as-of read pinned before the newest.
    ``supersedes`` self-references ``fact_insider_txn(id)`` with no ON DELETE, so deleting a chain out of
    order would raise; deleting the whole key at once is what keeps the FK satisfied."""
    v1 = _txn(
        db,
        security_id,
        accession="0001628280-24-010030",
        valid_from=date(23, 3, 23),
        recorded_at=datetime(2026, 3, 1, tzinfo=_UTC),
    )
    v2 = _txn(
        db,
        security_id,
        accession="0001628280-24-010030",
        valid_from=date(23, 3, 23),
        recorded_at=datetime(2026, 6, 1, tzinfo=_UTC),
        supersedes=v1,
    )
    db.commit()
    rows = repair.find_impossible_rows(db)
    assert {r.row_id for r in rows} == {v1, v2}  # BOTH versions, not just the latest

    assert repair.delete_rows(db, rows) == 2
    db.commit()
    assert _count(db) == 0


def test_dry_run_path_writes_nothing_count_the_table(db, seeded):
    """The read half on its own must not shrink the table — the dry-run prints from exactly this set."""
    before = _count(db)
    ids_before = _ids(db)

    repair.find_impossible_rows(db)
    db.rollback()

    assert _count(db) == before and _ids(db) == ids_before


def test_apply_deletes_exactly_the_impossible_rows(db, seeded):
    bad, good = seeded
    before = _count(db)

    rows = repair.find_impossible_rows(db)
    deleted = repair.delete_rows(db, rows)
    db.commit()

    assert deleted == len(bad)
    assert _count(db) == before - len(bad)  # the TABLE shrank by exactly the bad rows
    assert _ids(db) == set(good.values())  # every legitimate row survived


def test_a_second_apply_finds_nothing_left(db, seeded):
    """Idempotent: once repaired, a re-run has no candidates and the table does not change again."""
    repair.delete_rows(db, repair.find_impossible_rows(db))
    db.commit()
    after_first = _count(db)

    assert repair.find_impossible_rows(db) == []
    assert repair.delete_rows(db, []) == 0
    db.commit()
    assert _count(db) == after_first


def test_class_of_buckets_by_the_date_not_the_message_wording(db, seeded):
    bad, _ = seeded
    by_id = {r.row_id: repair._class_of(r) for r in repair.find_impossible_rows(db)}
    assert by_id[bad["bbai"]] == "below the Section 16 epoch"
    assert by_id[bad["lscc"]] == "after the accession's filing year"
