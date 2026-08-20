"""Band 03 S5 — the SC 13D/G activist-stake ingest (parsers + tape). The DB half runs against the
real test Postgres (the ``db`` fixture); the headline gates are COUNT-THE-TABLE idempotency (the
read dedups, so a duplicate append hides behind a correct read), the identity-resolve RE-VERSION,
the trap-#4 no-lookahead pin (valid_from = filed, NEVER the XML's dateOfEvent), the 8-string
both-era form match (the rename trap), and the 0037 same-filing-two-securities regression.

The parser fixtures are REAL filings, cited: COMPASS Pathways (CMPS, CIK 1816590) × atai — the
original SC 13D accession 0001193125-21-171001 (filed 2021-05-24, FILED BY ATAI Life Sciences B.V.,
CIK 0001840904; SGML header verbatim) and the structured-era SCHEDULE 13D/A accession
0001140361-26-005810 (filed 2026-02-17, AtaiBeckley Inc., CIK 0002081043, 4.96% — the raw
primary_doc.xml)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form8k import filing_index_url
from ingest.edgar.schedule13 import (
    ingest_schedule13,
    is_13d_family,
    parse_sgml_filed_by,
    parse_sgml_subject_cik,
    parse_structured_cover,
)
from ingest.edgar.submissions import (
    SCHEDULE13_D_FORMS,
    SCHEDULE13_FORMS,
    SCHEDULE13_G_FORMS,
    schedule13_filings,
)

# The feed-owner CIK the DB tests below ingest FOR. It is CMPS's real CIK on purpose: the identity
# fixtures (_REAL_XML / _REAL_HDR) are real 13Ds ABOUT CMPS, so owner == subject — the normal INBOUND
# case, where the subject-attribution skip must KEEP every row. The OUTBOUND (skip) case is exercised
# separately with the owner set to the FILER's CIK.
_CIK = "0001816590"  # COMPASS Pathways plc — the subject of the CMPS identity fixtures
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "schedule13"
_REAL_XML = (_FIXTURES / "cmps_schedule13da_primary_doc.xml").read_text(encoding="utf-8")
_REAL_HDR = (_FIXTURES / "cmps_sc13d_2021_header.txt").read_text(encoding="utf-8")

# Two REAL, cited outbound-filing instances for the subject-attribution skip (fetched live from EDGAR,
# committed as fixtures — the producing command verified each cover's <issuerCIK> against the SGML
# SUBJECT COMPANY block). Each 13D appears in the FILER's own submissions feed because the filer FILED
# it — about a DIFFERENT subject:
#   UEC's SCHEDULE 13D 0001437749-26-024641 — FILED BY Uranium Energy Corp 0001334933, SUBJECT Uranium
#     Royalty Corp 0002143673 (7.7%).
#   GameStop's SCHEDULE 13D 0001193125-26-202465 — FILED BY GameStop 0001326380, SUBJECT eBay 0001065088
#     (0.01% — a real anomalous sub-5% cover; the fire-side screen drops it, but the ingest never even
#     stores it under the wrong subject now).
_UEC_ACC, _UEC_CIK, _UEC_SUBJECT = "0001437749-26-024641", "0001334933", "0002143673"
_GME_ACC, _GME_CIK, _GME_SUBJECT = "0001193125-26-202465", "0001326380", "0001065088"
_UEC_XML = (_FIXTURES / "uec_schedule13d_primary_doc.xml").read_text(encoding="utf-8")
_GME_XML = (_FIXTURES / "gme_schedule13d_primary_doc.xml").read_text(encoding="utf-8")


class _Client:
    """A dict-backed EdgarClient stand-in for the identity fetches (cache_key -> text). A key not
    in the dict raises — the unfetchable-document face; ``calls`` records every fetch so tests can
    assert the no-refetch economy."""

    def __init__(self, by_key=None):
        self.by_key = dict(by_key or {})
        self.calls: list[str] = []

    def get_text(self, url: str, cache_key: str) -> str:
        self.calls.append(cache_key)
        if cache_key not in self.by_key:
            raise RuntimeError(f"unfetchable: {cache_key}")
        return self.by_key[cache_key]


def _filing(accession, form="SC 13G", filed="2026-05-01", primary_doc=""):
    return {"accession": accession, "form": form, "filed": filed, "primary_doc": primary_doc}


def _count(db, *, tenant=DEFAULT_TENANT_ID) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) n FROM fact_activist_stake WHERE tenant_id = %s", (tenant,))
        return cur.fetchone()["n"]


def _read(db, security_id, *, asof=date(2026, 12, 31), known_at=_KNOWN):
    return as_of(
        db,
        "fact_activist_stake",
        security_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
    )


# --- the form sets + the submissions walk (pure) ----------------------------------------------------


def test_schedule13_form_sets_cover_both_naming_eras():
    """THE RENAME TRAP (#9): EDGAR renamed the form type at the ~2024-12 structured-XML cutover —
    an SC-only match silently drops every 2025+ filing. All EIGHT strings, split D vs G."""
    assert SCHEDULE13_D_FORMS == {"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}
    assert SCHEDULE13_G_FORMS == {"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
    assert SCHEDULE13_FORMS == SCHEDULE13_D_FORMS | SCHEDULE13_G_FORMS
    assert is_13d_family("SCHEDULE 13D/A") and is_13d_family("SC 13D")
    assert not is_13d_family("SC 13G") and not is_13d_family("SCHEDULE 13G/A")


def _subs(rows):
    """A submissions JSON from (form, accession, filed, primary_doc) rows — parallel ``recent``
    arrays, the real document's shape."""
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "filingDate": [r[2] for r in rows],
                "primaryDocument": [r[3] for r in rows],
            }
        }
    }


