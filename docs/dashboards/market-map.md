# Market Map

> Slug `market-map` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 270` ·
> `layout: "page"`. The operator asked for this page on 2026-10-30 with three reference screenshots (a sector heat map
> of the S&P 500, the same map zoomed into one sector, and a whole-market map grouped by size): "a view comparable to
> the screenshots for value chains, sectors, business types, cards, companies, etc over different time frames." The
> choices this plan makes that the ask does not spell out are listed under "Choices to confirm" for the operator's
> review. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Market Map. **Description:** "A heat map of every company on the cards: tiles sized by market cap, colored
by the return over a chosen window, grouped by card and link, sector, business type or cap band."

It answers "where is the money moving across everything I follow" at a glance, the way a market heat map does, but over
the cards' own companies and grouped by the cards' own structure (the thesis is the spine, invariant #2: this is not a
whole-market screener). The Rotation Map answers the same question as a sortable table of group medians; this page
draws it. Display only: it never feeds a call, never writes to the database and never calls the LLM.

## What it shows

A nested treemap. Each company is a rectangle (a **tile**) whose area is its weight (market cap by default) and whose
color is its return over the chosen window. Tiles sit inside **group boxes**, each with a header strip.

### The controls (one bar above the map)

- **Group by:** `Card › Link` (default; the value chains: card boxes, each holding its link boxes, each holding its
  companies) · `Card` · `Sector › Business type` (supersector boxes holding business-type boxes) · `Sector` ·
  `Business type` · `Cap band` (nano · micro · small · mid · large · no cap) · `All companies` (one flat map).
- **Window:** `1d` (default) · `7d` · `30d` · `90d` · `YTD` · `1Y`. The first five are the Cockpit's own trailing
  returns (the `trailing_returns` display member, in trading bars: "7d" is seven sessions, not a calendar week). `YTD`
  is the last close against the last close on or before December 31 of the prior year.
- **Compare to:** `Nothing` (default) · `SPY` · `IWM`. A benchmark subtracts its own return for the same window from
  every return on the page (tiles, group figures, the info box). Disabled with a quiet reason when that benchmark has no
  tape at the as-of.
- **Size by:** `Market cap` (default) · `Dollar volume 20D` (the mean of close × volume over the last 20 sessions) ·
  `Equal`.
- **Tiles:** `Companies` (default) · `Groups`: each innermost group becomes ONE tile, sized by the sum of its
  companies' weights and colored by its median (so `Card › Link` + `Groups` = card boxes holding one tile per link;
  `Card` + `Groups` = one tile per card).
- **Show Context and Bench** (off by default).
- **Map / Table:** the table lists the companies in view (one row per tile) with every window, the cap and its band,
  dollar volume, sector, business type and the cards and links; every column sorts. It is the accessible path and the
  phone default.
- **Fullscreen:** the map's container through the browser's Fullscreen API.

### Tiles

- A tile shows its ticker and its return when it fits, the font scaling with the tile (as in the screenshots); a
  smaller tile shows the ticker only; a tiny one shows nothing (the hover still works).
- **Hover** opens an info box beside the cursor: ticker and company name; the return in every window (with the
  benchmark's subtraction when one is set, the raw figure beside it); the market cap with its band and its source (see
  Sizing); the 20D dollar volume; sector and business type; every card and link the company sits on; any gap ("no 90d
  reading: 61 of 91 bars").
- **Click** on a company tile opens the Cockpit on that name (`onSelectThesis(thesisId, ticker)`) when the tile belongs
  to one card (always true in the `Card` and `Card › Link` views). When the company sits on several cards in view, the
  click pins the info box instead, and its card list is the set of Cockpit links.

### Group boxes

- The header strip shows the group's label and its **median** return for the window over its priced companies, when at
  least 3 are priced (the Rotation Map's `MIN_PRICED` floor); fewer reads no figure. Hover on the header: "median of 7
  priced names · 9 names".
- **Click** on a header zooms into that group (the screenshots' "← Financials"): the group fills the map and a
  breadcrumb ("All › <card> › <link>") steps back out. Escape steps out one level. Zoom resets when Group by changes.

### Color

A diverging scale with a fixed end per window, so a color means the same move from one day to the next:

| window | full red | gray | full green |
|---|---|---|---|
| 1d | −3% | 0 | +3% |
| 7d | −8% | 0 | +8% |
| 30d | −15% | 0 | +15% |
| 90d | −25% | 0 | +25% |
| YTD | −40% | 0 | +40% |
| 1Y | −50% | 0 | +50% |

Nine steps (the end, ¾, ½ and ¼ of it on each side, and 0), drawn from the app's own `--neg` / `--pos` tokens blended
toward a neutral gray; beyond the end stays the end color. The legend under the map shows the nine steps with their
values for the window in force. A relative return (a benchmark set) uses the same scale. **No reading** for the window
(too little tape) is a darker, hatched tile with "—", never the flat-gray of a 0% move.

### Sizing

- **Market cap:** the approximate cap at the as-of by the Peer Table's rule (`dashboards.peer_table.cap_cell`: the
  latest close × the latest Workbench share count, ADS ratio and split list applied). When the company has no Workbench
  share count, the latest share count the nightly stored from its filings' cover pages (`fact_fundamentals`,
  `metric_key = 'shares_out_cover_xbrl'`, read through `pit.fundamentals_facts`, public from its filing date) stands
  in, for a company with at least one QUARTERLY cover count only (a 10-Q filer, so no ADS-ratio question); the split
  list scales it the same way, from the cover's own date (`period_end`). The info box names the source: "share count
  ratified in the Workbench" or "share count from the filing cover (not ratified)". A company with neither is drawn at
  the smallest weight in view with a dashed edge; its info box says "no market cap: drawn at the smallest size".
  MEASURED on dev (prod copy, 2026-10-30): 881 of the 1,489 live basket companies have a Workbench share count; 177 of
  the other 608 have a quarterly cover count, so about 1,058 (71%) get a cap and about 430 are drawn dashed.
- **Dollar volume 20D:** needs 20 sessions; a company with fewer is drawn at the smallest weight, dashed.
- **Equal:** every tile weighs 1.
- A company appears once per group it belongs to: once per card-and-link membership in `Card › Link`, once per card in
  `Card`, once in total in the sector, business-type, cap-band and all-companies views. In the card views a company on
  five cards is drawn five times, so the total area there is the cards' structure, not the market; the work line says so.

## The plan's choices (rule 4)

- **Loudness:** this is a heat map, so every priced tile carries its color: that is the page's purpose and the operator's
  ask. The scale is fixed per window (above), no accent marks anything beyond it, and nothing notifies.
- **Which names count:** the counted cards at the as-of (`baskets.cards_as_of`; archived out). Context and Bench
  memberships (`domain.thesis.is_silent`) are out by default; the toggle adds them, as their own "Context" and "Bench"
  link boxes in `Card › Link`, as members of their card in `Card`, and as companies everywhere else.
- **A flagged price history** (`dashboards.baskets.price_flags`, rule 5) leaves the company off the map and out of every
  median; the shared `FlaggedNote` ("N names left out for a flagged price history") lists each with its reason.
- **Unresolved basket rows** (no security) are counted on the work line, never drawn.
- **Groups with no link** read "Unsegmented"; a company with no business type or sector, "Unclassified"; no cap,
  "No cap" in the cap-band view.
- **Cache:** yes, the shared dashboard cache (rule 7).

## The quiet "show the work" line

Under the map: the as-of; cards counted (and created after the as-of); companies; tiles drawn; priced for the window;
caps by source (Workbench / filing cover / none); the Context/Bench state and the rows it leaves out; unresolved rows;
flagged names; the window's color scale; the benchmark; "business type and sector are today's"; the cache state and
`compute_ms`.

## Past as-of

- `known_at, clock = domain.market_time.serve_view(asof)` once per request, threaded into every read (rule 2): a price
  bar, a Workbench share count and a cover count count only from when they were public, up to the end of the as-of.
- Each card's roster as of that date (`baskets.cards_as_of`); a card with no roster snapshot by then keeps today's
  roster and the work line says so; cards created after the as-of are not on the map.
- YTD at a past as-of is against the last close of the year BEFORE the as-of's year.

## How it computes

- **One heavy read per as-of, cached:** `cached(conn, "market-map", (clock,), asof, read)` holds the counted cards and
  their memberships (each with its card, link and a `silent` flag), and per resolved company (chunked through
  `baskets.per_company`, the Rotation Map's read): the `trailing_returns` member's five windows, YTD and the 20D dollar
  volume from the same bars, the cap with its source; the SPY and IWM returns for all six windows; the price flags.
- **Identity per request** (outside the cache, like the Rotation Map): company name, business type, supersector from
  `securities.master.identity_for`.
- **The payload ships readings, not a layout.** The page builds every grouping, the medians, the colors and the
  squarified layout itself, so every control switches without a refetch. One endpoint:
  `GET /dashboards/market-map?asof=` (silent memberships included and marked; the page filters them).
- **Cost:** one display member plus the cap reads over about 1,500 companies; expect it near the Rotation Map's cold read.
  MEASURE the cold compute, the cache hit, the payload size (bar: under 1 MB gzipped) and the render time of the
  largest view (`Card › Link`, every company tile) on dev with a prod copy, and record them here.

## Where it lives

- Compute (pure): `backend/dashboards/market_map.py` (YTD, dollar volume, the cap with its fallback and source, the
  company reading).
- Router: `backend/app/routers/dashboards/market_map.py`, its wire models in the same module.
- Tests: `backend/tests/dashboards/test_market_map.py`; `frontend/src/dashboards/market-map/__tests__/`.
- Frontend: `frontend/src/dashboards/market-map/` (`entry.ts`, `MarketMap.tsx`, `useMarketMap.ts`, `squarify.ts`,
  `mapView.ts`, `market-map.css`). The squarified layout is written here (Bruls, Huizing and van Wijk's algorithm), no
  new dependency.

## Known limits (stated on the page too)

- **Caps are approximate** and about 29% of the companies have none today (no Workbench share count and no quarterly
  cover count). Filling share counts for every basket company is a separate data job.
- **Business type and sector are today's** at any as-of.
- **The card views repeat a company once per card or link,** so their areas are the cards' structure, not the market.
- **The color scale is fixed per window**, not relative to the day; a quiet day is a gray map.

## Later versions (out of scope now)

A mini price chart in the info box; color by other readings (RVOL, distance from the 50-day, 52-week range position);
a ticker search that highlights its tiles; playing the as-of forward as an animation; share counts for every basket
company (the data job above).

## Choices to confirm

Pre-decided here for the operator's review:

1. **One page with a Group-by selector**, not one page per grouping.
2. **Defaults:** `Card › Link`, `1d`, market cap, company tiles, Context and Bench off, no benchmark.
3. **The cap's stand-in share count** (the filing cover count, 10-Q filers only), on this page only. The Peer Table,
   Hot Board, Ticker Lookup and Insider Buying keep their "awaiting shares" cell, because there it is the TRIAGE to-do
   (a Workbench pull).
4. **The fixed color scale per window** (the table above).
5. **The windows** are the Cockpit's five (trading bars) plus YTD.
6. **Five cap bands** (the operator's fixed bands; no "mega" band as in the third screenshot).
7. **A flagged price history is left off the map** and listed, as on every price dashboard.
8. **Group figures are medians** over priced names with the floor of 3, as on the Rotation Map.
