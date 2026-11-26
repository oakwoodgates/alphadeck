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
  ``cover-after-filing`` flag + the note. Given the values filing's RAW document, a FLAG count is then re-read from
  its own inline XBRL (``ingest.edgar.inline_xbrl``): a multi-class cover offers the per-class sum dated by the
  cover's own as-of instant, flagged ``class-outside-eps`` when the filing's EPS share base leaves a class out; a
  single count newer than companyfacts' is offered flagged ``companyfacts-behind``. Either is AUTO when the same
  filing's EPS share base agrees within ``ExtractorConfig.cover_eps_tolerance`` (A1, ``_cover_applies``).
- **cash_burn** (runway): cash = cash+equiv + the same-dated short-term investments + long-term MARKETABLE
  securities (never the generic long-term investments line), the cash read under its most current label (the
  restricted-cash total says so in the note) and every component named in the note; a clean ~quarter of
  operating cash flow (read under its most current label, plain or continuing operations) ending on the cited
  filing's period, with cash of that date -> ``AUTO``; a burn spanning more or less than about a quarter, a burn
  or cash dated off the cited period, an underivable year-to-date column (or one derived across filings whose
  fiscal-year starts differ by days), a large one-time reconciliation line (named, with its printed line), or a
  composition that cannot be established (cash tags that disagree, a combined line that does not
  reconcile, investments only on a later balance sheet) -> ``FLAG`` (raw + locate). Given the values filing's RAW
  document, a companyfacts whose newest cash or operating cash flow ends before the cited period is read beside the
  document's own inline XBRL at the period end (``companyfacts-behind``; AUTO when every check passes on those figures,
  A2), and a FLAG's note names what the document shows that the reader does not count (``_cash_candidate``).
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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from domain.burn_pace import YtdPace, pace_caveat, pace_segment
from domain.config import DEFAULT_EXTRACTOR_CONFIG, ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, LocatedPassage, Tier
from domain.settings import get_settings
from ingest.edgar import inline_xbrl
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


def _shares(
    facts: dict,
    tenq_text: str,
    ref: str,
    period_end: date,
    *,
    raw: str | None = None,
    filed: date | None = None,
    cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG,
) -> ExtractedFact:
    """The shares candidate over the cover rows ``screen_cover_rows`` keeps; a refused row rides the candidate as
    the ``cover-after-filing`` flag and a sentence in its note (visible on the Workbench card, and stored with the
    fact when it is applied or ratified). With the filing's ``raw`` document a FLAG candidate is re-read from its
    inline XBRL (``_inline_shares``); without it (or on AUTO) the candidate is exactly the companyfacts one.
    """
    rows, refused = screen_cover_rows(
        _rows(facts, "dei", "EntityCommonStockSharesOutstanding", "shares")
    )
    fact = _shares_candidate(rows, tenq_text, ref, period_end)
    if raw is not None and fact.tier is Tier.FLAG:
        fact = _inline_shares(fact, rows, raw, tenq_text, ref, period_end, filed, cfg)
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
            "flags": _with(fact.flags, COVER_AFTER_FILING),
            "note": f"{note} {refused_cover_note(refused)}",
        }
    )


def _with(flags: list[str], flag: str) -> list[str]:
    return flags if flag in flags else [*flags, flag]


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


# THE FILING'S OWN COVER (W1-1, 2026-11-26; docs/EXTRACTOR.md "Shares on the periodic path"). companyfacts drops
# the class dimension (every per-class cover count) and misses combined multi-registrant filings for months (CEG's
# Q3 10-Q: its Q2 count under the Q3 link), while the cited document tags the cover itself. The two flags the inline
# re-read adds: the EPS share base leaves a cover class out (the sum is still offered); the filing's single count is
# newer than companyfacts' newest cover row.
CLASS_OUTSIDE_EPS = "class-outside-eps"
COMPANYFACTS_BEHIND = "companyfacts-behind"
# The cleaned-text span the cover's counts may occupy for one located passage: a retrieval window, not a cutoff (a
# cover too wide to locate keeps the old passage).
_COVER_SPAN = 1500


def _inline_shares(
    fact: ExtractedFact,
    rows: list[dict],
    raw: str,
    text: str,
    ref: str,
    period_end: date,
    filed: date | None,
    cfg: ExtractorConfig,
) -> ExtractedFact:
    """A FLAG share-count candidate re-read from the filing's inline XBRL: FLAG unless the A1 rule below applies, and
    ``fact`` unchanged when the document tags no usable cover, or tags one count no newer than companyfacts'.

    - **multi-class** (two or more classes of common stock on the cover): the per-class sum (or the total the filing
      tags beside its parts), dated by the cover's own as-of instant, flag ``dual-class``; plus
      ``class-outside-eps`` when the EPS share base of the cited period leaves a class out
      (``inline_xbrl.classes_outside_eps``). The sum is still offered and the note carries both figures.
    - **one count** newer than companyfacts' newest cover row (or with no row): that count, dated by its instant,
      flag ``companyfacts-behind`` (it replaces ``stale-cover`` / ``no-companyfacts``: the offer is no longer the
      stale or missing one).
    - **AUTO** (A1, operator decision D4): either reading, when ``_cover_applies`` (the EPS share base agrees within
      ``cfg.cover_eps_tolerance``, plain classes, one as-of date between the period end and the filing date): no flag,
      the note states both figures. Within the tolerance a cover above the base is not ``class-outside-eps``.

    Never a ``dei:LegalEntityAxis`` member (another registrant of a combined filing: EXC's parent count only), an ADR
    line, a preferred class or warrants (``inline_xbrl.read_cover``). An inline instant after the filing date is
    refused like a companyfacts row (``cover_after_filing``): the candidate is kept, flagged and named in the note.
    """
    contexts = inline_xbrl.read_contexts(raw)
    cover = inline_xbrl.read_cover(raw, contexts)
    if cover is None:
        return fact
    if filed is not None and cover_after_filing(cover.asof, filed):
        return fact.model_copy(
            update={
                "flags": _with(fact.flags, COVER_AFTER_FILING),
                "note": f"{fact.note} Refused the filing's inline cover count {cover.figure:,.0f} as of "
                f"{cover.asof.isoformat()}, dated after its filing date {filed.isoformat()} — a cover count is never "
                "dated after its filing.",
            }
        )
    latest_end = max((r["end"] for r in rows), default=None)
    behind = latest_end is None or date.fromisoformat(latest_end) < cover.asof
    if not cover.multi_class and not behind:
        return fact
    eps = inline_xbrl.eps_share_base(
        raw, period_end, cfg.quarterly_span_min_days, cfg.pace_ytd_max_days, contexts
    )
    gap, tol = _eps_gap(cover, eps), cfg.cover_eps_tolerance
    applied = _cover_applies(cover, gap, period_end, filed, tol)
    passage = _inline_cover_passage(text, ref, cover)
    cf_vals = sorted({float(r["val"]) for r in rows if r["end"] == latest_end})
    cf_row = (
        f"companyfacts' newest cover row: {', '.join(f'{v:,.0f}' for v in cf_vals)} as of {latest_end}"
        if latest_end
        else "companyfacts has no cover-shares row"
    )
    if cover.multi_class:
        outside = inline_xbrl.classes_outside_eps(cover, eps)
        if outside is not None and tol is not None and gap is not None and gap <= tol:
            outside = None  # a cover at most the tolerance above the base: not evidence of a class outside (A1)
        flags = ["dual-class", CLASS_OUTSIDE_EPS] if outside else ["dual-class"]
        parts = " + ".join(f"{c.label} {c.value:,.0f}" for c in cover.counted)
        total = (
            f" ({cover.total.label} {cover.total.value:,.0f} is their total, not added again)"
            if cover.total
            else ""
        )
        note = (
            f"Multiple share classes on the cover's inline XBRL as of {cover.asof.isoformat()}: {parts} = "
            f"{cover.figure:,.0f}{total}. Total economic = the sum of all classes; confirm against the cover (the "
            f"A/B split is usually voting).{_set_aside_note(cover)} {_eps_note(eps)}"
            f"{_outside_note(outside, eps, cover)} {cf_row} (not used)."
        )
    else:
        flags = [COMPANYFACTS_BEHIND]
        note = (
            f"The filing's own cover count (inline XBRL): {cover.figure:,.0f} as of {cover.asof.isoformat()}; "
            f"companyfacts is behind the filing ({cf_row}).{_set_aside_note(cover)} {_eps_note(eps)} Confirm "
            "against the located cover."
        )
    if applied:
        assert gap is not None and tol is not None
        flags = []
        what = "sum" if cover.multi_class else "count"
        note = (
            f"Cover shares from the filing's own inline XBRL as of {cover.asof.isoformat()}: "
            f"{' + '.join(f'{c.label} {c.value:,.0f}' for c in cover.counted)}"
            f"{f' = {cover.figure:,.0f}' if cover.multi_class else ''}.{_set_aside_note(cover)} {_eps_note(eps)} The "
            f"{what} is {gap:+.2%} from it, within {tol:.0%}: applied automatically (plain classes of common stock, one "
            f"as-of date between the period end and the filing date). {cf_row} (not used)."
        )
    return fact.model_copy(
        update={
            "tier": Tier.AUTO if applied else fact.tier,
            "value": cover.figure,
            "event_date": cover.asof,
            "flags": flags,
            "located_passages": [passage] if passage else fact.located_passages,
            "note": note,
        }
    )