def test_schedule13_filings_walks_both_eras_and_only_13dg():
    subs = _subs(
        [
            ("SCHEDULE 13G/A", "ACC-NEW-G", "2026-05-15", "xslSCHEDULE_13G_X02/primary_doc.xml"),
            ("SCHEDULE 13D/A", "ACC-NEW-D", "2026-02-17", "xslSCHEDULE_13D_X01/primary_doc.xml"),
            ("8-K", "ACC-8K", "2026-02-01", "form8k.htm"),
            ("4", "ACC-F4", "2026-01-15", "xslF345X05/form4.xml"),
            ("SC 13G", "ACC-OLD-G", "2024-11-08", "sc13g.htm"),
            ("SC 13D", "ACC-OLD-D", "2021-05-24", "d46702dsc13d.htm"),
            ("10-K", "ACC-10K", "2021-03-01", "form10k.htm"),
        ]
    )
    assert schedule13_filings(subs) == [
        {
            "accession": "ACC-NEW-G",
            "form": "SCHEDULE 13G/A",
            "filed": "2026-05-15",
            "primary_doc": "xslSCHEDULE_13G_X02/primary_doc.xml",
        },
        {
            "accession": "ACC-NEW-D",
            "form": "SCHEDULE 13D/A",
            "filed": "2026-02-17",
            "primary_doc": "xslSCHEDULE_13D_X01/primary_doc.xml",
        },
        {
            "accession": "ACC-OLD-G",
            "form": "SC 13G",
            "filed": "2024-11-08",
            "primary_doc": "sc13g.htm",
        },
        {
            "accession": "ACC-OLD-D",
            "form": "SC 13D",
            "filed": "2021-05-24",
            "primary_doc": "d46702dsc13d.htm",
        },
    ]


# --- the identity parsers, on the REAL fixtures (pure) ----------------------------------------------


def test_parse_structured_cover_real_cmps_13da_xml():
    """The real AtaiBeckley SCHEDULE 13D/A primary_doc.xml (accession 0001140361-26-005810):
    reporting person name + CIK + percentOfClass + the TRUE subject (issuerCIK = CMPS 0001816590),
    deterministically."""
    got = parse_structured_cover(_REAL_XML)
    assert got == {
        "filer_cik": "0002081043",
        "filer_name": "AtaiBeckley Inc.",
        "pct_owned": 4.96,
        "subject_cik": "0001816590",
    }


