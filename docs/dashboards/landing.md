# Price & Volume Landing

> Slug `landing` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> signed off this plan before the build. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Price & Volume Landing. **Description:** "Where price closed in each window's range, and whether
volume backed the move, by group."

It answers the operator's original ask, "where is price/volume landing over the past day/week/month/quarter
for a value chain, sector, etc.": WHERE in each window's range price closed, and WHETHER volume backed the
move. Display only: it never feeds a call, never writes to the database and never calls the LLM.

## What it shows

### Rows = groups

Exactly the Rotation Map's groupings, through the shared basket read (`backend/dashboards/baskets.py`):

- **Link** (default): every link of every live card, shown as "<link> · <card>".
- **Business type**: the business-type leaf.
- **Sector**: the business supersector.
- **Card**: one row per card.

### Windows

**1D · 7D · 30D · 90D**, in TRADING bars (the Cockpit's ladder): a window of N is the company's last N bars,
the as-of's own bar first. The PRIOR window is the N bars before those (bars N+1..2N back).

### Per name, per window (off its own price bars)

- **Range position** = (close − window low) ÷ (window high − window low), the window's highest high and lowest
  low. 0% = closed at the low, 100% = at the high. 1D = the as-of bar's own range. A zero-width range, a bar
  without a high or a low, or a tape shorter than the window reads "—".
- **Volume ratio** = the window's total volume ÷ the prior window's total. **Rising** = ratio > 1.2; anything
  else is "not rising". A tape shorter than 2N bars, a bar without volume in either window, or a zero prior
  total reads "—".
- **Quadrant** = the window return's sign × volume rising or not:
  - up + rising = **accumulation**
  - up + not = **thin rally**
  - down + rising = **distribution**
  - down + not = **rest**
  - an exactly-zero return (an unchanged close) = **flat**
  - missing pieces = "—"
- **Up/down-day volume** = the volume on the window's up-close days ÷ the volume on its down-close days, each
  bar against the close before it ("—" when there are no down days).

### Per group, per window

- The **median range position**, drawn as a small 0–100 bar with its n. Fewer than 3 names with a position
  shows "—" (the Rotation Map's rule).
- The **quadrant split** as counts ("3 accum · 1 thin · 1 rest"), never one verdict label. The hover lists
  every quadrant and the names without a reading.
- The **median up/down-day volume** with its n (same thin rule).

### Sorting

- By any window's median range position, or by that window's accumulation count. Default: the 7D median
  position, highest first. The active sort flips on a second click.
- Groups with "—" go last in both directions; ties fall back to the row label.
- Neutral wording only ("7D position", "accum"); no verdict words anywhere.

### Expanding a group

Clicking a row shows its names, each with every window's range position (a thin bar and the %), its quadrant
and its volume ratio (the return and the up/down-day volume in the hover). A name's card opens that card's
Cockpit on the name (`onSelectThesis(thesisId, ticker)`); a name on several cards in a group shows one card
per chip (with its links outside the Link view). A name not priced says so quietly ("no tape", "stale tape ·
last bar …").

### The quiet "show the work" line

Cards counted (and how many were created after the as-of), companies, groups, priced names (with how many
have no bar or a stale one), the Context/Bench toggle with the rows it left out, basket rows without a
resolved security, the rules ("volume vs the prior window of the same length; >1.2× = rising", "windows in
trading bars"), the cache state and the as-of. A legend line words the position, the quadrants and up/down.

## The plan's choices (rule 4)

- **Loudness:** gray by default. The range bars are thin gray fills; of the quadrant words only accumulation
  and distribution carry a quiet tone (the Cockpit's `--pos` / `--neg`, muted toward gray); thin rally, rest
  and flat stay gray. Nothing notifies.
- **Which names count:** Context and Bench memberships (`domain.thesis.is_silent`) are EXCLUDED by default;
  "Show Context and Bench" adds them, the Rotation Map's toggle.
- **How groups combine:** in the Link view a company in two links of a card counts in both; in the Business
  type, Sector and Card views each company counts ONCE per group (by `security_id`). "Unsegmented" /
  "Unclassified" as on the Rotation Map.
- **Thin data:** a name with too little tape for a window reads "—" there and is left out of that window's
  medians (shown in their n) and counted as "without a reading" in its split. A group under 3 names with a
  reading shows "—" for the median. A basket row with no resolved security is counted on the quiet line,
  never dropped.
- **Cache:** yes, the shared dashboard cache (rule 7), keyed `(clock,)` per as-of.

## Past as-of

- `known_at, clock = domain.market_time.serve_view(asof)`, threaded into every price read (rule 2): a past
  as-of reads what was public by the end of that date (a corrected bar from its own publication).
- Each card's roster as of that date (`dashboards.baskets.cards_as_of`). Rosters are point-in-time only from
  2026-09-15: before that, today's roster stands in and the page says so ("Today's roster stands in for N
  cards").
- Cards created after the as-of and archived cards are left out, as on the Rotation Map.

## How it computes

- One read per as-of, cached (`dashboards.cache.cached(conn, "landing", (clock,), asof, …)`): the live cards
  and their rosters, then each resolved company's price bars ONCE (`pit.price_history(sid, lookback_days=300)`,
  300 calendar days ≥ the 180 trading bars the 90D volume ratio needs), through the shared chunked
  point-in-time read (`dashboards.baskets.per_company`, the Cockpit's `/display-signals` view). The cache
  holds the per-company per-window readings; every grouping and toggle is derived per request.
- Business type and sector come from `securities.master.identity_for` per request (one query).
- The pure compute (`backend/dashboards/landing.py`) does the readings, medians, splits, dedupe and the
  default sort; the page re-sorts.

## Where it lives

- Compute (pure): `backend/dashboards/landing.py`, `company_landing(bars, asof)` and
  `landing(inputs, identity, ...)`.
- Router: `backend/app/routers/dashboards/landing.py`,
  `GET /dashboards/landing?asof=&group_by=link|business_type|sector|card&include_silent=`, its wire models in
  the same module.
- Tests: `backend/tests/dashboards/test_landing.py`; `frontend/src/dashboards/landing/__tests__/`.
- Frontend: `frontend/src/dashboards/landing/` (`Landing.tsx`, `useLanding.ts`, `landingView.ts`,
  `landing.css`).

## Known limits

- **Business type and sector come from today's master**, at any as-of.
- **1D volume is compared to the single prior bar**, which is noisy by nature.
- **1D up/down-day volume is degenerate by construction**: one bar is either an up day (no down days,
  so "—") or a down day (no up-day volume, so 0.00×). The group median reads 0.00× when at least 3
  names closed down that day, "—" otherwise. Kept as the plan specified it, for one consistent column.
- **Data flags are not applied** (the Rotation Map's limit): a value a data flag reaches counts in its group.
- **The cache refreshes on a new run, a roster edit, or a card created, archived or renamed** (rule 7).
- **Load time is unmeasured until review.**

## Later versions (out of scope now)

A fixed 60-day volume base as a toggle, and liquidity or market-cap filters.
