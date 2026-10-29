# Rotation Map

> Slug `rotation-map` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> signed off this plan before the build. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Rotation Map. **Description:** "Which links, business types and sectors are moving: returns,
breadth, volume and crosses by group."

It answers the operator's main cross-card question: which value-chain links, business types and sectors
have price moving up, and where golden / death crosses are printing. Display only: it never feeds a call,
never writes to the database and never calls the LLM.

## What it shows

### Rows = groups

A selector picks the grouping:

- **Link** (default): every link of every live card, shown as "<link> · <card>".
- **Business type**: the business-type leaf.
- **Sector**: the business supersector.
- **Card**: one row per card.

Clicking a row expands its names, each with: ticker · its card (and its link, in the Business type, Sector
and Card views) · 1d / 7d / 30d / 90d / 1Y returns · above the 50d yes/no · RVOL 20D · a ↑ / ↓ mark for a
golden / death cross inside the window. A name's card opens that card's Cockpit on the name
(`onSelectThesis(thesisId, ticker)`); a name on several cards in a group shows one card per chip.

### Columns

- **1d · 7d · 30d · 90d · 1Y median**: the MEDIAN trailing return of the group's priced names, with a quiet
  "x/n up" under each (x = names with a positive return in that window, n = names priced for it). The
  Cockpit's own windows and labels: the `trailing_returns` member's `ret_1d`, `ret_7d`, `ret_30d`, `ret_90d`,
  `ret_1y`, in trading bars.
- **Names**: the companies in the group (priced or not).
- **Above 50d**: names whose close is above their 50-day SMA (`sma_position`'s `pct_vs_fast` > 0), "x/n".
- **RVOL 20D median**: the median of `rvol`'s `rvol20`.
- **Golden 30d / Death 30d**: names whose `sma_position` `golden_cross` / `death_cross` event is dated within
  30 calendar days on or before the as-of (the Cockpit's `TAPE_CROSS_DAYS`,
  `frontend/src/cockpit/tapeMarks.ts`). The member emits only each name's most recent cross.
- **RS leaders**: names whose `relative_strength` headline key is `leading` (RS at a 13-week high), "x/n"
  (n = names with an RS reading).

### Raw / vs SPY

"vs SPY" subtracts SPY's own trailing return for the same window from each return (the group medians and the
names' returns). SPY's returns come from the `trailing_returns` member's pure `compute(bars, asof)` over
`pit.benchmark_prices("SPY")`, read with the member's own lookback. Under vs SPY the "x/n up" line reads
"x/n > SPY" (names beating SPY's return). With no SPY tape the toggle is disabled with a quiet reason. The
shift is one number per column, so it never changes an order or a color.

### Sorting

- Default: the 7d median, highest first. Every column is sortable; the active column flips on a second click.
- Groups with "—" sort last in both directions; ties fall back to the row label.
- A share (Above 50d) over fewer than 3 names is no group read, so it sorts with the "—"s (shown as x/n).
- Headers say plainly what they sort ("7d median"). No verdict words anywhere.

### Color only the unusual

A return median is green or red ONLY when it sits in the top or bottom 10% of the groups shown in that
column (cross-sectional, in the current view): `len // 10` groups on each side, ties at the cut included,
so a view of fewer than 10 groups colors nothing. All other cells, every name's cells and every "—" stay
gray. A quiet legend line says so.

### Thin groups

A group with fewer than 3 priced names for a window shows "—" for that window's median (its "x/n up" still
shows n) and is never colored: the Cockpit's link-header rule (`groupAggregate.ts`'s `AGG_MIN_PRICED`). The
RVOL median follows the same rule.

### The quiet "show the work" line

Live cards counted (and how many were created after the as-of), companies, groups, priced companies, the
Context/Bench toggle with the rows it left out, basket rows without a resolved security, raw or vs SPY, the
cache state ("cached" / "computed in 41 s — first load since the last run or basket edit") and the as-of.

## The plan's choices (rule 4)

- **Loudness:** gray by default; only the top / bottom 10% return medians color, in the Cockpit's own
  `--pos` / `--neg`. Nothing notifies.
