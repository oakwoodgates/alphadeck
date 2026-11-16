"""The scoring-fact FILING-PARSER (Slice hybrid-1) — the three-tier hybrid extractor.

Given a company's SEC ``companyfacts`` + its latest 10-Q/10-K text, produce candidate scoring facts
(``domain.extraction.ExtractedFact``) for the three meters:

- **shares_outstanding** (market cap): a single, CURRENT cover-share concept -> ``AUTO`` ("current" is
  judged against the filing's PERIOD OF REPORT — never the filing date, which every cover predates).
  Otherwise ``FLAG`` with the label naming the OBSERVED condition: ``dual-class`` (>1 distinct DEI values
  on the latest cover date, or >=2 per-class counts parsed from the cover — the A+B sum offered),
  ``stale-cover`` (a single count older than the period — offered, dated by its own as-of date), or
  ``no-companyfacts`` (nothing observed — located-only). One condition, one label; never a catch-all.
  A cover row dated after its own filing is refused first (``cover_after_filing``) and named by the
  ``cover-after-filing`` flag + the note.
- **cash_burn** (runway): cash = cash+equiv + the same-dated short-term investments + long-term MARKETABLE
  securities (never the generic long-term investments line), the cash read under its most current label (the
  restricted-cash total says so in the note) and every component named in the note; a clean ~quarter of
  operating cash flow (read under its most current label, plain or continuing operations) ending on the cited
  filing's period, with cash of that date -> ``AUTO``; a burn spanning more or less than about a quarter, a burn
  or cash dated off the cited period, an underivable year-to-date column, a large one-time reconciliation line,
  or a composition that cannot be established (cash tags that disagree, a combined line that does not
  reconcile, investments only on a later balance sheet) -> ``FLAG`` (raw + locate).
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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from domain.burn_pace import YtdPace, pace_caveat, pace_segment
from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from domain.settings import get_settings
from ingest.edgar.acceptance import (
    filing_date_bound,
    parse_filing_date,
    resolve_public_acceptance,
)
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
    """The first candidate concept that has any rows — the OLD label rule. No reader uses it any more (the
    revenue readers moved off it 2026-10-20, the cash reader 2026-10-24: ``_newest``); kept because the
    label-switch regression tests replay the old rule against the new one."""
    for tax, concept in candidates:
        r = _rows(facts, tax, concept, unit)
        if r:
            return r
    return []


def _newest(
    facts: dict, candidates: list[tuple[str, str]], unit: str
) -> tuple[str, list[dict]] | None:
    """The candidate concept whose NEWEST row ends latest, with its rows; two concepts whose newest rows end
    on the same date go to the one listed first. ``None`` when no candidate has a row.

    The first-present rule kept a company's FIRST label forever: a filer that moved its cash from
    ``CashAndCashEquivalentsAtCarryingValue`` to the restricted-cash total (or to plain ``Cash``) read the
    old label's last balance (MEASURED 2026-10-24 over prod's cached companyfacts: GE's cash stopped at 2017,
    TNC 2018, PLPC 2022; 2 companies picked an older known label over a current one, 80 were stale under a
    label the reader didn't know, and 13 left their current marketable securities out under an older one).
    """
    best: tuple[str, str, list[dict]] | None = None
    for tax, concept in candidates:
        rows = _rows(facts, tax, concept, unit)
        if not rows:
            continue
        end = max(r["end"] for r in rows)
        if best is None or end > best[0]:  # strictly newer: a tie keeps the earlier-listed concept
            best = (end, concept, rows)
    return None if best is None else (best[1], best[2])


def _days(row: dict) -> int:
    s, e = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
    return (e - s).days


def _pick_instant(facts: dict, candidates: list[tuple[str, str]]) -> tuple[float, str, str] | None:
    """The latest balance-sheet (value, as-of end date, concept) over the candidate concept that is the most
    CURRENT (``_newest``; instant fact = no span). The DATE rides along so the cash composer can STATE every
    input's as-of and detect a lagging companyfacts (the shares ``stale-cover`` rule, applied to cash) — the
    bare-value version silently composed instants of unknown, possibly mixed dates. The CONCEPT rides along
    so the note can say what the value includes (the restricted-cash total)."""
    picked = _newest(facts, candidates, "USD")
    if picked is None:
        return None
    concept, rows = picked
    end = max(r["end"] for r in rows)
    at = [r for r in rows if r["end"] == end]
    return float(max(at, key=lambda r: r.get("filed", ""))["val"]), end, concept


def _latest_instant(facts: dict, candidates: list[tuple[str, str]]) -> tuple[float, str] | None:
    """``_pick_instant`` without the concept: (value, as-of end date)."""
    picked = _pick_instant(facts, candidates)
    return None if picked is None else (picked[0], picked[1])


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


# THE COVER DATE'S BOUND (2026-10-29). The cover "as of" date is the filer's own XBRL context, taken verbatim, and
# a filer can mistype it: AerSale's 10-K/A (0001558370-24-004010, filed 2024-03-27) says "outstanding as of March
# 5, 2024 was 52,990,947" on the page, but the inline-XBRL context behind that count is ``As_Of_3_5_2034`` (instant
# 2034-03-05; ``tests/fixtures/edgar/cover_dates/``). companyfacts carries that row verbatim, the reader took the
# newest ``end``, and a two-year-old 10-K/A count dated 2034 beat every later cover: get-data auto-applied 52,990,947
# as of 2034-03-05 under the 2026-06-30 10-Q's URL, whose own cover says 47,726,763 as of August 3, 2026.
COVER_AFTER_FILING = "cover-after-filing"


def cover_after_filing(asof: date, filed: date) -> bool:
    """THE RULE (one home: the 10-Q and annual cover readers and ``pipeline.repair_shares_cover_dates`` import it):
    a cover share count dated AFTER its filing's date is refused. A count is never public before its filing, and
    never dated in the future.

    The bound is the FILING date: SEC accepts a filing on its filing date, or the business evening before for an
    XBRL filing accepted after 17:30 ET, so a cover dated after the filing date is after the acceptance in every
    case. companyfacts carries the filing date on every row; it carries no acceptance time."""
    return asof > filed


@dataclass(frozen=True)
class RefusedCover:
    """One companyfacts cover-share row refused by ``cover_after_filing`` — what the note names."""

    asof: date
    filed: date
    value: float
    accession: str
    form: str


def screen_cover_rows(rows: list[dict]) -> tuple[list[dict], list[RefusedCover]]:
    """Split ``dei:EntityCommonStockSharesOutstanding`` rows into (kept, refused) by ``cover_after_filing``. A
    row with no filing date cannot be judged and is kept (never dropped silently, #9)."""
    kept: list[dict] = []
    refused: list[RefusedCover] = []
    for r in rows:
        filed = r.get("filed")
        if filed and cover_after_filing(date.fromisoformat(r["end"]), date.fromisoformat(filed)):
            refused.append(
                RefusedCover(
                    asof=date.fromisoformat(r["end"]),
                    filed=date.fromisoformat(filed),
                    value=float(r["val"]),
                    accession=r.get("accn") or "",
                    form=r.get("form") or "",
                )
            )
        else:
            kept.append(r)
    return kept, refused


def refused_cover_note(refused: list[RefusedCover]) -> str:
    """The sentence a candidate's note carries for its refused rows (stored with the fact on a ratify)."""
    rows = "; ".join(
        f"{r.value:,.0f} as of {r.asof.isoformat()} ({r.form or 'filing'} {r.accession}, filed "
        f"{r.filed.isoformat()})"
        for r in sorted(refused, key=lambda r: (r.asof, r.accession))
    )
    return (
        f"Refused {len(refused)} companyfacts cover row(s) dated after their own filing: {rows} — a cover "
        "count is never dated after its filing."
    )


def _shares(facts: dict, tenq_text: str, ref: str, period_end: date) -> ExtractedFact:
    """The shares candidate over the cover rows ``screen_cover_rows`` keeps; a refused row rides the candidate as
    the ``cover-after-filing`` flag and a sentence in its note (visible on the Workbench card, and stored with the
    fact when it is applied or ratified)."""
    rows, refused = screen_cover_rows(
        _rows(facts, "dei", "EntityCommonStockSharesOutstanding", "shares")
    )
    fact = _shares_candidate(rows, tenq_text, ref, period_end)
    if not refused:
        return fact
    note = fact.note
    if "no-companyfacts" in fact.flags:  # every cover row was refused: "no concept" would be untrue
        note = (
            "companyfacts' only cover-shares rows are dated after their own filing and the cover yielded no "
            "per-class counts — author the count from the located cover."
        )
    return fact.model_copy(
        update={
            "flags": [*fact.flags, COVER_AFTER_FILING],
            "note": f"{note} {refused_cover_note(refused)}",
        }
    )


def _shares_candidate(
    rows: list[dict], tenq_text: str, ref: str, period_end: date
) -> ExtractedFact:
    latest_end = max(r["end"] for r in rows) if rows else None
    vals = sorted({float(r["val"]) for r in rows if r["end"] == latest_end}) if rows else []
    # the located cover rides EVERY branch — on AUTO too (show-the-work: the pre-filled count is
    # approvable AND inspectable; the UI keeps it collapsed for AUTO, never a black-box value)
    passage = _locate(tenq_text, ref, "cover", ["shares of Class", "Class A", "outstanding"])
    located = [p for p in [passage] if p]
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
            located_passages=located,
            note=f"Cover-page shares outstanding as of {latest_end} (single class).",
        )
    # FLAG — three DISTINCT observed conditions get three HONEST labels (#6: a flag is evidence, so it
    # must name what was OBSERVED; the old single catch-all stamped "dual-class" whichever branch fired,
    # which lied on every single-class name once the date gate broke):
    total = _cover_class_sum(
        tenq_text
    )  # >= 2 per-class counts located on the COVER text, else None
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

