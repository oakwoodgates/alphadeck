from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any, NamedTuple
from uuid import UUID
from xml.etree.ElementTree import Element

import psycopg
from psycopg.types.json import Json

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.coerce import to_float
from domain.insider_holds import SECTION_16_EPOCH_YEAR, accession_filing_year
from domain.market_time import market_date_of

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
# a test pins that too). The row is HELD (``insider_txn_hold``, migration 0082 — never a fact, see
# ``write_hold`` below) and printed in full, once; the rest of the filing still stores
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
# The year anchor judges a row from the accession alone, so it needs nothing threaded and the repair tools —
# which hold only the accession — apply it unchanged.
#
# AFTER ITS OWN FILING (rule log ``2026-11-insider-rows-dated-after-filing``, the third condition): when the
# filing's SEC acceptance is KNOWN, a transaction dated after the acceptance's market date (New York) cannot be
# true either — a Form 4 reports a trade that has already happened. One exception, MEASURED on a real filing
# (accepted 20:27 New York on one day, signed and traded the next): a filer in a time zone ahead of New York
# signs and trades on its own next calendar day, so the row is held only when it is ALSO dated after the
# filing's last signature date, when the filing has a readable one. No acceptance -> only the two year
# conditions, as before: that residual is the one the year anchor always named (MEASURED 2026-09-16: 38 rows
# dated after their own ``accepted`` within the same year; ``accepted`` is NULL on ~47% of raw rows, 0.67% of
# facts on the latest-version grain, ``docs/BACKTEST.md``), now closed wherever the acceptance is stored.
# ``pipeline.repair_impossible_txn_dates`` (which DELETES stored rows) keeps calling
# the rule without an acceptance, so it never deletes an after-filing row: those are HELD, and the read
# decides when they count (``domain.insider_holds``).
#
# The two YEAR pieces (``accession_filing_year`` and the 1934 epoch) live in ``domain.insider_holds`` since the
# read judges a held row's period of report with them, and the domain never imports the ingest; they are
# imported here, so the rule still has one statement (``implausible_txn_date_code`` below).
_SECTION_16_EPOCH_YEAR = SECTION_16_EPOCH_YEAR  # the 1934 Act created Section 16 reporting


# The rule's three outcomes as CODES — the ``reason`` a held row carries (``insider_txn_hold``, migration 0082,
# whose CHECK lists them).
HOLD_YEAR_BEFORE_1934 = "year_before_1934"
HOLD_YEAR_AFTER_FILING_YEAR = "year_after_filing_year"
HOLD_AFTER_FILING = "after_filing"

# the ``form`` a held row carries: a Form 4's own row, or a row of a Form 4/A
HOLD_FORM_4 = "4"
HOLD_FORM_4A = "4/A"


def _aware(at: datetime) -> datetime:
    """A naive acceptance read as UTC (no engine returns one)."""
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _after_filing(txn_date: date, accepted: datetime | None, signature_date: date | None) -> bool:
    """The third condition: the acceptance is known, the date is after the acceptance's market date AND (when
    the filing has a readable signature date) after it."""
    if accepted is None or txn_date <= market_date_of(_aware(accepted)):
        return False
    return signature_date is None or txn_date > signature_date


def implausible_txn_date_code(
    txn_date: date,
    accession: str,
    *,
    accepted: datetime | None = None,
    signature_date: date | None = None,
) -> str | None:
    """THE ONE RULE, as a code: why this transaction date cannot be true (``HOLD_YEAR_BEFORE_1934`` /
    ``HOLD_YEAR_AFTER_FILING_YEAR`` / ``HOLD_AFTER_FILING``), or ``None`` when it is plausible. See the block
    comment above for the derivation and the measurements. ``accepted`` = the filing's SEC acceptance
    (``None`` = unknown: only the two year conditions), ``signature_date`` = its last readable signature date.
    ``implausible_txn_date`` (below) is this rule in words; nothing else states it."""
    if txn_date.year < _SECTION_16_EPOCH_YEAR:
        return HOLD_YEAR_BEFORE_1934
    filing_year = accession_filing_year(accession)
    if filing_year is not None and txn_date.year > filing_year:
        return HOLD_YEAR_AFTER_FILING_YEAR
    if _after_filing(txn_date, accepted, signature_date):
        return HOLD_AFTER_FILING
    return None