def _eps_gap(cover: inline_xbrl.CoverReading, eps: inline_xbrl.EpsBase | None) -> float | None:
    """How far the cover figure is from the EPS share base, as a fraction of the base (+ above it); None without a
    positive base."""
    if eps is None or eps.figure is None or eps.figure <= 0:
        return None
    return (cover.figure - eps.figure) / eps.figure


def _cover_applies(
    cover: inline_xbrl.CoverReading,
    gap: float | None,
    period_end: date,
    filed: date | None,
    tol: float | None,
) -> bool:
    """THE A1 RULE (operator decision D4, 2026-11-26): an inline cover reading is AUTO when the cover tags its counts at
    ONE as-of date, on or after the period end and not after the filing date; every count is a plain class of common
    stock (``CoverReading.plain``: no ADR line, preferred class, warrants, total beside its parts or class-less member
    beside classes; another registrant's count set aside is fine); and the figure is within ``tol`` of the same
    filing's EPS share base for the cited period (the independent reading: the cover and its printed text are one).
    ``tol`` None (the dial off) or no filing date: never."""
    if tol is None or gap is None or filed is None:
        return False
    if not cover.plain or len(cover.instants) != 1:
        return False
    return period_end <= cover.asof <= filed and abs(gap) <= tol


def _set_aside_note(cover: inline_xbrl.CoverReading) -> str:
    """One sentence per reason a cover count was not counted, plus the ADS caution when an ADR line is set aside."""
    if not cover.set_aside:
        return ""
    by_why: dict[str, list[str]] = {}
    for c, why in cover.set_aside:
        by_why.setdefault(why, []).append(f"{c.label} {c.value:,.0f}")
    out = "".join(f" Not counted, {why}: {', '.join(items)}." for why, items in by_why.items())
    if any("ADR" in why for why in by_why):
        out += " The count is of ordinary shares: check the ADS ratio before approving (the price may be per ADS)."
    return out


def _eps_note(eps: inline_xbrl.EpsBase | None) -> str:
    if eps is None or eps.figure is None:
        return "No EPS share base for the period is tagged in the document."
    per_class = (
        f" (per class: {', '.join(f'{inline_xbrl.member_label(m)} {v:,.0f}' for m, v in eps.by_class)})"
        if eps.by_class
        else ""
    )
    return (
        f"EPS share base (basic weighted average, {eps.start.isoformat()} to {eps.end.isoformat()}): "
        f"{eps.figure:,.0f}{per_class}."
    )


def _outside_note(
    outside: inline_xbrl.OutsideEps | None,
    eps: inline_xbrl.EpsBase | None,
    cover: inline_xbrl.CoverReading,
) -> str:
    if outside is None or eps is None or eps.figure is None:
        return ""
    if outside.how == "per-class":
        names = " and ".join(f"{c.label} ({c.value:,.0f})" for c in outside.outside)
        verb = "is" if len(outside.outside) == 1 else "are"
        found = (
            f" {names} {verb} outside the EPS share base: its per-class facts name no such class (the other "
            f"classes sum to {outside.inside_sum:,.0f})."
        )
    else:
        inside = " + ".join(f"{c.label} {c.value:,.0f}" for c in outside.inside)
        found = (
            f" The EPS share base {eps.figure:,.0f} is nearer part of the cover ({inside}) than its sum "
            f"{cover.figure:,.0f}: a class may be outside earnings."
        )
    return f"{found} The sum is offered; confirm which classes carry economics before approving."


def _shown_re(shown: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\d.,]){re.escape(shown)}(?![\d]|[.,]\d)")


def _inline_cover_passage(
    text: str, ref: str, cover: inline_xbrl.CoverReading
) -> LocatedPassage | None:
    """The cover passage: the first place in the cleaned text where the cover's counts, as printed, appear in
    document order within ``_COVER_SPAN`` (``_locate``'s first anchor hit was not the cover in 7 of 8 checked
    filings: a convertible-notes or SAFE note). None when they are not found together."""
    counts = sorted(
        [*cover.counted, *([cover.total] if cover.total else [])], key=lambda c: c.offset
    )
    first, rest = counts[0].shown, [c.shown for c in counts[1:]]
    for m in _shown_re(first).finditer(text):
        last = m.end()
        for shown in rest:
            hit = _shown_re(shown).search(text, m.start(), m.start() + _COVER_SPAN)
            if hit is None:
                break
            last = max(last, hit.end())
        else:
            excerpt = re.sub(r"\s+", " ", text[max(0, m.start() - 110) : last + 110]).strip()
            return LocatedPassage(
                kind="cover",
                source_ref=ref,
                anchor=first,
                excerpt=f"… {excerpt} …",
                offset=m.start(),
            )
    return None


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
# (``docs/EXTRACTOR.md``, "The uniform cash rule") is cash + equivalents + ALL marketable securities, current
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
    # A3 (operator decision D4, 2026-11-26): a balance-sheet line of debt securities, current (NVDA's 34,143M of
    # marketable securities). ``ExtractorConfig.debt_securities_counted`` off leaves it out (``_counted``).
    ("us-gaap", "DebtSecuritiesCurrent"),
]
# Long-term MARKETABLE securities: noncurrent lines that are explicitly debt securities, which the written rule
# counts (D2 keeps them: CRSP's long-dated Treasuries).
_LT_MARKETABLE = [
    ("us-gaap", "MarketableSecuritiesNoncurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesNoncurrent"),
    ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"),
    # the noncurrent sibling of the short-term ex-accrued-interest tag (the 2026-11-16 re-audit: CRUS's 324.4M)
    ("us-gaap", "DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent"),
    ("us-gaap", "HeldToMaturitySecuritiesNoncurrent"),
    ("us-gaap", "DebtSecuritiesNoncurrent"),  # A3, its noncurrent sibling
]
# The A3 tags: counted only while ``ExtractorConfig.debt_securities_counted`` is on, else named in a FLAG's note.
_DEBT_SECURITIES = ("DebtSecuritiesCurrent", "DebtSecuritiesNoncurrent")


def _counted(tags: list[tuple[str, str]], cfg: ExtractorConfig) -> list[tuple[str, str]]:
    """An investment list as the composition reads it: the A3 tags only while their dial is on."""
    return (
        tags if cfg.debt_securities_counted else [t for t in tags if t[1] not in _DEBT_SECURITIES]
    )


# THE COMPOSITION GUARDS (2026-11-26; the fork audit of the 18 AUTO-applied cash values found two wrong, both through a
# cash-like tag on a line that is not a cash-like investment). Equity securities are never counted; their balances are
# also the evidence that an investment tag of EQUAL value on the same date is that equity line under a cash-like tag
# (``investments_equity_guard``; CNP's "Investment in equity securities", AT&T, Charter and WBD stock, tagged both
# ``MarketableSecuritiesCurrent`` and ``EquitySecuritiesFvNiCurrentAndNoncurrent`` at 425M).
_EQUITY_SECURITIES = (
    "EquitySecuritiesFvNi",
    "EquitySecuritiesFvNiCurrent",
    "EquitySecuritiesFVNINoncurrent",
    "EquitySecuritiesFvNiCurrentAndNoncurrent",
    "EquitySecuritiesFvNiAndWithoutReadilyDeterminableFairValue",
    "EquitySecuritiesWithoutReadilyDeterminableFairValueAmount",
    "AvailableForSaleSecuritiesEquitySecurities",
    "AvailableForSaleSecuritiesEquitySecuritiesCurrent",
    "AvailableForSaleSecuritiesEquitySecuritiesNoncurrent",
    "MarketableSecuritiesEquitySecurities",
    "TradingSecuritiesEquity",
)
# The statement tables (``investments_statement_guard``): a table of the raw document holding the registrant's own cash
# fact, or one of these balance-sheet totals, on the cash date. A counted investment tagged on that date outside them
# and only in a DEFERRED-TAX table (one holding a ``_DEFERRED_TAX`` fact) is a tax-note line, never a balance (LEXX:
# ``MarketableSecuritiesNoncurrent`` 349,120 on the "Marketable securities" deferred-tax row). Narrowed from "anywhere
# outside the statements": on the fork's applied population that also moved 18 of 100 replayed values (13 to FLAG),
# mostly debt securities a filer tags only in its investments note or in prose, the balance sheet printing them inside
# a broader line.
_STATEMENT_TOTALS = frozenset(
    {"Assets", "AssetsCurrent", "AssetsNoncurrent", "LiabilitiesAndStockholdersEquity"}
)
_DEFERRED_TAX = ("DeferredTax", "DeferredIncomeTax")
# the annual path's flag: an investment seen, quoted, not counted
_UNCONFIRMED = "investments-unconfirmed"


