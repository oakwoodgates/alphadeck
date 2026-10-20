"""§2.2 fundamentals ingest — the quarterly REVENUE series behind the acceleration-inflection detector.

Pulls a name's SEC ``companyfacts`` (XBRL) ONCE — which returns the FULL multi-year history, each period
carrying its own ``filed`` / ``accn`` / ``end`` — and reconstructs the honest historical knowability on the
EVENT axis: every quarter is stamped ``valid_from = its filing acceptance (``filed``) date`` (R17), never the
period end and never today, so a past-as-of read sees each quarter EXACTLY when it was filed (invariant #1,
no-lookahead) and a detector dates it by that filing (``knowable_from``, D3a). The TRANSACTION axis is the
honest one too: ``recorded_at`` is the database instant THIS system stored the row (since migration 0057; it
used to be backdated to ``filed 00:00 UTC`` — see ``store_quarters``). A DETERMINISTIC parse — the LLM never
sources the number (#3).

REUSE (one source of truth): it reads the SAME companyfacts client the Workbench AUTO tier uses and the SAME
revenue concepts (``ingest.edgar.extract.REVENUE_CONCEPTS`` — ``us-gaap:Revenues``, the two
``RevenueFromContractWithCustomer*`` tags, and since 2026-10-20 a utility's and a bank's total-revenue tag
appended after them), so "which XBRL tag IS revenue" can never drift between the extractor and this ingest.

WHICH LABEL — the company's CURRENT one (``select_revenue``; the 2026-10-20 label-switch fix). Most filers
reported ``Revenues`` until ASC 606 (~2018) and then moved to a ``RevenueFromContractWithCustomer*`` tag. The
old rule kept the FIRST label present, so such a company kept only its dead series (MEASURED on prod: 163 of
495 active companies with revenue had a revenue series ending more than a year before their share counts —
AMD's stopped at 2017-09-30), and ``revenue_acceleration`` could never fire on its real recent quarters. Now:

  * the CURRENT label is the one whose newest extractable quarter ends LATEST. When two end on the same date,
    the one with MORE quarters (MEASURED: 81 basket companies tie, and the list order kept TTMI on a
    ``Revenues`` tag 4 quarters old beside a contract tag of 70) — but never a tag another tied tag
    DOMINATES on their shared filings (a part of revenue cannot exceed the whole, ``_dominated``); then the
    ``REVENUE_CONCEPTS`` order. Each stored revenue row names its tag (``source = companyfacts:<concept>``);
  * the series is that ONE label's quarters — never a per-period join of two labels. A join splices two
    definitions of revenue and fakes an acceleration at the switch: MEASURED on the dev copy (2026-10-20),
    of the 533 filings on which a switched company reported a quarter under both labels, 127 disagree, some
    by 30-99% (a ``Revenues`` that is total revenue beside a contract-revenue subset, or the reverse). Even
    the narrowest join — an older label's EARLIER periods, only where the two agree exactly on every shared
    filing — rescued none of the 107 companies whose latest quarter the detector cannot evaluate, and its one
    live change was a phantom (TTMI: a Q2-2025 quarter dated at its 2026-08-05 comparative).

A label switch can leave a stored old-label number on the SAME filing the current label reports differently
(the store's key is the filing, not the label — MEASURED: 302 such filings in 40 companies). ``plan_versions``
then appends the current label's number as a version of that filing, one microsecond after it
(``valid_from`` unchanged, so its knowability is unchanged), and the read picks it: within one filing the
series never mixes the two labels, and the number is dated at the filing that first reported it — not at a
year-later comparative (the D3 phantom). The one exception: a held number the current label itself reports
for that period on a LATER filing is kept (it IS the current definition's number, public since this filing;
24 of the 302, so 278 are superseded). The stored history is kept, never rewritten. Only the revenue metric
does this; the share series keep their skip-on-key behavior.

What is NOT re-labeled: periods only the old label ever reported stay in the store as they were (append-only;
for a pre-2018 switch they lie years before any live read).

THE DATA-QUALITY SCREEN (``_label_series``, 2026-10-20). Two row shapes no genuine quarter can have are kept
out of the series — counted and reported (``ScreenedRow``; the re-ingest CLI prints each), never stored:
a fiscal-year total under a quarter context (``_fy_total_in_quarter`` — VICR's FY2025 10-K tags Q3-2025 as
the year's $407.7M; LHX's 10-Ks put the year under a Q4 context), and a derived Q4 whose FY total was
restated onto a basis its 9-month YTD never reached (``_same_basis`` — LFMD's $5.5M "Q4" against ~$50M
quarters; HON's Solstice recast). Every judgment uses only rows filed on or before the judged row's own
filing — a later filing never decides whether a public quarter exists. The nightly counts the rejections on
its summary line and records each on the run artifact (``pipeline.daily`` / ``pipeline.cron_run_log``). Rows
ALREADY stored by the old ingest are not touched by the screen (append-only) and remain in the store.

The quarterly series is:
  * every NATIVE ~3-month duration fact (Q1/Q2/Q3 arrive natively, each with its own ``filed``/``accn`` —
    clean per-row knowability and clean restatement-versioning), PLUS
  * a DERIVED Q4 = the fiscal-year 12-month total − the same-fiscal-year 9-month YTD, where no native Q4
    exists — so the FY/Q4 report (the NVDA-Jan-2024 tell) is not blind. A derived Q4's knowability is the
    LATER of its two source filings (you cannot know Q4 until the 10-K is filed), so it stamps the FY 10-K's
    ``filed`` and ``accn`` (the honest later-of knowability; provenance = the 10-K).

Missing / degenerate periods are simply ABSENT — the detector DECLINES, never fabricates (#9/#3).

§2.4 DEFERRED (margin / FCF): a later gross-margin or FCF inflection is a VARIANT over this same
``fact_fundamentals`` family — it adds ROWS under a new ``metric_key`` (needing COGS / OCF / capex concepts),
never a column. The (metric_key, value, unit) shape here is built for exactly that; only revenue ships now.

Band 03 S4 (share-count creep) adds the first sibling family: the quarterly SHARES-OUTSTANDING series —
three XBRL instant concepts stored as three metric_keys (``_xbrl``-suffixed to keep them verbally distinct
from the operator-ratified ``fact_shares_outstanding`` Workbench table). ALL THREE are stored (evidence is
complete, #9 — measured on the real 475-name basket no single concept covers everyone: dei 419 present /
us-gaap outstanding 361 / issued 372); the ``share_creep`` detector walks a fixed ladder on READ and never
mixes concepts inside one computation (issued vs outstanding differ by treasury stock). Instants carry no
``start``, so there is no span/derived-Q4 machinery — every point is 'native', stamped at its OWN ``filed``
(the same XBRL knowability trap + fix as revenue: knowable at 10-Q/K acceptance, never the instant date).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import REVENUE_CONCEPTS, companyfacts_url

# The metric_key the revenue series is stored under (the detector filters on the same literal — see
# ``signals.revenue_acceleration._REVENUE_METRIC``; kept a plain string so the pure detector needs no ingest
# import). §2.4 adds sibling keys (e.g. 'gross_margin', 'fcf') under this same family — rows, not columns.
REVENUE_METRIC = "revenue"

# Band 03 S4 — the shares-outstanding metric_keys (the ``share_creep`` detector mirrors these literals as
# its concept ladder; kept plain strings for the same no-ingest-import reason). ``_xbrl``-suffixed so the
# keys can never be confused with the operator-ratified ``fact_shares_outstanding`` table (a different
# store: ratified Workbench facts vs this deterministic companyfacts parse).
SHARES_OUT_METRIC = (
    "shares_out_xbrl"  # us-gaap:CommonStockSharesOutstanding (balance-sheet instant)
)
SHARES_OUT_COVER_METRIC = "shares_out_cover_xbrl"  # dei:EntityCommonStockSharesOutstanding (cover)
SHARES_ISSUED_METRIC = "shares_issued_xbrl"  # us-gaap:CommonStockSharesIssued

# (metric_key, taxonomy, concept) — all three stored; the detector's read-side ladder decides which
# expresses a name (the evidence/policy seam: re-ordering the ladder is a code edit, zero re-ingest).
SHARE_CONCEPTS: tuple[tuple[str, str, str], ...] = (
    (SHARES_OUT_METRIC, "us-gaap", "CommonStockSharesOutstanding"),
    (SHARES_OUT_COVER_METRIC, "dei", "EntityCommonStockSharesOutstanding"),
    (SHARES_ISSUED_METRIC, "us-gaap", "CommonStockSharesIssued"),
)

# companyfacts revenue DURATION rows classified by span (end − start). A clean fiscal quarter is ~85-98d; a
# 6/9/12-month YTD/annual is longer. These windows separate a standalone quarter from a cumulative period —
# they are DATA-parse dials (like the extractor's regexes/windows), never call-engine tuning.
_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100
_YTD9_MIN_DAYS = 250  # ~9 months (the Q1–Q3 cumulative on the Q3 10-Q)
_YTD9_MAX_DAYS = 295
_ANNUAL_MIN_DAYS = 350  # ~12 months (the fiscal-year total on the 10-K)
_ANNUAL_MAX_DAYS = 380

# The data-quality screen's one tolerance (2026-10-20): two totals within 0.5% are "the same total" — a
# quarter row equal to its fiscal year's FY total, and an FY total not MATERIALLY restated (a rounding
# re-report stays on its basis). A data-parse dial like the spans above, never call-engine tuning.
_SAME_TOTAL_TOL = 0.005
# ...and "the year's other quarters are a material share of it" (shape 1's guard): at least 5% of the total.
# Below that, a quarter equal to the year is a year earned in one quarter (a milestone beside a $30K
# quarter), which is real data and kept.
_OTHER_QUARTERS_MIN_SHARE = 0.05

# Why ``_label_series`` rejected a row (``ScreenedRow.reason``).
SCREEN_FY_IN_QUARTER = "fy_total_in_quarter"  # a fiscal-year total filed under a quarter context
SCREEN_YEAR_IN_Q4 = (
    "year_in_q4_context"  # an annual report's Q4-context row standing in for the year
)
SCREEN_RESTATED_FY_Q4 = (
    "q4_restated_fy_basis"  # a derived Q4 whose FY and 9-month sit on different bases
)


@dataclass(frozen=True)
class QuarterPoint:
    """One knowable quarterly metric point: the value, its fiscal period END, and the SINGLE filing that made
    it knowable (``filed`` = the acceptance date, ``accession`` = provenance). ``basis`` = 'native' (a
    standalone XBRL quarter) | 'derived' (FY − 9moYTD). A restatement of the same period is a DISTINCT point
    (a later ``filed``), so the store versions it rather than dropping it."""

    metric_key: str
    period_end: date
    value: float
    filed: date
    accession: str
    basis: str
    fiscal_period: str | None
    fiscal_year: int | None
    unit: str = (
        "USD"  # 'USD' (revenue) | 'shares' (the S4 share-count series) — the stored unit column
    )
    # the XBRL concept a revenue point was read from (``select_revenue`` stamps it). Stored in the ``source``
    # column as ``companyfacts:<concept>``, so a card citing an accession can say WHICH tag's number it used
    # — two tags can share one accession (#6). Not a D3a value column: dating never reads it.
    concept: str | None = None


@dataclass(frozen=True)
class FundamentalsResult:
    """The ingest receipt: ``appended`` = new (security, metric, period_end, filed) versions stored;
    ``skipped`` = already-stored versions (the idempotency counter — a re-run appends ZERO, count-the-table
    guarded); ``screened`` = the revenue rows the data-quality screen rejected (never stored) — carried
    whole, not as a count, so the nightly can print and record WHY (``pipeline.ingest_fundamentals``).
    """

    appended: int
    skipped: int
    screened: tuple[ScreenedRow, ...] = ()


@dataclass(frozen=True)
class ScreenedRow:
    """One revenue row the data-quality screen kept OUT of the quarterly series — counted and reported (the
    re-ingest CLI prints each), never stored. ``value`` is what the row would have stored as the quarter,
    ``reference`` the figure it was judged against (the FY total; for ``year_in_q4_context`` the 9-month
    YTD it reaches), ``accession`` the filing that carried it.

    - ``fy_total_in_quarter``: a quarter-span row carrying its fiscal year's FY total (MEASURED: LHX's 2026
      10-K puts FY2024's $21.325B under a Q4 context; VICR's FY2025 10-K tags Q3-2025 as $407.7M, the year).
    - ``year_in_q4_context``: an annual report's Q4-context row at least the year's 9-month YTD, the filing
      stating no FY total (MEASURED: LHX's FY2025 $21.865B).
    - ``q4_restated_fy_basis``: FY − 9-month where the FY total was materially restated after the 9-month
      was filed (MEASURED: LFMD's $5.5M Q4-2024 from a continuing-operations FY and a pre-restatement 9-month).
    """

    reason: str
    period_end: date
    filed: date
    value: float
    reference: float
    accession: str


def _span_days(row: dict[str, Any]) -> int | None:
    s, e = row.get("start"), row.get("end")
    if not s or not e:
        return None
    return (date.fromisoformat(e) - date.fromisoformat(s)).days


def _stampable_usd_duration_rows(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    """One concept's USD duration rows that carry every field the knowability stamp needs
    (start/end/val/filed/accn); anything sparse is dropped (honest — an unstampable row is not fabricated
    into one)."""
    rows = node.get("units", {}).get("USD", []) if node else []
    return [
        r
        for r in rows
        if r.get("start")
        and r.get("end")
        and r.get("val") is not None
        and r.get("filed")
        and r.get("accn")
    ]


@dataclass(frozen=True)
class RevenueSelection:
    """``select_revenue``'s answer: the company's CURRENT revenue label and its series.

    - ``concept`` — the chosen us-gaap concept name (``None`` when no label yields a single quarter).
    - ``points`` — that label's quarterly points (native + derived Q4), sorted — the series to store.
    - ``labels`` — per label with at least one quarter: ``(quarters, first period end, last period end)``,
      in ``REVENUE_CONCEPTS`` order (the re-ingest CLI reports it).
    - ``screened`` — the chosen label's rows the data-quality screen rejected (``ScreenedRow``)."""

    concept: str | None
    points: list[QuarterPoint]
    labels: dict[str, tuple[int, date, date]] = field(default_factory=dict)
    screened: list[ScreenedRow] = field(default_factory=list)


def select_revenue(companyfacts: dict[str, Any]) -> RevenueSelection:
    """Pure, deterministic (no LLM, #3): which revenue label is this company's CURRENT one, and its series.

    The rule (module docstring): among the ``REVENUE_CONCEPTS`` labels that yield at least one quarter (after
    the data-quality screen), the one whose newest quarter ends LATEST; when two end on the same date, the
    one with MORE quarters; then the ``REVENUE_CONCEPTS`` order. The series is that one label's quarters,
    never joined with another's. A label whose rows yield no quarter (only an odd span, a lone annual) is
    never chosen — MEASURED: the old first-present rule picked such a ``Revenues`` node for 13 basket
    companies (LHX, TDY, STX, AEIS …) and stored no revenue for them at all."""
    facts = companyfacts.get("facts", {})
    per: dict[str, tuple[list[QuarterPoint], list[ScreenedRow]]] = {}
    for tax, concept in REVENUE_CONCEPTS:
        pts, screened = _label_series(_stampable_usd_duration_rows(facts.get(tax, {}).get(concept)))
        if pts:
            per[concept] = (pts, screened)
    if not per:
        return RevenueSelection(concept=None, points=[])
    labels = {
        c: (len(p), p[0].period_end, max(q.period_end for q in p)) for c, (p, _s) in per.items()
    }
    newest = max(last for _n, _first, last in labels.values())
    tied = [c for c in per if labels[c][2] == newest]
    # a tie never prefers a PART of revenue over the whole: drop a tag that another tied tag dominates on
    # the filings both carry (``_dominated``); then the longest history, then the list order (``per`` is in
    # REVENUE_CONCEPTS order and ``max`` keeps the first of equal keys)
    whole = [c for c in tied if not any(_dominated(per[c][0], per[o][0]) for o in tied if o != c)]
    current = max(whole or tied, key=lambda c: labels[c][0])
    pts, screened = per[current]
    return RevenueSelection(
        concept=current,
        points=[replace(p, concept=current) for p in pts],
        labels=labels,
        screened=screened,
    )


def _dominated(part: list[QuarterPoint], whole: list[QuarterPoint]) -> bool:
    """Is tag ``part`` a SUBSET of tag ``whole``? True when they share at least one filing of a period and
    ``whole`` reports at least ``part``'s number on every shared filing (within 0.5%) and more on at least
    one. A component of revenue can never exceed the total, so on a tie the dominated tag is never chosen,
    however long its history. MEASURED on the dev copy: 6 ties go to the whole over a longer part (KOPN, LUNR,
    AEE, NRXP, MELI, SOFI); KOPN's 178-quarter ``Revenues`` carried a $3.0M partial Q2-2025 beside the total
    tag's $8.45M, a fake +322% YoY that was a live trigger."""
    mine = {(p.period_end, p.filed): p.value for p in part}
    pairs = [(mine[k], q.value) for q in whole if (k := (q.period_end, q.filed)) in mine]
    if not pairs:
        return False
    tol = _SAME_TOTAL_TOL
    return all(w >= v - tol * abs(v) for v, w in pairs) and any(
        w > v + tol * abs(v) for v, w in pairs
    )


def extract_revenue_quarters(companyfacts: dict[str, Any]) -> list[QuarterPoint]:
    """Pure, deterministic: a companyfacts dict -> the quarterly revenue points (native quarters + derived
    Q4) of the company's CURRENT revenue label (``select_revenue``), each stamped at its OWN filing. Testable
    offline against a companyfacts fixture (the ``extract_facts`` precedent — the caller owns fetching).
    Never raises on a sparse/odd doc — it yields fewer points."""
    return select_revenue(companyfacts).points


def _quarters_from_rows(rows: list[dict[str, Any]]) -> list[QuarterPoint]:
    """ONE label's stampable USD duration rows -> its quarterly points (``_label_series`` without the
    screened-row report)."""
    return _label_series(rows)[0]


def _near(a: float, b: float) -> bool:
    """Within ``_SAME_TOTAL_TOL`` of each other, relative to the larger magnitude."""
    return abs(a - b) <= _SAME_TOTAL_TOL * max(abs(a), abs(b))


_ANNUAL_FORMS = (
    "10-K",
    "20-F",
    "40-F",
)  # an annual report (incl. /A and 10-KT): where an FY total lives


def _fy_total_in_quarter(
    r: dict[str, Any],
    quarters: list[dict[str, Any]],
    annuals: list[dict[str, Any]],
    ytd9: list[dict[str, Any]],
) -> tuple[str, float] | None:
    """Is quarter-span row ``r`` a FISCAL-YEAR total filed under a quarter context? -> ``(reason, the figure it
    was judged against)``, else ``None``. Two shapes, neither of which a genuine quarter can have:

    1. ``fy_total_in_quarter`` — ``r`` equals (within 0.5%) a positive FY total whose period contains it, AND
       the year's OTHER quarters add up to a material share of that total (``_OTHER_QUARTERS_MIN_SHARE``), so
       the quarters would sum well past the year. A year earned almost all in one quarter — a milestone
       payment beside a $30K quarter — keeps that quarter: then it IS (nearly) the year.
    2. ``year_in_q4_context`` — ``r`` closes a fiscal year (it starts within a week after that year's 9-month
       YTD ends), comes from an annual report that carries NO FY total for that year, and is at least the
       9-month YTD. An annual report always states the year; when its only full-year-sized revenue figure
       sits in a quarter context, that figure is the year (MEASURED: LHX's FY2025 10-K — the $21.865B year
       under a Q4 context, no FY row — where shape 1 has no total to compare against). A genuine Q4 bigger
       than the first three quarters still passes, because its 10-K states the year beside it.

    Both judge ``r`` ONLY against rows filed on or before ``r``'s own filing: a later filing must never decide
    whether a quarter that was already public exists (else the store would depend on when a company was
    first ingested, and a replay before that later filing would miss a public number). Every measured catch
    rests on rows the bad row's own annual report — or an earlier one — carried."""
    val = float(r["val"])
    known = r["filed"]
    for a in annuals:
        a_val = float(a["val"])
        if a["filed"] > known or a_val <= 0:
            continue
        if not (a["start"] <= r["start"] and r["end"] <= a["end"]) or not _near(val, a_val):
            continue
        # the year's other quarters as of r's filing: the latest version of each, by its filing
        latest: dict[str, tuple[str, float]] = {}
        for q in quarters:
            if (
                q["filed"] <= known
                and q["end"] != r["end"]
                and a["start"] <= q["start"]
                and q["end"] <= a["end"]
            ):
                prior = latest.get(q["end"])
                if prior is None or q["filed"] >= prior[0]:
                    latest[q["end"]] = (q["filed"], float(q["val"]))
        others = sum(v for _f, v in latest.values() if v > 0 and not _near(v, a_val))
        if others >= _OTHER_QUARTERS_MIN_SHARE * a_val:
            return SCREEN_FY_IN_QUARTER, a_val
    if not str(r.get("form", "")).startswith(_ANNUAL_FORMS):
        return None
    if any(
        a["accn"] == r["accn"] and a["start"] <= r["start"] and r["end"] <= a["end"]
        for a in annuals
    ):
        return None  # the filing states the year: shape 1 already judged the row against it
    r_start, r_end = date.fromisoformat(r["start"]), date.fromisoformat(r["end"])
    for y in ytd9:
        if y["filed"] > known:
            continue
        y_val = float(y["val"])
        gap = (r_start - date.fromisoformat(y["end"])).days
        year = (r_end - date.fromisoformat(y["start"])).days
        if (
            y_val > 0
            and 0 < gap <= 7
            and _ANNUAL_MIN_DAYS <= year <= _ANNUAL_MAX_DAYS
            and val >= y_val
        ):
            return SCREEN_YEAR_IN_Q4, y_val
    return None


def _value_run(row: dict[str, Any], same_period: list[dict[str, Any]]) -> tuple[str, str | None]:
    """For ``row``'s value, among the versions of the same period filed on or before it: ``(the filing that
    established that value, the filing of the version before it)`` — the trailing run of near-equal (0.5%)
    values, D3a's run with the screen's tolerance. The second is ``None`` when the value was never
    materially changed (the run reaches the first version)."""
    hist = sorted((x["filed"], float(x["val"])) for x in same_period if x["filed"] <= row["filed"])
    value = float(row["val"])
    i = len(hist) - 1
    while i > 0 and _near(hist[i - 1][1], value):
        i -= 1
    return hist[i][0], (hist[i - 1][0] if i > 0 else None)


def _same_basis(a: dict[str, Any], y: dict[str, Any], annuals, ytd9) -> bool:
    """Can FY row ``a`` and 9-month row ``y`` (same fiscal year) be subtracted? Yes when the FY total was
    never materially restated (the original rule). After a restatement, only if ``y``'s value was established
    AFTER the FY total's previous value was filed — i.e. the 9-month moved to the new basis too (a recast
    reaches the 10-Q's 9-month before the 10-K's year, so ``y`` may predate ``a``). MEASURED: LFMD's FY2024
    restated to $154.8M (continuing operations) on 2026-03-10, after its 9-month last moved together with the
    PREVIOUS FY restatement on 2025-11-17 — $154.8M − $149.3M made a $5.5M "Q4" (screened); HON's FY2023
    recast for the Solstice spin on 2026-02-17 against a never-recast 9-month — a $5.8B "Q4" (screened); TTMI's
    FY2019 recast on 2021-02-22 after its 9-month was recast on 2020-11-04 — a plausible Q4, kept.
    """
    _fy_set, fy_prev = _value_run(
        a, [x for x in annuals if x["start"] == a["start"] and x["end"] == a["end"]]
    )
    if fy_prev is None:
        return True
    y_set, _y_prev = _value_run(
        y, [x for x in ytd9 if x["start"] == y["start"] and x["end"] == y["end"]]
    )
    return y_set > fy_prev


def _label_series(
    rows: list[dict[str, Any]],
) -> tuple[list[QuarterPoint], list[ScreenedRow]]:
    """ONE label's stampable USD duration rows -> (its quarterly points, the rows the data-quality screen
    rejected): native ~3-month facts + a derived Q4 (FY − same-year 9-month YTD) where no native quarter ends
    at the FY end. Never mixes labels. Two screens, each a shape a genuine quarter cannot have
    (``ScreenedRow``): a fiscal-year total under a quarter context, and a derived Q4 whose FY and 9-month rows
    sit on different restatement bases. A screened row is never a quarter: it is reported, not stored.
    """
    # dedup by (period_end, filed) — companyfacts can repeat a fact; the first wins (deterministic).
    points: dict[tuple[date, date], QuarterPoint] = {}
    screened: list[ScreenedRow] = []
    quarters = [
        r for r in rows if (s := _span_days(r)) and _QUARTER_MIN_DAYS <= s <= _QUARTER_MAX_DAYS
    ]
    annuals = [
        r for r in rows if (s := _span_days(r)) and _ANNUAL_MIN_DAYS <= s <= _ANNUAL_MAX_DAYS
    ]

    ytd9 = [r for r in rows if (s := _span_days(r)) and _YTD9_MIN_DAYS <= s <= _YTD9_MAX_DAYS]

    # (1) native standalone quarters (Q1/Q2/Q3 — each its own filing, so knowability + versioning are per-row)
    for r in quarters:
        e, f = date.fromisoformat(r["end"]), date.fromisoformat(r["filed"])
        hit = _fy_total_in_quarter(r, quarters, annuals, ytd9)
        if hit is not None:
            screened.append(ScreenedRow(hit[0], e, f, float(r["val"]), hit[1], r["accn"]))
            continue
        points.setdefault(
            (e, f),
            QuarterPoint(
                metric_key=REVENUE_METRIC,
                period_end=e,
                value=float(r["val"]),
                filed=f,
                accession=r["accn"],
                basis="native",
                fiscal_period=r.get("fp"),
                fiscal_year=r.get("fy"),
            ),
        )

    # (2) derived Q4 = FY(12mo) − 9moYTD(same fiscal-year start), only where no native quarter ends at the FY
    # end. Knowability = the LATER of the two filings (always the FY 10-K); provenance = the FY accession.
    native_ends = {e for (e, _f) in points}
    for a in annuals:
        a_end = date.fromisoformat(a["end"])
        if a_end in native_ends:
            continue  # a native Q4 already exists — never double it
        a_filed = date.fromisoformat(a["filed"])
        # the 9-month YTD of the SAME fiscal year (same start), knowable by the FY's filing; latest such wins
        cands = [
            y
            for y in ytd9
            if y["start"] == a["start"] and date.fromisoformat(y["filed"]) <= a_filed
        ]
        if not cands:
            continue
        y = max(cands, key=lambda r: r["filed"])
        # ...and on the FY total's own basis (the same start date alone is not enough — the LFMD Q4)
        if not _same_basis(a, y, annuals, ytd9):
            screened.append(
                ScreenedRow(
                    SCREEN_RESTATED_FY_Q4,
                    a_end,
                    a_filed,
                    float(a["val"]) - float(y["val"]),
                    float(a["val"]),
                    a["accn"],
                )
            )
            continue
        q4_val = float(a["val"]) - float(y["val"])
        if q4_val <= 0:
            continue  # a non-positive derived quarter is a bad pair, not real revenue — decline (no fabrication)
        filed = max(a_filed, date.fromisoformat(y["filed"]))  # the later-of knowability (the 10-K)
        points.setdefault(
            (a_end, filed),
            QuarterPoint(
                metric_key=REVENUE_METRIC,
                period_end=a_end,
                value=q4_val,
                filed=filed,
                accession=a["accn"],
                basis="derived",
                fiscal_period="Q4",
                fiscal_year=a.get("fy"),
            ),
        )

    return sorted(points.values(), key=lambda p: (p.period_end, p.filed)), screened


def extract_share_points(companyfacts: dict[str, Any]) -> list[QuarterPoint]:
    """Pure, deterministic (Band 03 S4): a companyfacts dict -> the shares-outstanding instant points for
    ALL THREE share concepts (``SHARE_CONCEPTS``), each stamped at its OWN filing. Instants carry no
    ``start`` so there is no span classification and no derived Q4 — every point is 'native':
    ``period_end`` = the instant's ``end`` (balance-sheet date / cover "as of" date), knowability =
    ``filed``. Only rows carrying every field the knowability stamp needs (end/val/filed/accn) are kept
    (the same honesty rule as revenue: an unstampable row is not fabricated into one). Degenerate values
    (a literal 0-share XBRL row) are STORED as-is — the evidence tape is honest and complete (#9); the
    ``share_creep`` detector is where garbage is screened, never the ingest (the threshold/guard is the
    detector's cut, never an ingest filter). Dedup per (metric, end, filed) — a restatement (same end,
    later filed) is a DISTINCT version the store keeps."""
    facts = companyfacts.get("facts", {})
    points: dict[tuple[str, date, date], QuarterPoint] = {}
    for metric_key, tax, concept in SHARE_CONCEPTS:
        node = facts.get(tax, {}).get(concept)
        rows = node.get("units", {}).get("shares", []) if node else []
        for r in rows:
            if not (r.get("end") and r.get("val") is not None and r.get("filed") and r.get("accn")):
                continue
            e, f = date.fromisoformat(r["end"]), date.fromisoformat(r["filed"])
            points.setdefault(
                (metric_key, e, f),
                QuarterPoint(
                    metric_key=metric_key,
                    period_end=e,
                    value=float(r["val"]),
                    filed=f,
                    accession=r["accn"],
                    basis="native",
                    fiscal_period=r.get("fp"),
                    fiscal_year=r.get("fy"),
                    unit="shares",
                ),
            )
    return sorted(points.values(), key=lambda p: (p.metric_key, p.period_end, p.filed))


# The metric whose stored value on a filing follows the CURRENT label (see the module docstring): a label
# switch can leave an old-label number on a filing the current label reports differently. The share series
# are stored per concept under their own metric_keys and never switch, so they keep plain skip-on-key.
_LABEL_FOLLOWING_METRICS = frozenset({REVENUE_METRIC})


@dataclass(frozen=True)
class HeldFiling:
    """What the store holds for one (metric_key, period_end, filed): how many versions, and the value the
    as-of read picks on that filing (the latest-recorded one)."""

    versions: int
    value: float


@dataclass(frozen=True)
class PlannedVersion:
    """One version ``plan_versions`` would append: the point and — for a label-switch supersession — the
    value the filing held before (``None`` for a plain new filing). No stamp: ``recorded_at`` is the
    writer's honest transaction time (``append_planned``), never a property of the plan.
    """

    point: QuarterPoint
    supersedes_value: float | None = None


def held_versions(
    conn: psycopg.Connection, security_id: UUID, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[tuple[str, date, date], HeldFiling]:
    """The (metric_key, period_end, filed=valid_from) filings already stored for this security, each with
    its version count and the value the read picks there (``recorded_at, id`` order — the as-of pick). The
    idempotency map: a re-ingest skips what is held, so the append-only table never silently grows
    (count-the-table)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metric_key, period_end, valid_from, value FROM fact_fundamentals "
            "WHERE tenant_id = %s AND security_id = %s ORDER BY recorded_at, id",
            (tenant_id, security_id),
        )
        held: dict[tuple[str, date, date], HeldFiling] = {}
        for r in cur.fetchall():
            key = (r["metric_key"], r["period_end"], r["valid_from"])
            prior = held.get(key)
            held[key] = HeldFiling((prior.versions if prior else 0) + 1, float(r["value"]))
        return held


def fundamentals_row(
    security_id: UUID, q: QuarterPoint, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> dict[str, Any]:
    """One quarter as its ``fact_fundamentals`` column values — everything EXCEPT ``recorded_at``, which is
    the writer's transaction-time stamp (``append_planned``), never a property of the quarter."""
    return {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "metric_key": q.metric_key,
        "period_end": q.period_end,
        "fiscal_period": q.fiscal_period,
        "fiscal_year": q.fiscal_year,
        "value": q.value,
        "unit": q.unit,
        "basis": q.basis,
        "accession": q.accession,
        # a revenue row names its XBRL tag (``select_revenue`` stamps ``concept``) — two tags can share one
        # accession, so the accession alone cannot say which tag's number a card cites (#6)
        "source": f"companyfacts:{q.concept}" if q.concept else "companyfacts",
        "valid_from": q.filed,  # KNOWABILITY: the filing acceptance date, never today
    }


def plan_versions(
    held: dict[tuple[str, date, date], HeldFiling], quarters: Iterable[QuarterPoint]
) -> tuple[list[PlannedVersion], int]:
    """PURE: which of ``quarters`` to append over what the store ``held`` -> (the versions, skipped count).

    - A filing not held -> append (``valid_from`` = its filing date, R17).
    - A filing held with the same number -> skip (the idempotency rule: a re-run appends ZERO).
    - A REVENUE filing held with a DIFFERENT number -> the label-switch case: append the current label's
      number as a new version of that same filing. ``valid_from`` stays the filing date, so its knowable date
      is the filing's (D3a); the writer's honest stamp is later than every version already held, so it is
      the latest-recorded version of that filing and the read picks it.
    - ...EXCEPT when the held number is one ``quarters`` reports for that period on a LATER filing: then it
      is the current label's own number, first made public on THIS filing under the old tag, and
      superseding it would date it at the later filing instead (MEASURED on the dev copy: PSQH's Q2-2024,
      $5,985,228 under ``Revenues`` on 2024-08-14 and under the contract tag only from 2025-08-12 — the
      contract tag said $2,290,574 on the 2024 filing; superseded, a swept event was dated a year late).
      Skipped; the read keeps the number and D3a dates it at this filing.
    - A share filing held with a different number -> skip (unchanged behavior for those series).

    ``held`` is not mutated."""
    held = dict(held)
    quarters = list(quarters)
    # (metric, period_end) -> every (filed, value) the incoming series reports for it
    reported: dict[tuple[str, date], list[tuple[date, float]]] = {}
    for q in quarters:
        reported.setdefault((q.metric_key, q.period_end), []).append((q.filed, q.value))
    out: list[PlannedVersion] = []
    skipped = 0
    for q in quarters:
        key = (q.metric_key, q.period_end, q.filed)
        prior = held.get(key)
        if prior is None:
            out.append(PlannedVersion(q))
            held[key] = HeldFiling(1, q.value)
        elif prior.value == q.value or q.metric_key not in _LABEL_FOLLOWING_METRICS:
            skipped += 1
        elif any(
            f > q.filed and v == prior.value for f, v in reported[(q.metric_key, q.period_end)]
        ):
            skipped += (
                1  # the held number IS the current label's, filed later under it — keep its date
            )
        else:
            out.append(PlannedVersion(q, supersedes_value=prior.value))
            held[key] = HeldFiling(prior.versions + 1, q.value)
    return out, skipped


def append_planned(
    conn: psycopg.Connection,
    security_id: UUID,
    planned: Iterable[PlannedVersion],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> int:
    """Append each planned version as a bitemporal fact: ``valid_from = filed`` (its public knowability —
    R17) and ``recorded_at`` = the DATABASE instant this call stored it (the honest transaction time).
    Append-only; the caller owns the txn (no commit here).

    THE STAMP (migration 0057, 2026-10-21; the §2.4 acceptance bar — "no backdated recorded_at on the live
    path"). ``recorded_at`` is honest bookkeeping — when THIS system stored the row — never the history gate
    (history is judged on the public clock, ``valid_from`` here; INVARIANTS.md §4). Before 0057 every row was
    stamped ``recorded_at = filed 00:00 UTC``, wrong as bookkeeping (MEASURED on prod: all 120,876 rows; a
    lower bound on the backdate puts the median at ~6.2 years, 108,919 rows over a year, since a name's whole
    history is written on the night it joins a basket). Those rows stay exactly as written (no rewrite of
    history; moot under the public clock); every NEW row carries the honest stamp. What keeps the CALL
    unmoved is on the read side (``db.bitemporal``): a quarter dates by its filing (``knowable_from``), and
    the pick is the latest FILING — so a name's full history written in one transaction (one shared stamp)
    dates and picks exactly as before.

    ONE stamp for the batch, read from the database clock (``db.clock.db_now`` — the one-clock rule, the D1
    benchmark precedent) right before the appends, i.e. after the companyfacts pull: the instant the rows
    were in hand. Not injectable — a production writer with a stamp argument is the backdating door this
    closes.
    """
    n = 0
    stamp: datetime | None = None
    for v in planned:
        if stamp is None:  # read once, and only when something is new (a no-op re-run asks nothing)
            stamp = db_now(conn)
        append_fact(
            conn,
            "fact_fundamentals",
            {**fundamentals_row(security_id, v.point, tenant_id=tenant_id), "recorded_at": stamp},
        )
        n += 1
    return n


def store_quarters(
    conn: psycopg.Connection,
    security_id: UUID,
    quarters: list[QuarterPoint],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> FundamentalsResult:
    """Append each quarter as a bitemporal fact per ``plan_versions``: ``valid_from = filed`` (its public
    knowability — R17) and ``recorded_at`` = the honest database instant of this batch (``append_planned``).
    Idempotent: a filing already held with the same number is skipped, so a re-run appends ZERO — and the
    table's UNIQUE on the filing (migration 0057) makes a duplicate a loud error, never a silent extra row.
    Append-only; the caller owns the txn (no commit here)."""
    planned, skipped = plan_versions(held_versions(conn, security_id, tenant_id), quarters)
    return FundamentalsResult(
        append_planned(conn, security_id, planned, tenant_id=tenant_id), skipped
    )


def ingest_fundamentals_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    client: EdgarClient,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> FundamentalsResult:
    """Fetch this security's companyfacts (cache-first via the injected client), extract the quarterly
    revenue series AND the S4 shares-outstanding series (both ride the SAME single pull — zero extra SEC
    traffic), and store them. A CIK-less security contributes nothing (like the Form-4 leg's no-CIK
    mirror). The multi-year backfill is intrinsic: companyfacts returns the full history in one pull, so a
    single call reconstructs every historical quarter at its own ``filed`` date."""
    if not sec.cik:
        return FundamentalsResult(0, 0)
    cf = client.get_json(
        companyfacts_url(sec.cik),
        f"companyfacts/CIK{int(sec.cik):010d}.json",
    )
    sel = select_revenue(cf)
    res = store_quarters(conn, sec.id, sel.points + extract_share_points(cf), tenant_id=tenant_id)
    return FundamentalsResult(res.appended, res.skipped, screened=tuple(sel.screened))


__all__ = [
    "REVENUE_METRIC",
    "SHARES_OUT_METRIC",
    "SHARES_OUT_COVER_METRIC",
    "SHARES_ISSUED_METRIC",
    "SHARE_CONCEPTS",
    "QuarterPoint",
    "FundamentalsResult",
    "RevenueSelection",
    "ScreenedRow",
    "SCREEN_FY_IN_QUARTER",
    "SCREEN_YEAR_IN_Q4",
    "SCREEN_RESTATED_FY_Q4",
    "HeldFiling",
    "PlannedVersion",
    "select_revenue",
    "extract_revenue_quarters",
    "extract_share_points",
    "fundamentals_row",
    "held_versions",
    "plan_versions",
    "append_planned",
    "store_quarters",
    "ingest_fundamentals_for_security",
]
