"""The scoring-fact FILING-PARSER (Slice hybrid-1) — the three-tier hybrid extractor.

Given a company's SEC ``companyfacts`` + its latest 10-Q/10-K text, produce candidate scoring facts
(``domain.extraction.ExtractedFact``) for the three meters:

- **shares_outstanding** (market cap): a single, current cover-share concept -> ``AUTO``; dual-class or an
  absent/stale concept -> ``FLAG`` (best-effort cover regex for the A+B sum + locate the cover).
- **cash_burn** (runway): cash = cash+equiv (+ marketable securities); a clean ~quarter of operating cash
  flow -> ``AUTO``; a year-to-date column -> ``FLAG`` (derive the quarter); a large one-time reconciliation
  line, or marketable-securities present (filer-specific tag basis) -> ``FLAG`` (raw + locate).
- **revenue_mix** (purity): ALWAYS ``HUMAN`` — locate the segment footnote (revenue names) or Item-1
  (pre-revenue); NEVER auto-valued (purity is the operator's exposure-concentration edge).

Deterministic. The detectors read ``companyfacts``; the located passages are deterministic keyword/section
retrieval over the filing text (reusing ``clean_filing_text``). Dials live in ``ExtractorConfig`` (no magic
numbers). **The extractor never DECIDES** — it auto-fills the clean cases and locates+flags the rest; the
operator ratifies (hybrid-2). companyfacts alone is insufficient (stale/absent dual-class shares, segment
dimensions, filer-specific marketable-securities tags), so the located passages point INTO the filing.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, LocatedPassage, Tier
from domain.settings import get_settings
from ingest.edgar.client import EdgarClient
from ingest.edgar.converts import clean_filing_text
from ingest.edgar.submissions import fetch_submissions, filings_of

# ---------------------------------------------------------------------------------------------------------
# companyfacts helpers
# ---------------------------------------------------------------------------------------------------------


def _rows(facts: dict, tax: str, concept: str, unit: str) -> list[dict]:
    node = facts.get(tax, {}).get(concept)
    return node.get("units", {}).get(unit, []) if node else []


def _first(facts: dict, candidates: list[tuple[str, str]], unit: str) -> list[dict]:
    """The first candidate concept that has any rows (us-gaap tags vary by filer)."""
    for tax, concept in candidates:
        r = _rows(facts, tax, concept, unit)
        if r:
            return r
    return []


def _days(row: dict) -> int:
    s, e = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
    return (e - s).days


def _latest_instant(facts: dict, candidates: list[tuple[str, str]]) -> float | None:
    """The latest balance-sheet value for the first present concept (instant fact = no span)."""
    rows = _first(facts, candidates, "USD")
    if not rows:
        return None
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"])


def _value_for_period(facts: dict, concept: str, start: str, end: str) -> float | None:
    for r in _rows(facts, "us-gaap", concept, "USD"):
        if r.get("start") == start and r.get("end") == end:
            return float(r["val"])
    return None


# ---------------------------------------------------------------------------------------------------------
# located-passage retrieval (deterministic — keyword/section, never interpretation)
# ---------------------------------------------------------------------------------------------------------


def _locate(
    text: str, source_ref: str, kind: str, anchors: list[str], window: int = 110
) -> LocatedPassage | None:
    """The first anchor found in the (cleaned) filing text -> a passage with a deterministic excerpt."""
    low = text.lower()
    for a in anchors:
        i = low.find(a.lower())
        if i >= 0:
            excerpt = re.sub(r"\s+", " ", text[max(0, i - window) : i + window]).strip()
            return LocatedPassage(
                kind=kind, source_ref=source_ref, anchor=a, excerpt=f"… {excerpt} …"
            )
    return None


# A FINANCIAL figure — either $-prefixed ("$ 2,350,090") or comma-grouped ("853,070"). Used to RANK candidate
# segment passages toward the actual revenue TABLE. CRUCIALLY it does NOT match bare integers — years (2024),
# CIKs (0001921865), or dates — because the filing text carries a huge inline-XBRL context block (dimension
# members + dates + CIK) that a plain digit-run regex scores as ultra-dense, burying the real table (this is
# exactly why ASPI declined: all top windows were XBRL noise). A revenue table uses thousands separators / $;
# the XBRL dump does not — so requiring one of those is the discriminator. Fail-open unchanged: no financial
# window -> the earliest hit -> the seam honestly declines.
_SEG_NUM_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _segment_passages(
    text: str, source_ref: str, anchors: list[str], window: int, keep: int = 3
) -> list[LocatedPassage]:
    """The segment passages MOST LIKELY to carry the revenue table — windows around segment anchors, RANKED by
    how many revenue figures each contains (a table is number-dense; the intro's 'segment' mentions are not).
    Returns up to ``keep`` numeric windows so the grounded purity seam sees the real segment $ / total $, even
    though 'segment' first appears in boilerplate. FAIL-OPEN: if no window has figures, return the earliest hit
    (the seam then honestly declines). Deterministic retrieval — never a reading."""
    low = text.lower()
    scored: list[tuple[int, int, str, str]] = []
    taken: list[int] = []
    for a in anchors:
        al, s = a.lower(), 0
        while (i := low.find(al, s)) >= 0:
            s = i + len(al)
            if any(abs(i - j) < window for j in taken):
                continue  # overlaps a window already taken — skip the near-duplicate
            taken.append(i)
            excerpt = re.sub(r"\s+", " ", text[max(0, i - window) : i + window]).strip()
            scored.append((len(_SEG_NUM_RE.findall(excerpt)), i, excerpt, a))
    if not scored:
        return []
    scored.sort(key=lambda t: (-t[0], t[1]))  # most figures first, then earliest
    top = [t for t in scored if t[0] > 0][:keep] or scored[
        :1
    ]  # numeric windows, else the earliest hit
    return [
        LocatedPassage(kind="segment", source_ref=source_ref, anchor=a, excerpt=f"… {e} …")
        for _, _, e, a in top
    ]


# ---------------------------------------------------------------------------------------------------------
# shares (market cap)
# ---------------------------------------------------------------------------------------------------------

# a per-class share count on the cover: a 6+ digit number within a short span of "Class X", in either
# order — "346,105,785 Class A common shares" (SMR) or "18,953,594 shares of the registrant's Class A
# Common Stock" (LEU). Scoped to the COVER (before "PART I") so financial-statement share figures don't
# leak in. ``[^.\d]`` keeps the match inside one phrase (no crossing a period or another number).
_COVER_CLASS_RE = re.compile(r"([\d,]{6,})\b[^.\d]{0,40}?\bClass\s+([A-Za-z])\b", re.IGNORECASE)


def _cover_class_sum(text: str) -> float | None:
    """Sum the distinct per-class share counts from the COVER. The A/B split is voting, not economics —
    total economic = the sum. Returns None (not a guess) if the cover doesn't yield >= 2 classes, so a FLAG
    never anchors the operator to a wrong number."""
    cut = text.lower().find("part i")
    cover = text[: cut if cut > 0 else 16000]
    by_class: dict[str, float] = {}
    for m in _COVER_CLASS_RE.finditer(cover):
        by_class.setdefault(m.group(2).upper(), float(m.group(1).replace(",", "")))
    return sum(by_class.values()) if len(by_class) >= 2 else None


def _shares(facts: dict, tenq_text: str, ref: str, period_end: date) -> ExtractedFact:
    rows = _rows(facts, "dei", "EntityCommonStockSharesOutstanding", "shares")
    if rows:
        latest_end = max(r["end"] for r in rows)
        vals = sorted({float(r["val"]) for r in rows if r["end"] == latest_end})
        # AUTO only when the cover concept is single-class AND current (the cover dates near the period)
        if len(vals) == 1 and date.fromisoformat(latest_end) >= period_end:
            return ExtractedFact(
                fact_type="shares_outstanding",
                tier=Tier.AUTO,
                value=vals[0],
                source="10-q-cover",
                source_ref=ref,
                event_date=date.fromisoformat(latest_end),
                note=f"Cover-page shares outstanding as of {latest_end} (single class).",
            )
    # dual-class, absent, or stale -> FLAG: best-effort cover A+B + locate the cover
    total = _cover_class_sum(tenq_text)
    passage = _locate(tenq_text, ref, "cover", ["shares of Class", "Class A", "outstanding"])
    return ExtractedFact(
        fact_type="shares_outstanding",
        tier=Tier.FLAG,
        value=total,
        source="10-q-cover",
        source_ref=ref,
        event_date=period_end,
        flags=["dual-class"],
        located_passages=[p for p in [passage] if p],
        note="Multiple share classes / companyfacts stale or absent — total economic = sum of all classes; "
        "confirm against the cover (Class B is economic common; the A/B split is voting).",
    )


# ---------------------------------------------------------------------------------------------------------
# cash + burn (runway)
# ---------------------------------------------------------------------------------------------------------

_CASH = [
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"),
]
_STI = [
    ("us-gaap", "ShortTermInvestments"),
    ("us-gaap", "MarketableSecuritiesCurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesCurrent"),
]
_LTI = [
    ("us-gaap", "LongTermInvestments"),
    ("us-gaap", "MarketableSecuritiesNoncurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesNoncurrent"),
]
_OCF = "NetCashProvidedByUsedInOperatingActivities"
# routine working-capital changes — NORMAL operations, never a one-time item (inventory swings, trade
# receivables/payables, deferred revenue, prepaids, leases). These can be LARGE relative to a small burn
# (LEU's inventory swing is 139% of its op-cash-use) yet are not one-time — excluded by CATEGORY, not size.
_ROUTINE_WC = (
    "inventor",
    "receivable",
    "deferredrevenue",
    "contractwithcustomer",
    "prepaid",
    "otheroperatingcapital",
    "operatinglease",
    "accountspayable",
)


def _is_routine_wc(concept: str) -> bool:
    c = concept.lower()
    if "accrued" in c or "settlement" in c or "milestone" in c:
        return False  # an accrued/settlement line can carry a one-time obligation (SMR's ENTRA1)
    return any(s in c for s in _ROUTINE_WC)


def _is_one_time_candidate(concept: str) -> bool:
    """A NON-ROUTINE operating-activities reconciliation line — an accrued/settlement change, not routine
    working capital and not a non-cash add-back (SBC/D&A don't start with ``IncreaseDecreaseIn``). This is
    where a one-time settlement PAYMENT lands (SMR's ENTRA1 in AccountsPayableAndAccruedLiabilities).
    """
    return concept.startswith("IncreaseDecreaseIn") and not _is_routine_wc(concept)


def _quarter(facts: dict, cfg: ExtractorConfig) -> tuple[float, tuple[str, str], bool] | None:
    """The latest ~quarter of operating cash flow. Returns (value, (start,end), ytd_derived). companyfacts
    cash-flow is often a YEAR-TO-DATE column; when the latest span exceeds a quarter we DERIVE the quarter
    (YTD - the prior YTD of the same fiscal year)."""
    durations = [r for r in _rows(facts, "us-gaap", _OCF, "USD") if r.get("start")]
    if not durations:
        return None
    latest_end = max(r["end"] for r in durations)
    row = min(
        (r for r in durations if r["end"] == latest_end), key=_days
    )  # shortest span at latest end
    if _days(row) <= cfg.quarterly_span_max_days:
        return float(row["val"]), (row["start"], row["end"]), False
    prior = max(
        (r for r in durations if r["start"] == row["start"] and r["end"] < row["end"]),
        key=lambda r: r["end"],
        default=None,
    )
    if prior is None:
        return (
            float(row["val"]),
            (row["start"], row["end"]),
            True,
        )  # can't derive; flag YTD, raw value
    return float(row["val"]) - float(prior["val"]), (prior["end"], row["end"]), True


def _detect_one_time(
    facts: dict, text: str, ref: str, start: str, end: str, op_cash_use: float, cfg: ExtractorConfig
) -> LocatedPassage | None:
    """A NON-ROUTINE operating line whose magnitude is a large fraction of |op-cash-use| is anomalous ->
    flag. Generic (never names a specific item); the filing text supplies the located passage — the flagged
    AMOUNT in the cash-flow statement (or a one-time keyword). The operator decides whether to back it out.
    """
    threshold = cfg.one_time_line_fraction * abs(op_cash_use)
    flagged: float | None = None
    for concept in facts.get("us-gaap", {}):
        if not _is_one_time_candidate(concept):
            continue
        v = _value_for_period(facts, concept, start, end)
        if v is not None and abs(v) >= threshold:
            flagged = v
            break
    if flagged is None:
        return None
    n = abs(int(flagged))
    amounts = [
        f"{n:,}",
        f"{n // 1000:,}",
        f"{n / 1e6:.1f}",
    ]  # the line, as full $, thousands, or $M
    return (
        _locate(text, ref, "cash-flow", amounts)
        or _locate(text, ref, "cash-flow", list(cfg.one_time_keywords))
        or LocatedPassage(
            kind="cash-flow",
            source_ref=ref,
            anchor="operating cash flow",
            excerpt="… a single non-routine operating line dominates operating cash use — review the "
            "cash-flow statement for a one-time item …",
        )
    )


def _cash_burn(
    facts: dict, tenq_text: str, ref: str, period_end: date, cfg: ExtractorConfig
) -> ExtractedFact:
    cash = _latest_instant(facts, _CASH) or 0.0
    marketable = (_latest_instant(facts, _STI) or 0.0) + (_latest_instant(facts, _LTI) or 0.0)
    cash_usd = cash + marketable

    q = _quarter(facts, cfg)
    flags: list[str] = []
    passages: list[LocatedPassage] = []
    if q is None:
        burn = 0.0
    else:
        qval, (start, end), ytd = q
        burn = -qval  # quarterly_burn_usd is POSITIVE when burning (op-cash-use is negative)
        if ytd:
            flags.append("ytd-derived")
            passages.append(
                LocatedPassage(
                    kind="cash-flow",
                    source_ref=ref,
                    anchor="year-to-date",
                    excerpt="… companyfacts reports a year-to-date cash-flow column; the quarter is derived "
                    "(YTD − prior period). Confirm the period basis …",
                )
            )
        ot = _detect_one_time(facts, tenq_text, ref, start, end, qval, cfg)
        if ot:
            flags.append("possible-one-time")
            passages.append(ot)
    if marketable > 0:
        flags.append("verify-marketable-securities")
        bs = _locate(
            tenq_text,
            ref,
            "balance-sheet",
            ["marketable securities", "short-term investments", "investments"],
        )
        if bs:
            passages.append(bs)

    tier = Tier.FLAG if flags else Tier.AUTO
    note = (
        f"cash_usd = cash+equivalents (${cash/1e6:.1f}M) + marketable securities (${marketable/1e6:.1f}M); "
        f"quarterly burn = operating cash use. "
        + (
            "FLAGS: " + ", ".join(flags) + " — confirm against the filing."
            if flags
            else "Clean quarter, no marketable-securities basis question."
        )
    )
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        cash_usd=cash_usd,
        quarterly_burn_usd=burn,
        source="10-q",
        source_ref=ref,
        event_date=period_end,
        flags=flags,
        located_passages=passages,
        note=note,
    )


# ---------------------------------------------------------------------------------------------------------
# purity (revenue mix) — ALWAYS HUMAN: locate, never value
# ---------------------------------------------------------------------------------------------------------

_REVENUE = [
    ("us-gaap", "Revenues"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
]

# The purity passage is WIDE (vs the default ±110): the grounded purity-estimate seam (SURFACE 1b) proposes a %
# ONLY from segment $ / total $ figures in the passage, so the located window must be big enough to carry the
# segment revenue TABLE, not just the heading. A retrieval window (not a scoring cutoff); a too-narrow window
# just means the seam can't ground it and honestly declines (fail-open to HUMAN) — never a wrong number.
_PURITY_WINDOW = 1500


def _has_annual_revenue(facts: dict) -> bool:
    rows = _first(facts, _REVENUE, "USD")
    return any((r.get("start") and _days(r) > 300 and float(r["val"]) > 0) for r in rows)


def _purity(facts: dict, tenk_text: str, ref: str, period_end: date) -> ExtractedFact:
    """Purity is interpretation-bound — the extractor LOCATES the evidence and the operator authors the %.
    Revenue names: the segment footnote; pre-revenue names: the Item-1 business description. Never valued.
    """
    if _has_annual_revenue(facts):
        # RANK segment windows by revenue-figure density (not first-match) so the actual segment TABLE — not
        # the intro's "segment" boilerplate — is what the grounded purity seam reads.
        passages = _segment_passages(
            tenk_text, ref, ["reportable segment", "segment", "revenue by"], _PURITY_WINDOW
        )
        source, why = (
            "10-k-segment",
            "Revenue reported — the theme % is the largest theme segment of total revenue (segment table located).",
        )
    else:
        passage = _locate(
            tenk_text,
            ref,
            "business-description",
            ["Item 1.", "Business", "We are", "Overview"],
            window=_PURITY_WINDOW,
        )
        passages = [passage] if passage else []
        source, why = (
            "10-k-business-description",
            "Pre-revenue — purity is a business-description read (Item-1 located).",
        )
    return ExtractedFact(
        fact_type="revenue_mix",
        tier=Tier.HUMAN,
        value=None,
        source=source,
        source_ref=ref,
        event_date=period_end,
        located_passages=passages,
        note=why
        + " The extractor never proposes a purity number — the operator authors it from the evidence.",
    )


# ---------------------------------------------------------------------------------------------------------
# the pure core + the live wrapper
# ---------------------------------------------------------------------------------------------------------


def extract_facts(
    companyfacts: dict[str, Any],
    tenq_text: str,
    tenk_text: str,
    *,
    tenq_ref: str,
    tenk_ref: str,
    tenq_date: date,
    tenk_date: date,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> list[ExtractedFact]:
    """Pure, deterministic: (companyfacts + the two filing texts) -> the three candidate facts. Testable
    offline against cached fixtures (the form4-parser precedent — the caller owns fetching)."""
    facts = companyfacts["facts"]
    return [
        _purity(facts, tenk_text, tenk_ref, tenk_date),
        _shares(facts, tenq_text, tenq_ref, tenq_date),
        _cash_burn(facts, tenq_text, tenq_ref, tenq_date, cfg),
    ]


def _doc_url(cik: int, accession: str, primary_doc: str) -> str:
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"{get_settings().sec_archives_base}/{cik}/{accession.replace('-', '')}/{doc}"


def companyfacts_url(cik: str | int) -> str:
    """The SEC XBRL companyfacts JSON endpoint for a CIK (data.sec.gov)."""
    return f"{get_settings().sec_data_base}/api/xbrl/companyfacts/CIK{int(cik):010d}.json"


def _latest_filing(client: EdgarClient, cik: int, form: str) -> tuple[str, str, date] | None:
    subs = fetch_submissions(client, cik)
    hits = filings_of(subs, form)
    if not hits:
        return None
    f = hits[0]
    url = _doc_url(cik, f["accession"], f["primary_doc"])
    text = clean_filing_text(
        client.get_text(url, f"forms/{f['accession']}/{f['primary_doc'].rsplit('/', 1)[-1]}")
    )
    return url, text, date.fromisoformat(f["filed"])


def extract_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> list[ExtractedFact]:
    """Live (cache-first) wrapper: CIK -> companyfacts + the latest 10-Q + the latest 10-K -> candidates.
    An EXPLICIT operator action (the extract endpoint), never auto-fired on a render."""
    cik = int(cik)
    cf = client.get_json(
        companyfacts_url(cik),
        f"companyfacts/CIK{cik:010d}.json",
    )
    tenq = _latest_filing(client, cik, "10-Q")
    tenk = _latest_filing(client, cik, "10-K")
    if tenq is None or tenk is None:
        return []
    return extract_facts(
        cf,
        tenq[1],
        tenk[1],
        tenq_ref=tenq[0],
        tenk_ref=tenk[0],
        tenq_date=tenq[2],
        tenk_date=tenk[2],
        cfg=cfg,
    )


__all__ = ["extract_facts", "extract_for_security", "ExtractedFact"]