# UNSPLIT debt-security TOTALS (the 2026-11-16 re-audit: GKOS keeps 174.4M of short-term investments only under
# ``AvailableForSaleSecuritiesDebtSecurities``), each mapped to its own (current, noncurrent) parts. Debt only:
# ``AvailableForSaleSecurities`` and ``MarketableSecurities`` can hold equity stakes and are never read. A total
# is not a plain addend: it may restate parts already read, and filers often tag securities classed as CASH
# EQUIVALENTS into it (GM's and NTSK's totals hold amounts already inside cash) or restricted holdings (NHC).
# So (``_compose``): beside its own parts it is only a CHECK (parts above it -> ``investments-unreconciled``; a
# total above them leaves the excess uncounted, named); beside other investment tags it is named, not counted;
# ALONE on the cash date it is counted only when the same-dated combined line confirms cash + it (AUTO).
# Otherwise it is QUOTED, NOT ADDED, and FLAGGED ``investments-unreconciled`` (the third re-audit, operator
# decision: of 17 such offers 10 were overstated — NVDA's 46.9bn total already holds cash equivalents, EHC's,
# NHC's and TPC's securities are restricted — and a steward's approve stores the offered number, so the offer
# is the conservative one; tags alone cannot tell it apart from cash equivalents: BMRN's same-shaped tag is not
# an addable amount).
_DEBT_TOTALS: dict[str, tuple[str, str]] = {
    "AvailableForSaleSecuritiesDebtSecurities": (
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
    ),
    "DebtSecuritiesAvailableForSaleExcludingAccruedInterest": (
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent",
    ),
}
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
    # A6 (operator decision D4, 2026-11-26): the AGGREGATE working-capital line, a sum of routine lines and not a line
    # item (FEIM's "Net changes in operating assets and liabilities"); routine only while
    # ``ExtractorConfig.operating_capital_routine`` is on
    "increasedecreaseinoperatingcapital",
)
_OPERATING_CAPITAL = "increasedecreaseinoperatingcapital"
# The reconciliation lines' tag prefix (the one-time check reads every such us-gaap line).
_LINE_PREFIX = "IncreaseDecreaseIn"


def _is_routine_wc(concept: str, cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG) -> bool:
    c = concept.lower()
    if "accrued" in c or "settlement" in c or "milestone" in c:
        return False  # an accrued/settlement line can carry a one-time obligation (SMR's ENTRA1)
    return any(
        s in c for s in _ROUTINE_WC if s != _OPERATING_CAPITAL or cfg.operating_capital_routine
    )


def _is_one_time_candidate(concept: str, cfg: ExtractorConfig = DEFAULT_EXTRACTOR_CONFIG) -> bool:
    """A NON-ROUTINE operating-activities reconciliation line — an accrued/settlement change, not routine
    working capital and not a non-cash add-back (SBC/D&A don't start with ``IncreaseDecreaseIn``). This is
    where a one-time settlement PAYMENT lands (SMR's ENTRA1 in AccountsPayableAndAccruedLiabilities).
    """
    return concept.startswith(_LINE_PREFIX) and not _is_routine_wc(concept, cfg)


# A QUARTER DERIVED ACROSS FILINGS (W1-4, 2026-11-26; docs/EXTRACTOR.md "The burn"). Two filings can date one fiscal
# year's start days apart (a 52/53-week year: FUBO's 10-K from 2025-09-28, its 10-Qs from 2025-10-01), so no earlier
# year to date has the same start. One of the same tag starting at most ``ExtractorConfig.ytd_start_slack_days`` away
# is subtracted and the quarter offered flagged, never AUTO (operator decision A5, 2026-11-26).
YTD_DERIVED_ACROSS_FILINGS = "ytd-derived-across-filings"


@dataclass(frozen=True)
class _Quarter:
    """The latest ~quarter of operating cash flow, with what it covers and where it came from.

    ``start`` is the native column's start, or the subtracted prior year to date's END for a derived quarter (what
    the note prints, ``burn over start → end``); ``days`` is the span between the two, the figure the
    ``quarterly_span_*_days`` window judges (a calendar quarter reads 90 native, 91 or 92 derived). A derived quarter's
    ``ytd_start`` is the year to date's own start; derived across filings, ``prior_start`` and ``prior_accn`` are the
    subtracted one's.
    """

    value: float
    start: str
    end: str
    basis: str
    concept: str
    ytd_start: str = ""
    prior_start: str = ""
    prior_accn: str = ""

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
    - ``"ytd-derived-across-filings"`` — no same-start prior, but an earlier YTD of the same tag starting within
      ``cfg.ytd_start_slack_days`` (``_near_start_prior``): YTD − that one, always flagged.
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
        near = _near_start_prior(durations, row, cfg.ytd_start_slack_days)
        if near is not None:
            return _Quarter(
                float(row["val"]) - float(near["val"]),
                near["end"],
                row["end"],
                YTD_DERIVED_ACROSS_FILINGS,
                concept,
                ytd_start=row["start"],
                prior_start=near["start"],
                prior_accn=near.get("accn") or "",
            )
        return _Quarter(float(row["val"]), row["start"], row["end"], "ytd-raw", concept)
    return _Quarter(
        float(row["val"]) - float(prior["val"]),
        prior["end"],
        row["end"],
        "derived",
        concept,
        ytd_start=row["start"],
    )


