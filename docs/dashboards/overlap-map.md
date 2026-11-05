# Overlap Map

> Slug `overlap-map` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 300` (the
> ETFs page holds 290). The operator approved this plan on 2026-11-05 for a local build; the "Choices for the operator"
> below are built as recommended until the operator says otherwise, and the choices the build makes that this plan does
> not spell out go under "Choices to confirm". The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Overlap Map. **Description:** "Where the cards overlap: cards as bubbles joined by the companies they share,
a Venn of any two or three, and each card's sector mix."

## What it answers

Where do my cards touch? The operator asked for "a venn diagram and/or bubble maps type of view for companies on
cards, sectors on cards, etc." (2026-11-05). Shared Names answers the same question as a table and lists "a graph
view" as out of scope. This page is that graph view. It shows structure only, with no prices and no calls. Display
only: it never feeds a call, never writes, and never calls the LLM (rule 1). It never suggests merging, splitting or
ranking cards (invariant #4: deferential on thesis).

Three views (tabs) on one read: **Map** (default), **Venn**, **Sectors**.

## Measured on dev (a copy of prod from the night of 2026-11-04)

- **Cards and rows:** 65 live cards; 2,171 basket rows (652 silent, 0 unresolved).
- **Companies:** 1,590 companies, 1,196 of them counting (in a non-silent row).
- **Counting companies per card:** 1 to 64, median 21.
- **Companies on 2+ cards, counting names only:** 228 (186 on 2 cards, 34 on 3, 6 on 4, 1 on 5, 1 on 6).
- **Companies on 2+ cards, every row:** 357. The most-shared companies are Context names: one sits on 15 cards and
  counts on none.
- **Card pairs sharing a counting name:** 150. The closest pair shares 16 names, and no pair shares more than 26% of
  their combined names.
- **Lines by threshold (pairs sharing at least N counting names):** 1+: 150 · 2+: 69 · 3+: 40 · 4+: 24 · 5+: 17.
- **Groups (cards joined when they share at least N counting names):**

  | N | Largest groups (cards) | Cards with no line |
  |---|---|---|
  | 1 | 57, 3 | 5 |
  | 2 | 34, 7, 6, 3, 2 | 13 |
  | 3 | 26, 6, 5, 2, 2 | 24 |
  | 5 | 6, 4, 3, 3, 3 | 40 |

  With silent rows included, 64 of the 65 cards join into one group at 1+. That is why counting names are the
  default.
- **Sectors:** the stored sector is the SEC's industry text, 188 different values among counting companies. That is
  too fine to draw, so the page uses the business-type rollup: 10 supersectors (Other and Unclassified included) and
  20 business types.
  - **Supersectors per card, counting names only:** 1: 10 cards · 2: 12 · 3: 14 · 4: 14 · 5: 7 · 6: 6 · 7: 2.
  - **Cards per supersector:** industrials 49 · technology 40 · consumer and comms 22 · materials 21 ·
    healthcare 21 · unclassified 17 · other 15 · financials 14 · energy and utilities 13 · real estate 5.
- **Roster history:** 10 of the 65 cards existed before 2026-09-15, when roster snapshots begin.

## Who counts (all three views)

- **Cards:** the cards counted at the as-of, `baskets.cards_as_of(conn, asof, known_at)`, each with its roster then.
  Today's roster stands in for a card with no snapshot by then, with a quiet note.
- **Rows:** counting names by default. One toggle, **"Include Context, ETFs and Bench"**, adds the silent rows.
  - The rule is `baskets.counted_rows(memberships_of(cards), include_silent)`, which goes through
    `domain.thesis.is_silent`.
  - So the ETFs link is silent from the start. Never hardcode the labels.
- **Matching is by `security_id`.** A company with several rows on one card (several links) counts once for that
  card. A row with no resolved security is counted on the quiet line, never matched.
- **Sector and business type:** today's master, through `securities.master.identity_for`'s `business_type` and
  `business_supersector`, the same read `baskets.group_of` uses. A company with neither is "Unclassified", never
  dropped.

## View 1: Map (the default)

**The bubbles:**
- One bubble per counted card.
- Bubble area is proportional to the card's counting companies. A minimum radius keeps a 1-name card visible.
- Gray, no color by anything.
- A card's name sits on its bubble when it fits; otherwise it shows on hover or focus.

**The lines:**
- A line joins two cards that share at least N companies.
- The control **"Lines at 1 · 2 · 3 · 4 · 5+ shared names"** defaults to 3 (measured: 40 lines).
- Line thickness grows with the shared count and is capped.
- Hover: "A · B: 16 shared names · 26% of their combined names".
- Clicking a line opens the Venn tab with those two cards.

**The layout:**
- It is computed in the backend, pure Python and deterministic, so the map looks the same on every visit.
- It is a force layout over ALL pair weights: a pull proportional to the shared count, a push between every pair of
  bubbles, and no overlap.
- Each card's starting point comes from a hash of its id, so a new card moves the others only a little.
- The iteration count is fixed. Positions are normalized to a fixed box.
- **The threshold only hides lines; bubbles never move when it changes.**
- **Cards that share no name at all** sit in a quiet strip under the map titled "Shares no names", A→Z. They are
  never dropped.

**Shared companies:**
- The toggle **"Show shared companies"** is off by default.
- When on, it draws each company on 2+ counted cards as a small dot at the center of its cards' bubbles. A company on
  two cards sits on the midpoint between them.
- Dots with the same set of cards spread along a short, deterministic offset.
- Hovering a dot draws thin connectors to its cards and shows the ticker, the company name, and each card with its
  links. Clicking it pins that box, whose card list holds the Cockpit links.

**Clicking a bubble:**
- A panel beside the map lists "Shares names with:" each other card, most shared first, each row naming its count and
  its tickers.
- Each row has "Open in Venn".
- The card's name calls `onSelectThesis(thesisId)`.
- A ticker opens the Cockpit on that card (`onSelectThesis(thesisId, ticker)`).

**The groups line:** one quiet line for the current threshold, for example "At 3+ shared names: groups of 26, 6, 5,
2 and 2 cards; 24 cards with no line". Counts only, no group names.

**Access:**
- The map is one tab stop, and the arrow keys move between bubbles.
- On a narrow screen the map scales to the width; tap shows what hover shows.

## View 2: Venn (pick 2 or 3)

**The sets** come from one searchable picker, grouped by kind:
- a **card** (its companies in view);
- a **link on a card**;
- a **supersector** or a **business type** (the companies in view across all counted cards that carry it).

**The default** is the pair of cards sharing the most names. A line click or "Open in Venn" sets the pair.

**Regions:**
- Every region gets its count, which is exact: 3 regions for two sets, 7 for three.
- Clicking a region lists its companies: ticker, name, and one chip per card with its links (the chip opens the
  Cockpit).
- A region named in words above the list: "In A and B, not C".

**The geometry:**
- Circle area is proportional to set size.
- **Two sets are exact:** the distance between the centers is solved by bisection so the lens matches the shared
  count.
- **Three sets are approximate:** each pair's distance is solved the same way, then the three are placed as a triangle
  (clamped when the distances cannot form one). The page says: "areas are approximate for three sets; the counts are
  exact."
- A subset draws inside its parent, and disjoint sets draw apart.
- When one set is more than 50 times another, the small circle keeps a minimum radius, and the page says so.

**Colors:** three muted neutral tints from the app's tokens, no accent.

## View 3: Sectors (a bubble grid)

**The axes:**
- Rows are the counted cards.
- Columns are the supersectors. A toggle switches to business types.
- Columns are ordered by the number of cards they appear on, with Other and Unclassified last.

**Cells and headers:**
- Each cell has a bubble whose area is proportional to the card's companies in that sector (in view, each company
  once). The number shows on hover.
- The column header shows "N cards · M companies", each company counted once across cards.
- The row label shows the card name and "k sectors".

**Row order:** similar cards sit next to each other, by a simple stated rule. Sort by each card's largest column (in
column order), then by that column's share of the card, descending, then by name.

**Clicking:**
- A cell lists that card's companies in that sector, each opening the Cockpit.
- The card name calls `onSelectThesis(thesisId)`.

**On a phone:** only the grid scrolls sideways, never the page.

## The quiet "show the work" line

It shows:
- the as-of;
- cards counted, and cards not yet created at the as-of;
- basket rows;
- silent rows left out (or included);
- unresolved rows;
- companies in view, and companies on 2+ cards;
- pairs sharing a name;
- the cache state;
- "Sector and business type come from today's master";
- the roster note before 2026-09-15.

## API

`GET /dashboards/overlap-map?asof=&include_silent=` returns one payload:
- **cards:** id, name, counting companies, x, y, r, and whether it shares no names;
- **pairs:** card A, card B, and the shared count, for every pair sharing at least one company;
- **groups:** the group sizes and the count of cards with no line, for each threshold 1–5;
- **companies:** id, ticker, name, supersector, business type, and its rows (card index, links, silent, context role);
- **the quiet-line counts and the cache meta.**

The client derives the threshold view, the Venn regions and the grid from that payload; no second request. The wire
models live in the router (rule 8), and the contract is regenerated in the same PR (rule 9). The builder measures the
payload size. The bar is under 1 MB raw: reference cards by index, not by repeated ids.

## Cache

`cached(conn, "overlap-map", (clock, include_silent), asof, compute)` holds the whole payload, the layout included.
The page makes no price or fact reads, only rosters, the master's identity and the layout, so a cold load should take
a few seconds. The builder measures the cold time and the cache hit. If the cold time is over 3 s, the builder adds the
page's default view to `pipeline.warm_dashboards.PAGES`.

## Loudness

Gray throughout. The only marks are the hover and selection outlines. No sector color on the map, because the
Sectors tab carries that. Counts and percents only: no ranking, no labels like "related" or "duplicate", no group
names.

## Thumbnail (one line)

Gray circles of different sizes joined by thin lines, with two overlapping circles in one corner.

## Files

- `backend/dashboards/overlap_map.py` (pure: the pair counts, the groups per threshold, the layout, the payload)
- `backend/app/routers/dashboards/overlap_map.py` (`APIRouter(prefix="/overlap-map")`, one `GET ""`, its wire models)
- `backend/tests/dashboards/test_overlap_map.py`
- `frontend/src/dashboards/overlap-map/`:
  - `entry.ts`, `capability.json` (every capability false, `order: 300`; then `npm run gen:mcp`) and `thumbnail.svg`;
  - `OverlapMap.tsx`, `useOverlapMap.ts`, `vennLayout.ts`, `sectorGrid.ts`, `overlap-map.css` and tests.
- `frontend/src/dashboards/__tests__/registry.test.ts` (the pinned list and orders gain `overlap-map` / 300)
- `docs/dashboards/overlap-map.md` (this plan; the build adds "Choices to confirm") and its Index row in `docs/DASHBOARDS.md`
- the regenerated `backend/openapi.json` + `frontend/src/api/types.gen.ts`

## Reuse (never a second copy)

- `baskets.cards_as_of`, `memberships_of` and `counted_rows`, and `domain.thesis.is_silent` (through those).
- `securities.master.identity_for` for the supersector and business type, as `baskets.group_of` reads them.
- `dashboards.cache.cached`.
- **No new chart library:** the force layout is hand-written in Python, and the Venn geometry in TypeScript, like the
  Market Map's `squarify.ts`.

## Tests

**Backend pure:**
- **Pair counts:**
  - a company in two links of one card counts once for that card;
  - silent rows are out by default and in with the toggle;
  - an ETFs-link row counts as silent;
  - an unresolved row is counted on the quiet line and never matched.
- **Groups per threshold:** on a hand-built fixture, with the hand-computed sizes and the count of cards with no line.
- **The layout:**
  - identical input gives identical positions;
  - no two bubbles overlap;
  - a card sharing nothing goes to the strip;
  - the positions do not depend on the threshold.
- **Point-in-time:**
  - at a past as-of, a card created after it is absent;
  - a roster row added after it is absent;
  - a fixed timestamp, so any lookahead fails the test.
- The isolation tests cover no writes and no LLM on their own.

**Frontend:**
- the three tabs;
- the threshold hides lines without moving bubbles;
- the shared-companies toggle draws dots at the right midpoints;
- a line click opens the Venn on that pair;
- the Venn regions for two and three sets, including a subset and a disjoint pair;
- the two-set geometry's lens area within 1% of its target;
- the sector grid's row order and a cell click;
- a ticker chip calls `onSelectThesis(thesisId, ticker)`.

## Known limits (on the page's quiet line and in the doc)

- **Matching is by security,** so two share classes of one company count as two companies.
- **Sector and business type are today's master** at any as-of.
- **Rosters are point-in-time only from 2026-09-15.**
- **Positions are our own layout:** closer means more shared names, not a measured distance. A new card can shift the
  others a little.
- **Venn areas are approximate for three sets;** the counts are exact.
- **Structure only:** no prices, no calls.

## Choices for the operator (my recommendation first)

1. **Bubble size = counting companies,** not market cap. Market cap would need the Market Map's price and share
   reads: 60 to 80 s cold, measured on that page. It could be added later as an option.
2. **Line threshold default 3** (measured: 40 lines; a group of 26, then 6 and 5; 24 cards with no line), with a 1–5
   control.
3. **Venn sets: cards, links, supersectors and business types** (vs cards and links only). It is the same set math,
   and it answers "sectors on cards".
4. **The default Venn is the pair of cards sharing the most names.**
5. **No color on the map;** the sector mix lives in the Sectors tab.
6. **Sectors default to supersectors (10),** with business types (20) as a toggle; never the raw SEC industry text
   (188).
7. **Name and place:** "Overlap Map", slug `overlap-map`, menu order 300.

## Out of scope

- Price correlation between cards.
- A card family tree (lineage).
- Any suggestion to merge, split or rank cards.
- Saved Venn picks or a saved layout (that needs storage).
- A per-company bubble view (the shared-companies dots cover it).

## Choices to confirm (made by the build; the plan did not spell them out)

**Payload**
1. **Wire names.** The counts are `companies_in_view` and `companies_shared`; `companies` is the list of company objects (the plan used the one word for both). `pairs` holds only `a`, `b`, `shared`; the page derives the percent of combined names from the cards' counts.
2. **A company's place on a card** (`rows[]`): `card` index, its ticker there, its links there (basket order, deduped; `null` = no link), `silent` (every row of it there is Context, ETFs or Bench), and `context_role`. Links are carried so the Venn can offer "a link on a card" and the region lists can print them.
3. **A card in the strip** has `x` and `y` as `null` but a real `r`, drawn at the same scale as the map. A card with 0 companies in view (for example, all its rows are silent and the toggle is off) is also in the strip.
4. **Groups** are computed for thresholds 1–5. A group is a connected set of 2+ cards; a card in no group "has no line". Sizes are listed largest first.
5. **Sector and business type** ride per company as the master's strings (`business_supersector`, `business_type`); `null` is "Unclassified". The Sectors tab puts "Other" then "Unclassified" last, in that order.

**Layout**
6. **Constants** (in `backend/dashboards/overlap_map.py`): bubble radius `6 × sqrt(companies)` with a floor of 7; spring stiffness `1 × shared count` with a rest length of the two radii plus 3; a push of `900 / distance` between every pair; a weak pull to the middle; 240 iterations with a cooling step cap (28 down to 0.6); a final pass that separates any touching bubbles; one uniform scale (positions and radii together, at most 2×) into a 1000 × 640 box with a 24 margin. Starting points come from a SHA-256 of the card id. None of these were tuned against real data; the operator's first look on dev decides.
7. **No numpy.** The layout is plain Python (about 0.35 s for 65 cards).

**Page**
8. **A bubble's panel rows.** The row's card name opens that card's Cockpit; a ticker in the row opens the Cockpit on the ROW's card (the other card) on that company's ticker there. The plan says "that card" without saying which.
9. **Dots.** A company with the same set of cards as others spreads on a golden-angle spiral (4 × sqrt(k) units) around the shared center, the first one exactly on it. Hover shows a readout line under the map (not a floating box); a click pins it and lists each card as a Cockpit link; a click on the empty map or Escape unpins.
10. **Line hit area.** A line is clickable through an invisible 12-unit-wide stroke, since its drawn width is 1–7. Lines are not tab stops (the map is one stop, per the plan); the panel's "Open in Venn" is the keyboard route to a pair.
11. **Keyboard.** Focus on the map lands on the card with the most companies; an arrow moves to the nearest placed bubble that way (off-axis distance counts double); Enter selects it; Escape clears the selection and the pinned dot.
12. **Bubble names.** A name shows on its bubble when its estimated width (5.6 units a character) fits inside 88% of the diameter; otherwise it shows on hover or keyboard focus.
13. **The Venn picker.** A "Add a set" button opens a search box and the sets grouped by kind (cards, links on a card, supersectors, business types); it lists at most 30 per kind until the search narrows it. The Add button is off at 3 sets and the remove "×" is off at 2. The picks survive a tab switch and an as-of change (a pick that no longer exists falls back to the default pair).
14. **Venn sizes and colors.** The largest circle always has radius 100 in its own box; the minimum radius is that ÷ sqrt(50) (so area ratio 50). Disjoint sets draw with a 14-unit gap. Region counts sit at the mean of the region's sampled area (a 60 × 60 grid). Tints are `--txt-4`, `--txt-3`, `--txt-2` at 28% fill.
15. **Region click.** Click the count (or the region itself) to list it. A region's list names its companies by ticker and name, with one chip per card (card name · links) that opens that card's Cockpit on the company's ticker there.
16. **Sectors defaults.** Supersectors; the toggle to business types clears an open cell. A cell's bubble radius is area-proportional to its companies, with the grid's biggest cell at 13 px. A card with no companies in view shows an empty row, last. A cell click lists tickers (each opens the Cockpit on that card).
17. **Selected card / cell** are page state that resets on a tab switch (the Venn picks and the threshold do not).
18. **Two limits lines.** The quiet line holds the counts the plan lists; a second quiet line holds the known limits (matching by security, our own layout, approximate three-set areas, structure only).

**Process**
19. **The Index** in `docs/DASHBOARDS.md` is a paragraph pointing at the `/dashboards` page, with no list of rows, so the "row" is one bullet appended at its end.
20. **Menu order 300** leaves 290 free for the ETFs page, which this branch does not contain. `registry.test.ts` pins `overlap-map` after `daily-digest` (280).
21. **No capture adapter or markers**: the MCP catalog entry advertises every capability false (discoverable, not observable), like Shared Names, with `asofAware: true`.