def test_parse_structured_cover_carries_the_true_subject_cik():
    """THE FREE HALF of the ingest subject-attribution fix: the structured cover's ``<issuerCIK>`` is
    the schedule's TRUE subject — the same document already fetched for filer identity, no extra pull.
    Verified on the two real cited outbound 13Ds: UEC's cover names Uranium Royalty (0002143673) as
    subject while UEC (0001334933) is the filer; GameStop's names eBay (0001065088) while GameStop
    (0001326380) is the filer."""
    assert parse_structured_cover(_UEC_XML) == {
        "filer_cik": "0001334933",
        "filer_name": "URANIUM ENERGY CORP",
        "pct_owned": 7.7,
        "subject_cik": "0002143673",
    }
    assert parse_structured_cover(_GME_XML) == {
        "filer_cik": "0001326380",
        "filer_name": "GameStop Corp.",
        "pct_owned": 0.01,
        "subject_cik": "0001065088",
    }


def test_parse_sgml_subject_cik_real_cmps_header():
    """The classic-era subject: the SGML ``SUBJECT COMPANY`` block's CIK (CMPS 0001816590) — and NEVER
    the filer's (ATAI 0001840904). None when no SUBJECT COMPANY block is present (the caller then keeps
    the row, recall-safe — unresolved ≠ mis-attributed)."""
    assert parse_sgml_subject_cik(_REAL_HDR) == "0001816590"
    assert parse_sgml_subject_cik("FILED BY:\n\tCENTRAL INDEX KEY:\t\t0001840904\n") is None


def test_parse_sgml_filed_by_real_cmps_13d_header():
    """The real 2021 atai SC 13D SGML header (accession 0001193125-21-171001): the FILED BY block's
    conformed name + CIK — and NEVER the subject company's (CMPS, 0001816590)."""
    got = parse_sgml_filed_by(_REAL_HDR)
    assert got == {"filer_cik": "0001840904", "filer_name": "ATAI Life Sciences B.V."}


def test_parse_sgml_filed_by_truncates_at_a_following_subject_block():
    """Block order varies filing to filing: with FILED BY first and SUBJECT COMPANY after, the
    parser must stop at the subject boundary rather than read the subject's keys."""
    hdr = (
        "FILED BY:\n"
        "\tCOMPANY DATA:\n"
        "\t\tCOMPANY CONFORMED NAME:\t\tActivist Fund LP\n"
        "\t\tCENTRAL INDEX KEY:\t\t0001111111\n"
        "SUBJECT COMPANY:\n"
        "\tCOMPANY DATA:\n"
        "\t\tCOMPANY CONFORMED NAME:\t\tTarget Corp\n"
        "\t\tCENTRAL INDEX KEY:\t\t0002222222\n"
    )
    assert parse_sgml_filed_by(hdr) == {"filer_cik": "0001111111", "filer_name": "Activist Fund LP"}
    # no FILED BY block at all -> both None (the row still lands, with NULL identity)
    assert parse_sgml_filed_by("SUBJECT COMPANY:\n...") == {"filer_cik": None, "filer_name": None}


# --- the tape (DB) ----------------------------------------------------------------------------------


def test_ingest_lands_stakes_and_valid_from_is_filed_never_the_xml_event_date(db, security_id):
    """THE TRAP-#4 PIN (gold-doc §8): the real structured XML carries dateOfEvent 01/21/2026 (the
    stake event INSIDE the filing) — the stored valid_from must be the FILED date 2026-02-17 (the
    knowable moment), never the in-document event date. Identity + pct ride as evidence."""
    filings = [
        _filing(
            "0001140361-26-005810",
            form="SCHEDULE 13D/A",
            filed="2026-02-17",
            primary_doc="xslSCHEDULE_13D_X02/primary_doc.xml",
        ),
        _filing("ACC-OLD-G", form="SC 13G", filed="2024-11-08", primary_doc="sc13g.htm"),
    ]
    client = _Client({"forms/0001140361-26-005810/primary_doc.xml": _REAL_XML})
    res = ingest_schedule13(db, security_id, _CIK, filings, client)
    db.commit()
    assert (res.appended, res.reversioned, res.identity_skipped) == (2, 0, 0)
    rows = {r["accession"]: r for r in _read(db, security_id)}
    da = rows["0001140361-26-005810"]
    assert da["valid_from"] == date(2026, 2, 17)  # = filed; the XML's 01/21/2026 is NEVER time
    assert da["filed"] == date(2026, 2, 17)
    assert da["filer_cik"] == "0002081043" and da["filer_name"] == "AtaiBeckley Inc."
    assert float(da["pct_owned"]) == 4.96
    assert da["source_ref"] == filing_index_url(_CIK, "0001140361-26-005810")  # checkable (#6)
    # the classic-era 13G: stored WITHOUT an identity fetch (out of the bounded depth, BY DESIGN —
    # it can never fire; not counted as a failure), NULL identity
    g = rows["ACC-OLD-G"]
    assert g["filer_cik"] is None and g["filer_name"] is None and g["pct_owned"] is None
    assert client.calls == ["forms/0001140361-26-005810/primary_doc.xml"]  # ONE fetch, not two