def implausible_txn_date(
    txn_date: date,
    accession: str,
    *,
    accepted: datetime | None = None,
    signature_date: date | None = None,
) -> str | None:
    """Why this transaction date cannot be true, in words, or ``None`` when it is plausible.

    The ONE rule (``implausible_txn_date_code``) — imported by the ingest (which HOLDS the row instead of
    storing it), ``pipeline.backfill_insider_holds`` (which holds the rows an earlier ingest dropped) and
    ``pipeline.repair_impossible_txn_dates`` (which deletes rows already stored), so the live bound and the
    repairs can never disagree.
    """
    code = implausible_txn_date_code(
        txn_date, accession, accepted=accepted, signature_date=signature_date
    )
    if code == HOLD_YEAR_BEFORE_1934:
        return (
            f"year {txn_date.year:04d} predates the Securities Exchange Act of "
            f"{_SECTION_16_EPOCH_YEAR} — Section 16 reporting did not exist"
        )
    if code == HOLD_YEAR_AFTER_FILING_YEAR:
        return (
            f"postdates the accession's filing year {accession_filing_year(accession)} — a Form 4 "
            "reports a transaction that has already occurred"
        )
    if code == HOLD_AFTER_FILING and accepted is not None:
        signed = f", signed {signature_date.isoformat()}" if signature_date is not None else ""
        return (
            f"postdates its own filing (accepted on {market_date_of(_aware(accepted)).isoformat()}"
            f"{signed}) — a Form 4 reports a transaction that has already occurred"
        )
    return None


def held_rows(
    rows: Sequence[Mapping[str, Any]],
    accession: str,
    *,
    accepted: datetime | None = None,
    signature_date: date | None = None,
) -> dict[int, str]:
    """``{position in the filing: reason code}`` for each parsed row the rule refuses — THE ONE reading of
    "which rows of this filing are held", shared by the Form 4 ingest, the Form 4/A ingest and the backfill
    (``pipeline.backfill_insider_holds``). The position is the ingest's ``txn_seq`` (``parse_form4``'s order).
    A row with no transaction date at all is not in it: the ingest skips such a row as it always has.
    ``accepted`` / ``signature_date`` feed the after-filing condition (``filing_held_rows`` reads the signature
    from the document only when it can matter).
    """
    out: dict[int, str] = {}
    for i, t in enumerate(rows):
        if t.get("txn_date") is None:
            continue
        code = implausible_txn_date_code(
            t["txn_date"], accession, accepted=accepted, signature_date=signature_date
        )
        if code is not None:
            out[i] = code
    return out


def signature_date_of(xml: str) -> date | None:
    """The filing's LAST ``<ownerSignature><signatureDate>`` as a date, or ``None`` when it has none or it is
    not a readable date (the after-filing condition then reads the acceptance alone)."""
    try:
        text = hold_evidence(xml).signature_date
        return _txn_date(text) if text else None
    except (ET.ParseError, ValueError):
        return None


def filing_held_rows(
    rows: Sequence[Mapping[str, Any]], accession: str, xml: str, accepted: datetime | None
) -> dict[int, str]:
    """``held_rows`` for one filing, as the ingest (Form 4 and 4/A) and the backfill read it: the signature
    date is read from the document only when a row is dated after the acceptance's market date (the one case
    it can change)."""
    sig: date | None = None
    if accepted is not None:
        day = market_date_of(_aware(accepted))
        if any(t.get("txn_date") is not None and t["txn_date"] > day for t in rows):
            sig = signature_date_of(xml)
    return held_rows(rows, accession, accepted=accepted, signature_date=sig)


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