- **Which names count:** Context and Bench memberships (`domain.thesis.is_silent`) are EXCLUDED by default.
  "Show Context and Bench" adds them: their own rows ("Context · <card>", "Bench · <card>") in the Link
  view, their companies included in the other views.
- **How groups combine:** in the Link view a company in two links of a card counts in both; in the Business
  type, Sector and Card views each company counts ONCE per group (by `security_id`). A row with no link groups
  as "Unsegmented"; a company with no business type / sector as "Unclassified".
- **Thin data:** a name with too little tape for a window is left out of that window's median and shows in
  its n; a group under 3 priced names reads "—". A basket row with no resolved security is counted on the
  quiet line, never dropped.
- **Cache:** yes, the shared dashboard cache (rule 7), built with this dashboard.

## Past as-of

- `known_at, clock = domain.market_time.serve_view(asof)`, threaded into every price read (rule 2): a past
  as-of reads what was public by the end of that date.
- Each card's roster as of that date comes from `thesis_repo.get_asof(conn, thesis_id, known_at)`. Rosters
  are point-in-time only from 2026-09-15: a card with no roster snapshot by then keeps today's roster, and the
  page says so ("Today's roster stands in for N cards").
- Cards created after the as-of are left out (`thesis_repo.created_at_for`, judged by
  `pipeline.backfill.thesis_existed_on`); archived cards are left out (`thesis_repo.list_all`).

## How it computes

- One read per as-of, cached (keyed by the as-of and its read's clock): the live cards and their rosters,
  then the four display members (`trailing_returns`, `sma_position`, `rvol`, `relative_strength`, picked by
  name from `registered_display_members()`) run ONCE per resolved company, through the same
  `PointInTimeData` the Cockpit's `/display-signals` builds (`bounds=display_bounds()`, the `serve_view`
  pair). Companies are read in chunks of 100 per point-in-time view so the whole universe's price window is
  never held in memory at once; the prefetch scope only batches the reads, so the values are the same as one
  view.
- That read includes the silent memberships' companies, so one cold compute serves all four groupings and
  both Context/Bench states. Business type and sector come from `securities.master.identity_for` per
  request (one query).
- The pure grouping (`backend/dashboards/rotation_map.py`) does the medians, x/n, tones, dedupe and the
  default sort; the page re-sorts by column.

## Where it lives

- Compute (pure): `backend/dashboards/rotation_map.py`, `rotation_map(inputs, identity, ...)`.
- The shared cache: `backend/dashboards/cache.py`.
- Router: `backend/app/routers/dashboards/rotation_map.py`,
  `GET /dashboards/rotation-map?asof=&group_by=link|business_type|sector|card&include_silent=`, its wire
  models in the same module.
- Tests: `backend/tests/dashboards/test_rotation_map.py`, `backend/tests/dashboards/test_cache.py`;
  `frontend/src/dashboards/rotation-map/__tests__/`.
- Frontend: `frontend/src/dashboards/rotation-map/` (`RotationMap.tsx`, `useRotationMap.ts`,
  `rotationView.ts`, `rotation-map.css`).

## Known limits

- **Coloring is cross-sectional**, against the other groups in view, not against each group's own history.
- **Business type and sector come from today's master**, at any as-of.
- **Data flags are not applied.** The Cockpit's link header leaves out a 7d value a data flag (split plan
  S3) reaches; this page does not read the data flags yet, so a flagged value counts in its group's median.
- **The cache refreshes on a new run, a roster edit, or a card created, archived or renamed.** Prices a
  Workbench pull or an onboarding batch writes between runs show after the next run or roster edit. (A
  business-type re-tag shows at once: identity is read per request, outside the cache.)
- **Load time is unmeasured until review.** The heavy read was measured on prod at 1.4–3.7 s per card and
  estimated at 30–75 s cold for all ~1,500 companies; a cached view is one lookup plus the grouping.

## Later versions (out of scope now)

Range position within each window, the price × volume quadrant, the rotation quadrant with tails, a
market-cap filter, and the "theme or one stock" spread read.