def test_classic_13d_identity_from_the_sgml_header(db, security_id):
    """A classic-era 13D-family filing takes the SGML-header identity path: the {accession}.txt
    FILED BY block (the real 2021 atai header)."""
    acc = "0001193125-21-171001"
    filings = [_filing(acc, form="SC 13D", filed="2021-05-24", primary_doc="d46702dsc13d.htm")]
    client = _Client({f"forms/{acc}/{acc}.txt": _REAL_HDR})
    res = ingest_schedule13(db, security_id, _CIK, filings, client)
    db.commit()
    assert (res.appended, res.reversioned, res.identity_skipped) == (1, 0, 0)
    (row,) = _read(db, security_id)
    assert row["filer_cik"] == "0001840904" and row["filer_name"] == "ATAI Life Sciences B.V."
    assert row["pct_owned"] is None  # no cover parse in the classic era — evidence stays absent


def test_rerun_appends_zero_rows_count_the_table(db, security_id):
    """THE idempotency gate: an unchanged tape re-run appends NOTHING — verified by COUNTING the
    table, not by reading (the as-of read dedups, so a duplicate append would hide behind it)."""
    acc = "0001193125-21-171001"
    filings = [
        _filing(acc, form="SC 13D", filed="2021-05-24", primary_doc="d46702dsc13d.htm"),
        _filing("ACC-G", form="SCHEDULE 13G", filed="2026-02-17", primary_doc="x/primary_doc.xml"),
    ]
    client = _Client(
        {f"forms/{acc}/{acc}.txt": _REAL_HDR, "forms/ACC-G/primary_doc.xml": _REAL_XML}
    )
    ingest_schedule13(db, security_id, _CIK, filings, client)
    db.commit()
    before = _count(db)
    res = ingest_schedule13(db, security_id, _CIK, filings, client)  # identical second run
    db.commit()
    assert _count(db) == before  # the TABLE did not grow
    assert (res.appended, res.reversioned, res.identity_skipped) == (0, 0, 0)


def test_identity_failure_keeps_the_row_and_counts_loud(db, security_id):
    """#9: an unfetchable identity document NEVER drops the filing — the row lands with NULL filer
    and the failure is counted loud (identity_skipped), not silent."""
    filings = [
        _filing("ACC-D", form="SCHEDULE 13D", filed="2026-05-01", primary_doc="x/primary_doc.xml")
    ]
    res = ingest_schedule13(db, security_id, _CIK, filings, _Client())  # every fetch raises
    db.commit()
    assert (res.appended, res.reversioned, res.identity_skipped) == (1, 0, 1)
    (row,) = _read(db, security_id)
    assert row["form"] == "SCHEDULE 13D" and row["filer_cik"] is None and row["filer_name"] is None


def test_identity_resolving_appends_one_new_version(db, security_id):
    """The resolve RE-VERSION: a stored NULL-identity filing whose identity later resolves appends
    exactly ONE new version (never an UPDATE), the as-of read returns the resolved identity, and a
    further unchanged re-run appends zero."""
    filings = [
        _filing("ACC-D", form="SCHEDULE 13D", filed="2026-05-01", primary_doc="x/primary_doc.xml")
    ]
    ingest_schedule13(db, security_id, _CIK, filings, _Client())  # identity fetch fails -> NULL
    db.commit()
    base = _count(db)

    ok = _Client({"forms/ACC-D/primary_doc.xml": _REAL_XML})
    res = ingest_schedule13(db, security_id, _CIK, filings, ok)
    db.commit()
    assert (res.appended, res.reversioned, res.identity_skipped) == (0, 1, 0)
    assert _count(db) == base + 1  # one new VERSION row
    (row,) = _read(db, security_id)
    assert row["filer_name"] == "AtaiBeckley Inc."  # the read dedups to the resolved version

    res2 = ingest_schedule13(db, security_id, _CIK, filings, ok)
    db.commit()
    assert (res2.appended, res2.reversioned) == (0, 0) and _count(db) == base + 1


