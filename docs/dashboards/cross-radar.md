# Cross Radar

> Slug `cross-radar` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> signed off this plan before the build; the choices the build made that the signed-off brief did not spell
> out are listed under "Choices to confirm" for the operator's review. The rules every dashboard follows:
> `docs/DASHBOARDS.md`.

**Label:** Cross Radar. **Description:** "Golden and death crosses by group: clusters, first movers, names
closing in, and the last 90 sessions."

It answers the operator's ask about golden and death crosses, especially when several names in a link,
sector or card move the same way: which groups have names crossing together, which have one name out ahead of
the rest ("the strongest horse warming up the basket"), which names are close to a cross, and the crosses of
the last 90 sessions per group. Display only: it never feeds a call, never writes to the database and never
calls the LLM.

## Groupings and counting

Exactly the Rotation Map's, through the shared read in `backend/dashboards/baskets.py`:

- **Link** (default), **Business type**, **Sector**, **Card**.
- Context and Bench memberships are left out by default, behind the same "Show Context and Bench" toggle
  (their own groups in the Link view, their companies included in the other views).
- In the Link view a company in two links of a card counts in both; the other views count each company once
  per group (by `security_id`). A row with no link groups as "Unsegmented"; a company with no business type /
  sector as "Unclassified". A basket row with no resolved security is counted on the quiet line, never
  grouped.
