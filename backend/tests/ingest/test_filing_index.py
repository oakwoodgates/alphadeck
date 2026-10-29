"""The filing index (migration 0070): the pure parser over the submissions ``recent`` arrays
(``submissions.index_filings``) and the insert-only store (``ingest.edgar.filing_index``). DB-backed tests COUNT
THE TABLE (the as-of read dedups, so a duplicate append would hide behind a correct read)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.filing_index import (
    SKIP_BAD_DATE,
    SKIP_LISTED_TWICE,
    SKIP_NO_ACCESSION,
    SKIP_NO_FORM,
    ingest_filing_index,
)
from ingest.edgar.form8k import filing_index_url
from ingest.edgar.submissions import index_filings

CIK = "0001234567"


def _subs(rows, *, accepts=None):
    """A submissions JSON whose ``recent`` arrays hold ``rows`` = (form, accession, doc, filed)."""
    recent = {
        "form": [r[0] for r in rows],
        "accessionNumber": [r[1] for r in rows],
        "primaryDocument": [r[2] for r in rows],
        "filingDate": [r[3] for r in rows],
    }
    if accepts is not None:
        recent["acceptanceDateTime"] = list(accepts)
    return {"filings": {"recent": recent}}


def _count(db, sid) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_filing_index WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


# --- the parser (pure) -------------------------------------------------------------------------------------


def test_index_filings_returns_EVERY_form_in_recent_in_edgar_order():
    subs = _subs(
        [
            ("424B5", "0001-26-000003", "d424b5.htm", "2026-06-03"),
            ("4", "0001-26-000002", "xslF345X05/f4.xml", "2026-06-02"),
            ("S-1", "0001-26-000001", "s1.htm", "2026-06-01"),
        ],
        accepts=["2026-06-03T14:00:00.000Z", "", "2026-06-01T20:00:00.000Z"],
    )
    rows = index_filings(subs)
    assert [r["form"] for r in rows] == ["424B5", "4", "S-1"]
    assert rows[0] == {
        "form": "424B5",
        "accession": "0001-26-000003",
        "primary_doc": "d424b5.htm",
        "filed": "2026-06-03",
        "accepted_raw": "2026-06-03T14:00:00.000Z",
    }
    assert rows[1]["accepted_raw"] == ""


def test_index_filings_is_defensive_on_short_arrays_and_reads_recent_ONLY():
    subs = {
        "filings": {
            "recent": {
                "form": ["S-1", "8-K"],
                "accessionNumber": ["A-1"],  # one short: the second row has no accession
                "primaryDocument": [],
                "filingDate": ["2026-06-01"],
            },
            # the older pages: never read (they would cost extra SEC requests)
            "files": [{"name": "CIK0001234567-submissions-001.json"}],
        }
    }
    rows = index_filings(subs)
    assert rows == [
        {
            "form": "S-1",
            "accession": "A-1",
            "primary_doc": "",
            "filed": "2026-06-01",
            "accepted_raw": "",
        },
        {"form": "8-K", "accession": "", "primary_doc": "", "filed": "", "accepted_raw": ""},
    ]
    assert index_filings({}) == [] and index_filings({"filings": {}}) == []


# --- the store (DB) ----------------------------------------------------------------------------------------


def test_the_store_appends_every_new_filing_and_a_rerun_appends_ZERO_count_the_table(
    db, security_id
):
    rows = index_filings(
        _subs(
            [
                ("424B5", "0001234567-26-000003", "d424b5.htm", "2026-06-03"),
                ("DEF 14A", "0001234567-26-000002", "", "2026-06-02"),
                ("S-1", "0001234567-26-000001", "s1.htm", "2026-06-01"),
            ]
        )
    )
    first = ingest_filing_index(db, security_id, CIK, rows)
    db.commit()
    assert first.appended == 3 and first.skipped == () and _count(db, security_id) == 3

    before = _count(db, security_id)
    again = ingest_filing_index(db, security_id, CIK, rows)
    db.commit()
    assert again.appended == 0 and _count(db, security_id) == before  # the TABLE did not grow

    # one NEW filing later: exactly one row appended
    more = index_filings(
        _subs(
            [("10-Q", "0001234567-26-000004", "q.htm", "2026-06-10")]
            + [
                ("424B5", "0001234567-26-000003", "d424b5.htm", "2026-06-03"),
                ("DEF 14A", "0001234567-26-000002", "", "2026-06-02"),
                ("S-1", "0001234567-26-000001", "s1.htm", "2026-06-01"),
            ]
        )
    )
    third = ingest_filing_index(db, security_id, CIK, more)
    db.commit()
    assert third.appended == 1 and _count(db, security_id) == before + 1

    with db.cursor() as cur:
        cur.execute(
            "SELECT form, accession, filed, primary_doc, source_ref, valid_from, recorded_at "
            "FROM fact_filing_index WHERE security_id = %s AND accession = %s",
            (security_id, "0001234567-26-000002"),
        )
        row = cur.fetchone()
    assert row["form"] == "DEF 14A" and row["filed"] == date(2026, 6, 2)
    assert row["primary_doc"] is None  # EDGAR left it blank: NULL, never ""
    assert row["valid_from"] == row["filed"]  # the public date
    assert row["source_ref"] == filing_index_url(
        CIK, "0001234567-26-000002"
    )  # the 8-K tape's shape
    # recorded_at is the DB's now(), never backdated to the filing
    assert row["recorded_at"] > datetime(2026, 6, 3, tzinfo=timezone.utc)


def test_a_stored_accession_is_NEVER_re_versioned_first_sighting_wins(db, security_id):
    """Insert-only: a later index listing the same accession with a different form / document appends
    nothing (SEC's listed fields drift; re-versioning would churn the table nightly)."""
    ingest_filing_index(
        db, security_id, CIK, index_filings(_subs([("S-1", "A-1", "s1.htm", "2026-06-01")]))
    )
    db.commit()
    changed = index_filings(_subs([("S-1/A", "A-1", "s1a.htm", "2026-06-01")]))
    out = ingest_filing_index(db, security_id, CIK, changed)
    db.commit()
    assert out.appended == 0 and _count(db, security_id) == 1
    with db.cursor() as cur:
        cur.execute("SELECT form FROM fact_filing_index WHERE security_id = %s", (security_id,))
        assert cur.fetchone()["form"] == "S-1"


def test_a_malformed_row_is_SKIPPED_AND_COUNTED_with_its_reason_and_the_rest_stored(
    db, security_id
):
    rows = index_filings(
        _subs(
            [
                ("S-1", "", "s1.htm", "2026-06-01"),  # no accession
                ("", "A-2", "x.htm", "2026-06-02"),  # no form
                ("8-K", "A-3", "k.htm", "06/03/2026"),  # unreadable date
                ("424B5", "A-4", "p.htm", "2026-06-04"),  # fine
                ("424B5", "A-4", "p.htm", "2026-06-04"),  # the same accession listed twice
                ("10-Q", "A-5", "q.htm", ""),  # no date at all
            ]
        )
    )
    out = ingest_filing_index(db, security_id, CIK, rows)
    db.commit()
    assert out.appended == 1 and _count(db, security_id) == 1
    assert out.skipped == (
        ("row 0", SKIP_NO_ACCESSION),
        ("A-2", SKIP_NO_FORM),
        ("A-3", f"{SKIP_BAD_DATE} '06/03/2026'"),
        ("A-4", SKIP_LISTED_TWICE),
        ("A-5", f"{SKIP_BAD_DATE} ''"),
    )


def test_an_UNTRUSTED_listed_acceptance_is_stored_NULL_and_counted(db, security_id):
    """The listed SEC time is stored only when its ET date is the filing date within EDGAR's hours
    (``acceptance.trusted_listed``); otherwise NULL, and the set-aside is counted. A blank listed time is
    simply NULL (nothing was set aside)."""
    rows = index_filings(
        _subs(
            [
                ("S-1", "A-1", "s1.htm", "2026-06-01"),  # 14:00 ET on the filing date: trusted
                ("S-1", "A-2", "s1.htm", "2026-06-01"),  # 00:00:20 ET the NEXT day: set aside
                ("S-1", "A-3", "s1.htm", "2026-06-01"),  # 04:00 ET (EDGAR closed): set aside
                ("S-1", "A-4", "s1.htm", "2026-06-01"),  # blank: NULL, not counted
            ],
            accepts=[
                "2026-06-01T18:00:00.000Z",
                "2026-06-02T04:00:20.000Z",
                "2026-06-01T08:00:00.000Z",
                "",
            ],
        )
    )
    out = ingest_filing_index(db, security_id, CIK, rows)
    db.commit()
    assert out.appended == 4 and out.accepted_set_aside == 2
    with db.cursor() as cur:
        cur.execute(
            "SELECT accession, accepted FROM fact_filing_index WHERE security_id = %s ORDER BY accession",
            (security_id,),
        )
        got = {r["accession"]: r["accepted"] for r in cur.fetchall()}
    assert got == {
        "A-1": datetime(2026, 6, 1, 18, tzinfo=timezone.utc),
        "A-2": None,
        "A-3": None,
        "A-4": None,
    }


def test_the_table_is_append_only(db, security_id):
    import psycopg
    import pytest

    ingest_filing_index(
        db, security_id, CIK, index_filings(_subs([("S-1", "A-1", "s1.htm", "2026-06-01")]))
    )
    db.commit()
    with pytest.raises(psycopg.Error):
        with db.cursor() as cur:
            cur.execute(
                "UPDATE fact_filing_index SET form = 'X' WHERE security_id = %s", (security_id,)
            )
    db.rollback()


def test_the_store_writes_under_the_given_tenant(db, security_id):
    out = ingest_filing_index(
        db,
        security_id,
        CIK,
        index_filings(_subs([("S-1", "A-1", "s1.htm", "2026-06-01")])),
        tenant_id=DEFAULT_TENANT_ID,
    )
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT tenant_id FROM fact_filing_index WHERE security_id = %s", (security_id,)
        )
        assert cur.fetchone()["tenant_id"] == DEFAULT_TENANT_ID and out.appended == 1