def test_stored_identity_is_never_refetched(db, security_id):
    """The fetch economy: once a filing's identity is on the tape, a re-run must NOT re-fetch its
    document (the raising client proves no network decision is even made)."""
    acc = "0001193125-21-171001"
    filings = [_filing(acc, form="SC 13D", filed="2021-05-24", primary_doc="d46702dsc13d.htm")]
    ingest_schedule13(
        db, security_id, _CIK, filings, _Client({f"forms/{acc}/{acc}.txt": _REAL_HDR})
    )
    db.commit()
    raising = _Client()
    res = ingest_schedule13(db, security_id, _CIK, filings, raising)
    db.commit()
    assert (res.appended, res.reversioned, res.identity_skipped) == (0, 0, 0)
    assert raising.calls == []  # identity already stored -> zero fetch attempts


def test_no_lookahead_on_both_axes(db, security_id):
    """valid_from = filed: a 13D is invisible to an as-of BEFORE its filed date (the stake crossing
    inside it happened earlier — trap #4); recorded_at = now (never backdated): the fact is
    invisible to a known_at pinned before the ingest — the replay guarantee on both axes."""
    filings = [_filing("ACC-D", form="SC 13D", filed="2026-05-01", primary_doc="d.htm")]
    ingest_schedule13(db, security_id, _CIK, filings, _Client())
    db.commit()
    # valid-time axis
    assert _read(db, security_id, asof=date(2026, 4, 30)) == []
    assert len(_read(db, security_id, asof=date(2026, 5, 1))) == 1  # visible AT filed
    # transaction-time axis
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert _read(db, security_id, known_at=past) == []


# --- the subject-attribution skip (the ingest root-cause fix; real cited outbound 13Ds) -------------


def test_outbound_structured_13d_skipped_under_filer_feed_kept_under_subject_feed(db, security_id):
    """THE INGEST ROOT-CAUSE FIX on the two real cited outbound 13Ds. EDGAR lists a schedule in BOTH
    the filer's and the subject's submissions feed, so the FILER's feed also enumerates its OUTBOUND
    stakes about other companies. Under the FILER's own feed the cover's ``issuerCIK`` names a
    DIFFERENT subject → the row is DROPPED (``subject_skipped``), never stored under the wrong subject;
    the SAME filing under the TRUE subject's feed lands (subject == owner). This is the go-forward
    guarantee that the Part-2 repair need never re-clean."""
    for acc, filer_cik, subject_cik, filed, xml in (
        (_UEC_ACC, _UEC_CIK, _UEC_SUBJECT, "2026-07-28", _UEC_XML),
        (_GME_ACC, _GME_CIK, _GME_SUBJECT, "2026-05-04", _GME_XML),
    ):
        filings = [
            _filing(
                acc,
                form="SCHEDULE 13D",
                filed=filed,
                primary_doc="xslSCHEDULE_13D_X01/primary_doc.xml",
            )
        ]
        client = _Client({f"forms/{acc}/primary_doc.xml": xml})
        # under the FILER's feed → skipped (the outbound-filing case), nothing stored
        res = ingest_schedule13(db, security_id, filer_cik, filings, client)
        db.commit()
        assert (res.appended, res.reversioned, res.subject_skipped) == (0, 0, 1), acc
        assert _read(db, security_id) == [], acc  # never stored under the wrong subject
        # the SAME filing under the TRUE subject's feed → lands (subject == owner)
        res2 = ingest_schedule13(db, security_id, subject_cik, filings, client)
        db.commit()
        assert (res2.appended, res2.subject_skipped) == (1, 0), acc
        (row,) = _read(db, security_id)
        assert row["accession"] == acc and row["filer_cik"] == filer_cik, acc
        with db.cursor() as cur:  # reset the shared scope for the next real instance
            cur.execute("DELETE FROM fact_activist_stake WHERE security_id = %s", (security_id,))
        db.commit()


