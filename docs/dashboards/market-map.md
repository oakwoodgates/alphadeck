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
  list scales it the same way, from the cover's own date (`period_end`). **The cover count stands in only when it is
  recent and a share count** (2026-10-30 review fix): the latest cover row must be filed within 200 calendar days on or
  before the AS-OF (judged against the as-of, so a past as-of reads the same rule against its own day) and be at least
  10,000 shares. A company that stopped tagging the cover count keeps its last, old value forever (Visa's and
  Mastercard's latest are from 2010, FOXA's from 2019 with the value 1, MOBX's from 2023); some tag a placeholder of 1.
  A refused count leaves the gap and the info box says why ("the filing cover's count is from 2010-11-02, too old to
  stand in", "the filing cover's count (1) is not a share count"). The info box names the source: "share count
  ratified in the Workbench" or "share count from the filing cover (not ratified)". A company with neither is drawn at
  the smallest weight in view with a dashed edge; its info box says "no market cap: drawn at the smallest size".
  MEASURED on dev (prod copy, 2026-10-30) BEFORE the gate: caps 870 Workbench / 173 cover / 416 none of 1,459 drawn
  (about 71% with a cap). The gate moves some of the 173 to none; the count after it awaits the dev re-measure (the
  work line shows it: "caps: N Workbench / N filing cover / N none").
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

- **Caps are approximate** and about 29% of the companies had none before the cover count's recency gate (416 of 1,459
  drawn: no Workbench share count and no usable quarterly cover count); the gate moves some cover-count companies to
  none, and the figure after it awaits the dev re-measure. Filling share counts for every basket company is a separate
  data job.
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

### Built choices to confirm

Choices the build made that the plan above does not spell out (2026-10-30). Each is one line to keep or change.

1. **The payload is readings, not a layout.** `GET /dashboards/market-map?asof=` returns every drawable company (six returns with a reason for each blank, the cap with its band and source, the 20D dollar volume, today's sector and business type), the memberships (card, link, `silent`), the cards, SPY's and IWM's six returns and the work-line counts. Every control is client side, so the as-of is the only thing in the query key.
2. **A flagged company's prices are never read** (the flags are read first, the point-in-time read runs over the rest). It is left off the map and listed with a `silent_only` mark, so the page hides a Context-only flagged name until the toggle is on.
3. **YTD gaps.** Besides "no close on or before Dec 31", a company whose latest bar is still dated on or before that Dec 31 ("no session yet in 2026") or whose base close is not positive reads a stated gap, never 0%.
4. **Dollar volume 20D is strict:** the last 20 sessions with a close, and a volume missing on ANY of them is a gap. This differs slightly from `volume_regime`'s `adv_usd_20d` (the Peer Table's figure), which skips volume-less bars.
5. **The cover stand-in.** Gate: at least one cover count with `fiscal_period` Q1, Q2 or Q3 among the as-of rows. The latest cover row by filing date then period end is used even when it is a 10-K's (`FY`) row. Its date is `min(period_end, filing date)` (a cover date later than its own filing is a typo; ASLE's 2034 date is the known case). It is consulted only when there is NO Workbench count; a Workbench count that `cap_cell` withholds (an unread ADS ratio) is not replaced, and shows `cap_source` null with the gap reason. Review fix (2026-10-30): only the LATEST cover row is a candidate (never an older one: older is staler), and it is used only when filed within `COVER_MAX_AGE_DAYS` = 200 calendar days on or before the as-of and at least `COVER_MIN_SHARES` = 10,000 shares; otherwise `cap_note` carries the reason shown in the info box.
6. **A company with no price bars is still drawn** (a hatched no-reading tile, its reason in the info box); only a flagged history is left out.
7. **Color steps are the nearest of the nine** (a value rounds to the step; 0.3% on the 1d scale stays gray). Step colors are 13 / 26 / 39 / 52% of `--pos` / `--neg` over a neutral `#232932`; no-reading is a darker diagonal hatch.
8. **Compare to** is disabled per window with the reason ("no IWM tape at this as-of", "SPY has no 1Y reading"). If the chosen benchmark stops being available after a window change, the page falls back to raw returns and says so; it never shows a raw figure as a relative one.
9. **The smallest weight** a no-weight company is drawn at is the smallest positive weight among the companies in view (after the Context/Bench toggle), across the whole map, so zooming never changes a size. If no company has a weight, every tile weighs 1 and is dashed.
10. **Context and Bench off:** their rows are dropped from the card views and a company with only Context/Bench rows is in no view; a company with a voting row and a silent row is in view either way, and its silent card or link only when the toggle is on.
11. **Tiles: Groups:** the innermost group becomes one tile (sum of weights, median color, no figure below 3 priced names). `All companies` + Groups is one tile. A group tile under a card opens that card on a click; any other group tile pins its info box.
12. **The click rule outside the card views** counts the cards among the memberships in view: one opens the Cockpit on that card and the ticker it carries there, several pin the info box, whose card list is the Cockpit links. Escape unpins first, then steps out one zoom level.
13. **Zoom** resets when Group by changes (not when the window or size changes); a path the tree no longer holds resolves to its longest valid prefix. The breadcrumb's "← All" and each earlier crumb step back.
14. **The table** has one row per tile (so a company on three cards has three rows in the card views, each with its Group path), the cards as quiet Cockpit chips, and no color on its cells. Tiles: Groups lists the group tiles with a median per window. A phone (under 768px) opens on the table; the first view is chosen once, on mount.
15. **Layout details:** a group box shows its header strip only when it is at least 36px wide and 36px tall; a tile shows ticker and return when both fit, the ticker alone on a smaller one, nothing on a tiny one. The map height is the viewport minus its own top and 96px for the legend and work lines (minimum 480px). Fullscreen fills the page body (controls, map and legend), and the button is not drawn where the browser has no Fullscreen API.
16. **The work line** adds "tiles drawn", "caps: N Workbench / N filing cover / N none", "N companies drawn dashed at the smallest size", the scale end for the window and the roster-fallback line (as on the Rotation Map); the card views add the note that a company is drawn once per card or link.
17. **The shared registry test** (`frontend/src/dashboards/__tests__/registry.test.ts`) lists every slug and order, so it gains `market-map` / 270: a parallel dashboard PR will conflict there as well as in the two contract files.
18. **The work line counts what the note lists.** Its "names left out for a flagged price history" counts the flagged names in view under the Context/Bench toggle (a Context-or-Bench-only flagged company counts only when the toggle is on), the same set `FlaggedNote` lists (review fix: it had counted all 30 while the note listed 14).

