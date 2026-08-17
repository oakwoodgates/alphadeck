"""Band 03 S3 — the 8-K corporate-event ingest (parser + tape). The DB half runs against the real
test Postgres (the ``db`` fixture); the headline gates are COUNT-THE-TABLE idempotency (the read
dedups, so a duplicate append hides behind a correct read), the items-resolve RE-VERSION, the
no-lookahead axes, and the 0037 same-filing-two-securities natural-key regression."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form8k import existing_8k_events, filing_index_url, ingest_form8k
from ingest.edgar.submissions import form8k_filings, parse_item_codes

_CIK = "0001234567"
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def _filing(accession, filed="2026-05-01", items=None, form="8-K"):
    return {"accession": accession, "form": form, "filed": filed, "items": items}


def _count(db, *, tenant=DEFAULT_TENANT_ID) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_corporate_event WHERE tenant_id = %s", (tenant,))
        return cur.fetchone()["n"]


def _read(db, security_id, *, asof=date(2026, 12, 31), known_at=_KNOWN):
    return as_of(
        db,
        "fact_corporate_event",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
    )


# --- the shared parser + the submissions walk (pure) ------------------------------------------------


def test_parse_item_codes_splits_and_strips():
    assert parse_item_codes("1.01,9.01") == ["1.01", "9.01"]
    assert parse_item_codes(" 1.01 , ") == ["1.01"]


def test_parse_item_codes_blank_is_none_never_empty_list():
    """None = UNRESOLVED, and an empty string parses to None too — "unresolved" and "no items" can
    never silently conflate into an empty list the detectors would treat as resolved."""
    assert parse_item_codes(None) is None
    assert parse_item_codes("") is None
    assert parse_item_codes("  ,  ") is None


def _subs(rows):
    """A submissions JSON from (form, accession, filed, items_raw) rows — parallel ``recent`` arrays."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "filingDate": [r[2] for r in rows],
                "items": [r[3] for r in rows],
            }
        }
    }


def test_form8k_filings_walks_8k_and_amendments_only():
    subs = _subs(
        [
            ("8-K", "ACC-1", "2026-05-01", "1.01,9.01"),
            ("4", "ACC-F4", "2026-05-02", ""),
            ("8-K/A", "ACC-2", "2026-05-03", "4.02"),
            ("10-K", "ACC-10K", "2026-05-04", ""),
            ("8-K", "ACC-3", "2026-05-05", ""),
        ]
    )
    assert form8k_filings(subs) == [
        {"accession": "ACC-1", "form": "8-K", "filed": "2026-05-01", "items": ["1.01", "9.01"]},
        {"accession": "ACC-2", "form": "8-K/A", "filed": "2026-05-03", "items": ["4.02"]},
        {"accession": "ACC-3", "form": "8-K", "filed": "2026-05-05", "items": None},
    ]


def test_form8k_filings_tolerates_a_missing_items_array():
    """An old/sparse submissions doc with no ``items`` array degrades honestly: every 8-K row reads
    unresolved (items=None), never a raise and never a fabricated empty list."""
    subs = _subs([("8-K", "ACC-1", "2026-05-01", "x")])
    del subs["filings"]["recent"]["items"]
    assert form8k_filings(subs) == [
        {"accession": "ACC-1", "form": "8-K", "filed": "2026-05-01", "items": None}
    ]


def test_filing_index_url_shape():
    assert filing_index_url("0001234567", "0001234567-26-000001") == (
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/"
        "0001234567-26-000001-index.htm"
    )


# --- the tape (DB) ----------------------------------------------------------------------------------


def test_ingest_lands_events_with_provenance_and_valid_from_filed(db, security_id):
    res = ingest_form8k(
        db,
        security_id,
        _CIK,
        [_filing("ACC-1", "2026-05-01", ["1.01", "9.01"]), _filing("ACC-2", "2026-05-03", None)],
    )
    db.commit()
    assert (res.appended, res.reversioned) == (2, 0)
    rows = {r["accession"]: r for r in _read(db, security_id)}
    assert rows["ACC-1"]["items"] == ["1.01", "9.01"]
    assert rows["ACC-1"]["valid_from"] == date(2026, 5, 1)  # = filed (knowability, #1)
    assert rows["ACC-1"]["filed"] == date(2026, 5, 1)
    assert rows["ACC-1"]["source_ref"] == filing_index_url(_CIK, "ACC-1")  # checkable source (#6)
    assert rows["ACC-2"]["items"] is None  # unresolved stored as NULL, shown-not-guessed


