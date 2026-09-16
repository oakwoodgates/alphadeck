from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import NamedTuple
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

# --- the transaction-date sanity bound (P1) ---------------------------------------------------------
# MEASURED on the dev copy of prod (595,376 rows; re-verified independently 2026-09-16, same numbers):
# 29 rows / 15 distinct facts carry a transaction date that CANNOT be true — 10 below the Section 16 epoch,
# 19 after the accession's own filing year; txn codes A=8, F=6, M=2, S=13 and ZERO ``P``, so no arm can
# change. Two shapes, and the root cause is settled IN THE REPO, not in prose: both documents below are
# committed verbatim as fixtures (``tests/fixtures/edgar/form4_bbai_leading_zero_year.xml`` /
# ``form4_lscc_future_year.xml``, fetched live from EDGAR 2026-09-16 with the declared User-Agent) and
# ``tests/ingest/test_form4.py::test_the_real_sec_xml_really_says_the_impossible_date`` asserts it:
#   * a leading-zero year — 0001628280-24-010030 (BBAI) really contains
#     ``<transactionDate><value>0023-03-23</value></transactionDate>`` (and its OTHER two transactions are
#     correctly dated 2023-11-13 / 2024-03-05); 0000913760-24-000032 (SNEX) carries ``0024-02-12`` beside a
#     correct ``2024-02-12`` in the same document.
#   * a year AHEAD of the filing — 0001437749-24-004874 (LSCC) really says ``2027-02-17`` on an accession
#     filed 2024-02-20; 0001628280-23-035012 (CRDO) says ``2024-10-24`` on one filed 2023-10-24.
# So this is FILER GARBAGE, not a parser bug, and the disposition follows from #3: we must NOT invent a
# corrected date (``periodOfReport`` is the EARLIEST reportable transaction date, not this row's, and on the
# SNEX/BBAI/GS filings it is itself corrupt — the BBAI fixture's periodOfReport is ALSO ``0023-03-23``, and
# a test pins that too). The row is REJECTED and printed in full; the rest of the filing still stores
# (#9 — one filer typo never blanks the filing's good transactions: the LSCC document holds six
# transactions, of which ``parse_form4`` stores the four non-derivative ones, so THREE correctly-dated rows
# survive the rejection — not the five an earlier draft claimed by counting all six).
#
# The bound is deliberately STRUCTURAL — derived from the filing's own identity, never from an ambient
# clock and never from a tuned constant:
#
# LOWER — Section 16 insider reporting was created by the Securities Exchange Act of 1934; no reportable
# transaction can predate it. That catches every leading-zero year (0023/0024/0025) with ~59 years of
# headroom over the oldest genuine row in the corpus (1993-05-11). NOT "predates EDGAR's 2003 electronic
# Form 4 mandate": MEASURED, 42 corpus rows carry a legitimate 1993–2002 transaction date, reported on Form
# 4s filed in 2006/2007 (accessions 0001181431-06-*, 0001144204-07-*) — a 2003 floor would reject real data.
#
# UPPER — an EDGAR accession is ``NNNNNNNNNN-YY-NNNNNN`` with the two-digit year assigned at submission, so
# it names the filing year. A Form 4 reports a transaction that has ALREADY occurred (Section 16(a): filed
# within two business days AFTER), so the transaction cannot fall in a later calendar year than the filing.
# Coarse on purpose: the exact filing date is not available here (the ownership document carries none, and
# only the enumeration knows ``filed``), and the year anchor needs nothing threaded. Its residual is
# MEASURED and stated rather than guessed: 38 further rows are dated after their own ``accepted`` datetime
# within the SAME year and are NOT rejected — tightening to that clock would make the bound depend on a
# column that is NULL for ~47% of rows, so the same filing would be judged differently run to run.
_EDGAR_ACCESSION = re.compile(r"^\d{10}-(\d{2})-\d{6}$")
_SECTION_16_EPOCH_YEAR = 1934  # the Securities Exchange Act of 1934 created Section 16 reporting


def accession_filing_year(accession: str) -> int | None:
    """The filing YEAR encoded in an EDGAR accession (``0001628280-24-010030`` -> 2024), or ``None`` when
    ``accession`` is not EDGAR-shaped (a seed/test label like ``"acc-planned"``).

    EDGAR's electronic era starts in 1993, so a two-digit ``93``–``99`` is the 1990s and everything else is
    2000+ (unambiguous through 2092). ``None`` means "no upper anchor" and the caller ABSTAINS from the
    upper bound rather than guessing — erring toward keeping a row (#9)."""
    m = _EDGAR_ACCESSION.match(accession.strip())
    if m is None:
        return None
    yy = int(m.group(1))
    return (1900 if 93 <= yy <= 99 else 2000) + yy


