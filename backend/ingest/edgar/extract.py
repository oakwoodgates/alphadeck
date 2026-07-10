"""The scoring-fact FILING-PARSER (Slice hybrid-1) — the three-tier hybrid extractor.

Given a company's SEC ``companyfacts`` + its latest 10-Q/10-K text, produce candidate scoring facts
(``domain.extraction.ExtractedFact``) for the three meters:

- **shares_outstanding** (market cap): a single, CURRENT cover-share concept -> ``AUTO`` ("current" is
  judged against the filing's PERIOD OF REPORT — never the filing date, which every cover predates).
  Otherwise ``FLAG`` with the label naming the OBSERVED condition: ``dual-class`` (>1 distinct DEI values
  on the latest cover date, or >=2 per-class counts parsed from the cover — the A+B sum offered),
  ``stale-cover`` (a single count older than the period — offered, dated by its own as-of date), or
  ``no-companyfacts`` (nothing observed — located-only). One condition, one label; never a catch-all.
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


def _latest_instant(facts: dict, candidates: list[tuple[str, str]]) -> tuple[float, str] | None:
    """The latest balance-sheet (value, as-of end date) for the first present concept (instant fact = no
    span). The DATE rides along so the cash composer can STATE every input's as-of and detect a lagging
    companyfacts (the shares ``stale-cover`` rule, applied to cash) — the bare-value version silently
    composed instants of unknown, possibly mixed dates."""
    rows = _first(facts, candidates, "USD")
    if not rows:
        return None
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"]), end


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
    latest_end = max(r["end"] for r in rows) if rows else None
    vals = sorted({float(r["val"]) for r in rows if r["end"] == latest_end}) if rows else []
    # AUTO only when the cover concept is single-class AND current. ``period_end`` is the filing's
    # PERIOD OF REPORT: a 10-Q cover is dated "as of the latest practicable date" — AFTER the period
    # end, BEFORE the filing date — so a current cover always passes this gate. (Comparing against the
    # FILING date instead made AUTO unreachable on live data: every single-class name fell through and
    # wore the old catch-all "dual-class" flag. MU: cover 06-17 vs filed 06-25 vs period 05-28.)
    if latest_end is not None and len(vals) == 1 and date.fromisoformat(latest_end) >= period_end:
        return ExtractedFact(
            fact_type="shares_outstanding",
            tier=Tier.AUTO,
            value=vals[0],
            source="10-q-cover",
            source_ref=ref,
            event_date=date.fromisoformat(latest_end),
            note=f"Cover-page shares outstanding as of {latest_end} (single class).",
        )
    # FLAG — three DISTINCT observed conditions get three HONEST labels (#6: a flag is evidence, so it
    # must name what was OBSERVED; the old single catch-all stamped "dual-class" whichever branch fired,
    # which lied on every single-class name once the date gate broke):
    total = _cover_class_sum(
        tenq_text
    )  # >= 2 per-class counts located on the COVER text, else None
    passage = _locate(tenq_text, ref, "cover", ["shares of Class", "Class A", "outstanding"])
    located = [p for p in [passage] if p]
    if len(vals) > 1 or total is not None:
        # multiple classes OBSERVED — either >1 distinct DEI values on the latest cover date, or >=2
        # per-class counts parsed from the cover itself. (Dual-class filers usually report DEI per class
        # with dimension members that companyfacts DROPS — so "no dei rows + a class-rich cover" is the
        # common dual-class shape: LEU/SMR in the golden seed.)
        return ExtractedFact(
            fact_type="shares_outstanding",
            tier=Tier.FLAG,
            value=total,
            source="10-q-cover",
            source_ref=ref,
            event_date=date.fromisoformat(latest_end) if latest_end else period_end,
            flags=["dual-class"],
            located_passages=located,
            note="Multiple share classes observed — total economic = sum of all classes; "
            "confirm against the cover (Class B is economic common; the A/B split is voting).",
        )
    if vals:
        # a single-class count whose as-of date PREdates the filing's period — a lagging companyfacts,
        # not a class structure. Offer the stale value honestly, dated by ITS OWN as-of date (valid-time
        # honesty); the operator confirms currency against the located cover.
        return ExtractedFact(
            fact_type="shares_outstanding",
            tier=Tier.FLAG,
            value=vals[0],
            source="10-q-cover",
            source_ref=ref,
            event_date=date.fromisoformat(latest_end) if latest_end else period_end,
            flags=["stale-cover"],
            located_passages=located,
            note=f"Single-class cover count as of {latest_end} — OLDER than the filing period end "
            f"({period_end}); confirm currency against the located cover.",
        )
    return ExtractedFact(
        fact_type="shares_outstanding",
        tier=Tier.FLAG,
        value=None,
        source="10-q-cover",
        source_ref=ref,
        event_date=period_end,
        flags=["no-companyfacts"],
        located_passages=located,
        note="companyfacts has no cover-shares concept and the cover yielded no per-class counts — "
        "author the count from the located cover.",
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


def _quarter(facts: dict, cfg: ExtractorConfig) -> tuple[float, tuple[str, str], str] | None:
    """The latest ~quarter of operating cash flow: (value, (start, end), basis). ``basis`` names what the
    value IS — one condition, one label (the runway audit: the old bool lumped "derived" together with
    "couldn't derive", so a RAW year-to-date figure went out wearing the ytd-derived label and a passage
    claiming a derivation that never happened — runway would read ~3-4x too short):

    - ``"quarter"`` — a native quarterly column (span <= quarterly_span_max_days).
    - ``"derived"`` — the quarter computed as YTD − the prior same-start YTD.
    - ``"ytd-raw"`` — a long-span column with NO prior to subtract: the value IS the raw YTD, not a quarter.
    """
    durations = [r for r in _rows(facts, "us-gaap", _OCF, "USD") if r.get("start")]
    if not durations:
        return None
    latest_end = max(r["end"] for r in durations)
    row = min(
        (r for r in durations if r["end"] == latest_end), key=_days
    )  # shortest span at latest end
    if _days(row) <= cfg.quarterly_span_max_days:
        return float(row["val"]), (row["start"], row["end"]), "quarter"
    prior = max(
        (r for r in durations if r["start"] == row["start"] and r["end"] < row["end"]),
        key=lambda r: r["end"],
        default=None,
    )
    if prior is None:
        return float(row["val"]), (row["start"], row["end"]), "ytd-raw"
    return float(row["val"]) - float(prior["val"]), (prior["end"], row["end"]), "derived"


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
    """Cash + quarterly burn. A FLAG marks an EXCEPTION needing judgment (the re-tier — honest loudness:
    ``ytd-derived`` fired on ~3 of 4 filings [GAAP 10-Q cash-flow statements are YTD] and
    ``verify-marketable-securities`` on ~every real filer, so AUTO was structurally empty and the flags
    carried no information): a raw YTD that COULDN'T be derived is ``ytd-raw`` (never claimed derived);
    an anomalous line is ``possible-one-time``; an instant older than the filing period is ``stale-cash``;
    a missing input is its own flag with a **None** value — never a fake $0 cash or a fake $0 burn (which
    ratified into a fake "cash-generative"). COMPOSITION is provenance, not an alarm: a cleanly-derived
    quarter (YTD − prior YTD — reproducible arithmetic on two filed columns, the market-cap trust class)
    and the marketable-securities basis (same-dated included / off-date excluded, which errs CONSERVATIVE —
    understated cash reads runway shorter) ride the NOTE and stay AUTO. Every input's as-of rides the note.
    """
    cash_at = _latest_instant(facts, _CASH)
    sti_at = _latest_instant(facts, _STI)
    lti_at = _latest_instant(facts, _LTI)
    q = _quarter(facts, cfg)

    # nothing observed anywhere -> a located-only FLAG (the shares no-companyfacts treatment). The old
    # `or 0.0` coercions sent a no-data filer out AUTO / $0 cash / $0 burn / "Clean quarter" — a
    # confirmable fake zero, one tier WORSE than the shares bug because AUTO invites confirm-as-is.
    if cash_at is None and q is None:
        locs = [
            _locate(
                tenq_text,
                ref,
                "balance-sheet",
                ["cash and cash equivalents", "total current assets"],
            ),
            _locate(tenq_text, ref, "cash-flow", ["operating activities", "cash flows"]),
        ]
        return ExtractedFact(
            fact_type="cash_burn",
            tier=Tier.FLAG,
            cash_usd=None,
            quarterly_burn_usd=None,
            source="10-q",
            source_ref=ref,
            event_date=period_end,
            flags=["no-companyfacts"],
            located_passages=[p for p in locs if p],
            note="companyfacts has neither a cash instant nor an operating-cash-flow column for this "
            "filer — author cash + quarterly burn from the located statements.",
        )

    flags: list[str] = []
    passages: list[LocatedPassage] = []
    # A balance sheet is ONE date: marketable instants join the sum ONLY when they carry the SAME as-of
    # as cash. An off-date instant is not "stale, confirm it" — it's a DIFFERENT balance sheet (usually a
    # discontinued tag: MU's AvailableForSaleSecurities* last reported 2018 — the old bare-value composer
    # silently added those eight-year-old balances into current cash). Excluded + named, never summed.
    mk_included = [x for x in (sti_at, lti_at) if x and cash_at and x[1] == cash_at[1]]
    mk_offdate = [x for x in (sti_at, lti_at) if x and (not cash_at or x[1] != cash_at[1])]
    marketable = sum(x[0] for x in mk_included)
    cash_usd = (cash_at[0] + marketable) if cash_at else None
    asofs: list[str] = []
    if cash_at:
        asofs.append(f"cash as of {cash_at[1]}")
    if mk_included:
        asofs.append(f"marketable as of {mk_included[0][1]}")
    if mk_offdate:
        asofs.append(
            "marketable tags dated "
            + ", ".join(sorted({x[1] for x in mk_offdate}))
            + " ≠ the cash date — EXCLUDED from the sum (likely discontinued tags; verify where "
            "current investments live)"
        )

    burn: float | None = None
    basis_suffix = (
        ""  # the derivation basis, stated in the note (composition = provenance, not a flag)
    )
    if q is None:
        # cash present but NO operating-cash-flow column: burn=0 here used to ratify straight into a
        # fake "cash-generative" (top-pip runway) on zero evidence — burn stays None, its own flag.
        flags.append("no-cashflow-column")
        cf = _locate(tenq_text, ref, "cash-flow", ["operating activities", "cash flows"])
        if cf:
            passages.append(cf)
    else:
        qval, (start, end), basis = q
        burn = -qval  # quarterly_burn_usd is POSITIVE when burning (op-cash-use is negative)
        asofs.append(f"burn over {start} → {end}")
        if basis == "derived":
            # a clean derivation (both YTD columns on file, quarter = YTD − prior YTD) is reproducible
            # arithmetic, not a judgment fork — stated, never alarmed. As a FLAG it fired on ~3 of 4
            # filings (GAAP 10-Qs report cash flow YTD), marking the RULE instead of the exception.
            basis_suffix = ", derived (YTD − prior YTD)"
        elif basis == "ytd-raw":
            flags.append("ytd-raw")
            basis_suffix = " — the RAW year-to-date, NOT a quarter (see the flag)"
            passages.append(
                LocatedPassage(
                    kind="cash-flow",
                    source_ref=ref,
                    anchor="year-to-date",
                    excerpt="… companyfacts carries ONLY a year-to-date cash-flow column and no prior "
                    "period to subtract — this value IS the year-to-date operating cash use, NOT a "
                    "quarter. Derive the quarter manually (or enter the YTD basis deliberately) …",
                )
            )
        ot = _detect_one_time(facts, tenq_text, ref, start, end, qval, cfg)
        if ot:
            flags.append("possible-one-time")
            passages.append(ot)
    if cash_at is None:
        # a burn column but NO cash instant — runway can't compute; offer the burn, name the miss
        flags.append("no-cash-instant")
        bs = _locate(tenq_text, ref, "balance-sheet", ["cash and cash equivalents"])
        if bs:
            passages.append(bs)
    # a lagging companyfacts: the cash balance sheet predates the filing's period end (stale-cover, for
    # cash — included marketable share cash's date by construction)
    if cash_at and date.fromisoformat(cash_at[1]) < period_end:
        flags.append("stale-cash")
    # NO marketable-securities flag (the re-tier): same-dated inclusion is the textbook liquidity
    # composition and off-date exclusion errs conservative (cash understated -> runway reads SHORTER) —
    # both are stated in the note's as-ofs above; the alarm, when it matters, is the meter reading short.

    tier = Tier.FLAG if flags else Tier.AUTO
    cash_part = (
        f"cash_usd = cash+equivalents (${cash_at[0] / 1e6:.1f}M) + marketable securities "
        f"(${marketable / 1e6:.1f}M)"
        if cash_at
        else "cash: NOT FOUND in companyfacts (author it from the located balance sheet)"
    )
    burn_part = (
        f"quarterly burn = operating cash use{basis_suffix}"
        if burn is not None
        else "burn: NOT FOUND (no operating-cash-flow column)"
    )
    note = (
        f"{cash_part}; {burn_part}"
        + (f" [{'; '.join(asofs)}]" if asofs else "")
        + ". "
        + (
            "FLAGS: " + ", ".join(flags) + " — confirm against the filing."
            if flags
            else "No attention flags."
        )
    )
    # the value's OWN as-of (the #132 event-date rule): the burn period end, else the cash instant's end
    evt = (
        date.fromisoformat(q[1][1])
        if q is not None
        else (date.fromisoformat(cash_at[1]) if cash_at else period_end)
    )
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        cash_usd=cash_usd,
        quarterly_burn_usd=burn,
        source="10-q",
        source_ref=ref,
        event_date=evt,
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
    # The date threaded to the extractors is the PERIOD OF REPORT (quarter/year end), NOT the filing
    # date — their staleness gates and event_date stamps are period semantics. Threading the filing
    # date here made the shares AUTO gate unreachable live (a cover's "as of" date is always BEFORE
    # the filing date), mis-flagging every single-class name "dual-class". Falls back to the filing
    # date only when a submissions row carries no reportDate.
    return url, text, date.fromisoformat(f.get("report_date") or f["filed"])


def extract_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> list[ExtractedFact]:
    """Live (cache-first) wrapper: CIK -> companyfacts + the latest 10-Q and/or 10-K -> candidates.
    An EXPLICIT operator action (the extract endpoint), never auto-fired on a render.

    COVERAGE: an empty list means the issuer has NO 10-K/10-Q at all — a foreign private issuer files
    20-F/6-K, which this extractor doesn't parse — so the caller can surface an honest "not covered"
    message instead of a silent empty rail (and the FE stops calling an empty result "data ready").

    BOTH-FORMS RELAXATION: it no longer requires BOTH forms. Each role falls back to whichever exists —
    a 10-K carries a cover-share count (shares) + a cash-flow statement (burn); a 10-Q carries interim
    segment notes (purity) — so a domestic filer with only one recent form still yields candidates. The
    both-present case is UNCHANGED (q=10-Q, k=10-K). A fallen-back annual cash-flow column stays honest:
    a derivable YTD is stated as derived in the note (reproducible arithmetic — AUTO, the re-tier); an
    underivable one FLAGs ``ytd-raw`` rather than mislabel a clean quarter."""
    cik = int(cik)
    cf = client.get_json(
        companyfacts_url(cik),
        f"companyfacts/CIK{cik:010d}.json",
    )
    tenq = _latest_filing(client, cik, "10-Q")
    tenk = _latest_filing(client, cik, "10-K")
    if tenq is None and tenk is None:
        return (
            []
        )  # no domestic periodic filing (foreign 20-F/6-K issuer) — nothing the extractor covers
    q = tenq or tenk  # shares + cash_burn source (prefer the 10-Q; fall back to the 10-K)
    k = tenk or tenq  # purity / segment source (prefer the 10-K; fall back to the 10-Q)
    return extract_facts(
        cf,
        q[1],
        k[1],
        tenq_ref=q[0],
        tenk_ref=k[0],
        tenq_date=q[2],
        tenk_date=k[2],
        cfg=cfg,
    )


__all__ = ["extract_facts", "extract_for_security", "ExtractedFact"]