def test_rerun_appends_zero_rows_count_the_table(db, security_id):
    """THE idempotency gate: an unchanged tape re-run appends NOTHING — verified by COUNTING the
    table, not by reading (the as-of read dedups, so a duplicate append would hide behind it)."""
    filings = [_filing("ACC-1", "2026-05-01", ["1.01"]), _filing("ACC-2", "2026-05-03", None)]
    ingest_form8k(db, security_id, _CIK, filings)
    db.commit()
    before = _count(db)
    res = ingest_form8k(db, security_id, _CIK, filings)  # identical second run
    db.commit()
    assert _count(db) == before  # the TABLE did not grow
    assert (res.appended, res.reversioned) == (0, 0)


def test_resolved_items_append_one_new_version(db, security_id):
    """The resolve RE-VERSION: a stored items=NULL filing whose items later resolve appends exactly
    ONE new version (never an UPDATE), the as-of read returns the resolved codes, and a further
    unchanged re-run appends zero."""
    ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", None)])
    db.commit()
    base = _count(db)

    res = ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", ["4.02"])])
    db.commit()
    assert (res.appended, res.reversioned) == (0, 1)
    assert _count(db) == base + 1  # one new VERSION row
    rows = _read(db, security_id)
    assert len(rows) == 1 and rows[0]["items"] == [
        "4.02"
    ]  # the read dedups to the resolved version

    res2 = ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", ["4.02"])])
    db.commit()
    assert (res2.appended, res2.reversioned) == (0, 0) and _count(db) == base + 1


def test_existing_events_reads_the_latest_version(db, security_id):
    # commit between the two runs (as production does — per-leg commits): within ONE transaction
    # Postgres' now() is constant, and the natural-key constraint rightly refuses two same-instant
    # versions of one (tenant, security, accession) — the same-scope duplicate face tested below.
    ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", None)])
    db.commit()
    ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", ["1.01"])])
    db.commit()
    latest = existing_8k_events(db, security_id)
    assert latest["ACC-1"]["items"] == ["1.01"]  # the compare surface is the LATEST version


def test_no_lookahead_on_both_axes(db, security_id):
    """valid_from = filed: a filing is invisible to an as-of BEFORE its filed date (even though the
    event happened); recorded_at = now (never backdated): the fact is invisible to a known_at pinned
    before the ingest — the replay guarantee on both bitemporal axes."""
    ingest_form8k(db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", ["1.01"])])
    db.commit()
    # valid-time axis
    assert _read(db, security_id, asof=date(2026, 4, 30)) == []
    assert len(_read(db, security_id, asof=date(2026, 5, 1))) == 1  # visible AT filed
    # transaction-time axis
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert _read(db, security_id, known_at=past) == []


def test_same_filing_two_securities_same_instant_does_not_collide(db, security_id):
    """THE 0037 REGRESSION, as a test this time: one issuer held as TWO master rows (share classes /
    dual listings) stores the SAME filing once per security scope — two same-instant versions under
    two securities are two DIFFERENT logical facts and MUST both land. The 0038 constraint carries
    security_id from birth; an under-keyed constraint (the 0002 mistake) would abort here."""
    sid2 = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid2, DEFAULT_TENANT_ID, "DEVCO.B", _CIK, date(2026, 1, 1)),
        )
    instant = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)  # one batch's shared now()
    ingest_form8k(
        db, security_id, _CIK, [_filing("ACC-SHARED", "2026-05-01", ["1.01"])], recorded_at=instant
    )
    ingest_form8k(
        db, sid2, _CIK, [_filing("ACC-SHARED", "2026-05-01", ["1.01"])], recorded_at=instant
    )
    db.commit()
    assert len(_read(db, security_id)) == 1
    one = as_of(
        db,
        "fact_corporate_event",
        security_id=sid2,
        asof=date(2026, 12, 31),
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(one) == 1  # each scope owns its own logical fact


def test_same_scope_same_instant_duplicate_is_refused(db, security_id):
    """The constraint's other face: the SAME (tenant, security, accession, recorded_at) tuple IS a
    duplicate and the DB refuses it — proving the natural-key constraint exists at exactly the
    as-of read's grain (not wider, not narrower)."""
    instant = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    ingest_form8k(
        db, security_id, _CIK, [_filing("ACC-1", "2026-05-01", ["1.01"])], recorded_at=instant
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        # bypass the append-if-changed compare (which would skip) to hit the constraint itself:
        # a re-version of the same logical fact at the same instant with a CHANGED surface
        ingest_form8k(
            db,
            security_id,
            _CIK,
            [_filing("ACC-1", "2026-05-01", ["1.01", "9.01"])],
            recorded_at=instant,
        )
    db.rollback()