def _direct_indirect(t: Element) -> str | None:
    """The transaction's ``<ownershipNature><directOrIndirectOwnership>``: ``'D'`` (the reporting person's own
    shares) or ``'I'`` (held through a fund, trust, spouse, ...). Anything else — absent, empty, a value the
    SEC schema does not define — is ``None`` (unknown), never a guess: the joint-report rule
    (``domain/insider_joint_reports.py``) never merges a row without it."""
    raw = (t.findtext("ownershipNature/directOrIndirectOwnership/value") or "").strip().upper()
    return raw if raw in ("D", "I") else None


def _shares_owned_after(t: Element) -> float | None:
    """The transaction's ``<sharesOwnedFollowingTransaction>`` — the holding the trade left — or ``None`` when
    absent (a filing may report ``valueOwnedFollowingTransaction`` instead) or not a number. TOLERANT on
    purpose: this is evidence for the joint-report rule only, so a malformed value must cost the row its
    evidence (it then never merges), never the filing its ingest (#9 — shares/price stay strict as before).
    """
    raw = (t.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value") or "").strip()
    try:
        v = float(raw) if raw else None
    except ValueError:
        return None
    return v if v is not None and math.isfinite(v) else None


def _text(raw: str | None) -> str | None:
    """Whitespace-collapsed text, ``None`` when empty — the filer's words as stated (never rewritten)."""
    s = " ".join((raw or "").split())
    return s or None


def _ownership_nature(el: Element) -> str | None:
    """The element's ``<ownershipNature><natureOfOwnership>`` ("By Spouse", "By Sujoda Investments, LP") — the
    filer's own statement of WHOSE shares it reports — or ``None`` when the filing states none (migration 0055).
    """
    return _text(el.findtext("ownershipNature/natureOfOwnership/value"))


def _footnotes_cited(el: Element, footnotes: dict[str, str]) -> list[str]:
    """The text of every footnote ``el`` cites (any ``<footnoteId>`` inside it — on the shares, the price, the
    nature, ...), in first-reference order, each once; a cited id the filing does not define is skipped.
    ``[]`` = the transaction cites none. The located passage behind the private-purchase note (0055).
    """
    seen: list[str] = []
    for f in el.iter("footnoteId"):
        fid = f.get("id")
        if fid and fid not in seen:
            seen.append(fid)
    return [footnotes[fid] for fid in seen if footnotes.get(fid)]


def _filing_natures(root: Element) -> list[str]:
    """Every INDIRECT ownership nature the filing states on its Table I transactions AND holdings, each once,
    in document order — the filer's statement of whose shares it reports (Susan Y. Kim's AMKR filing: "By
    James J. Kim 2024 GRAT dtd. 8/5/24"). The internal-transfer screen's linking evidence
    (``domain/insider_internal_transfers.py``); ``[]`` = the filing states none (migration 0055)."""
    out: list[str] = []
    for el in root.findall("nonDerivativeTable/*"):
        if _direct_indirect(el) != "I":
            continue
        nature = _ownership_nature(el)
        if nature and nature not in out:
            out.append(nature)
    return out


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

    And the per-transaction OWNERSHIP evidence (migration 0054): ``direct_indirect`` (``'D'``/``'I'``) and
    ``shares_owned_after`` (``<sharesOwnedFollowingTransaction>`` — the holding the trade left; ``None`` when
    the filing reports a value instead). They are what proves that two filings by different reporting persons
    report ONE trade (a joint report — ``domain/insider_joint_reports.py``). A joint filing's co-owners are NOT
    stored: each transaction is ONE row under the first ``<reportingOwner>``, so one filing never
    double-counts (``tests/ingest/test_form4_joint_reports.py``).

    And the filer's own WORDS about whose shares it reports (migration 0055): ``ownership_nature`` (the
    transaction's ``<natureOfOwnership>``), ``txn_footnotes`` (the text of every footnote the transaction
    cites) and the filing-level ``filing_natures`` (every indirect nature the filing states, transactions and
    holdings). The internal-transfer screen links a buy and a sale on them
    (``domain/insider_internal_transfers.py``); the Event Ledger's private-purchase note locates its passage in
    ``txn_footnotes`` (``domain/insider_private_purchases.py``).
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
    footnotes = {
        f.get("id"): text
        for f in root.findall("footnotes/footnote")
        if f.get("id") and (text := _text("".join(f.itertext())))
    }
    natures = _filing_natures(root)  # filing-level -> stamped onto every row below (0055)

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
                # the per-txn ownership evidence (0054) — the joint-report rule's only inputs
                "direct_indirect": _direct_indirect(t),
                "shares_owned_after": _shares_owned_after(t),
                # the filer's own words (0055) — the internal-transfer screen's linking evidence + the
                # private-purchase note's located passage; a fresh list per row (never shared)
                "ownership_nature": _ownership_nature(t),
                "txn_footnotes": _footnotes_cited(t, footnotes),
                "filing_natures": list(natures),
            }
        )
    return txns