- **A flagged price history is left out of everything** (`docs/DASHBOARDS.md` rule 5): a company whose data
  flags reach its price readings at the as-of (`dashboards.baskets.price_flags`) is never priced and in no
  cluster, first mover, near-cross list, timeline count or posture bar, because a scale break makes fake
  crosses; its bars never shape the timeline's axis. It counts in its group's names ("N left out" beside "not
  priced", never inside it); expanded, a timeline row lists it grayed as "left out: <reason>" with no strip.
  The page shows a quiet "N names left out for a flagged price history" that opens to each ticker and reason.

## Definitions

- **A cross** is the 50-day SMA crossing the 200-day SMA: **golden** = the 50d moves above the 200d, **death**
  = it moves below. The SMAs are the Cockpit's own arithmetic: the radar imports `signals/display/sma.py`'s
  `_sma_series` (deliberately the same math; `sma.py` is not modified). The walk over the `SMA50 − SMA200`
  series is `_last_flip`'s: exact zeros are skipped (a touch is not a cross) and a cross is stamped on the
  first bar on the far side. Where the member keeps only each name's most recent cross, the radar keeps EVERY
  cross inside the last 90 sessions (`cross_history`).
- **Sessions** are trading bars from each company's own price history up to the as-of
  (`PointInTimeData.price_history` with `sma.LOOKBACK_DAYS`, 600 calendar days ≈ 410 bars). "N sessions ago"
  counts bars back from its last bar (0 = the as-of's own bar, shown "on the as-of session"); "within 10
  sessions" is its last 10 bars (bar 10 in, bar 11 out).
- **Priced** = enough bars for a 200-day SMA at its last bar, and that bar no more than 10 calendar days before
  the as-of (`sma.STALE_GAP_DAYS`, the member's own stale-tape tell). Every other name is counted as **not
  priced** (too few bars / stale tape), on the quiet line and in each group, never dropped and never read as
  "no cross". A priced name with fewer than 290 bars reads a shorter cross history, and its line says so.
- **A name's recent cross** = its LATEST cross within the last 10 sessions. A name that crossed up and then
  down inside the window counts once, as down.
- **RS leader** = the `relative_strength` display member's headline key `leading` (RS at a 13-week high),
  run through the same point-in-time view, as the Rotation Map runs it.
- **Posture** = the `sma_position` member's headline key: `above_rising`, `above_falling`, `below_rising`,
  `below_falling`. Every other key (`partial_*`, `level_*`, a flat or unreadable slope) and every name not
  priced counts as "other".

## Sections, top to bottom, in order of loudness

1. **Clusters (loud; exceptions only).** A group clusters when, within the last 10 sessions, **at least 2
   names AND at least 40% of its priced names** have the same recent cross (2/2 ✓ · 2/4 ✓ · 3/7 ✓ · 2/5 ✓ ·
   1/2 ✗ · 2/10 ✗). Each row shows its pieces, with no weighted score: "3 of 7 golden · within 6 sessions ·
   2 are RS leaders", plus the names and their dates. Golden and death clusters each carry their own accent
   (the Cockpit's `--pos` / `--neg`, a left rule and the direction text). No group clustering = one quiet line.
2. **First movers (quiet accent).** A group of at least 2 priced names where EXACTLY ONE has a recent golden
   cross. The leader: ticker, cross date (sessions ago), RS leader or not. The rest of the priced names in
   three counts: **closing in** (the near-cross test below, approaching golden), **already above** (50d over
   200d with no cross within 10 sessions) and **not yet** (everything else, a recent death cross included).
   For example "BWXT golden 4 sessions ago · 2 of 5 closing in · 1 already above · 2 not yet". The mirror,
   **first to roll over**, for exactly one recent death cross (closing in on death · already below · not yet).
3. **Near-cross watch (quiet list).** A priced name where `|SMA50/SMA200 − 1| ≤ 2%` AND that gap is smaller
   than 5 sessions ago, with no cross within the last 10 sessions. Approaching golden when the 50d is under
   the 200d, approaching death when it is over. Grouped by the current grouping; each name shows the gap % and
   its 5-session change in points. It states the tape only, never a "crosses in N days" forecast.
4. **Cross timeline.** One row per group: the x axis is the last 90 sessions (index-spaced, newest at the
   right), a small green up-tick per golden cross and a red down-tick per death cross. The row label carries
   the group and its golden / death counts over the 90 sessions (every cross, not names). Expanding a row lists
   each name with its cross dates (or "no cross in 90 sessions", or why it is not priced); a name opens its
   card's Cockpit on it (`onSelectThesis(thesisId, ticker)`). The **Price × 200d ticks** toggle (off by
   default) adds price-crosses-the-200d events as a lighter second layer of ticks, on the timeline only: they
   never count toward a cluster, a first mover or the near-cross watch.
5. **Posture bar per group.** A thin stacked bar of the four posture shares, "other" counted, beside each
   timeline row; its hover lists the counts.

## Sorting

- Clusters: by share, then count. First movers: by cross date, newest first. Near-cross: by gap, smallest
  first (the names inside each group by the same column). Timeline: by total crosses in the 90 sessions, then
  name.
- Every list is sortable by its headers; the active column flips on a second click.
- Wording stays neutral: what the tape shows ("3 of 7 golden", "approaching golden"), never a verdict.

## The quiet "show the work" line

Cards counted (and how many were created after the as-of), companies, priced vs not priced (too few bars /
stale tape), groups, the Context/Bench toggle with the rows it left out, basket rows without a resolved
security, the price × 200d toggle, the rules (cluster ≥ 2 names and ≥ 40% within 10 sessions; near-cross band
±2% narrowing over 5 sessions), the cache state and the as-of. A legend line says what a cross, a session and
"priced" mean.

## Past as-of

- `known_at, clock = domain.market_time.serve_view(asof)`, called once per request and threaded into every
  read (rule 2): a past as-of reads only what was public by the end of that date.
- Each card's roster as of that date (`thesis_repo.get_asof`), via `dashboards.baskets.cards_as_of`. Rosters
  are point-in-time only from 2026-09-15: a card with no roster snapshot by then keeps today's roster, and the
  page says so ("Today's roster stands in for N cards"), the Rotation Map's quiet note.
- Cards created after the as-of and archived cards are left out, as on the Rotation Map.

## How it computes

- **The heavy read, cached once per as-of** (`dashboards.cache.cached(conn, "cross-radar", (clock,), asof,
  ...)`): the counted cards and their rosters (`dashboards.baskets.cards_as_of`), then per resolved company,
  ONCE, through the chunked point-in-time read (`dashboards.baskets.per_company`: 100 companies per
  `PointInTimeData`, `bounds=display_bounds()`, the `serve_view` pair): its price history, the `sma_position`
  and `relative_strength` members, and the cross walk (`dashboards.cross_radar.company_crosses`). The cached
  value holds each company's cross history (both layers), gap now and 5 sessions back, posture and RS reading,
  plus the timeline's sessions (SPY's, plus any session a priced company printed).
- **Per request**, pure (`dashboards.cross_radar.cross_radar`): the grouping, the silent toggle, the cluster
  and first-mover rules, the near-cross watch, the timeline rows and the price × 200d layer, all derived from
  the cached read. Business type and sector come from `securities.master.identity_for` per request.

## Where it lives

- Compute (pure): `backend/dashboards/cross_radar.py`.
- The shared basket read (with the Rotation Map): `backend/dashboards/baskets.py`; the shared cache:
  `backend/dashboards/cache.py`.
- Router: `backend/app/routers/dashboards/cross_radar.py`,
  `GET /dashboards/cross-radar?asof=&group_by=link|business_type|sector|card&include_silent=&price_200d=`, its
  wire models in the same module.
- Tests: `backend/tests/dashboards/test_cross_radar.py` (+ `test_baskets.py` for the shared read);
  `frontend/src/dashboards/cross-radar/__tests__/`.
- Frontend: `frontend/src/dashboards/cross-radar/` (`CrossRadar.tsx`, `useCrossRadar.ts`, `crossView.ts`,
  `cross-radar.css`). The timeline is inline SVG (no chart library).

## Choices to confirm

The signed-off brief did not specify these; the build made the call below, each one a constant or a line to
flip:

- **Stale tape is not priced.** A name whose last bar is more than 10 calendar days before the as-of (a halt, a
  delisting, a feed gap) is counted as not priced, so its old crosses can't read as "within 10 sessions".
- **A name counts by its latest cross in the window**, so one name never sits in a golden and a death count at
  once.
- **A first mover needs at least 2 priced names in its group** (`FIRST_MOVER_MIN_PRICED`): a one-name link
  has no one to lead. Its cross still shows on the timeline.
- **One group can show twice**: as a golden and a death cluster (2 + 2 of 4), or as a cluster one way and a
  first mover the other.
- **"Already above / below" excludes a name with any cross in the window**; a name that crossed the other way
  counts as "not yet".
- **The near-cross gap is rounded** to 4 decimals of a percent before the band test (so a gap of exactly 2.0%
  is in), and a gap exactly on the line takes the side it came from.
- **The timeline axis** is SPY's last 90 sessions plus any session a priced company printed; a cross older
  than the axis's first session (only a name with gaps in its tape) pins to the left edge. Every group gets a
  row, zero-cross groups last.
- **The price × 200d layer also lists its dates** in an expanded row's names, beside the ticks.

## Known limits

- **SMA crosses lag by construction**: a 50 / 200-day cross confirms a move after it has happened.
- **Business type and sector come from today's master**, at any as-of.
- **No weighting** by market cap, liquidity or recency in v1: a cluster is a count and a share. A weight would
  be added later, only with its formula on the page.
- **The price flags are read inside the cached read** (as on the Narrative Index), so an operator's split
  review in Admin (which clears a flag) reaches this page at the next run or roster edit; the flags are
  read once per as-of, so a flag on any price cell leaves the name out of every window.
- **The cache refreshes on a new run, a roster edit, or a card created, archived or renamed** (the shared
  cache's rule). Prices a Workbench pull or an onboarding batch writes between runs show after the next run or
  roster edit.
- **Load time is unmeasured until review.** It reads one more price history per company than the members
  already read (the same point-in-time view, memoized) and two members instead of the Rotation Map's four.

## Later versions (out of scope now)

- **"What happened after a cross cluster."** That is an event-outcome report, so under `CLAUDE.md`'s rule it
  needs the market-cap tiers measured at the event, the horizon, the comparison, n and the hit rate per tier
  (`docs/BACKTEST.md` §"Sliced by market cap").
- **Feeding new clusters into What Changed.**
