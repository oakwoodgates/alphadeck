"""JOINT REPORTS on the insider tape — the root cause of INFQ's doubled insider-sell line, pinned on the real SEC
documents (found 2026-10-21 during R8-B: "$95.2M across 2 insiders" = Maverick Capital's and David Singer's
4/As each adding the SAME four 2026-05-27 sales, $47.6M each).

PROVENANCE. Every fixture below is the ownership XML the app itself fetched from EDGAR, copied READ-ONLY and
byte-for-byte out of prod's EDGAR cache (``/data/edgar_cache/forms/<accession>/`` in the backend container) on
2026-10-21:

  form4_infq_maverick_joint.xml — INFQ 0001012975-26-000481 (cached 2026-09-01): a JOINT filing — three
                                  reporting owners (Maverick Capital Ltd, Maverick Capital Management LLC,
                                  Lee S. Ainslie III) — 17 Table I sales by the Maverick funds, 05-21/05-22.
  form4_infq_singer.xml         — INFQ 0001012975-26-000486 (cached 2026-09-01): David B. Singer's OWN Form 4
                                  (a director; a managing partner of the funds' general partner) reporting 12
                                  of those same fund sales as his indirect holdings.
  form4_alab_mohan.xml          — ALAB 0001736297-25-000115 (cached 2026-07-10): Jitendra Mohan's sell-to-cover.
  form4_alab_gajendra.xml       — ALAB 0001736297-25-000117 (cached 2026-07-10): Sanjay Gajendra's sell-to-cover —
                                  identical in every number to Mohan's, and a DIFFERENT person's own shares.

WHAT THE DOCUMENTS SAY (and what the measurement on prod found, 2026-10-21, read-only):

1. ONE filing never double-counts. ``parse_form4`` stores each Table I transaction ONCE, attributed to the FIRST
   ``<reportingOwner>`` — a joint filing's co-owners are not stored at all. MEASURED: 0 of 146,111 stored
   (security, accession) pairs carry a second reporting owner. The duplicate is NOT inside one accession.
2. The double count is ACROSS filings: a second reporting person files their OWN Form 4 for the same trade
   (a fund + its partner; a spouse reporting the other's sale "By Spouse"). The documents prove it is the same
   trade: same date, code, shares and price, the same post-trade holding, held INDIRECTLY by at least one of
   them. ``fact_insider_txn`` stores none of that evidence (no post-trade holding, no direct/indirect flag).
3. An identical (date, code, shares, price) — even an identical post-trade holding — is NOT enough on its own:
   ALAB's two co-founders sold identical sell-to-cover lots of their OWN shares (both DIRECT). Those are two
   trades and must stay two (the negative case any fix keeps green).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from ingest.edgar.form4 import ingest_form4, parse_form4
from tests.form4a_fixtures import as_issuer

_FIX = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"
_MAVERICK = (_FIX / "form4_infq_maverick_joint.xml").read_text(encoding="utf-8")
_SINGER = (_FIX / "form4_infq_singer.xml").read_text(encoding="utf-8")
_ALAB_MOHAN = (_FIX / "form4_alab_mohan.xml").read_text(encoding="utf-8")
_ALAB_GAJENDRA = (_FIX / "form4_alab_gajendra.xml").read_text(encoding="utf-8")
_KNOWN = datetime(
    2100, 1, 1, tzinfo=timezone.utc
)  # "everything recorded so far" — the valid-time axis only


def _table_one(xml: str) -> list[dict]:
    """Each Table I transaction's ownership EVIDENCE, read straight from the document (``parse_form4`` does not
    capture it): the direct/indirect flag and the post-trade holding, beside the fields a stored row has.
    """
    out = []
    for t in ET.fromstring(xml).findall("nonDerivativeTable/nonDerivativeTransaction"):
        out.append(
            {
                "date": t.findtext("transactionDate/value"),
                "code": t.findtext("transactionCoding/transactionCode"),
                "shares": float(t.findtext("transactionAmounts/transactionShares/value")),
                "price": float(t.findtext("transactionAmounts/transactionPricePerShare/value")),
                "after": float(
                    t.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value")
                ),
                "direct_or_indirect": t.findtext("ownershipNature/directOrIndirectOwnership/value"),
            }
        )
    return out


def _owners(xml: str) -> list[str]:
    return [
        o.findtext("reportingOwnerId/rptOwnerName")
        for o in ET.fromstring(xml).findall("reportingOwner")
    ]


def test_a_joint_filing_stores_each_transaction_once(db, security_id):
    """Finding 1 — the hypothesis "one filing's rows are stored once per co-reporting owner" is FALSE, by
    construction: three reporting owners, seventeen transactions, seventeen rows, ONE insider. COUNT THE TABLE.
    (A future change that records a joint filing's co-owners must not fan the rows out per owner — that would
    create exactly the within-filing double count this test rules out.)"""
    assert _owners(_MAVERICK) == [
        "MAVERICK CAPITAL LTD",
        "MAVERICK CAPITAL MANAGEMENT LLC",
        "AINSLIE LEE S III",
    ]
    rows = parse_form4(_MAVERICK)
    assert len(rows) == len(_table_one(_MAVERICK)) == 17
    assert {(r["insider_name"], r["rpt_owner_cik"]) for r in rows} == {
        ("MAVERICK CAPITAL LTD", "934639")
    }  # the FIRST reporting owner only

    as_issuer(db, security_id, "0002007825")  # INFQ (the read's other-issuer screen keys on it)
    assert ingest_form4(db, security_id, _MAVERICK, "0001012975-26-000481") == (17, 0)
    db.commit()
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n, count(DISTINCT insider_name) AS owners FROM fact_insider_txn"
        )
        assert cur.fetchone() == {"n": 17, "owners": 1}


def test_the_real_documents_report_the_same_sales_from_two_filers():
    """Finding 2 — the double count is ACROSS filings. Every one of Singer's twelve sales is one of Maverick's
    seventeen: the same date, code, shares, price AND post-trade holding, held INDIRECTLY in both documents
    (the Maverick funds own the shares). The two filings share no reporting owner. Stored side by side, the
    sell detector sums these $181.6M twice and counts two insiders."""
    assert _owners(_SINGER) == ["SINGER DAVID B"]
    assert not set(_owners(_SINGER)) & set(_owners(_MAVERICK))

    maverick = _table_one(_MAVERICK)
    singer = _table_one(_SINGER)
    key = ("date", "code", "shares", "price", "after", "direct_or_indirect")
    maverick_keys = {tuple(t[k] for k in key) for t in maverick}
    assert all(tuple(t[k] for k in key) in maverick_keys for t in singer)
    assert {t["direct_or_indirect"] for t in singer + maverick} == {"I"}
    assert {t["code"] for t in singer} == {"S"}

    # four of them leave the fund with NOTHING (post-trade 0) — a sold-out balance identifies no position on
    # its own, which is why any fix must tie such a row to a filing pair already proven to be one report
    assert sum(1 for t in singer if t["after"] == 0) == 4

    doubled = sum(r["usd"] for r in parse_form4(_SINGER))
    assert doubled == pytest.approx(181_595_286.54)


def test_identical_trades_by_two_direct_holders_stay_two_trades(db):
    """Finding 3, the NEGATIVE — separate filings by different insiders with the same size on the same day are
    separate trades when each holds the shares DIRECTLY: ALAB's two co-founders' automatic sell-to-cover sales
    match on date, code, shares, price and even the post-trade holding, yet each sold his OWN vested shares.
    The as-of read returns all four sales from both insiders — a joint-report fix must keep this green.
    """
    mohan, gajendra = _table_one(_ALAB_MOHAN), _table_one(_ALAB_GAJENDRA)
    assert mohan == gajendra  # every number identical, including the post-trade holding
    assert {t["direct_or_indirect"] for t in mohan + gajendra} == {"D"}  # each person's own shares
    assert "sell to cover" in _ALAB_MOHAN and "sell to cover" in _ALAB_GAJENDRA

    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (gen_random_uuid(), %s, 'ALAB', '0001736297', 'Astera Labs, Inc.', '2020-01-01') "
            "RETURNING id",
            (DEFAULT_TENANT_ID,),
        )
        sid = cur.fetchone()["id"]
    db.commit()
    assert ingest_form4(db, sid, _ALAB_MOHAN, "0001736297-25-000115") == (2, 0)
    assert ingest_form4(db, sid, _ALAB_GAJENDRA, "0001736297-25-000117") == (2, 0)
    db.commit()

    rows = as_of(
        db,
        "fact_insider_txn",
        security_id=sid,
        asof=date(2025, 8, 18),
        known_at=_KNOWN,
        tenant_id=DEFAULT_TENANT_ID,
    )
    assert len(rows) == 4
    assert {r["insider_name"] for r in rows} == {"Mohan Jitendra", "Gajendra Sanjay"}
    each = sum(r["usd"] for r in parse_form4(_ALAB_MOHAN))
    assert sum(float(r["usd"]) for r in rows) == pytest.approx(2 * each)  # both men's sales count