class Form4Ingest(NamedTuple):
    """One filing's ingest outcome: rows appended, and transactions the sanity bound refused that were NEWLY
    HELD by this call (``insider_txn_hold``; a row already held counts 0 — it was counted and printed when it
    was first held).

    ``rejected`` exists so a gate can watch the aggregate — it is never the primary report. Every new hold
    also PRINTS in full (accession, insider, sequence, raw date, reason); the earlier Form-4 defect hid
    precisely because a counter was rising and a rising counter reads as normal operation."""

    appended: int
    rejected: int


# --- THE HOLD (migration 0082) ------------------------------------------------------------------------
# A row the rule refuses is written to ``insider_txn_hold`` instead of being dropped: the row as parsed, the
# filing's own words around it (its transaction date text, its period of report, its signature date) and the
# filing date + acceptance the ingest resolved. It is NOT a fact and no corrected date is invented (#3). Since
# rule 2026-11-insider-rows-dated-after-filing the insider READ resolves holds (``domain.insider_holds.
# resolve_holds``): an on-time filing's held Form 4 row counts from its filing night, a late one waits for a
# steward. The ingest writes a hold under the company's write lock, whose release moves the company's change
# stamp (``db.write_lock``); the backfill, which writes outside it, stamps each company itself.


class HoldEvidence(NamedTuple):
    """What the filing itself says around a held row — TEXT, as filed (surrounding whitespace trimmed, an empty
    element = ``None``), never parsed into a date: each of these can itself be a typo (the BBAI filing's
    ``periodOfReport`` is ``0023-03-23`` too), so they are evidence for a later decision, never a correction.
    """

    period_of_report: str | None
    # the LAST ``<ownerSignature><signatureDate>`` when there are several
    signature_date: str | None
    # each non-derivative transaction's date text, in ``parse_form4`` order
    txn_dates: tuple[str | None, ...]


def _as_filed(raw: str | None) -> str | None:
    s = (raw or "").strip()
    return s or None


def hold_evidence(xml: str) -> HoldEvidence:
    """Read a filing's hold evidence from its ownership document. A second, separate read on purpose:
    ``parse_form4``'s rows are exactly what is stored (in ``fact_insider_txn`` and in a 4/A record's ``rows``),
    so nothing new may ride them — and this runs only for a filing with a row to hold."""
    root = ET.fromstring(xml)
    signed = [
        s for el in root.findall("ownerSignature") if (s := _as_filed(el.findtext("signatureDate")))
    ]
    return HoldEvidence(
        period_of_report=_as_filed(root.findtext("periodOfReport")),
        signature_date=signed[-1] if signed else None,
        txn_dates=tuple(
            _as_filed(t.findtext("transactionDate/value"))
            for t in root.findall("nonDerivativeTable/nonDerivativeTransaction")
        ),
    )


