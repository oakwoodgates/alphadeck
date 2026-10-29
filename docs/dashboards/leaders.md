# Leaders

> Slug `leaders` · status `experimental` · follows the **as-of dial** (`asofAware: true`). The operator
> signed off this plan before the build, including folding "insider clusters by group" into this board. The
> choices the build made that the signed-off brief did not spell out are listed under "Choices to confirm" for
> the operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`.

**Label:** Leaders. **Description:** "Names across every card at new highs, leading the market, coiling, or
with insider buying, and the groups where insiders cluster."

The Cockpit already computes, per company and per card, the readings this page reads: the 52-week range, RS
vs the market at a 13-week high, the VCP "coiling" read, open-market insider buys, RVOL, trailing returns and
the SMA posture. The Leaders board assembles them across every card into one name-level "look at this now"
list. Display only: it never feeds a call, never writes to the database and never calls the LLM.

## Who's included

- The companies on the counted cards, through the shared read in `backend/dashboards/baskets.py` (the Rotation
  Map's and the Cross Radar's): the live cards that existed by the as-of, each with its roster then.
- Context and Bench memberships are left out by default, behind the same "Show Context and Bench" toggle.
- **One row per company** (deduped by `security_id`), with a chip for every card it sits on and its links on
  that card. A basket row with no resolved security is counted on the quiet line, never listed.

## The lists (a name can be on several)

Every list reads a display member's own output (`signals/display/`), run once per company through the same
point-in-time view the Cockpit's `/display-signals` builds. The thresholds are module constants in
`backend/dashboards/leaders.py`.

| List | Member | On the list when | Its own value |
| --- | --- | --- | --- |
| **Near 52-week high** | `range_52w` | `pct_off_52w_high` ≥ −2.0% (within 2% of the high) | the % off and the high's print date (from the `high_52w` metric's note) |
| **RS leader** | `relative_strength` | the headline key `leading` (RS at a fresh 13-week high) | which benchmark(s) the RS is at a 13-week high against |
| **Coiling** | `vcp` | the headline key `coiling` | the contraction ratio (the newest base segment's depth ÷ the oldest's) |
| **Insider buying** | `insider_flow_90d` | `buy_count_30d` ≥ 1 | buys/buyers over 30 days ("3/2"); a leader-blue **cluster** accent when `distinct_buyers_30d` ≥ 2, matching the Cockpit's Ins 30d column |
| **Volume** | `rvol` | `rvol20` ≥ 1.5 (the member's own `LOUD_MULT_20`, the Cockpit's "loud" threshold) | RVOL 20D |

The columns every list shows beside its own value: ticker, company, cards, 7D / 30D return
(`trailing_returns`) and above-50d (`sma_position`'s `pct_vs_fast` > 0), RVOL 20D.

## Sections, top to bottom

1. **On several lists.** Names on 2 or more of the lists, with the lists as quiet chips. The top section.
   Default order: the number of lists, highest first, then the 30D return.
2. **One tab per list** (a segmented control, each tab with its count). Default orders: Near 52-week high by
   % off the high (closest first); RS leader and Volume by the 30D return and RVOL 20D, highest first; Coiling
   by the contraction ratio (tightest first); Insider buying by buys, then buyers.
3. **Insider clusters by group.** Groups (Link · Card · Business type · Sector, the Rotation Map's grouping
   selector; Link by default) where **at least 2 names** had open-market buys in the last 30 days. Each row
   shows its names with buys/buyers, the group's buys, and the share of its names with buys ("2 of 5"), plus
   how many of its names have no insider data ingested. In the Link view a company in two links of a card
   counts in both; elsewhere each company counts once per group. Default order: names with buys, then the
   share, then the group's label.

## Sorting and wording

- Every list is sortable by its headers; the active column flips on a second click; a name without a value
  sorts last both ways.
- Headers stay neutral: "% off 52w high", "30D return", "RVOL 20D", "Contraction", "Buys/buyers 30d". Never a
  verdict word such as "best" or "buy".

## Honest gaps

A name whose member returns nothing for a list is not on that list, and the quiet line counts how many
companies had **no reading** per member, so a missing name is a visible count, never silently gone:

- `range_52w`: no closes at the as-of.
- `relative_strength`: no benchmark tape overlaps the name, or fewer than 60 aligned bars on both SPY and IWM
  (no 13-week verdict yet; the member's own `MIN_BARS`).
- `vcp`: fewer than 60 bars, too little tape to read a base.
- `insider_flow_90d`: no Form 4 ingested for the name (zero ingested is not proof of zero filings; the member's
  caveat).
- `rvol`: no reading for the 20-bar window (fewer than 20 base bars, no volume on the as-of bar, or a zero base).

**A flagged price history** (`docs/DASHBOARDS.md` rule 5): a company whose data flags reach its price readings
at the as-of (`dashboards.baskets.price_flags`) is on **no list**, the insider list included (every list row
shows its price columns), counted in no member's no-reading tally, and out of every insider group's names,
buyers and share (the group shows "N left out"). The page shows a quiet "N names left out for a flagged price
history" that opens to each ticker and reason, so it is never silently gone.

## Clicks

A ticker opens its first card's Cockpit on it; each card chip opens that card's Cockpit on the name:
`onSelectThesis(thesisId, ticker)`.

## The quiet "show the work" line

Cards counted (and how many were created after the as-of), companies, the Context/Bench toggle with the rows it
left out, basket rows without a resolved security, the thresholds (within 2% of the 52w high, RVOL 20D ≥ 1.5,
insider cluster = 2+ buyers in 30d, a group = 2+ names with buys), each member's no-reading count, the cache
state and the as-of.

## Past as-of

- `known_at, clock = domain.market_time.serve_view(asof)`, called once per request and threaded into every
  read (rule 2): a past as-of reads only what was public by the end of that date.
- Each card's roster as of that date (`dashboards.baskets.cards_as_of`). Rosters are point-in-time only from
  2026-09-15: a card with no roster snapshot by then keeps today's roster, and the page says so.
- Cards created after the as-of and archived cards are left out.

## How it computes

- **The heavy read, cached once per as-of** (`dashboards.cache.cached(conn, "leaders", (clock,), asof, ...)`):
  the counted cards and their rosters (`dashboards.baskets.cards_as_of`), then per resolved company, ONCE,
  through the chunked point-in-time read (`dashboards.baskets.per_company`): the seven display members
  `range_52w`, `relative_strength`, `vcp`, `insider_flow_90d`, `rvol`, `trailing_returns` and `sma_position`,
  each reading reduced to the few numbers this page shows (`dashboards.leaders.company_reading`).
- **Per request**, pure (`dashboards.leaders.leaders`): the silent toggle, the lists and their default orders,
  the several-lists section, the no-reading counts and the insider clusters by the chosen grouping. Company
  names, business type and sector come from `securities.master.identity_for` per request (today's master).

## Where it lives

- Compute (pure): `backend/dashboards/leaders.py`.
- Router: `backend/app/routers/dashboards/leaders.py`,
  `GET /dashboards/leaders?asof=&include_silent=&group_by=link|business_type|sector|card`, its wire models in
  the same module.
- Tests: `backend/tests/dashboards/test_leaders.py`; `frontend/src/dashboards/leaders/__tests__/`.
- Frontend: `frontend/src/dashboards/leaders/` (`Leaders.tsx`, `useLeaders.ts`, `leadersView.ts`,
  `leaders.css`).

## Choices to confirm

The signed-off brief did not specify these; the build made the call below, each one a constant or a line to
flip:

- **RS leader reads the member's headline `leading`, which fires on SPY OR IWM.** The brief's gloss said "vs
  SPY"; the headline key is what the Cockpit's Tape chip, the Rotation Map and the Cross Radar read, so the
  page follows it and shows which benchmark(s) per name ("SPY", "IWM", "SPY · IWM").
- **A thin RS counts as no reading**, not as "not leading": fewer than 60 aligned bars on both benchmarks
  means the member has no 13-week verdict yet.
- **A sub-year 52-week range still counts** (a name with under 245 bars reads its high over the bars it has,
  as the Cockpit's Off 52w high column does), and its row carries a quiet "N bars" tag.
- **The coiling value is the contraction ratio** (the vcp member's `contraction` metric), shown as a percent of
  the base start ("62%").
- **An insider-cluster group's share counts every company in the group**, the ones with no insider data
  included (they are counted beside it), so "2 of 5" is out of all five names.
- **The cluster accent marks a NAME (2+ buyers)**; a group row itself carries no accent, the section being the
  exception list already.
- **No RVOL warm accent**: on the Volume tab every row clears the threshold, so an accent there would mark the
  rule, not the exception (WB #3).
- **The group-by selector sits in the insider-cluster section**, the only section it changes.

## Known limits

- These readings are **end-of-day and display-only**; none feeds a call.
- The insider read is the **Cockpit's open-market screen**, so its documented caveats apply
  (`docs/DISPLAY_SIGNALS.md`): zero ingested is not proof of zero filings; an issuer self-filing still counts as
  a buy in this net-flow read (the deferred re-base); an offer-price subscription is set aside only when a day
  low is on file.
- **Business type, sector and company names come from today's master**, at any as-of.
- **The price flags are read inside the cached read** (as on the Narrative Index), so an operator's split
  review in Admin (which clears a flag) reaches this page at the next run or roster edit; the flags are
  read once per as-of, so a flag on any price cell leaves the name out of every window.
- **The cache refreshes on a new run, a roster edit, or a card created, archived or renamed** (the shared
  cache's rule).
- **Load time is unmeasured until review.** It runs seven members per company (the Rotation Map runs four),
  all off the same memoized point-in-time view.

## Out of scope

- **What happened after a name joined a list.** That is an event-outcome report, so under `CLAUDE.md`'s rule it
  needs the market-cap tiers measured at the event, the horizon, the comparison, n and the hit rate per tier
  (`docs/BACKTEST.md` §"Sliced by market cap").
- **Alerts.** The page never notifies.