### Measured at build time (2026-10-30)

The build agent does not touch the dev stack; the dev numbers below were measured by the reviewer.

- **On dev with a prod copy, MEASURED (2026-10-30):** cold compute 62.6 s; cache hit 0.12 s; payload 1,013,283 bytes of raw JSON (the gzipped size is not measured on real data; 151,565 bytes on the synthetic payload below); the Rotation Map's cold read in the same session 51.8 s (the Market Map costs about 20% more than the Rotation Map).
- **Caps before the recency gate, MEASURED:** 870 Workbench / 173 cover / 416 none of 1,459 drawn. The unfiltered cover count was wrong for companies that stopped tagging it: Visa's latest cover row is from 2010 (cap $193B), Mastercard's from 2010 ($78B), FOXA's from 2019 and QVCG's the value 1 (cap $0), MOBX's from 2023.
- **The gate validated, MEASURED:** of the 855 companies with both a Workbench and a cover count, the gated cover count is within 5% of the Workbench count for 807, within 25% for 34 more and further off for 14; all 14 are a split between the two counts' dates or new shares issued.

- **Payload, MEASURED on synthetic data** (1,489 companies, 2,255 memberships, 31 cards, random returns): 1,017,750 bytes of JSON, 151,565 bytes gzipped (bar: under 1 MB gzipped).
- **Render, MEASURED on synthetic data in a Vite dev build** (React development mode, the in-app browser pane while hidden, JavaScript and commit only, no paint): 1,489 companies, 2,412 tiles in `Card › Link`. Switching Group by to `Card › Link` 130 to 140 ms; Sector 75 to 130 ms; Table to Map 215 to 240 ms; the table of 2,412 rows 790 ms. A production build will be faster; the real-data figure is still to measure.