def _json_value(v: Any) -> Any:
    """One parsed value as JSON: a date as ISO text, a non-finite number as its text (JSON has no NaN — a held
    row must never cost its filing the ingest), everything else verbatim."""
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    return v


def hold_row(t: Mapping[str, Any], txn_date_as_filed: str | None) -> dict[str, Any]:
    """The ``row`` a hold stores: the parsed row as ``parse_form4`` returned it (dates as ISO text) plus
    ``txn_date_as_filed``, the text of the filing's own ``<transactionDate>`` element."""
    out = {k: _json_value(v) for k, v in t.items()}
    out["txn_date_as_filed"] = txn_date_as_filed
    return out


def write_hold(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    accession: str,
    txn_seq: int,
    form: str,
    reason: str,
    row: Mapping[str, Any],
    evidence: HoldEvidence,
    filed: date | None,
    accepted: datetime | None,
    recorded_at: datetime | None = None,
) -> bool:
    """Append ONE held row in the caller's transaction (no commit here). Returns ``True`` when a row was
    written and ``False`` when this row is already held (``ON CONFLICT DO NOTHING`` on the table's natural key:
    a re-read never grows the table, and its caller prints nothing). ``recorded_at`` as the fact writers take
    it (``None`` = the database's ``now()``)."""
    as_filed = evidence.txn_dates[txn_seq] if 0 <= txn_seq < len(evidence.txn_dates) else None
    stamp = "%(rec)s" if recorded_at is not None else "now()"
    with conn.cursor() as cur:
        cur.execute(
            'INSERT INTO insider_txn_hold (tenant_id, security_id, accession, txn_seq, form, reason, "row", '
            "period_of_report_as_filed, signature_date_as_filed, filed, accepted, recorded_at) "
            "VALUES (%(tenant)s, %(sid)s, %(acc)s, %(seq)s, %(form)s, %(reason)s, %(row)s, %(por)s, "
            f"%(sig)s, %(filed)s, %(accepted)s, {stamp}) "  # noqa: S608 — stamp is one of two literals
            "ON CONFLICT (tenant_id, security_id, accession, txn_seq, form) DO NOTHING RETURNING id",
            {
                "tenant": tenant_id,
                "sid": security_id,
                "acc": accession,
                "seq": txn_seq,
                "form": form,
                "reason": reason,
                "row": Json(hold_row(row, as_filed)),
                "por": evidence.period_of_report,
                "sig": evidence.signature_date,
                "filed": filed,
                "accepted": accepted,
                "rec": recorded_at,
            },
        )
        return cur.fetchone() is not None


def hold_reason_text(
    t: Mapping[str, Any], accession: str, code: str, xml: str, accepted: datetime | None
) -> str:
    """The rule's words for one held row (``implausible_txn_date``), else its code."""
    sig = signature_date_of(xml) if code == HOLD_AFTER_FILING else None
    words = implausible_txn_date(t["txn_date"], accession, accepted=accepted, signature_date=sig)
    return words or code


def hold_line(form: str, accession: str, txn_seq: int, t: Mapping[str, Any], reason: str) -> str:
    """The ONE printed line a new hold gets (the ingest prints it only when ``write_hold`` wrote the row)."""
    kind = "insider txn" if form == HOLD_FORM_4 else f"insider {form} txn"
    return (
        f"  HELD {kind} {accession} seq={txn_seq} "
        f"{t.get('insider_name') or '?'} ({t.get('txn_code') or '?'}): "
        f"transactionDate {t['txn_date'].isoformat()} {reason}. "
        "The filing says this verbatim — the row is HELD (insider_txn_hold), not stored as a fact, and no "
        "corrected date is invented (#3)."
    )