# Every candidate list below is read by ``_pick_instant``: the most CURRENT label wins, the listed order only
# breaks a tie (the same newest date). The cash order puts the BALANCE-SHEET lines first, so a company that
# reports its balance-sheet cash as currently as the restricted-cash total reads the balance-sheet line.
_CASH = [
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
    ("us-gaap", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"),
    # 2026-10-24 — two labels current cash sits under that the reader didn't know. MEASURED over 937 basket
    # companies with US-GAAP data: 120 had their current cash only under these (80 read an older label's stale
    # balance, 40 read none); the new rule reads 61 from the restricted-cash total, 60 from plain ``Cash``. ``Cash``
    # is a balance-sheet line; the restricted-cash total is the cash-flow statement's, so it is LAST and the
    # note says it includes restricted cash (``_RESTRICTED``).
    ("us-gaap", "Cash"),
    ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
]
# The cash concept whose value INCLUDES restricted cash — stated in the note whenever it is the source.
_RESTRICTED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
# THE CASH COMPOSITION (V2 extractor correctness PR 1, operator decisions D1 + D2, 2026-11-15). The written rule
# (``docs/WORKBENCH_SCORING.md``, the uniform cash rule) is cash + equivalents + ALL marketable securities, current
# and noncurrent. The investment lists below are NOT read through ``_pick_instant``: every tag is read AT the cash
# date (a balance sheet is one date), and among the tags present there the LARGEST value is taken, never the sum,
# because they often restate one another (an AFS tag can equal ``ShortTermInvestments``). Every tag seen is named.
# Short-term investments (current). The first three are the old list; the rest held 1.3bn at RBRK and 362M at
# LMAT that the old reader left out while its note claimed "marketable securities ($0.0M)" (the 2026-11-15 audit:
# 37 companies with more than half their cash in uncounted investments).
_STI = [
    ("us-gaap", "ShortTermInvestments"),
    ("us-gaap", "MarketableSecuritiesCurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesCurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
    ("us-gaap", "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent"),
    ("us-gaap", "HeldToMaturitySecuritiesCurrent"),
    ("us-gaap", "OtherShortTermInvestments"),
    ("us-gaap", "CertificatesOfDepositAtCarryingValue"),
    ("us-gaap", "TimeDepositsAtCarryingValue"),
]
# Long-term MARKETABLE securities: noncurrent lines that are explicitly debt securities, which the written rule
# counts (D2 keeps them: CRSP's long-dated Treasuries).
_LT_MARKETABLE = [
    ("us-gaap", "MarketableSecuritiesNoncurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesNoncurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"),
    ("us-gaap", "HeldToMaturitySecuritiesNoncurrent"),
]
# NEVER cash (D2): the generic long-term investments line and its kin, often stakes in other companies (DIS stored
# 12.8bn against 5.2bn of cash, KMI 8.8bn against 0.9bn). Read only to NAME a same-dated one in the note as not
# counted. Equity securities and restricted investments are never read at all.
_NOT_CASH = [
    ("us-gaap", "LongTermInvestments"),
    ("us-gaap", "EquityMethodInvestments"),
    ("us-gaap", "OtherLongTermInvestments"),
    ("us-gaap", "LongTermInvestmentsAndReceivablesNet"),
]
# The combined line: never an addend, only the check that cash + short-term investments reconcile to it.
_COMBINED = "CashCashEquivalentsAndShortTermInvestments"
# Balance-sheet nesting (narrower, wider): on one date the narrower line can never exceed the wider one. ``Cash``
# below cash-and-equivalents is its cash part (OKLO: 15.6M beside 1,594.1M), consistent; ``Cash`` ABOVE it is a
# contradiction (SOAR: cash-and-equivalents 0 beside ``Cash`` 8.4M) -> ``cash-tags-disagree``.
_CASH_NESTING = (
    ("Cash", "CashAndCashEquivalentsAtCarryingValue"),
    ("Cash", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"),
    ("Cash", _RESTRICTED),
    (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    ),
    ("CashAndCashEquivalentsAtCarryingValue", _RESTRICTED),
)
# Operating cash flow, read like cash under its most CURRENT label (``_newest``; V2 extractor correctness PR 2,
# 2026-11-15). The 2026-11-15 audit found 8 of 460 auto-applied burns stale (EHC's from 2023; QXL's and T's from
# 2025): those filers moved their operating total to the continuing-operations tag, which the reader did not
# know, so it kept the plain tag's last period. When both tags reach the same newest period, the plain tag (the
# statement's total, continuing plus discontinued) is listed first and wins the tie. The note names the tag.
_OCF = "NetCashProvidedByUsedInOperatingActivities"
_OCF_CONTINUING = "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"
_OCF_TAGS = [("us-gaap", _OCF), ("us-gaap", _OCF_CONTINUING)]
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


@dataclass(frozen=True)
class _Quarter:
    """The latest ~quarter of operating cash flow, with what it covers and where it came from.

    ``start`` is the native column's start, or the subtracted prior year to date's END for a derived quarter (what
    the note prints, ``burn over start → end``); ``days`` is the span between the two, the figure the
    ``quarterly_span_*_days`` window judges (a calendar quarter reads 90 native, 91 or 92 derived).
    """

    value: float
    start: str
    end: str
    basis: str
    concept: str

    @property
    def days(self) -> int:
        return (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days


def _quarter(facts: dict, cfg: ExtractorConfig) -> _Quarter | None:
    """The latest ~quarter of operating cash flow, read under the most current operating-cash-flow label
    (``_OCF_TAGS``). ``basis`` names what the value IS — one condition, one label (the runway audit: the old
    bool lumped "derived" together with "couldn't derive", so a RAW year-to-date figure went out wearing the
    ytd-derived label and a passage claiming a derivation that never happened — runway would read ~3-4x too
    short):

    - ``"quarter"`` — a native quarterly column (span <= quarterly_span_max_days).
    - ``"derived"`` — the quarter computed as YTD − the prior same-start YTD.
    - ``"ytd-raw"`` — a long-span column with NO prior to subtract: the value IS the raw YTD, not a quarter.

    The derivation subtracts within ONE tag: a total minus a continuing-operations part is not a quarter of
    anything. Whether the result covers about one quarter is judged by the caller (``burn-span``): when the
    prior same-start YTD is missing, the latest earlier one is subtracted (URI: nine months minus the first
    quarter, 183 days), and that value is offered flagged, never AUTO.
    """
    picked = _newest(facts, _OCF_TAGS, "USD")
    if picked is None:
        return None
    concept, rows = picked
    durations = [r for r in rows if r.get("start")]
    if not durations:
        return None
    latest_end = max(r["end"] for r in durations)
    row = min(
        (r for r in durations if r["end"] == latest_end), key=_days
    )  # shortest span at latest end
    if _days(row) <= cfg.quarterly_span_max_days:
        return _Quarter(float(row["val"]), row["start"], row["end"], "quarter", concept)
    prior = max(
        (r for r in durations if r["start"] == row["start"] and r["end"] < row["end"]),
        key=lambda r: r["end"],
        default=None,
    )
    if prior is None:
        return _Quarter(float(row["val"]), row["start"], row["end"], "ytd-raw", concept)
    return _Quarter(
        float(row["val"]) - float(prior["val"]), prior["end"], row["end"], "derived", concept
    )


def _ytd_pace(facts: dict, q: _Quarter, cfg: ExtractorConfig) -> YtdPace | None:
    """The year to date behind a quarter's burn, for the PACE CHECK (V2 extractor correctness PR 3, plan Q5): the
    operating-cash-flow row of the quarter's own tag that ends on the quarter's end and has the LONGEST span, when
    that span covers more than one quarter (above ``quarterly_span_max_days``: a first fiscal quarter has no separate
    pace) and no more than a fiscal year (``pace_ytd_max_days``: never an inception-to-date column). Only a burn
    that passed the quarter window gets a pace (a ``burn-span`` or ``ytd-raw`` value is not a quarter to compare).

    No lookahead: among rows for that one period, the EARLIEST filed is read, the figure as first published; a later
    filing's restated comparative column was not public on the cited filing."""
    if q.basis == "ytd-raw" or not (
        cfg.quarterly_span_min_days <= q.days <= cfg.quarterly_span_max_days
    ):
        return None
    rows = [
        r
        for r in _rows(facts, "us-gaap", q.concept, "USD")
        if r.get("start")
        and r["end"] == q.end
        and cfg.quarterly_span_max_days < _days(r) <= cfg.pace_ytd_max_days
    ]
    if not rows:
        return None
    longest = max(_days(r) for r in rows)
    first = min(
        (r for r in rows if _days(r) == longest), key=lambda r: r.get("filed", "")
    )  # the original publication
    return YtdPace(burn_usd=-float(first["val"]), start=first["start"], end=first["end"])


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


def _at(facts: dict, concept: str, end: str) -> float | None:
    """A us-gaap USD balance (instant) dated exactly ``end``: the latest-filed row, else None (never 0)."""
    at = [
        r for r in _rows(facts, "us-gaap", concept, "USD") if r["end"] == end and not r.get("start")
    ]
    return None if not at else float(max(at, key=lambda r: r.get("filed", ""))["val"])


def _m(v: float) -> str:
    return f"${v / 1e6:.1f}M"


@dataclass(frozen=True)
class _Composition:
    """What the cash date's balance sheet says, every part named (the note is built from this)."""

    cash: float
    sti: list[tuple[str, float]]  # every short-term investment tag on the cash date, list order
    lt_marketable: list[tuple[str, float]]  # every long-term marketable tag on the cash date
    not_counted: list[tuple[str, float]]  # same-dated never-cash lines, named only
    older: list[
        tuple[str, str]
    ]  # (tag, its newest date) — an investment tag that stopped before the cash date
    newer: list[
        tuple[str, str]
    ]  # (tag, its newest date) — an investment tag only on a later balance sheet
    cash_tags: list[
        tuple[str, float]
    ]  # every balance-sheet cash tag on the cash date (for a disagreement)
    disagree: bool
    combined: float | None
    reconciled: bool

    @staticmethod
    def _largest(seen: list[tuple[str, float]]) -> tuple[str, float] | None:
        return max(seen, key=lambda x: x[1]) if seen else None  # a tie keeps the first listed

    @property
    def sti_value(self) -> float | None:
        best = self._largest(self.sti)
        return None if best is None else best[1]

    @property
    def lt_value(self) -> float | None:
        best = self._largest(self.lt_marketable)
        return None if best is None else best[1]

    @property
    def investments(self) -> float | None:
        if not self.sti and not self.lt_marketable:
            return None  # nothing tagged on the cash date — None, never a fake 0
        return (self.sti_value or 0.0) + (self.lt_value or 0.0)


def _compose(facts: dict, cash: float, end: str, cfg: ExtractorConfig) -> _Composition:
    """The cash composition on ONE balance-sheet date (``end``, the cash instant's): the investments read AT that
    date (the largest per role, never a sum of overlapping tags), the never-cash lines named, the cash tags'
    nesting checked, and the combined line used only as a reconciliation."""

    def seen(lst: list[tuple[str, str]]) -> list[tuple[str, float]]:
        out = [(c, _at(facts, c, end)) for _, c in lst]
        return [(c, v) for c, v in out if v is not None]

    older: list[tuple[str, str]] = []
    newer: list[tuple[str, str]] = []
    for _, c in _STI + _LT_MARKETABLE:
        rows = _rows(facts, "us-gaap", c, "USD")
        if rows and _at(facts, c, end) is None:
            newest = max(r["end"] for r in rows)
            (newer if newest > end else older).append((c, newest))
    cash_tags = seen(_CASH)
    vals = dict(cash_tags)
    disagree = any(n in vals and w in vals and vals[n] > vals[w] for n, w in _CASH_NESTING)
    sti = seen(_STI)
    combined = _at(facts, _COMBINED, end)
    reconciled = True
    if combined is not None:
        parts = cash + (max(v for _, v in sti) if sti else 0.0)
        bound = cfg.investments_reconcile_tolerance * max(abs(combined), abs(parts))
        reconciled = abs(parts - combined) <= bound
    return _Composition(
        cash=cash,
        sti=sti,
        lt_marketable=seen(_LT_MARKETABLE),
        not_counted=seen(_NOT_CASH),
        older=older,
        newer=newer,
        cash_tags=cash_tags,
        disagree=disagree,
        combined=combined,
        reconciled=reconciled,
    )


def _composition_note(c: _Composition, cash_label: str) -> str:
    """The note's cash part: every component with its amount, a missing one SAID missing (the old note printed
    "marketable securities ($0.0M)" for tags it never read), the never-cash lines named as not counted.
    """

    def tags(seen: list[tuple[str, float]]) -> str:
        best = _Composition._largest(seen)
        assert best is not None
        if len(seen) == 1:
            return best[0]
        listed = ", ".join(f"{t} {_m(v)}" for t, v in seen)
        return f"{best[0]}, the largest of {listed}; overlapping tags are never summed"

    total = c.cash + (c.investments or 0.0)
    parts = [f"{cash_label} {_m(c.cash)}"]
    missing: list[str] = []
    if c.sti:
        parts.append(f"short-term investments {_m(c.sti_value or 0.0)} ({tags(c.sti)})")
    else:
        missing.append("no short-term investments tagged on the cash date")
    if c.lt_marketable:
        parts.append(
            f"long-term marketable securities {_m(c.lt_value or 0.0)} ({tags(c.lt_marketable)})"
        )
    else:
        missing.append("no long-term marketable securities tagged on the cash date")
    out = f"cash_usd {_m(total)} = " + " + ".join(parts)
    if missing:
        out += "; " + "; ".join(missing)
    if c.not_counted:
        out += (
            "; not counted as cash: "
            + ", ".join(f"{t} {_m(v)}" for t, v in c.not_counted)
            + " (a long-term investments line, often stakes in other companies, never cash)"
        )
    if c.combined is not None:
        parts_sum = c.cash + (c.sti_value or 0.0)
        out += (
            f"; cash + short-term investments reconciles to {_COMBINED} {_m(c.combined)}"
            if c.reconciled
            else f"; {_COMBINED} {_m(c.combined)} ≠ cash + short-term investments {_m(parts_sum)}"
            " (investments may sit under a tag not read)"
        )
    if c.disagree:
        out += "; cash tags disagree on the cash date: " + ", ".join(
            f"{t} {_m(v)}" for t, v in c.cash_tags
        )
    return out


def _cash_burn(
    facts: dict,
    tenq_text: str,
    ref: str,
    period_end: date,
    cfg: ExtractorConfig,
    source: str = "10-q",
) -> ExtractedFact:
    """Cash + quarterly burn. A FLAG marks an EXCEPTION needing judgment (the re-tier — honest loudness:
    ``ytd-derived`` fired on ~3 of 4 filings [GAAP 10-Q cash-flow statements are YTD] and
    ``verify-marketable-securities`` on ~every real filer, so AUTO was structurally empty and the flags
    carried no information): a raw YTD that COULDN'T be derived is ``ytd-raw`` (never claimed derived);
    an anomalous line is ``possible-one-time``; an instant older than the filing period is ``stale-cash``;
    a missing input is its own flag with a **None** value — never a fake $0 cash or a fake $0 burn (which
    ratified into a fake "cash-generative"). COMPOSITION is provenance, not an alarm: a cleanly-derived
    quarter (YTD − prior YTD — reproducible arithmetic on two filed columns, the market-cap trust class)
    and the investment basis (same-dated included / an older off-date tag excluded, which errs CONSERVATIVE —
    understated cash reads runway shorter) ride the NOTE and stay AUTO. Every input's as-of rides the note.
    A composition the extractor cannot establish is a FLAG (``_compose``: ``cash-tags-disagree``,
    ``investments-unreconciled``, ``investments-off-date``), never a silent AUTO.

    The cash reads its most CURRENT label (``_pick_instant``), so a filer that moved its cash to plain
    ``Cash`` or the restricted-cash total is read where it reports today; the note names a non-default cash
    tag and says when the value includes restricted cash. The investments are read AT the cash date, every
    known tag (``_STI``, ``_LT_MARKETABLE``), the largest per role; ``investments_usd`` carries their part.
    ``source`` is the cited form's provenance kind (``10-q``, or ``10-k`` when the values filing is a 10-K).

    THE PERIOD AND CITATION RULE (V2 extractor correctness PR 2, 2026-11-15): operating cash flow is read under
    its most current label (``_OCF_TAGS``: plain or continuing operations), and the burn is AUTO only when it
    spans ``quarterly_span_min_days``-``quarterly_span_max_days`` (``burn-span``), ends on the cited filing's
    period of report (``burn-off-period``), and the cash is dated that period (older: ``stale-cash``; newer:
    ``cash-after-period``). A failure offers the value as found with its flag; it never turns a flag into AUTO.
    The note states the burn's start, end, days and tag, and the filing it is cited to.
    """
    cash_pick = _pick_instant(facts, _CASH)
    cash_at = None if cash_pick is None else (cash_pick[0], cash_pick[1])
    restricted = cash_pick is not None and cash_pick[2] == _RESTRICTED
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
            source=source,
            source_ref=ref,
            event_date=period_end,
            flags=["no-companyfacts"],
            located_passages=[p for p in locs if p],
            note="companyfacts has neither a cash instant nor an operating-cash-flow column for this "
            "filer — author cash + quarterly burn from the located statements.",
        )

    flags: list[str] = []
    passages: list[LocatedPassage] = []
    # A balance sheet is ONE date: investments join the sum ONLY when tagged on the SAME date as cash. An
    # off-date instant is not "stale, confirm it" — it's a DIFFERENT balance sheet (usually a discontinued tag:
    # MU's AvailableForSaleSecurities* last reported 2018 — the old bare-value composer silently added those
    # eight-year-old balances into current cash). An OLDER one is excluded + named, never summed (conservative:
    # runway reads shorter); one only on a NEWER balance sheet means the composition at one date is not
    # established — ``investments-off-date``.
    comp = _compose(facts, cash_at[0], cash_at[1], cfg) if cash_at else None
    investments = comp.investments if comp else None
    cash_usd = (cash_at[0] + (investments or 0.0)) if cash_at else None
    asofs: list[str] = []
    if cash_pick is not None:
        # the label is named whenever it is not the plain balance-sheet cash line (show the work: which tag
        # the number came from — the reader now follows a filer that switched labels)
        tag = "" if cash_pick[2] == _CASH[0][1] else f" (tagged {cash_pick[2]})"
        asofs.append(f"cash as of {cash_pick[1]}{tag}")
    if cash_at and investments is not None:
        asofs.append(f"investments as of {cash_at[1]}")
    if comp and comp.older:
        asofs.append(
            "investment tags not on the cash date — EXCLUDED from the sum (likely discontinued tags): "
            + ", ".join(f"{t} ({d})" for t, d in comp.older)
        )
    if comp and comp.newer:
        asofs.append(
            "investment tags dated AFTER the cash date and not on it: "
            + ", ".join(f"{t} ({d})" for t, d in comp.newer)
        )

    burn: float | None = None
    ytd: YtdPace | None = (
        None  # the year-to-date pace behind the burn (PR 3); None = no separate year to date
    )
    caveat: str | None = None  # the pace caveat, only when the quarter is far off that pace (D3)
    checks: list[str] = []  # each period/citation flag's own sentence, stated in the note
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
        qval, start, end, basis = q.value, q.start, q.end, q.basis
        burn = -qval  # quarterly_burn_usd is POSITIVE when burning (op-cash-use is negative)
        # the burn's period, its length and its tag, always (show the work: which column, over which days)
        asofs.append(f"burn over {start} → {end} ({q.days} days, tagged {q.concept})")
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
        # THE PERIOD RULE (V2 extractor correctness PR 2): AUTO only when the burn covers about one quarter
        # (``quarterly_span_min_days``-``quarterly_span_max_days``, native or derived) AND ends on the cited
        # filing's period of report. The audit's 460: 7 "quarters" spanned 6-9 months (a missing prior year to
        # date: URI stored two quarters as one), 2 under 80 days, 14 ended off the cited period (a stale tag or a
        # 20-F's numbers under an old 10-Q). The value is still offered, as found, with the flag and its span.
        # A raw year to date already says it is not a quarter (``ytd-raw``): one condition, one label.
        if basis != "ytd-raw" and not (
            cfg.quarterly_span_min_days <= q.days <= cfg.quarterly_span_max_days
        ):
            flags.append("burn-span")
            checks.append(
                f"the burn spans {q.days} days, outside the {cfg.quarterly_span_min_days}-"
                f"{cfg.quarterly_span_max_days}-day quarter window, so it is not established as one quarter"
            )
        if end != period_end.isoformat():
            flags.append("burn-off-period")
            checks.append(
                f"the burn ends {end}, not on the cited filing's period of report {period_end.isoformat()}"
            )
        ot = _detect_one_time(facts, tenq_text, ref, start, end, qval, cfg)
        if ot:
            flags.append("possible-one-time")
            passages.append(ot)
        # THE PACE CHECK (V2 extractor correctness PR 3, operator decision D3): the year-to-date quarterly pace
        # rides the fact and the note, and a quarter far off it carries a CAVEAT. Never a flag, never the tier.
        ytd = _ytd_pace(facts, q, cfg)
        if ytd is not None:
            asofs.append(pace_segment(ytd))
            caveat = pace_caveat(cash_usd, burn, ytd.quarterly_burn_usd, cfg)
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
    # ... and the other direction (V2 extractor correctness PR 2): cash dated AFTER the cited filing's period
    # is a later balance sheet than the filing cited (the audit: RMIX +31 days, EXYN the filing day, and every
    # 20-F number read under an old 10-Q) — the old check passed it silently.
    if cash_at and date.fromisoformat(cash_at[1]) > period_end:
        flags.append("cash-after-period")
        checks.append(
            f"cash is dated {cash_at[1]}, after the cited filing's period of report "
            f"{period_end.isoformat()}"
        )
    # NO flag for the ordinary composition (the re-tier): same-dated inclusion is the textbook liquidity
    # composition and an older off-date exclusion errs conservative (cash understated -> runway reads SHORTER)
    # — both are stated in the note. A composition the extractor CANNOT establish is a flag, never a silent
    # AUTO: cash tags of one date that contradict each other, cash + short-term investments that miss the
    # same-dated combined line, investments only on a later balance sheet.
    if comp is not None:
        if comp.disagree:
            flags.append("cash-tags-disagree")
        if not comp.reconciled:
            flags.append("investments-unreconciled")
        if comp.newer:
            flags.append("investments-off-date")

    # a period or citation flag points at the statement it concerns (located, never interpreted)
    for kind, keys, raised in (
        (
            "cash-flow",
            ["operating activities", "cash flows"],
            {"burn-span", "burn-off-period"} & set(flags),
        ),
        ("balance-sheet", ["cash and cash equivalents"], {"cash-after-period"} & set(flags)),
    ):
        if raised and not any(p.kind == kind for p in passages):
            p = _locate(tenq_text, ref, kind, keys)
            if p:
                passages.append(p)

    if not flags:
        # show-the-work on AUTO: attach the same deterministic locates the FLAG paths carry, so the
        # pre-filled clean quarter still shows its statements (the UI keeps them collapsed for AUTO —
        # the source is on demand, never demanded, and never a black box).
        for kind, keys in (
            ("balance-sheet", ["cash and cash equivalents", "total current assets"]),
            ("cash-flow", ["operating activities", "cash flows"]),
        ):
            p = _locate(tenq_text, ref, kind, keys)
            if p:
                passages.append(p)

    tier = Tier.FLAG if flags else Tier.AUTO
    cash_label = "cash+equivalents (includes restricted cash)" if restricted else "cash+equivalents"
    cash_part = (
        _composition_note(comp, cash_label)
        if comp
        else "cash: NOT FOUND in companyfacts (author it from the located balance sheet)"
    )
    burn_part = (
        f"quarterly burn = operating cash use{basis_suffix}"
        if burn is not None
        else "burn: NOT FOUND (no operating-cash-flow column)"
    )
    # the filing the value is cited to, then each period/citation flag's sentence (V2 extractor correctness PR 2)
    cited = (
        f"cited filing: the {source.upper()} for the period ending {period_end.isoformat()} ({ref})"
    )
    stated = [*asofs, cited, *checks, *([f"pace caveat: {caveat}"] if caveat else [])]
    note = (
        f"{cash_part}; {burn_part}"
        + f" [{'; '.join(stated)}]"
        + ". "
        + (
            "FLAGS: " + ", ".join(flags) + " — confirm against the filing."
            if flags
            else "No attention flags."
        )
    )
    # the value's OWN as-of (the #132 event-date rule): the burn period end, else the cash instant's end
    evt = (
        date.fromisoformat(q.end)
        if q is not None
        else (date.fromisoformat(cash_at[1]) if cash_at else period_end)
    )
    return ExtractedFact(
        fact_type="cash_burn",
        tier=tier,
        cash_usd=cash_usd,
        quarterly_burn_usd=burn,
        investments_usd=investments,
        ytd_quarterly_burn_usd=None if ytd is None else round(ytd.quarterly_burn_usd),
        ytd_burn_days=None if ytd is None else ytd.days,
        pace_caveat=caveat,
        source=source,
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
    # 2026-10-20 — the TOTAL-revenue tags of two industries the three above miss, appended AFTER them so the
    # order-based tie fallback is unchanged for everyone else. Utilities: total operating revenue (XEL, DTE,
    # TVE report it and none of the above). Banks: total net revenue = net interest income + noninterest
    # income (HAPN). Deliberately NOT ``RevenuesExcludingInterestAndDividends`` (a bank's noninterest income —
    # one component) nor broker-dealer lines (``PrincipalTransactionsRevenue``, ``BrokerageCommissionsRevenue``,
    # ``SalesCommissionsAndFees``): parts of revenue, never the total.
    ("us-gaap", "RegulatedAndUnregulatedOperatingRevenue"),
    ("us-gaap", "RevenuesNetOfInterestExpense"),
]

# Public alias: the §2.2 fundamentals ingest (``ingest.fundamentals``) reads the SAME revenue concepts so
# "which companyfacts tag IS revenue" has ONE source of truth and can never drift between the Workbench and
# the quarterly-series backfill. Neither reader takes the FIRST present tag any more (2026-10-20): most filers
# moved from ``Revenues`` to a ``RevenueFromContractWithCustomer*`` tag around ASC 606, so the first present
# tag is often a dead series. The ingest picks the CURRENT tag (``ingest.fundamentals.select_revenue``); the
# purity branch below asks whether ANY tag reports annual revenue. The ORDER here is the last tie-break: two
# tags whose newest quarter ends on the same date go to the one with more quarters, then to the earlier tag.
REVENUE_CONCEPTS = _REVENUE

# The purity passage is WIDE (vs the default ±110): the grounded purity-estimate seam (SURFACE 1b) proposes a %
# ONLY from segment $ / total $ figures in the passage, so the located window must be big enough to carry the
# segment revenue TABLE, not just the heading. A retrieval window (not a scoring cutoff); a too-narrow window
# just means the seam can't ground it and honestly declines (fail-open to HUMAN) — never a wrong number.
_PURITY_WINDOW = 1500


def _has_annual_revenue(facts: dict) -> bool:
    """Does the company report positive annual revenue under ANY revenue tag? (It used ``_first``, so a
    company whose first-present tag is a dead or odd-span series — a ``Revenues`` node with no annual row,
    the current tag carrying it — read as pre-revenue and got the Item-1 passage instead of its segment
    table.) Only which passage the HUMAN purity candidate locates depends on this; never a value."""
    return any(
        r.get("start") and _days(r) > 300 and float(r["val"]) > 0
        for tax, concept in _REVENUE
        for r in _rows(facts, tax, concept, "USD")
    )


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
    values_form: str = "10-Q",
) -> list[ExtractedFact]:
    """Pure, deterministic: (companyfacts + the two filing texts) -> the three candidate facts. Testable
    offline against cached fixtures (the form4-parser precedent — the caller owns fetching).

    ``values_form`` is the form of the VALUES filing (the ``tenq_*`` arguments, which since B1 can be a 10-K):
    the cash candidate's stored provenance kind names it (``10-k`` for a 10-K; it said ``10-q`` for every
    value, the 18 audited 10-K citations included)."""
    facts = companyfacts["facts"]
    cash_source = "10-k" if values_form == "10-K" else "10-q"
    return [
        _purity(facts, tenk_text, tenk_ref, tenk_date),
        _shares(facts, tenq_text, tenq_ref, tenq_date),
        _cash_burn(facts, tenq_text, tenq_ref, tenq_date, cfg, source=cash_source),
    ]


def _doc_url(cik: int, accession: str, primary_doc: str) -> str:
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"{get_settings().sec_archives_base}/{cik}/{accession.replace('-', '')}/{doc}"


def companyfacts_url(cik: str | int) -> str:
    """The SEC XBRL companyfacts JSON endpoint for a CIK (data.sec.gov)."""
    return f"{get_settings().sec_data_base}/api/xbrl/companyfacts/CIK{int(cik):010d}.json"


def _read_filing(client: EdgarClient, cik: int, f: dict[str, str]) -> tuple[str, str, date]:
    """One submissions row (``filings_of``'s shape) -> (document URL, cleaned text, period of report). The ONE
    document request of a filing (cache-first, ``forms/`` = cached forever)."""
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


def _latest_filing(client: EdgarClient, cik: int, form: str) -> tuple[str, str, date] | None:
    hits = filings_of(fetch_submissions(client, cik), form)
    return _read_filing(client, cik, hits[0]) if hits else None


def _public_at(f: dict[str, str]) -> datetime:
    """When a 10-Q / 10-K / 20-F / 40-F became PUBLIC (CLAUDE.md invariant #1; the F7 rule for forms whose
    filing date rolls): its listed acceptance when ``resolve_public_acceptance`` trusts it, else the END of its
    filing date in ET — late, never early. Request-free (no header read). Orders the latest 10-Q against the latest 10-K (``_newer``) and
    the four periodic forms against one another (``_takes_annual_path``).
    """
    accepted = resolve_public_acceptance(f.get("accepted"), f.get("filed")).at
    if accepted is not None:
        return accepted
    filed = parse_filing_date(f.get("filed"))
    # no readable filing date (never seen on EDGAR): order it oldest rather than guess a public time
    return (
        filing_date_bound(filed) if filed is not None else datetime.min.replace(tzinfo=timezone.utc)
    )


# The forms the router weighs (V2 extractor correctness PR 2): the periodic path's and the annual path's. Exact
# match, as everywhere: an amendment or a current report (8-K, 6-K) never decides the path.
_PERIODIC_FORMS = ("10-Q", "10-K")
_ANNUAL_FORMS = ("20-F", "40-F")


def _takes_annual_path(subs: dict[str, Any]) -> bool:
    """Whether a company goes down the annual (20-F/40-F) path: its NEWEST periodic filing — the latest 10-Q,
    10-K, 20-F and 40-F compared by when each became public (``_public_at``; no lookahead, never by period of
    report) — is a 20-F or 40-F, or it has no 10-Q/10-K at all (unchanged).

    Before 2026-11-15 any company that had EVER filed a 10-Q or 10-K took the quarterly path. The audit's five
    form switchers (EUDA, CURR, SCNI, CHR, BTCT) now file 20-F/6-K: the quarterly path read the 20-F's numbers
    from companyfacts and cited the old 10-Q, AUTO. A company that filed a 20-F years ago and files 10-Qs now
    stays quarterly. A tie on public time keeps the quarterly path (the earlier-listed form; the quarterly path's
    own period checks still flag numbers that are not the cited filing's)."""
    newest: tuple[datetime, str] | None = None
    for form in _PERIODIC_FORMS + _ANNUAL_FORMS:
        row = next(iter(filings_of(subs, form)), None)
        if row is None:
            continue
        at = _public_at(row)
        if newest is None or at > newest[0]:  # strictly newer: a tie keeps the earlier-listed form
            newest = (at, form)
    return newest is None or newest[1] in _ANNUAL_FORMS


def _newer(tenq: dict[str, str] | None, tenk: dict[str, str] | None) -> dict[str, str] | None:
    """The values filing (operator decision B1, 2026-11-14): the NEWER of the latest 10-Q and the latest 10-K by
    when each became public — never by period of report. A tie keeps the 10-Q (the old preference).
    """
    if tenq is None or tenk is None:
        return tenq or tenk
    return tenk if _public_at(tenk) > _public_at(tenq) else tenq


def extract_for_security(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    values_only: bool = False,
) -> list[ExtractedFact]:
    """Live (cache-first) wrapper: CIK -> companyfacts + the latest 10-Q and/or 10-K -> candidates.
    An EXPLICIT operator action (the extract endpoint), never auto-fired on a render.

    COVERAGE: an empty list means the issuer has NO 10-K/10-Q at all (a foreign private issuer files
    20-F/6-K instead). The extract ROUTE no longer stops there — ``extract_with_annual_fallback``
    (below) routes those names through the annual-cover shares path (``annual_shares.py``, Slice 1)
    with an honest, distinct empty reason when even that has nothing to read. THIS function stays
    10-Q/10-K-only (the auto-confirm path keys off it — annual candidates are FLAG, never applied).

    WHICH FILING EACH ROLE CITES (operator decision B1, 2026-11-14). The share count and cash/burn cite the
    NEWER of the latest 10-Q and the latest 10-K, by when each became PUBLIC (``_newer``): their numbers are
    companyfacts' newest rows, which after a 10-K are the 10-K's, so the link, the period of report and the
    located passages must be the 10-K's too (before B1 they cited the older 10-Q: a 10-K's number under a
    10-Q's link, and the machine writer skipped it as already on file). Purity keeps preferring the 10-K
    (the segment note), falling back to the 10-Q. Each role falls back to whichever form exists. An annual
    cash-flow column stays honest: a derivable quarter is stated as derived in the note (reproducible
    arithmetic — AUTO, the re-tier); an underivable one FLAGs ``ytd-raw`` rather than mislabel a clean quarter.

    FETCHES. Each document is read at most once, and only when a role needs it: the values filing, plus the
    purity filing when it is a different one. ``values_only=True`` (the platform-value runs, whose machine
    writer ignores purity) never reads a second document: the purity candidate is then built from the values
    filing."""
    cik = int(cik)
    cf = client.get_json(
        companyfacts_url(cik),
        f"companyfacts/CIK{cik:010d}.json",
    )
    subs = fetch_submissions(client, cik)
    tenq = next(iter(filings_of(subs, "10-Q")), None)
    tenk = next(iter(filings_of(subs, "10-K")), None)
    values_row = _newer(tenq, tenk)
    if values_row is None:
        return (
            []
        )  # no domestic periodic filing (foreign 20-F/6-K issuer) — nothing the extractor covers
    purity_row = tenk or tenq  # purity / segment source (prefer the 10-K; fall back to the 10-Q)
    if values_only:
        purity_row = values_row
    v = _read_filing(client, cik, values_row)
    p = (
        v
        if purity_row["accession"] == values_row["accession"]
        else _read_filing(client, cik, purity_row)
    )
    return extract_facts(
        cf,
        v[1],
        p[1],
        tenq_ref=v[0],
        tenk_ref=p[0],
        tenq_date=v[2],
        tenk_date=p[2],
        cfg=cfg,
        values_form="10-K" if values_row is tenk else "10-Q",
    )


def extract_with_annual_fallback(
    client: EdgarClient,
    cik: str | int,
    *,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
    values_only: bool = False,
) -> ExtractionResult:
    """The extract ROUTE's entry (Retrieval Slices 1 + A): the 10-Q/10-K path when the company's newest
    periodic filing is a 10-Q or 10-K, else the annual path (20-F/40-F, FLAG-only): the cover SHARES candidate
    (Slice 1) plus the statements CASH/RUNWAY candidate or its honest ``runway_empty_reason`` (Slice A), with
    the DISTINCT empty reasons on ``ExtractionResult``. The decision is ``_takes_annual_path`` (V2 extractor
    correctness PR 2): a company that moved from 10-Q to 20-F goes annual even though it once filed 10-Qs.

    The dark-name trigger (no 10-Q AND no 10-K, or a newer 20-F/40-F) is checked FIRST from submissions because
    the periodic path fetches companyfacts up front and fails loudly for the no-companyfacts names (GLAS/CRLBF/TRSG — data.sec.gov serves 404 there) that
    the annual path must still serve cover-only.

    ``values_only`` is handed to the periodic path (``extract_for_security``: no second document for purity).
    The annual path already reads one document for both value types, so it has nothing to skip.
    """
    cik = int(cik)
    subs = fetch_submissions(client, cik)
    if not _takes_annual_path(subs):
        return ExtractionResult(
            facts=extract_for_security(client, cik, cfg=cfg, values_only=values_only)
        )
    # Lazy import keeps the module DAG acyclic: the annual modules import this module's URL helpers.
    # annual_facts_for_security fetches the annual document ONCE and feeds BOTH the cover-shares and
    # the statements-runway extractors from that one text (never a second multi-MB pull).
    from ingest.edgar.annual_runway import annual_facts_for_security

    return annual_facts_for_security(client, cik, cfg=cfg)


__all__ = [
    "COVER_AFTER_FILING",
    "RefusedCover",
    "cover_after_filing",
    "extract_facts",
    "extract_for_security",
    "extract_with_annual_fallback",
    "ExtractedFact",
    "refused_cover_note",
    "screen_cover_rows",
]
