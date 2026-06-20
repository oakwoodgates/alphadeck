from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from uuid import UUID
from xml.etree.ElementTree import Element

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.coerce import to_float


def _role(rel: Element | None) -> str | None:
    if rel is None:
        return None
    title = rel.findtext("officerTitle")
    if title:
        return title
    flags = []
    if rel.findtext("isDirector") in ("1", "true"):
        flags.append("Director")
    if rel.findtext("isOfficer") in ("1", "true"):
        flags.append("Officer")
    if rel.findtext("isTenPercentOwner") in ("1", "true"):
        flags.append("10% owner")
    return ", ".join(flags) or None


def parse_form4(xml: str) -> list[dict]:
    """Parse a Form 4 ownership document into open-market-aware transaction rows.

    Returns one row per non-derivative transaction with its raw ``txn_code`` (e.g. 'P' = open-market
    purchase, 'S' = sale); the insider-conviction detector (M2b) is what isolates code 'P'.
    """
    root = ET.fromstring(xml)
    owner = root.findtext("reportingOwner/reportingOwnerId/rptOwnerName")
    role = _role(root.find("reportingOwner/reportingOwnerRelationship"))

    txns: list[dict] = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = to_float(t.findtext("transactionAmounts/transactionShares/value"))
        price = to_float(t.findtext("transactionAmounts/transactionPricePerShare/value"))
        d = t.findtext("transactionDate/value")
        txns.append(
            {
                "insider_name": owner,
                "insider_role": role,
                "txn_code": t.findtext("transactionCoding/transactionCode"),
                "shares": shares,
                "price": price,
                "usd": (shares or 0.0) * (price or 0.0),
                "txn_date": date.fromisoformat(d) if d else None,
                "acquired_disposed": t.findtext(
                    "transactionAmounts/transactionAcquiredDisposedCode/value"
                ),
            }
        )
    return txns


def ingest_form4(
    conn: psycopg.Connection,
    security_id: UUID,
    xml: str,
    accession: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> int:
    """Parse a Form 4 and append its transactions to ``fact_insider_txn`` (append-only); the caller
    owns the transaction (no commit here). Returns the count appended."""
    count = 0
    for i, t in enumerate(parse_form4(xml)):
        if t["txn_date"] is None:
            continue
        values = {
            "tenant_id": tenant_id,
            "security_id": security_id,
            "insider_name": t["insider_name"],
            "insider_role": t["insider_role"],
            "txn_code": t["txn_code"],
            "shares": t["shares"],
            "price": t["price"],
            "usd": t["usd"],
            "accession": accession,
            "valid_from": t["txn_date"],
            "txn_seq": i,  # position within the filing — distinguishes same-insider same-day txns
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        append_fact(conn, "fact_insider_txn", values)
        count += 1
    return count


def existing_accessions(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> set[str]:
    """The Form-4 accessions already ingested for (tenant, security) — so the per-thesis ingest can SKIP a
    filing it already has and re-ingest ONLY new ones. Accession is the right grain: it is the filing
    identity and the lead column of the insider natural key, so "accession present" ⇔ "its txns stored".
    A re-run of an already-ingested name therefore appends NOTHING (the append-only table never silently
    grows)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT accession FROM fact_insider_txn WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        return {r["accession"] for r in cur.fetchall()}
