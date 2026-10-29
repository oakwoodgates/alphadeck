# Fundamentals Multiples

> Slug `fundamentals-multiples` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu
> `order: 160`. The operator approved this dashboard and the choices below (2026-10-29); the choices the build makes
> that this plan does not spell out go under "Choices to confirm" for the operator's review. The rules every dashboard
> follows: `docs/DASHBOARDS.md`. The fifth page of the RESEARCH set.

**Label:** Fundamentals Multiples. **Description:** "One card's revenue series, name by name and link by link: the
last twelve quarters as small bars, each dated by its filing, with the link's median growth above the row."

It answers "is the whole link's revenue inflecting, or one name's": one card at a time, one row per link, one small
chart per name. Every bar is a quarterly revenue print dated by the filing that made it public (the public clock,
invariant #1), so at a past as-of a name's chart ends at the last quarter that had been filed by then. Display only:
it never feeds a call (the revenue-acceleration detector reads the same series; this page only draws it), never writes,
never calls the LLM.

## The rows (one card), top to bottom

1. **The card line:** the median YoY per calendar quarter across the card's counting names ("median of k names" on
   hover), as a thin line with dots over the same twelve-quarter axis.
2. **One row per real link** in chain order (`thesis.segments` as loaded: today's chain, Discovered wherever it sits):
   the link's label, its median-YoY line, then its names as small multiples in roster order. A roster link not on the
   card today sits in an "old link" row after the real links; rows with no link in a "No link" row. Never dropped.
3. **Context and Bench** rows behind a toggle ("Show Context and Bench", off by default); their names never enter the
   card line or a link's median, whatever the toggle says.

## The small multiple (one name)

- **The series:** `pit.fundamentals_facts(sid)` with `metric_key = "revenue"`, the as-of read (the latest version per
  period end; an active data-quality set-aside already falls back to the version it superseded). The last **twelve
  period ends** on or before the as-of, by period end; the x axis is the calendar quarter of each period end
  (fiscal calendars differ, so two names' Q2 may be different months: the hover says the period end).
- **Bars:** revenue per period, the name's OWN scale (a tiny axis label for the top value, "$1.6B"); a `derived` basis
  (FY minus nine months) is hatched; a `native` one solid.
- **YoY dots** above the bars: the change against the row for the same period end one year earlier within 10 days,
  when that row is in the same read (`filings.prior_year_row`, the Filing Stream's rule); no dot otherwise. The dot's
  sign is the only tone on the chart (the Rotation Quadrant's muted pair, at most).
- **Dating:** each bar is placed by period end but DATED by its filing: the hover shows the period end, the value, the
  basis, the filing date (`valid_from`), the accession with its EDGAR link (`filings.filing_index_url`), and
  "first knowable <date>" when `knowable_from` is earlier than the picked version's `valid_from` (a re-report).
- **The last print:** under the chart, "Q2 2026 · filed Aug 7 · 83 days before the as-of" and its YoY as text.
- **No series:** a name with no revenue facts shows an empty frame reading "no revenue series stored" and is counted;
  a name with fewer than five period ends is drawn as is and marked thin.
- **Set-asides:** the count of active set-asides on the card's names (`repositories.fundamentals_set_aside_repo.active`
  filtered to the roster's ids, read only) rides in the work line, and a name with one shows "1 period set aside" under
  its chart. The chart draws what the as-of read returns.
- A flagged price history does not touch this page (no price is read); the shared `FlaggedNote` is not shown.

## The link's median line

Per calendar quarter, the median of the YoY dots across the link's counting names that have one that quarter; drawn
only where at least two names contribute, with "median of k" on hover; a link with one name shows that name's dots as
the line, labeled "1 name". Equal weight, no score.

## Picking the card

The shared picker (`baskets.picker_cards`): the cards counted at the as-of, A to Z, the first by default; the card's
name calls `onSelectThesis(thesisId)`; each name's ticker chip calls `onSelectThesis(thesisId, ticker)`.

## The quiet "show the work" line

The card and the as-of; the clock; roster rows / resolved / unresolved / silent; names with a series / without; thin
names; active set-asides on the card's names; the twelve-quarter span (first and last calendar quarter drawn); the
cache state and `compute_ms`; the roster note when today's roster stands in. Below it: "revenue is the XBRL series only
(companyfacts), quarterly, Q4 derived from the annual where no quarterly fact exists"; "placed by period end, dated by
filing"; "medians, equal weight".

## Cache

`dashboards.cache.cached(conn, "fundamentals-multiples", (str(thesis_id), clock), asof, ...)` holds one card's series
and dots for every roster name, Context and Bench included (the toggle is client side). The picker list is cached as
`("cards", clock)`. The cache clears on a new run or a basket edit (rule 7).

## Backend and frontend

- `backend/dashboards/fundamentals_multiples.py`, pure: the twelve-period window, the quarter axis, the YoY dots, the
  link and card medians with their n's, the thin rule, the counts.
- `backend/app/routers/dashboards/fundamentals_multiples.py`: `GET /dashboards/fundamentals-multiples/cards?asof=`
  (the shared picker) and `GET /dashboards/fundamentals-multiples/{thesis_id}?asof=` (the rows, each name's series,
  dots and last print; 404 for a card not counted at the as-of). `known_at, clock = serve_view(asof)` threads into
  every read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_fundamentals_multiples.py`: the window (twelve, fewer, none), the quarter axis across
  fiscal calendars, the YoY rule (±10 days, missing prior, zero prior), the derived/native basis, the re-report tag, the
  medians and n's on hand-computed numbers (one name, two names, a quarter with one contributor), Context and Bench
  excluded from medians, the counts; the router off one cached read with its 404; and, DB-backed, **a revenue print
  filed after a past as-of is not drawn** (two prints for one period end with different `valid_from`, the earlier
  as-of shows the first, the later as-of the second) and a roster row added after the as-of is not a chart
  (`thesis_repo.get_asof`).
- `frontend/src/dashboards/fundamentals-multiples/`: `entry.ts` (slug `fundamentals-multiples`, order 160,
  `asofAware: true`), `FundamentalsMultiples.tsx` (inline SVG small multiples laid out by a pure `multiplesLayout.ts`;
  no chart library, no new dependency; the hover readout above the grid; the toggle), `useFundamentalsMultiples.ts`,
  `fundamentals-multiples.css`, tests. The Chain Map's SVG pattern (pure layout, keyboard focus moving between charts,
  one readout line) is the one to follow.
- **Layout:** the research set's document-like look; charts in a responsive grid (about 160 px wide each, wrapping),
  rows separated by the link header; readable on a phone with one or two charts per line.

## Known limits (stated on the page too)

- **Revenue only** today; the series is rows-not-columns, so margins or cash flow slot in later as more rows.
- **Fiscal calendars differ:** the axis is the calendar quarter of the period end, not a fiscal label.
- **Q4 is derived** where the filer reports no quarterly fact (FY minus nine months), shown hatched.
- **Thin series** (fewer than five periods) are drawn and marked, not hidden.
- **The roster is the card's roster as of the as-of; links are today's.**
- **Medians, equal weight, no score.**

## Out of scope

Other metrics (EPS, margins, cash flow); any ranking; estimates or consensus; any Cockpit change; any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review: twelve period ends; the calendar-quarter axis; the link median drawn only
with two or more contributors; Context and Bench never in a median.
