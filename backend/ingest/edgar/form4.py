from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from uuid import UUID
from xml.etree.ElementTree import Element

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.coerce import to_float

# A Form 4 transactionDate is a calendar date ('YYYY-MM-DD'). Some filing agents serialize it with a
# trailing UTC offset (e.g. '2026-05-13-05:00') or time/'Z' suffix, which ``date.fromisoformat`` rejects —
# an offset is only valid on a datetime, not a date. That silently skipped RECENT, valid Form 4s (the offset
# suffix showed up on 2026 filings from agent 0001654954 and others), dropping open-market buys before they
# could reach the Key-1 insider detector (#3-adjacent: a real number lost). The leading ``YYYY-MM-DD`` IS the
# trade date the filer stated; the offset is spurious agent metadata that must be discarded, never used to
# shift the calendar date (there is no time component to shift). ``datetime.fromisoformat().date()`` is NOT a
# safe substitute: it rejects 'YYYY-MM-DDZ' and misreads the bare offset '-05:00' as a 5 a.m. time.
_ISO_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}")


def _txn_date(raw: str | None) -> date | None:
    """Parse a Form 4 transaction date, tolerating a spurious trailing tz-offset / time suffix.

    Returns ``None`` for an empty value. Takes the leading ``YYYY-MM-DD`` when present (discarding any
    offset/time the agent tacked on); a value with no ISO date prefix falls through to ``date.fromisoformat``
    so a genuinely malformed date still raises ``ValueError`` — which the ingest leg tolerates as one
    skipped-and-counted filing (``pipeline.ingest_thesis._tolerable_filing_error``), never a silent drop.
    """
    if not raw:
        return None
    s = raw.strip()
    m = _ISO_DATE_PREFIX.match(s)
    return date.fromisoformat(m.group(0) if m else s)


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


def _aff_10b5_1(root: ET.Element) -> bool | None:
    """The filing's Rule 10b5-1 checkbox (``<aff10b5One>``) — was this trade PRE-PLANNED or discretionary?

    THREE-STATE, and the None is load-bearing: True = a planned trade, False = the box is present and clear
    (discretionary), None = UNKNOWN — the element is absent, which is the norm for anything filed before the
    SEC's Dec-2022 amendments added the checkbox. Absence must NEVER collapse to False: that would assert
    "this sale was discretionary" about a filing that never said so — inventing a fact (#3).

    DOCUMENT-level by construction: the element sits on the ownership document (after ``</reportingOwner>``),
    not on a transaction, so it stamps every row parsed from the filing. A filing mixing a planned and a
    discretionary trade is ambiguous — that is what the SEC gives us, and we record it, not resolve it.

    CAPTURE-ONLY: nothing reads this. It is stored so the history accrues while the call-logic question
    (should discretionary selling feed the counter-case?) stays open for the operator.
    """
    raw = root.findtext("aff10b5One")
    if raw is None:
        return None  # pre-2023 filing / no checkbox — UNKNOWN, never "not planned"
    v = raw.strip().lower()
    if v in ("1", "true"):
        return True
    if v in ("0", "false"):
        return False
    return None  # an unparseable value is unknown, not a guess


def _norm_cik(raw: str | None) -> str | None:
    """Normalize an EDGAR CIK for identity comparison — strip whitespace + leading zeros ('0001773751' ->
    '1773751'). Both the issuer CIK and the owner CIK come from the SAME filing (identically padded), so this
    is belt-and-suspenders; it also lets a stored CIK compare equal to an unpadded one. Empty -> None.
    """
    if not raw:
        return None
    s = raw.strip().lstrip("0")
    return s or None


def parse_form4(xml: str) -> list[dict]:
    """Parse a Form 4 ownership document into open-market-aware transaction rows.

    Returns one row per non-derivative transaction with its raw ``txn_code`` (e.g. 'P' = open-market
    purchase, 'S' = sale); the insider-conviction detector (M2b) is what isolates code 'P'.

    Each row carries the filing's ``aff_10b5_1`` (the Rule 10b5-1 checkbox, tri-state — see ``_aff_10b5_1``;
    CAPTURE-ONLY, no detector reads it) and the issuer + reporting-owner IDENTITY (``issuer_cik``,
    ``issuer_name``, ``rpt_owner_cik``). The identity is what lets the insider detector recognise a
    self-filing (reporting owner IS the issuer — a buyback/treasury/ADR mechanic, never personal insider
    conviction) and screen it out of the open-market conviction total; see ``signals/insider_conviction.py``.
    """
    root = ET.fromstring(xml)
    owner = root.findtext("reportingOwner/reportingOwnerId/rptOwnerName")
    owner_cik = _norm_cik(root.findtext("reportingOwner/reportingOwnerId/rptOwnerCik"))
    issuer_name = root.findtext("issuer/issuerName")
    issuer_cik = _norm_cik(root.findtext("issuer/issuerCik"))
    role = _role(root.find("reportingOwner/reportingOwnerRelationship"))
    aff = _aff_10b5_1(root)  # filing-level -> stamped onto every row below

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
                "txn_date": _txn_date(d),
                "acquired_disposed": t.findtext(
                    "transactionAmounts/transactionAcquiredDisposedCode/value"
                ),
                "aff_10b5_1": aff,  # filing-level; tri-state (True/False/None=unknown)
                # filing-level identity (stamped on every row) — the issuer-self screen (#3); issuer-self
                # ⇔ rpt_owner_cik == issuer_cik. Kept for the deferred affiliate-block pass too.
                "issuer_cik": issuer_cik,
                "issuer_name": issuer_name,
                "rpt_owner_cik": owner_cik,
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
            # the filing's Rule 10b5-1 checkbox — CAPTURE-ONLY (no detector reads it); NULL = unknown
            # (pre-Dec-2022 filings have no checkbox), never coerced to False
            "aff_10b5_1": t["aff_10b5_1"],
            # issuer + reporting-owner identity — the insider detector's issuer-self screen reads these
            # (rpt_owner_cik == issuer_cik ⇒ the company filed on itself). NULL on rows ingested before
            # this column existed; the detector falls back to a name match there. See migration 0024.
            "issuer_cik": t["issuer_cik"],
            "issuer_name": t["issuer_name"],
            "rpt_owner_cik": t["rpt_owner_cik"],
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