def implausible_txn_date(txn_date: date, accession: str) -> str | None:
    """Why this transaction date cannot be true, or ``None`` when it is plausible.

    The ONE rule — imported by both the ingest (which rejects the row) and
    ``pipeline.repair_impossible_txn_dates`` (which deletes rows already stored), so the live bound and the
    repair can never disagree. See the block comment above for the derivation and the measurements.
    """
    if txn_date.year < _SECTION_16_EPOCH_YEAR:
        return (
            f"year {txn_date.year:04d} predates the Securities Exchange Act of "
            f"{_SECTION_16_EPOCH_YEAR} — Section 16 reporting did not exist"
        )
    filing_year = accession_filing_year(accession)
    if filing_year is not None and txn_date.year > filing_year:
        return (
            f"postdates the accession's filing year {filing_year} — a Form 4 reports a transaction "
            "that has already occurred"
        )
    return None


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

    READ by the SELL-side risk detector (``signals/insider_sell.py``, Band 03 S1 — the migration-0022
    question "should discretionary selling feed the counter-case?" answered): a True (planned) sale is
    screened out of the discretionary sell cluster; None stays UNKNOWN and is KEPT (absence never asserts
    either way). The BUY detector (``insider_conviction``) still does not read it — the buy-side 10b5-1
    screen is a separate, later decision.
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
    read by the SELL-side risk detector, not the buy side) and the issuer + reporting-owner IDENTITY
    (``issuer_cik``, ``issuer_name``, ``rpt_owner_cik``). The identity is what lets the insider detector recognize a
    self-filing (reporting owner IS the issuer — a buyback/treasury/ADR mechanic, never personal insider
    conviction) and screen it out of the open-market conviction total; see ``signals/insider_conviction.py``.

    It also carries the per-transaction ``security_title`` (``<securityTitle>``) and the filing-level
    ``issuer_foreign_symbol`` (``<issuerForeignTradingSymbol>``) — the two fields the ADR/dual-listed screen
    reads to drop a home-market ordinary-share row mis-filed on a US ADR's tape (S2c; see
    ``_is_foreign_ordinary`` in the insider detectors). Both empty/absent -> None (recall-safe #9).
    """
    root = ET.fromstring(xml)
    owner = root.findtext("reportingOwner/reportingOwnerId/rptOwnerName")
    owner_cik = _norm_cik(root.findtext("reportingOwner/reportingOwnerId/rptOwnerCik"))
    issuer_name = root.findtext("issuer/issuerName")
    issuer_cik = _norm_cik(root.findtext("issuer/issuerCik"))
    # filing-level foreign trading symbol (the issuer's OWN dual-listed tell — "2330.TW" for TSM), stamped
    # onto every row for the ADR/dual-listed mis-attribution screen (see ``_is_foreign_ordinary`` in the
    # insider detectors). Empty/absent -> None (a US issuer declares none; recall-safe #9).
    issuer_fsym = (root.findtext("issuer/issuerForeignTradingSymbol") or "").strip() or None
    role = _role(root.find("reportingOwner/reportingOwnerRelationship"))
    aff = _aff_10b5_1(root)  # filing-level -> stamped onto every row below

    txns: list[dict] = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = to_float(t.findtext("transactionAmounts/transactionShares/value"))
        price = to_float(t.findtext("transactionAmounts/transactionPricePerShare/value"))
        d = t.findtext("transactionDate/value")
        # PER-TRANSACTION security title — "Common Shares (2330.TW)" (home-market ordinary) vs "American
        # Depositary Shares (TSM)" (the ADR). The discriminator that separates the mis-filed foreign line
        # from the ADR we hold; captured per-row because ONE filing carries both. Empty/absent -> None.
        title = (t.findtext("securityTitle/value") or "").strip() or None
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
                # the S2c ADR/dual-listed screen inputs — per-txn title + filing-level foreign symbol
                "security_title": title,
                "issuer_foreign_symbol": issuer_fsym,
            }
        )
    return txns


class Form4Ingest(NamedTuple):
    """One filing's ingest outcome: rows appended, and transactions REJECTED by the sanity bound.

    ``rejected`` exists so a gate can watch the aggregate — it is never the primary report. Every rejection
    also PRINTS in full (accession, insider, sequence, raw date, reason); the earlier Form-4 defect hid
    precisely because a counter was rising and a rising counter reads as normal operation."""

    appended: int
    rejected: int


def ingest_form4(
    conn: psycopg.Connection,
    security_id: UUID,
    xml: str,
    accession: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
    accepted=None,
) -> Form4Ingest:
    """Parse a Form 4 and append its transactions to ``fact_insider_txn`` (append-only); the caller
    owns the transaction (no commit here). Returns ``(appended, rejected)``.

    ``accepted`` is the SEC acceptance datetime (the real "disclosed" clock) threaded from the
    enumeration (``submissions.acceptanceDateTime``, parsed via ``parse_acceptance``) — the ownership XML
    itself carries no acceptance datetime, so ``parse_form4`` is UNCHANGED and this rides as a per-filing
    kwarg like ``recorded_at``. ``None`` leaves the column NULL (the read gate/display fall back to
    ``recorded_at``/"ingested" — recall-safe #9). It is FILING-level, stamped identically on every row.

    THE SANITY BOUND (P1): a transaction whose date cannot be true (``implausible_txn_date``) is REJECTED —
    never stored, never clamped to a guessed date (#3: a clamp fabricates a fact). Each rejection PRINTS the
    accession, the insider, the sequence and the raw date, AND is tallied on the returned ``rejected``.

    **The count ACCOMPANIES the per-row print; it never replaces it.** That ordering is the whole lesson of
    the earlier Form-4 bug: a rising skip COUNTER is how a real defect hid in plain sight, because a number
    going up reads as "working as designed". The print is what makes a rejection investigable; the count is
    only so a gate can watch the aggregate (it rides ``NameResult.form4_txn_rejected`` into the cron's
    per-thesis summary, which prints it ONLY when nonzero — honest loudness).

    Rejection is per-TRANSACTION, not per-filing: the LSCC filing (``0001437749-24-004874``, committed as
    ``tests/fixtures/edgar/form4_lscc_future_year.xml``) carries one ``2027-02-17`` row beside THREE
    correctly-dated non-derivative ones, and dropping the filing would lose them (#9). (The filing holds six
    transactions in total, but ``parse_form4`` stores the non-derivative ones only — hence three, not five;
    the fixture makes that checkable rather than a claim in prose.)
    """
    count = 0
    rejected = 0
    for i, t in enumerate(parse_form4(xml)):
        if t["txn_date"] is None:
            continue
        reason = implausible_txn_date(t["txn_date"], accession)
        if reason is not None:
            rejected += 1
            print(
                f"  REJECT insider txn {accession} seq={i} "
                f"{t['insider_name'] or '?'} ({t['txn_code'] or '?'}): "
                f"transactionDate {t['txn_date'].isoformat()} {reason}. "
                "The filing says this verbatim — NOT stored, and no corrected date is invented (#3)."
            )
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
            # the filing's Rule 10b5-1 checkbox — read by the SELL-side risk detector
            # (signals/insider_sell.py: True screens the sale out of the discretionary cluster); the
            # BUY detector still does not read it. NULL = unknown (pre-Dec-2022 filings have no
            # checkbox), never coerced to False
            "aff_10b5_1": t["aff_10b5_1"],
            # issuer + reporting-owner identity — the insider detector's issuer-self screen reads these
            # (rpt_owner_cik == issuer_cik ⇒ the company filed on itself). NULL on rows ingested before
            # this column existed; the detector falls back to a name match there. See migration 0024.
            "issuer_cik": t["issuer_cik"],
            "issuer_name": t["issuer_name"],
            "rpt_owner_cik": t["rpt_owner_cik"],
            # the S2c ADR/dual-listed screen inputs (migration 0041) — the per-txn security title + the
            # filing's foreign trading symbol. NULL on rows ingested before these columns existed; the
            # screen keeps a NULL-title row (recall-safe #9). Repaired on history by
            # pipeline.repair_adr_insider_misattribution (re-parse the cached XML, append-only re-version).
            "security_title": t["security_title"],
            "issuer_foreign_symbol": t["issuer_foreign_symbol"],
        }
        if recorded_at is not None:
            values["recorded_at"] = recorded_at
        if accepted is not None:  # filing-level SEC acceptance datetime; NULL when unresolved (#9)
            values["accepted"] = accepted
        append_fact(conn, "fact_insider_txn", values)
        count += 1
    return Form4Ingest(appended=count, rejected=rejected)


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