def test_outbound_classic_13d_skipped_under_filer_feed(db, security_id):
    """The classic-era half on the real 2021 atai×CMPS SC 13D header (SUBJECT COMPANY = COMPASS
    Pathways 0001816590, FILED BY ATAI 0001840904). Under ATAI's own feed (owner == the FILER) the
    schedule is DROPPED (subject != owner, read from the ``.txt`` SUBJECT COMPANY block already
    fetched for identity); under CMPS's feed (owner == the subject) it lands with ATAI as the filer.
    """
    acc = "0001193125-21-171001"
    filings = [_filing(acc, form="SC 13D", filed="2021-05-24", primary_doc="d46702dsc13d.htm")]
    client = _Client({f"forms/{acc}/{acc}.txt": _REAL_HDR})
    res = ingest_schedule13(db, security_id, "0001840904", filings, client)  # ATAI = the filer
    db.commit()
    assert (res.appended, res.reversioned, res.subject_skipped) == (0, 0, 1)
    assert _read(db, security_id) == []
    res2 = ingest_schedule13(db, security_id, "0001816590", filings, client)  # CMPS = the subject
    db.commit()
    assert (res2.appended, res2.subject_skipped) == (1, 0)
    (row,) = _read(db, security_id)
    assert row["filer_cik"] == "0001840904" and row["form"] == "SC 13D"  # the activist, kept


def test_unresolved_subject_keeps_the_row_recall_safe(db, security_id):
    """RECALL-SACRED (#9): a 13D-family filing whose subject CANNOT be resolved (the identity document
    is unfetchable) is KEPT with NULL identity — unresolved ≠ mis-attributed. The skip only ever fires
    on a POSITIVELY-different subject, never on an absent one; the fire-side ``_is_misattributed``
    screen remains the guard for any residual."""
    filings = [
        _filing("ACC-D", form="SCHEDULE 13D", filed="2026-05-01", primary_doc="x/primary_doc.xml")
    ]
    res = ingest_schedule13(db, security_id, _GME_CIK, filings, _Client())  # every fetch raises
    db.commit()
    assert (res.appended, res.subject_skipped, res.identity_skipped) == (1, 0, 1)
    (row,) = _read(db, security_id)
    assert row["filer_cik"] is None and row["form"] == "SCHEDULE 13D"  # kept, not skipped


def test_same_filing_two_securities_same_instant_does_not_collide(db, security_id):
    """THE 0037 REGRESSION: one issuer held as TWO master rows (share classes / dual listings)
    stores the SAME 13D once per security scope — two same-instant versions under two securities
    are two DIFFERENT logical facts and MUST both land (the 0039 constraint carries security_id
    from birth)."""
    sid2 = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid2, DEFAULT_TENANT_ID, "DEVCO.B", _CIK, date(2026, 1, 1)),
        )
    instant = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)  # one batch's shared now()
    filings = [_filing("ACC-SHARED", form="SC 13G", filed="2026-05-01")]
    ingest_schedule13(db, security_id, _CIK, filings, _Client(), recorded_at=instant)
    ingest_schedule13(db, sid2, _CIK, filings, _Client(), recorded_at=instant)
    db.commit()
    assert len(_read(db, security_id)) == 1
    other = as_of(
        db,
        "fact_activist_stake",
        security_id=sid2,
        asof=date(2026, 12, 31),
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(other) == 1  # each scope owns its own logical fact


def test_same_scope_same_instant_duplicate_is_refused(db, security_id):
    """The constraint's other face: the SAME (tenant, security, accession, recorded_at) tuple IS a
    duplicate and the DB refuses it — the natural-key constraint exists at exactly the as-of
    read's grain."""
    instant = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    ingest_schedule13(
        db,
        security_id,
        _CIK,
        [_filing("ACC-1", form="SC 13G", filed="2026-05-01")],
        _Client(),
        recorded_at=instant,
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        # bypass the append-if-changed compare (which would skip) to hit the constraint itself:
        # a re-version of the same logical fact at the same instant with a CHANGED surface
        ingest_schedule13(
            db,
            security_id,
            _CIK,
            [_filing("ACC-1", form="SC 13G/A", filed="2026-05-01")],
            _Client(),
            recorded_at=instant,
        )
    db.rollback()