def other_issuer_cik(xml: str, member_cik: str | None) -> str | None:
    """THE OTHER-ISSUER SCREEN (R8-A): the filing's ``issuerCik`` when it is a DIFFERENT company from the
    member (``member_cik``), else ``None``. A company's own submissions feed lists every ownership filing it
    is a party to — including the ones it filed as the REPORTING OWNER of someone else's stock (Honeywell's
    Form 4s on Garrett Motion, Novartis selling Aileron) — and those are not this security's insider flow.
    Keep-when-unknown (#9): no member CIK, or no issuer CIK in the document, is ``None``. A document that does
    not parse raises ``ET.ParseError`` exactly as ``parse_form4`` would (the leg's per-filing tolerance).
    """
    member = _norm_cik(member_cik)
    issuer = _norm_cik(ET.fromstring(xml).findtext("issuer/issuerCik"))
    return issuer if member is not None and issuer is not None and issuer != member else None


def ingest_form4(
    conn: psycopg.Connection,
    security_id: UUID,
    xml: str,
    accession: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
    accepted=None,
    member_cik: str | None = None,
    filed: date | None = None,
) -> Form4Ingest:
    """Parse a Form 4 and append its transactions to ``fact_insider_txn`` (append-only); the caller
    owns the transaction (no commit here). Returns ``(appended, rejected)``.

    ``filed`` is the filing date from the enumeration (the ownership XML carries none). It is EVIDENCE on a
    held row only (``insider_txn_hold.filed``): no stored fact carries it and the sanity bound does not read
    it. ``None`` (a caller that has no filing date, like the seed's fixtures) leaves the column NULL.

    ``accepted`` is the SEC acceptance datetime (the real "disclosed" clock — and, since 2026-10-21, the
    fact's PUBLIC instant on every history view) threaded from the enumeration
    (``submissions.acceptanceDateTime``, checked against the filing date by the leg's
    ``ingest.edgar.acceptance.resolve_acceptance``) — the ownership XML itself carries no acceptance
    datetime, so ``parse_form4`` is UNCHANGED and this rides as a per-filing kwarg like ``recorded_at``.
    ``None`` leaves the column NULL (a history view then counts the fact from when we first held it, the
    display falls back to "ingested" — recall-safe #9). It is FILING-level, stamped identically on every row.

    THE SANITY BOUND (P1): a transaction whose date cannot be true (``implausible_txn_date``) is never stored
    as a fact and never clamped to a guessed date (#3: a clamp fabricates a fact). Since migration 0082 it is
    HELD instead of dropped: written once to ``insider_txn_hold`` (``write_hold``) in this same transaction,
    with the filing's own date text, period of report and signature date. A NEW hold PRINTS the accession, the
    insider, the sequence and the raw date, AND is tallied on the returned ``rejected``; a row already held
    (a re-read of the same filing) writes nothing, prints nothing and counts 0. Because a held filing is now
    listed by ``existing_accessions``, the nightly reads a filing whose every row is held ONCE instead of every
    pass. The operator is told once per held row by the nightly's ``insider_holds`` block
    (``pipeline/insider_hold_scan.py``).

    **The count ACCOMPANIES the per-row print; it never replaces it.** That ordering is the whole lesson of
    the earlier Form-4 bug: a rising skip COUNTER is how a real defect hid in plain sight, because a number
    going up reads as "working as designed". The print is what makes a hold investigable; the count is
    only so a gate can watch the aggregate (it rides ``NameResult.form4_txn_rejected`` into the cron's
    per-thesis summary, which prints it ONLY when nonzero — honest loudness).

    Rejection is per-TRANSACTION, not per-filing: the LSCC filing (``0001437749-24-004874``, committed as
    ``tests/fixtures/edgar/form4_lscc_future_year.xml``) carries one ``2027-02-17`` row beside THREE
    correctly-dated non-derivative ones, and dropping the filing would lose them (#9). (The filing holds six
    transactions in total, but ``parse_form4`` stores the non-derivative ones only — hence three, not five;
    the fixture makes that checkable rather than a claim in prose.)

    THE OTHER-ISSUER SCREEN (R8-A, ``other_issuer_cik``): with ``member_cik`` given, a filing whose
    ``issuerCik`` is a DIFFERENT company stores NOTHING (``(0, 0)``) — the nightly leg checks it first and
    records a ``form4_other_issuer`` skip; this is the writer's own guard. The rows stored before the screen
    existed are kept and screened at read (``domain.insider_amendments``).
    """
    count = 0
    rejected = 0
    if other_issuer_cik(xml, member_cik) is not None:
        return Form4Ingest(appended=0, rejected=0)
    rows = parse_form4(xml)
    # {txn_seq: reason code} — the rows the sanity bound refuses (since 2026-11-insider-rows-dated-after-filing
    # also a row dated after its own filing, judged on ``accepted``)
    held = filing_held_rows(rows, accession, xml, accepted)
    # the filing's own words around a held row — read only for a filing with a row to hold
    evidence = hold_evidence(xml) if held else HoldEvidence(None, None, ())
    for i, t in enumerate(rows):
        if t["txn_date"] is None:
            continue
        if i in held:
            # HELD, never stored as a fact (0082). Counted and printed only when the row is NEW to the hold
            # table, so a re-read of the same filing says nothing twice.
            if write_hold(
                conn,
                tenant_id=tenant_id,
                security_id=security_id,
                accession=accession,
                txn_seq=i,
                form=HOLD_FORM_4,
                reason=held[i],
                row=t,
                evidence=evidence,
                filed=filed,
                accepted=accepted,
                recorded_at=recorded_at,
            ):
                rejected += 1
                print(
                    hold_line(
                        HOLD_FORM_4,
                        accession,
                        i,
                        t,
                        hold_reason_text(t, accession, held[i], xml, accepted),
                    )
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
            # the ownership evidence (migration 0054) — NULL on rows ingested before it; the stored history
            # is repaired by pipeline.repair_insider_ownership. A NULL row never merges as a joint report.
            "direct_indirect": t["direct_indirect"],
            "shares_owned_after": t["shares_owned_after"],
            # the filer's own words (migration 0055) — NULL on rows ingested before it; the stored history is
            # repaired by pipeline.repair_insider_filer_text. A NULL filing_natures links nothing as an
            # internal transfer (never screened on missing evidence).
            "ownership_nature": t["ownership_nature"],
            "txn_footnotes": t["txn_footnotes"],
            "filing_natures": t["filing_natures"],
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
    """The Form-4 accessions already HANDLED for (tenant, security) — so the per-thesis ingest can SKIP a
    filing it already has and re-ingest ONLY new ones. Accession is the right grain: it is the filing
    identity and the lead column of the insider natural key, so "accession present" ⇔ "its txns stored".
    A re-run of an already-ingested name therefore appends NOTHING (the append-only table never silently
    grows).

    Since migration 0082 that includes an accession with a Form 4 HOLD (``insider_txn_hold``, form ``'4'``):
    a filing whose EVERY row the sanity bound refuses stores no fact, and before the hold existed it was
    re-read and re-printed on every pass. The ingest writes a filing's holds in the same transaction as its
    good rows, so "a hold is present" also means "its good rows are stored". A Form 4/A hold is not listed:
    the 4/A leg has its own skip (``form4a.existing_amendment_accessions``)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT accession FROM fact_insider_txn WHERE tenant_id = %(t)s AND security_id = %(s)s "
            "UNION "
            "SELECT accession FROM insider_txn_hold "
            "WHERE tenant_id = %(t)s AND security_id = %(s)s AND form = %(form)s",
            {"t": tenant_id, "s": security_id, "form": HOLD_FORM_4},
        )
        return {r["accession"] for r in cur.fetchall()}