def _start_gap(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _near_start_prior(durations: list[dict], row: dict, slack: int) -> dict | None:
    """The latest-ending earlier year to date of ``row``'s tag whose start lies 1 to ``slack`` days from ``row``'s (on a
    tie: the nearest start, then the earliest filed), or None. Never a start further off: AMSC's six months tagged
    from January 1, 90 days before its fiscal year's April start, cannot be told from a real one.
    """
    near = [
        r
        for r in durations
        if row["start"] < r["end"] < row["end"]
        and 0 < _start_gap(r["start"], row["start"]) <= slack
    ]
    if not near:
        return None
    end = max(r["end"] for r in near)
    return min(
        (r for r in near if r["end"] == end),
        key=lambda r: (_start_gap(r["start"], row["start"]), r.get("filed", ""), r["start"]),
    )


def _across_sentence(q: _Quarter) -> str:
    """The note's sentence for a quarter derived across filings."""
    accn = f" ({q.prior_accn})" if q.prior_accn else ""
    return (
        f"no earlier year to date starts on {q.ytd_start}: the quarter is this year to date {q.ytd_start} → {q.end} "
        f"minus the previous filing's{accn} {q.prior_start} → {q.start} (same tag), starts "
        f"{_start_gap(q.ytd_start, q.prior_start)} days apart, so it is off by those days' cash flow"
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


# NAMING WHAT TRIPPED IT (W1-4, 2026-11-26): the flag used to locate the first place a bare figure appeared and name
# no line, and the steward review misread two of them (FEIM's working-capital aggregate as a financing item, COHR's
# accrued compensation as an inventory build). Lines named at most (the note counts the rest); how far back a printed
# line's words are read; the scale word a sentence prints after its figure.
_ONE_TIME_NAMED = 3
_LINE_SPAN = 120
_LINE_WORDS_RE = re.compile(r"[A-Za-z][^\d]*$")  # the words after the last figure before this one
_SCALE_WORD_RE = re.compile(r"\s*(million|billion|thousand)\b", re.IGNORECASE)
# zero-width characters some filers print between cells; how far back a parenthetical's opening is looked for
_INVISIBLE_RE = re.compile("[\u200b-\u200d\u2060\ufeff]")
_PAREN_SPAN = 400


@dataclass(frozen=True)
class _OneTime:
    """What tripped ``possible-one-time``: a passage on each named line as printed, and the note's sentence."""

    passages: list[LocatedPassage]
    stated: str


def _detect_one_time(
    facts: dict,
    text: str,
    ref: str,
    start: str,
    end: str,
    op_cash_use: float,
    cfg: ExtractorConfig,
    raw: str | None = None,
    ytd_start: str | None = None,
) -> _OneTime | None:
    """A NON-ROUTINE operating line whose magnitude is a large fraction of |op-cash-use| is anomalous ->
    flag. Generic (never branches on a specific item); the filing text supplies the located passage — the flagged
    AMOUNT in the cash-flow statement (or a one-time keyword). The operator decides whether to back it out.

    It names what tripped it (W1-4): every such line, largest first, by tag and figure in the note, and a passage on
    each as printed, anchored by its words and figure ("Net changes in operating assets and liabilities (4,102)").
    Given the ``raw`` document, the passage sits on the tagged figure itself; else on its first whole-number print.

    ``ytd_start`` (A2: a derived quarter read beside a lagging companyfacts): each line's quarter is derived like the
    quarter's own, its year to date ``ytd_start`` → ``end`` minus its year to date ``ytd_start`` → ``start`` (a side
    not tagged counts 0), and the passage sits on the year to date's printed line.
    """
    threshold = cfg.one_time_line_fraction * abs(op_cash_use)
    lines: list[tuple[str, float]] = []
    for concept in facts.get("us-gaap", {}):
        if not _is_one_time_candidate(concept, cfg):
            continue
        v = (
            _value_for_period(facts, concept, start, end)
            if ytd_start is None
            else _derived_line(facts, concept, ytd_start, start, end)
        )
        if v is not None and abs(v) >= threshold:
            lines.append((concept, v))
    if not lines:
        return None
    lines.sort(key=lambda cv: (-abs(cv[1]), cv[0]))
    named = lines[:_ONE_TIME_NAMED]
    contexts = inline_xbrl.read_contexts(raw) if raw else None
    passages: list[LocatedPassage] = []
    for concept, v in named:
        at_v, at_start = (
            (v, start)
            if ytd_start is None
            else (_ytd_line(facts, concept, ytd_start, end), ytd_start)
        )
        hit = (
            _tagged_figure(raw, contexts, text, concept, at_v, at_start, end)
            if raw and contexts and at_v is not None
            else None
        ) or (_printed_figure(text, at_v) if at_v is not None else None)
        if hit is not None and all(p.offset != hit[0] for p in passages):
            passages.append(_line_passage(text, ref, *hit))
    if not passages:
        passages.append(
            _locate(text, ref, "cash-flow", list(cfg.one_time_keywords))
            or LocatedPassage(
                kind="cash-flow",
                source_ref=ref,
                anchor="operating cash flow",
                excerpt="… a single non-routine operating line dominates operating cash use — review the "
                "cash-flow statement for a one-time item …",
            )
        )
    more = len(lines) - len(named)
    listed = ", ".join(f"{c} {_m(abs(v))}" for c, v in named) + (
        f" and {more} more" if more else ""
    )
    each = "each " if len(lines) > 1 else ""
    return _OneTime(
        passages,
        f"possible-one-time: {listed}, {each}at least {cfg.one_time_line_fraction:g} of the operating cash flow's "
        f"{_m(abs(op_cash_use))}",
    )


def _ytd_line(facts: dict, concept: str, ytd_start: str, end: str) -> float | None:
    return _value_for_period(facts, concept, ytd_start, end)


def _derived_line(facts: dict, concept: str, ytd_start: str, start: str, end: str) -> float | None:
    """A reconciliation line's derived quarter: its year to date to ``end`` minus its year to date to ``start`` (same
    start); a side not tagged counts 0; None when neither is."""
    ytd, prior = _ytd_line(facts, concept, ytd_start, end), _ytd_line(
        facts, concept, ytd_start, start
    )
    if ytd is None and prior is None:
        return None
    return (ytd or 0.0) - (prior or 0.0)


def _lines_tagged(facts: dict, start: str, end: str) -> bool:
    """Whether any reconciliation line (``IncreaseDecreaseIn*``) is tagged for ``start`` → ``end``."""
    return any(
        r.get("start") == start and r["end"] == end
        for concept, node in facts.get("us-gaap", {}).items()
        if concept.startswith(_LINE_PREFIX)
        for r in node.get("units", {}).get("USD", [])
    )


def _one_time_runs(facts: dict, q: _Quarter) -> bool:
    """Whether the one-time check has lines to read for the offered quarter (A2): a native quarter's own lines; a
    derived quarter's on both years to date it is derived from. Never on any other basis (they are FLAGs already).
    """
    if q.basis == "quarter":
        return _lines_tagged(facts, q.start, q.end)
    if q.basis == "derived":
        return _lines_tagged(facts, q.ytd_start, q.end) and _lines_tagged(
            facts, q.ytd_start, q.start
        )
    return False


def _tagged_figure(
    raw: str,
    contexts: dict[str, inline_xbrl.Context],
    text: str,
    concept: str,
    v: float,
    start: str,
    end: str,
) -> tuple[int, str] | None:
    """Where the filing prints the line's own tagged fact (undimensioned, of the burn's period, the same value) in the
    cleaned text ``clean_filing_text(raw)``, and its printed figure; None when it is not found there.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    for f in inline_xbrl.read_facts(raw, frozenset({concept}), contexts):
        if not (_own(f) and f.context.start == s and f.context.end == e and f.value is not None):
            continue
        if abs(abs(f.value) - abs(v)) >= 0.5:
            continue
        at = len(clean_filing_text(raw[: f.offset]))
        m = _shown_re(f.shown).search(text, max(0, at - 2))
        if m and m.start() <= at + 2:
            return m.start(), f.shown
    return None


def _printed_figure(text: str, v: float) -> tuple[int, str] | None:
    """The line's figure as first printed whole in the text: full dollars, thousands, or millions to one decimal
    (with or without a thousands comma; never inside a longer number: "4,102" is not the end of "14,102").
    """
    n = abs(int(v))
    printed = (f"{n:,}", f"{n // 1000:,}", f"{n / 1e6:.1f}", f"{n / 1e6:,.1f}")
    for shown in dict.fromkeys(printed):
        if shown in ("0", "0.0"):
            continue
        m = _shown_re(shown).search(text)
        if m:
            return m.start(), shown
    return None


def _line_passage(text: str, ref: str, at: int, shown: str) -> LocatedPassage:
    excerpt = re.sub(r"\s+", " ", text[max(0, at - 110) : at + 110]).strip()
    return LocatedPassage(
        kind="cash-flow",
        source_ref=ref,
        anchor=_printed_line(text, at, shown),
        excerpt=f"… {excerpt} …",
        offset=at,
    )


def _printed_line(text: str, at: int, shown: str) -> str:
    """The figure printed at ``at`` with its line's words (those since the previous figure) and its parentheses,
    "$" and scale word as printed: "Net changes in operating assets and liabilities (4,102)", "we recognized $4.0
    million". Words ending a parenthetical that prints figures of its own are replaced by the words before it
    (SMCI: "Accrued liabilities (including changes in related party balances of $(65) and $554 ..., respectively)
    445,557" reads "Accrued liabilities (…) 445,557"). The figure alone when no words precede it."""
    lo = max(0, at - _LINE_SPAN)
    m = _LINE_WORDS_RE.search(text, lo, at)
    start = at if m is None else m.start()
    if m is not None and start == lo and lo > 0:
        start = text.find(" ", start, at) + 1 or at  # the window cut a word in two: drop it
    words = text[start:at]
    close = _unbalanced_close(words)
    opened = None if close is None else _opening_paren(text, start + close)
    if close is not None and opened is not None:
        before = _LINE_WORDS_RE.search(text, max(0, opened - _LINE_SPAN), opened)
        if before is not None:
            words = text[before.start() : opened].rstrip() + " (…)" + words[close + 1 :]
    words = re.sub(r"\s+", " ", _INVISIBLE_RE.sub("", words))
    lead = re.search(r"[\s$(]*$", words).group(0)  # type: ignore[union-attr]
    label = words[: len(words) - len(lead)].strip()
    after = text[at + len(shown) : at + len(shown) + 20]
    figure = f"({shown})" if "(" in lead and after.lstrip().startswith(")") else shown
    figure = ("$" if "$" in lead else "") + figure
    scale = _SCALE_WORD_RE.match(after)
    if scale:
        figure += f" {scale.group(1)}"
    return f"{label} {figure}".strip()


def _unbalanced_close(words: str) -> int | None:
    """The index of the first ")" in ``words`` with no "(" before it in ``words``, or None."""
    depth = 0
    for i, ch in enumerate(words):
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return i
            depth -= 1
    return None


def _opening_paren(text: str, close: int) -> int | None:
    """The "(" that the ")" at ``close`` closes, at most ``_PAREN_SPAN`` characters back, or None."""
    depth = 0
    for i in range(close - 1, max(-1, close - _PAREN_SPAN), -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            if depth == 0:
                return i
            depth -= 1
    return None


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
    reconciled: bool  # cash + the current investments meet the combined line (or there is none)
    # every unsplit debt total on the cash date (``_DEBT_TOTALS``); counted only when nothing else is tagged
    # and the combined line confirms cash + it (``counted_unsplit``), else quoted (``quoted_unsplit``)
    totals: list[tuple[str, float]] = field(default_factory=list)
    # (total tag, total, its own current + noncurrent parts on the cash date, "reconciles" | "short" | "exceeds")
    total_checks: list[tuple[str, float, float, str]] = field(default_factory=list)
    # (tag, value, why, "sti" | "lt") of each investment tag on the cash date a guard left out (the note's sentence)
    excluded: list[tuple[str, float, str, str]] = field(default_factory=list)
    # the statement guard lowered the figure: a counted investment is tagged only in a deferred-tax table
    unconfirmed: bool = False

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
    def unsplit(self) -> list[tuple[str, float]]:
        """The unsplit debt totals standing ALONE: no current or noncurrent investment tag is on the cash date
        (beside one, a total may restate it — a check or a name, never an addend)."""
        return [] if self.sti or self.lt_marketable or self.excluded else self.totals

    @property
    def unsplit_value(self) -> float | None:
        best = self._largest(self.unsplit)
        return None if best is None else best[1]

    @property
    def counted_unsplit(self) -> list[tuple[str, float]]:
        """A lone total COUNTS only when the same-dated combined line confirms cash + it (established as the
        short-term investments, none of it inside cash)."""
        return self.unsplit if self.combined is not None and self.reconciled else []

    @property
    def quoted_unsplit(self) -> list[tuple[str, float]]:
        """A lone total nothing confirms: QUOTED in the note, never added (it may already be inside cash
        equivalents, NVDA, or restricted, EHC/NHC/TPC); the steward adds the eligible part."""
        return [] if self.counted_unsplit else self.unsplit

    @property
    def uncounted_totals(self) -> list[tuple[str, float]]:
        """Totals beside other investment tags with no part of their own to check against: named, not counted."""
        checked = {t for t, _, _, _ in self.total_checks}
        return [(t, v) for t, v in self.totals if not self.unsplit and t not in checked]

    @property
    def established(self) -> bool:
        """False -> ``investments-unreconciled``: the combined line is missed, a total is smaller than its own
        parts, or a counted unsplit total is not confirmed by the combined line (it may hold cash equivalents).
        """
        if not self.reconciled or any(s == "short" for _, _, _, s in self.total_checks):
            return False
        return not self.unsplit or self.combined is not None

    @property
    def investments(self) -> float | None:
        if not self.sti and not self.lt_marketable and not self.counted_unsplit:
            return None  # nothing counted on the cash date — None, never a fake 0
        counted = self.unsplit_value if self.counted_unsplit else None
        return (self.sti_value or 0.0) + (self.lt_value or 0.0) + (counted or 0.0)


def _compose(
    facts: dict,
    cash: float,
    end: str,
    cfg: ExtractorConfig,
    outside: frozenset[str] = frozenset(),
) -> _Composition:
    """The cash composition on ONE balance-sheet date (``end``, the cash instant's): the investments read AT that
    date (the largest per role, never a sum of overlapping tags), the never-cash lines named, the cash tags'
    nesting checked, and the combined line used only as a reconciliation.

    The guards leave out a non-zero investment tag whose value equals a same-dated equity-securities balance
    (``investments_equity_guard``) or that the raw document tags outside its statement tables and only in a
    deferred-tax table (``outside``, ``_outside_statements``); each is named in the note, and the second sets
    ``unconfirmed`` when it lowers a role's figure."""

    def seen(lst: list[tuple[str, str]]) -> list[tuple[str, float]]:
        out = [(c, _at(facts, c, end)) for _, c in lst]
        return [(c, v) for c, v in out if v is not None]

    sti_tags, lt_tags = _counted(_STI, cfg), _counted(_LT_MARKETABLE, cfg)
    older: list[tuple[str, str]] = []
    newer: list[tuple[str, str]] = []
    for _, c in sti_tags + lt_tags:
        rows = _rows(facts, "us-gaap", c, "USD")
        if rows and _at(facts, c, end) is None:
            newest = max(r["end"] for r in rows)
            (newer if newest > end else older).append((c, newest))
    cash_tags = seen(_CASH)
    vals = dict(cash_tags)
    disagree = any(n in vals and w in vals and vals[n] > vals[w] for n, w in _CASH_NESTING)
    sti = seen(sti_tags)
    lt_marketable = seen(lt_tags)
    equity = (
        [(c, v) for c in _EQUITY_SECURITIES if (v := _at(facts, c, end))]
        if cfg.investments_equity_guard
        else []
    )
    excluded: list[tuple[str, float, str, str]] = []

    def guard(role: list[tuple[str, float]], name: str) -> tuple[list[tuple[str, float]], bool]:
        """The role's tags the guards keep, and whether the statement guard lowered its figure."""
        kept: list[tuple[str, float]] = []
        not_equity: list[tuple[str, float]] = []
        for t, v in role:
            eq = next((e for e, ev in equity if v and abs(v - ev) < 0.5), None)
            if eq is not None:
                why = f"equals the same-dated equity securities {eq} {_m(v)} (equity securities are never cash)"
                excluded.append((t, v, why, name))
                continue
            not_equity.append((t, v))
            if v and t in outside:
                why = (
                    "is tagged in the filing only outside the statement tables that present its cash, in a"
                    " deferred-tax table (a tax-note line, not a balance), so it is quoted, not counted"
                )
                excluded.append((t, v, why, name))
                continue
            kept.append((t, v))
        before = _Composition._largest(not_equity)
        after = _Composition._largest(kept)
        return kept, (before[1] if before else 0.0) > (after[1] if after else 0.0)

    sti, sti_lowered = guard(sti, "sti")
    lt_marketable, lt_lowered = guard(lt_marketable, "lt")
    totals = seen([("us-gaap", t) for t in _DEBT_TOTALS])
    tol = cfg.investments_reconcile_tolerance

    def within(a: float, b: float) -> bool:
        return abs(a - b) <= tol * max(abs(a), abs(b))

    # a total beside its OWN current/noncurrent parts is a check: it holds them, so it can never be smaller
    # (``short``); a larger total holds something more (securities classed as cash equivalents, already in cash,
    # or an untagged part) that tags cannot place — the excess is named, never added (conservative).
    total_checks: list[tuple[str, float, float, str]] = []
    for t, v in totals:
        own = [x for x in (_at(facts, p, end) for p in _DEBT_TOTALS[t]) if x is not None]
        if own:
            parts_sum = sum(own)
            status = (
                "reconciles" if within(v, parts_sum) else ("short" if v < parts_sum else "exceeds")
            )
            total_checks.append((t, v, parts_sum, status))
    combined = _at(facts, _COMBINED, end)
    reconciled = True
    if combined is not None:
        if sti:
            current = max(v for _, v in sti)
        elif not lt_marketable and totals:
            current = max(
                v for _, v in totals
            )  # a counted unsplit total, confirmed only by this line
        else:
            current = 0.0
        reconciled = within(cash + current, combined)
    return _Composition(
        cash=cash,
        sti=sti,
        lt_marketable=lt_marketable,
        not_counted=seen(_NOT_CASH),
        older=older,
        newer=newer,
        cash_tags=cash_tags,
        disagree=disagree,
        combined=combined,
        reconciled=reconciled,
        totals=totals,
        total_checks=total_checks,
        excluded=excluded,
        unconfirmed=sti_lowered or lt_lowered,
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
    left_out = {role for _, _, _, role in c.excluded}
    if c.sti:
        parts.append(f"short-term investments {_m(c.sti_value or 0.0)} ({tags(c.sti)})")
    else:
        what = "counted" if "sti" in left_out else "tagged on the cash date"
        missing.append(f"no short-term investments {what}")
    if c.lt_marketable:
        parts.append(
            f"long-term marketable securities {_m(c.lt_value or 0.0)} ({tags(c.lt_marketable)})"
        )
    else:
        what = "counted" if "lt" in left_out else "tagged on the cash date"
        missing.append(f"no long-term marketable securities {what}")
    if c.counted_unsplit:
        # the only investment tag on the cash date is an unsplit debt total the combined line confirms: counted
        parts.append(
            f"debt securities {_m(c.unsplit_value or 0.0)} ({tags(c.unsplit)}), a total not split into"
            " current and noncurrent"
        )
    if c.unsplit:
        missing = []  # a total IS tagged: "no investments tagged" would be false
    out = f"cash_usd {_m(total)} = " + " + ".join(parts)
    if missing:
        out += "; " + "; ".join(missing)
    for t, v, why, _ in c.excluded:
        out += f"; not counted: {t} {_m(v)} {why}"
    if c.quoted_unsplit:
        confirm = (
            f"no same-dated {_COMBINED} line confirms it"
            if c.combined is None
            else f"the same-dated {_COMBINED} line does not confirm it"
        )
        out += (
            f"; not counted: a debt-securities total of {_m(c.unsplit_value or 0.0)}"
            f" ({tags(c.quoted_unsplit)}) was reported on the cash date, a total not split into current and"
            f" noncurrent; {confirm}, and tags cannot tell whether it is already inside cash equivalents or"
            " restricted; the steward should add the eligible part from the balance sheet"
        )
    for t, v, own, status in c.total_checks:
        if status == "reconciles":
            out += f"; {t} {_m(v)} reconciles to its current + noncurrent parts {_m(own)}"
        elif status == "short":
            out += (
                f"; {t} {_m(v)} ≠ its current + noncurrent parts {_m(own)} (a total smaller than its"
                " own parts: the composition is contradicted)"
            )
        else:
            out += (
                f"; {t} {_m(v)} exceeds its current + noncurrent parts {_m(own)}; the {_m(v - own)}"
                " more is not counted (it may be securities classed as cash equivalents, already inside"
                " cash)"
            )
    if c.uncounted_totals:
        out += (
            "; not counted: "
            + ", ".join(f"{t} {_m(v)}" for t, v in c.uncounted_totals)
            + " (an unsplit debt-securities total beside the investment tags read; it may restate them)"
        )
    if c.not_counted:
        out += (
            "; not counted as cash: "
            + ", ".join(f"{t} {_m(v)}" for t, v in c.not_counted)
            + " (a long-term investments line, often stakes in other companies, never cash)"
        )
    if c.combined is not None:
        what = "the debt-securities total" if c.unsplit else "short-term investments"
        parts_sum = c.cash + (c.sti_value or c.unsplit_value or 0.0)
        out += (
            f"; cash + {what} reconciles to {_COMBINED} {_m(c.combined)}"
            if c.reconciled
            else f"; {_COMBINED} {_m(c.combined)} ≠ cash + {what} {_m(parts_sum)}"
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
    *,
    behind: str | None = None,
    raw: str | None = None,
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

    ``behind`` (``_cash_candidate``, W1-2): ``facts`` carries the cited filing's own rows beside a lagging
    companyfacts, and this sentence says so; it flags ``companyfacts-behind`` and rides the note. With
    ``cfg.cash_auto_companyfacts_behind`` (A2) it is AUTO instead when no other flag stands and the one-time check
    had the offered quarter's own lines to read (``_one_time_runs``; a derived quarter's lines derived like it).
    ``raw`` (W1-4): the values filing's raw document, where a one-time line's passage is found by its tagged fact,
    and where a counted investment tagged only in a deferred-tax table is found (``_outside_statements``,
    ``investments-unconfirmed``).
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
    outside = (
        _outside_statements(raw, cash_at[1], cash_pick[2], cfg)
        if raw is not None and cash_pick is not None and cash_at and cfg.investments_statement_guard
        else frozenset()
    )
    comp = _compose(facts, cash_at[0], cash_at[1], cfg, outside) if cash_at else None
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
        elif basis == YTD_DERIVED_ACROSS_FILINGS:
            # FLAG-only (W1-4): the quarter subtracts a year to date read from a start days apart; offered, flagged
            flags.append(YTD_DERIVED_ACROSS_FILINGS)
            basis_suffix = (
                ", derived across filings (YTD − the previous filing's YTD, see the flag)"
            )
            checks.append(_across_sentence(q))
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
        # A2: a derived quarter read beside a lagging companyfacts checks its own lines, derived like the quarter
        derive = behind is not None and cfg.cash_auto_companyfacts_behind and basis == "derived"
        ot = _detect_one_time(
            facts, tenq_text, ref, start, end, qval, cfg, raw, q.ytd_start if derive else None
        )
        if ot:
            flags.append("possible-one-time")
            passages.extend(ot.passages)
            checks.append(ot.stated)
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
        if not comp.established:
            flags.append("investments-unreconciled")
        if comp.newer:
            flags.append("investments-off-date")
        if comp.unconfirmed:
            flags.append(_UNCONFIRMED)
    if behind is not None:
        # A2 (operator decision D4): AUTO when no other flag stands and the one-time check had lines to read for the
        # offered quarter; else W1-2's flag
        auto = (
            cfg.cash_auto_companyfacts_behind
            and not flags
            and q is not None
            and _one_time_runs(facts, q)
        )
        if not auto:
            flags.append(COMPANYFACTS_BEHIND)
        checks.append(behind)
        if auto:
            checks.append(
                "applied: the period, span, composition and one-time checks pass on the filing's own figures"
            )
        elif cfg.cash_auto_companyfacts_behind and flags == [COMPANYFACTS_BEHIND]:
            checks.append(
                "the one-time check cannot run: no reconciliation line is tagged for the offered quarter's own "
                "period(s), so the value is not applied"
            )

    # a period or citation flag points at the statement it concerns (located, never interpreted)
    for kind, keys, raised in (
        (
            "cash-flow",
            ["operating activities", "cash flows"],
            {"burn-span", "burn-off-period", COMPANYFACTS_BEHIND, YTD_DERIVED_ACROSS_FILINGS}
            & set(flags),
        ),
        (
            "balance-sheet",
            ["cash and cash equivalents"],
            {"cash-after-period", COMPANYFACTS_BEHIND, _UNCONFIRMED} & set(flags),
        ),
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
        # every figure on this value was read from companyfacts' USD unit (``_pick_instant``, ``_compose``,
        # ``_quarter`` and ``_ytd_pace`` read no other unit; ``_filing_cash`` adds only the filing's USD facts), so
        # the value says so explicitly (D4); the nothing-read return above claims no currency
        statement_currency="USD",
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


# THE FILING'S OWN CASH (W1-2, 2026-11-26; docs/EXTRACTOR.md "The burn"). companyfacts misses combined multi-registrant
# filings for months (CEG's Q3 10-Q: its Q2 cash and burn under the Q3 link; 17 of the 27 waiting stale-cash or
# burn-off-period candidates on 2026-11-25), while the cited document tags its own statements. What is read from it:
# the concepts the reader reads from companyfacts, the registrant's own facts only (undimensioned, in USD), at the
# period end. Never another registrant of a combined filing (``dei:LegalEntityAxis``), never a subsidiary's.
def _filing_instants(cfg: ExtractorConfig) -> frozenset[str]:
    return frozenset(
        [c for _, c in _CASH + _counted(_STI, cfg) + _counted(_LT_MARKETABLE, cfg) + _NOT_CASH]
        + [*_DEBT_TOTALS, _COMBINED]
        + list(_EQUITY_SECURITIES if cfg.investments_equity_guard else ())
    )


_FILING_DURATIONS = frozenset(c for _, c in _OCF_TAGS)
# The reconciliation lines a document tags (A2 reads the registrant's own beside its cash, for the one-time check).
_LINE_TAG_RE = re.compile(r"name=\"us-gaap:(IncreaseDecreaseIn\w+)\"")
# Debt securities under tags the composition does not count while ``debt_securities_counted`` (A3) is off (NVDA:
# 34,143M of ``DebtSecuritiesCurrent`` beside 22,443M of cash): a FLAG's note names them, never adds them.
_DEBT_UNCOUNTED = _DEBT_SECURITIES
# A quoted lone debt total EQUAL to the same-dated cash-equivalents fair value sits inside cash already (AMGN: 12,621M
# both; operator decision A4, 2026-11-26: note evidence only). Only equality: a larger disclosure is no evidence (EBAY).
_CE_FAIR_VALUE = "CashAndCashEquivalentsFairValueDisclosure"
_USD = "iso4217:usd"


def _usd_facts(
    raw: str, contexts: dict[str, inline_xbrl.Context], concepts: frozenset[str]
) -> list[inline_xbrl.InlineFact]:
    """The document's numeric facts of ``concepts`` in USD (any context; the caller screens the dimensions)."""
    units = {k: v.lower() for k, v in inline_xbrl.read_units(raw).items()}
    return [
        f
        for f in inline_xbrl.read_facts(raw, concepts, contexts)
        if f.value is not None and units.get(f.unit) == _USD
    ]


def _own(f: inline_xbrl.InlineFact) -> bool:
    """The registrant's own fact: no dimension at all."""
    return not f.context.members and not f.context.other_axis


def _outside_statements(
    raw: str, end: str, cash_concept: str, cfg: ExtractorConfig
) -> frozenset[str]:
    """The counted investment tags the raw document tags on the cash date (``end``) outside its statement tables (a
    table holding the registrant's own ``cash_concept`` fact or a ``_STATEMENT_TOTALS`` fact on that date) and only in
    deferred-tax tables (``_DEFERRED_TAX``). Empty when the guard cannot run: no table holds the cash fact (statements
    laid out without tables, or a cash date the document does not tag). A tag the document never tags on that date, or
    tags in prose or another note, is not judged."""
    on = date.fromisoformat(end)
    tags = {c for _, c in _counted(_STI, cfg) + _counted(_LT_MARKETABLE, cfg)}
    spans = inline_xbrl.table_spans(raw)
    contexts = inline_xbrl.read_contexts(raw)
    tax = {
        inline_xbrl.table_of(spans, f.offset)
        for f in inline_xbrl.read_facts(raw, None, contexts)
        if f.concept.startswith(_DEFERRED_TAX)
    } - {None}
    read = frozenset({*tags, cash_concept, *_STATEMENT_TOTALS})
    at = [
        (f.concept, inline_xbrl.table_of(spans, f.offset))
        for f in _usd_facts(raw, contexts, read)
        if _own(f) and f.context.instant == on
    ]
    cash_tables = {t for c, t in at if c == cash_concept and t is not None}
    if not cash_tables or not tax:
        return frozenset()
    statements = cash_tables | {t for c, t in at if c in _STATEMENT_TOTALS and t is not None}
    where: dict[str, set[int | None]] = {}
    for c, t in at:
        if c in tags:
            where.setdefault(c, set()).add(t)
    return frozenset(c for c, ts in where.items() if not ts & statements and ts <= tax)


def _companyfacts_late(facts: dict, period_end: date, cfg: ExtractorConfig) -> list[str]:
    """What companyfacts is behind on: its newest cash instant, its newest operating-cash-flow column, each named
    when it ends before the cited period (empty: not behind)."""
    pe = period_end.isoformat()
    cash, q = _pick_instant(facts, _CASH), _quarter(facts, cfg)
    late = [f"its newest cash is as of {cash[1]}"] if cash is not None and cash[1] < pe else []
    if q is not None and q.end < pe:
        late.append(f"its newest operating cash flow ends {q.end}")
    return late


@dataclass(frozen=True)
class _FilingCash:
    """companyfacts' facts with the cited filing's own rows at the period end beside them, and the sentence the
    note states."""

    facts: dict
    stated: str


@dataclass(frozen=True)
class PriorFiling:
    """The previous 10-Q of a values filing whose companyfacts is two quarters behind (A2): its document URL, raw
    document and period of report. ``extract_for_security`` reads it only when the quarter needs it.
    """

    ref: str
    raw: str
    period_end: date


def _filing_cash(
    facts: dict,
    raw: str,
    contexts: dict[str, inline_xbrl.Context],
    period_end: date,
    filed: date | None,
    late: list[str],
    cfg: ExtractorConfig,
    prior: PriorFiling | None = None,
) -> _FilingCash | None:
    """``late`` (companyfacts is behind the cited period): the cited document's own cash, investment and
    operating-cash-flow rows at the period end, added beside companyfacts' (a concept and period companyfacts carries
    is never replaced; one concept and period tagged with two values is not read). None when the document tags
    neither a cash instant nor an operating cash flow of its own at the period end: today's candidate stands.

    ``_cash_burn`` then runs unchanged on the result: the year to date minus companyfacts' same-start prior is a
    derived quarter (one quarter behind: CEG); with that prior missing the latest earlier one is subtracted and the
    span is flagged ``burn-span`` (two behind: DUK, FE).

    With ``cfg.cash_auto_companyfacts_behind`` (A2) the document's own reconciliation lines (``IncreaseDecreaseIn*``)
    at the period end are read too (the one-time check's), and the ``prior`` 10-Q's own operating cash flow and lines
    of the same year-to-date start at its period end (two behind: the quarter is then the year to date minus that).
    """
    pe = period_end.isoformat()
    a2 = cfg.cash_auto_companyfacts_behind
    durations = _FILING_DURATIONS | (frozenset(_LINE_TAG_RE.findall(raw)) if a2 else frozenset())
    found: dict[tuple[str, str | None], set[float]] = {}
    for f in _usd_facts(raw, contexts, _filing_instants(cfg) | durations):
        c = f.context
        if not _own(f):
            continue
        if f.concept in durations:
            if c.end != period_end or c.start is None:
                continue
            k: tuple[str, str | None] = (f.concept, c.start.isoformat())
        elif c.instant == period_end:
            k = (f.concept, None)
        else:
            continue
        found.setdefault(k, set()).add(f.value)  # type: ignore[arg-type]
    us = dict(facts.get("us-gaap", {}))
    added = _add_rows(facts, us, found, pe, filed.isoformat() if filed else "")
    if not any(c in _FILING_DURATIONS or c in {x for _, x in _CASH} for c, _, _ in added):
        return None
    from_prior: list[tuple[str, str | None, float]] = []
    if a2 and prior is not None:
        starts = {s for c, s, _ in added if c in _FILING_DURATIONS and s}
        from_prior = _add_rows(
            facts, us, _prior_durations(prior, starts), prior.period_end.isoformat(), ""
        )
    merged = {**facts, "us-gaap": us}
    # an equity balance is read only for the composition's guard, which names it when it applies
    shown = [
        x for x in added if not x[0].startswith(_LINE_PREFIX) and x[0] not in _EQUITY_SECURITIES
    ]
    read = "; ".join(
        f"{c} {start} → {pe} {_m(v)}" if start else f"{c} {_m(v)}" for c, start, v in shown
    )
    n_lines = sum(1 for x in added if x[0].startswith(_LINE_PREFIX))
    stated = (
        f"companyfacts is behind the cited filing ({' and '.join(late)}): read from the filing's own inline XBRL at "
        f"{pe}, the registrant's undimensioned USD facts only: {read}"
        + (f"; and {n_lines} reconciliation line(s) for the one-time check" if n_lines else "")
    )
    q = _quarter(merged, cfg)
    if q is not None and q.end == pe and q.basis == "derived":
        sub = next(
            (
                v
                for c, _, v in from_prior
                if prior is not None and c == q.concept and q.start == prior.period_end.isoformat()
            ),
            None,
        )
        if sub is not None and prior is not None:
            stated += (
                f"; the quarter subtracts the previous 10-Q's own year to date ending {q.start} ({prior.ref}: "
                f"{q.concept} {_m(sub)})"
            )
        else:
            stated += f"; the quarter subtracts companyfacts' year to date ending {q.start}"
    return _FilingCash(merged, stated)


def _add_rows(
    facts: dict,
    us: dict,
    found: dict[tuple[str, str | None], set[float]],
    end: str,
    filed: str,
) -> list[tuple[str, str | None, float]]:
    """Add each (concept, start) -> value read from a document to ``us`` as a companyfacts-shaped row ending ``end``,
    unless companyfacts carries that concept and period already or the document tags it with two values.
    """
    added: list[tuple[str, str | None, float]] = []
    for (concept, start), vals in found.items():
        on_file = _rows(facts, "us-gaap", concept, "USD")
        if len(vals) != 1 or any(r["end"] == end and r.get("start") == start for r in on_file):
            continue
        row: dict[str, Any] = {"end": end, "val": vals.pop(), "accn": "", "form": ""}
        row["filed"] = filed
        if start is not None:
            row["start"] = start
        node = us.get(concept, {})
        units = dict(node.get("units", {}))
        units["USD"] = [*units.get("USD", []), row]
        us[concept] = {**node, "units": units}
        added.append((concept, start, row["val"]))
    return added


def _prior_durations(
    prior: PriorFiling, starts: set[str]
) -> dict[tuple[str, str | None], set[float]]:
    """The previous 10-Q's own (undimensioned, USD) operating cash flow and reconciliation lines ending at its period
    end and starting on one of ``starts`` (the cited year to date's start): the prior year to date (A2).
    """
    concepts = _FILING_DURATIONS | frozenset(_LINE_TAG_RE.findall(prior.raw))
    found: dict[tuple[str, str | None], set[float]] = {}
    for f in _usd_facts(prior.raw, inline_xbrl.read_contexts(prior.raw), concepts):
        c = f.context
        if _own(f) and c.end == prior.period_end and c.start and c.start.isoformat() in starts:
            found.setdefault((f.concept, c.start.isoformat()), set()).add(f.value)  # type: ignore[arg-type]
    return found


def _needs_previous_quarter(facts: dict, raw: str, period_end: date, cfg: ExtractorConfig) -> bool:
    """A2's one more document: whether the values filing's quarter can be derived only with the previous 10-Q's own
    year to date (companyfacts behind by two quarters: the cited year to date minus companyfacts' latest same-start
    one is not about a quarter, or there is none). Pure; ``extract_for_security`` then reads that 10-Q.
    """
    if not cfg.cash_auto_companyfacts_behind:
        return False
    late = _companyfacts_late(facts, period_end, cfg)
    if not late:
        return False
    filing = _filing_cash(facts, raw, inline_xbrl.read_contexts(raw), period_end, None, late, cfg)
    q = None if filing is None else _quarter(filing.facts, cfg)
    return (
        q is not None
        and q.end == period_end.isoformat()
        and q.basis in ("derived", "ytd-raw")
        and not cfg.quarterly_span_min_days <= q.days <= cfg.quarterly_span_max_days
    )


def _cash_evidence(
    facts: dict, raw: str, contexts: dict[str, inline_xbrl.Context], cfg: ExtractorConfig
) -> str:
    """A FLAG note's evidence from the filing's own inline XBRL on the cash date, never a figure: the debt securities
    under a tag the composition does not count (``_DEBT_UNCOUNTED``: NVDA), and a quoted lone debt total equal to
    the same-dated cash-equivalents fair value (``_CE_FAIR_VALUE``, any dimension but another registrant's: AMGN).
    The empty string when there is none."""
    cash = _pick_instant(facts, _CASH)
    if cash is None:
        return ""
    on = date.fromisoformat(cash[1])
    facts_on = [
        f
        for f in _usd_facts(raw, contexts, frozenset({*_DEBT_UNCOUNTED, _CE_FAIR_VALUE}))
        if f.context.instant == on
    ]
    out: list[str] = []
    named = []
    for concept in () if cfg.debt_securities_counted else _DEBT_UNCOUNTED:
        vals = {f.value for f in facts_on if f.concept == concept and _own(f)}
        if len(vals) == 1:
            named.append(f"{concept} {_m(vals.pop())}")  # type: ignore[arg-type]
    if named:
        out.append(
            "The filing's inline XBRL tags debt securities on the cash date under a tag the reader does not count: "
            f"{', '.join(named)}; add the eligible part unless a figure above already holds it."
        )
    comp = _compose(facts, cash[0], cash[1], cfg)
    for tag, total in comp.quoted_unsplit:
        hit = next(
            (
                f
                for f in facts_on
                if f.concept == _CE_FAIR_VALUE
                and not f.context.other_axis
                and all(a != "LegalEntityAxis" for a, _ in f.context.members)
                and abs(f.value - total) < 0.5  # type: ignore[operator]
            ),
            None,
        )
        if hit is not None:
            where = ", ".join(f"{a} {m}" for a, m in hit.context.members) or "undimensioned"
            out.append(
                f"The quoted {tag} {_m(total)} equals the filing's {_CE_FAIR_VALUE} on the cash date ({where}): "
                "the total looks already inside cash and equivalents, so adding it would count it twice."
            )
    return " ".join(out)


def _cash_candidate(
    facts: dict,
    tenq_text: str,
    ref: str,
    period_end: date,
    cfg: ExtractorConfig,
    source: str,
    *,
    raw: str | None,
    filed: date | None,
    prior: PriorFiling | None = None,
) -> ExtractedFact:
    """The cash candidate (W1-2): ``_cash_burn`` over companyfacts; given the values filing's ``raw`` document, over
    companyfacts plus the document's own rows when companyfacts is behind the cited period (``_filing_cash``,
    ``companyfacts-behind``; AUTO when every check passes on them, A2; ``prior``: the previous 10-Q, two quarters
    behind), and a FLAG's note carries ``_cash_evidence``. An AUTO candidate of companyfacts is never re-read, and no
    figure, flag or tier comes from the evidence.
    """
    if raw is None:
        return _cash_burn(facts, tenq_text, ref, period_end, cfg, source)
    late = _companyfacts_late(facts, period_end, cfg)
    contexts = inline_xbrl.read_contexts(raw) if late else None
    filing = (
        _filing_cash(facts, raw, contexts, period_end, filed, late, cfg, prior)
        if contexts is not None
        else None
    )
    read = facts if filing is None else filing.facts
    fact = _cash_burn(
        read,
        tenq_text,
        ref,
        period_end,
        cfg,
        source,
        behind=None if filing is None else filing.stated,
        raw=raw,
    )
    if fact.tier is not Tier.FLAG:
        return fact
    evidence = _cash_evidence(
        read, raw, contexts if contexts is not None else inline_xbrl.read_contexts(raw), cfg
    )
    return fact.model_copy(update={"note": f"{fact.note} {evidence}"}) if evidence else fact


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
    tenq_raw: str | None = None,
    tenq_filed: date | None = None,
    tenq_prior: PriorFiling | None = None,
) -> list[ExtractedFact]:
    """Pure, deterministic: (companyfacts + the two filing texts) -> the three candidate facts. Testable
    offline against cached fixtures (the form4-parser precedent — the caller owns fetching).

    ``values_form`` is the form of the VALUES filing (the ``tenq_*`` arguments, which since B1 can be a 10-K):
    the cash candidate's stored provenance kind names it (``10-k`` for a 10-K; it said ``10-q`` for every
    value, the 18 audited 10-K citations included).

    ``tenq_raw`` is the values filing's RAW document (the one ``tenq_text`` was cleaned from) and ``tenq_filed`` its
    filing date: a FLAG share count is re-read from its inline XBRL (``_inline_shares``), and so are cash and burn
    when companyfacts is behind, a FLAG's note naming what it shows uncounted (``_cash_candidate``). None (a
    text-only caller): the candidates are exactly the companyfacts-and-text ones. ``tenq_prior`` is the previous
    10-Q (``PriorFiling``), passed only when ``_needs_previous_quarter`` (A2, two quarters behind).
    """
    facts = companyfacts["facts"]
    cash_source = "10-k" if values_form == "10-K" else "10-q"
    return [
        _purity(facts, tenk_text, tenk_ref, tenk_date),
        _shares(facts, tenq_text, tenq_ref, tenq_date, raw=tenq_raw, filed=tenq_filed, cfg=cfg),
        _cash_candidate(
            facts,
            tenq_text,
            tenq_ref,
            tenq_date,
            cfg,
            cash_source,
            raw=tenq_raw,
            filed=tenq_filed,
            prior=tenq_prior,
        ),
    ]


def _doc_url(cik: int, accession: str, primary_doc: str) -> str:
    doc = primary_doc.rsplit("/", 1)[-1]
    return f"{get_settings().sec_archives_base}/{cik}/{accession.replace('-', '')}/{doc}"


def companyfacts_url(cik: str | int) -> str:
    """The SEC XBRL companyfacts JSON endpoint for a CIK (data.sec.gov)."""
    return f"{get_settings().sec_data_base}/api/xbrl/companyfacts/CIK{int(cik):010d}.json"


@dataclass(frozen=True)
class _Document:
    """One read filing: its URL, the raw document (its inline XBRL), the cleaned text, the period of report and the
    filing date (None when the submissions row has none readable)."""

    url: str
    raw: str
    text: str
    period_end: date
    filed: date | None


def _read_document(client: EdgarClient, cik: int, f: dict[str, str]) -> _Document:
    """One submissions row (``filings_of``'s shape) -> the read document. The ONE document request of a filing
    (cache-first, ``forms/`` = cached forever); the raw text is kept for the inline-XBRL reads (shares, cash).
    """
    url = _doc_url(cik, f["accession"], f["primary_doc"])
    raw = client.get_text(url, f"forms/{f['accession']}/{f['primary_doc'].rsplit('/', 1)[-1]}")
    # The date threaded to the extractors is the PERIOD OF REPORT (quarter/year end), NOT the filing
    # date — their staleness gates and event_date stamps are period semantics. Threading the filing
    # date here made the shares AUTO gate unreachable live (a cover's "as of" date is always BEFORE
    # the filing date), mis-flagging every single-class name "dual-class". Falls back to the filing
    # date only when a submissions row carries no reportDate.
    return _Document(
        url,
        raw,
        clean_filing_text(raw),
        date.fromisoformat(f.get("report_date") or f["filed"]),
        parse_filing_date(f.get("filed")),
    )


def _read_filing(client: EdgarClient, cik: int, f: dict[str, str]) -> tuple[str, str, date]:
    """``_read_document`` as (document URL, cleaned text, period of report)."""
    d = _read_document(client, cik, f)
    return d.url, d.text, d.period_end


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
    v = _read_document(client, cik, values_row)
    prior = _previous_quarter(client, cik, subs, cf["facts"], v, cfg)
    p = (
        (v.url, v.text, v.period_end)
        if purity_row["accession"] == values_row["accession"]
        else _read_filing(client, cik, purity_row)
    )
    return extract_facts(
        cf,
        v.text,
        p[1],
        tenq_ref=v.url,
        tenk_ref=p[0],
        tenq_date=v.period_end,
        tenk_date=p[2],
        cfg=cfg,
        values_form="10-K" if values_row is tenk else "10-Q",
        tenq_raw=v.raw,
        tenq_filed=v.filed,
        tenq_prior=prior,
    )


def _previous_quarter(
    client: EdgarClient,
    cik: int,
    subs: dict[str, Any],
    facts: dict,
    v: _Document,
    cfg: ExtractorConfig,
) -> PriorFiling | None:
    """A2's one more document (cache-first, like every document): the newest 10-Q whose period of report precedes the
    values filing's, read only when ``_needs_previous_quarter``; None otherwise or when there is none.
    """
    if not _needs_previous_quarter(facts, v.raw, v.period_end, cfg):
        return None
    pe = v.period_end.isoformat()
    row = next(
        (r for r in filings_of(subs, "10-Q") if r["report_date"] and r["report_date"] < pe), None
    )
    if row is None:
        return None
    d = _read_document(client, cik, row)
    return PriorFiling(d.url, d.raw, d.period_end)


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


def extract_purity_for_security(client: EdgarClient, cik: str | int) -> ExtractionResult:
    """A plain user's extract (W7a): the purity candidate ALONE, fetching only what it needs.

    A share count and cash/burn are platform values (``docs/PLATFORM_VALUES.md``): extracted in the background,
    decided by the steward, and never shown to a plain user unverified. So this entry never reads the values
    filing, an annual (20-F/40-F) document or an exhibit. It reads the submissions index; for a company whose
    newest periodic filing is a 20-F or 40-F (``_takes_annual_path``) it stops there with the
    ``purity-not-covered`` empty reason (the annual path never covered purity), or ``no-annual-filing`` when the
    company has no periodic or annual filing at all (unchanged words). Otherwise it reads companyfacts (the
    revenue test choosing the segment table or the Item-1 passage) and ONE document: the latest 10-K, else the
    latest 10-Q. The candidate is the same HUMAN purity candidate the full extract builds (located, never valued).
    """
    cik = int(cik)
    subs = fetch_submissions(client, cik)
    if _takes_annual_path(subs):
        has_annual = any(filings_of(subs, form) for form in _ANNUAL_FORMS)
        return ExtractionResult(
            facts=[], empty_reason="purity-not-covered" if has_annual else "no-annual-filing"
        )
    row = next(iter(filings_of(subs, "10-K")), None) or next(iter(filings_of(subs, "10-Q")))
    cf = client.get_json(companyfacts_url(cik), f"companyfacts/CIK{cik:010d}.json")
    ref, text, period_end = _read_filing(client, cik, row)
    return ExtractionResult(facts=[_purity(cf["facts"], text, ref, period_end)])


__all__ = [
    "CLASS_OUTSIDE_EPS",
    "COMPANYFACTS_BEHIND",
    "COVER_AFTER_FILING",
    "PriorFiling",
    "RefusedCover",
    "YTD_DERIVED_ACROSS_FILINGS",
    "cover_after_filing",
    "extract_facts",
    "extract_for_security",
    "extract_purity_for_security",
    "extract_with_annual_fallback",
    "ExtractedFact",
    "refused_cover_note",
    "screen_cover_rows",
]
