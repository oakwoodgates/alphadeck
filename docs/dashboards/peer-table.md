# Peer Table

> Slug `peer-table` · status `experimental` · follows the **as-of dial** (`asofAware: true`) · menu `order: 150`.
> The operator approved this dashboard and the choices below (2026-10-29; cross-card by business type is the default
> view, operator choice 5 of the research set); the choices the build makes that this plan does not spell out go under
> "Choices to confirm" for the operator's review. The rules every dashboard follows: `docs/DASHBOARDS.md`. The fourth
> page of the RESEARCH set.

**Label:** Peer Table. **Description:** "The names across your cards side by side: size, revenue, runway, dilution,
purity, tape and insider buying, grouped by business type, link or card, with every blank counted."

It answers "how do these names compare, and where is my data thin": one wide sortable table of companies across the
cards counted at the as-of, each company ONCE, grouped by what it does. It is the cross-card view the Cockpit cannot
give (the Cockpit is one card). Coverage is part of the answer: the Workbench facts (shares, cash and burn, revenue
mix, convertible notes) exist only where the operator marked a name for data or the automated legs reached it, so the
blanks are the operator's "mark for data" to-do list, counted per column, never hidden. Display only: it never feeds a
call, never writes, never calls the LLM; every number is a figure the Workbench or a display member already computes.

## Scope and grouping

The table reads ONE scope at a time; the scope is the page's first control and the cache key:

- **Business type** (the default): one `BusinessType` leaf, across every card counted at the as-of; the picker lists
  the leaves present on the live rosters with their company counts, the first with 2+ companies by default.
- **Supersector:** one `BusinessSupersector`, grouped by leaf inside.
- **Card:** one card (the shared picker, `baskets.picker_cards`), grouped by link in chain order (old links, No link,
  then Context and Bench).
- **All live cards:** everything, grouped by supersector then leaf. The heaviest read; the page says "the first load
  after a run can take a minute" and shows `compute_ms`.

**Which names count:** every resolved roster row of the counted cards (`baskets.cards_as_of` → `memberships_of`),
Context and Bench included, each company once by `security_id` (its cards and links folded into chips; its role as a
quiet tag; "silent on N cards" when every row is silent). A "Counting names only" toggle leaves out companies whose
every row is silent. Unresolved rows are counted in the work line and listed on expand, never a row.

## The columns

Every cell comes from an existing reader through the SAME point-in-time view the Cockpit builds (`baskets.per_company`
→ `PointInTimeData`, the request's `known_at, clock = serve_view(asof)`); a column's header carries its coverage
("41 of 58"). Blank means not stated, never zero.

| Column | Rule | Source |
|---|---|---|
| Company | ticker chip (opens the Cockpit name panel on its first card) · name · cards (count, chips on hover) · role or silent tag | memberships, `master.identity_for` |
| Business type | the leaf (and the royalty overlay mark when set) | `master.identity_for` (the operator's re-tag wins) |
| Cap tier · cap | **approximate** market cap = the latest close × the latest shares fact, with the Workbench's ADS rule exactly (`workbench/scoring._market_cap`: `ads_ratio_status = "known"` divides by the ratio, `"unread"` WITHHOLDS the cap, NULL = 1:1); the tier from the fixed bands; blank with its reason ("awaiting price", "awaiting shares", "ADS ratio unread") | `pit.price_history`, `pit.shares_outstanding_facts` |
| Revenue · YoY | the latest period's revenue (its period end on hover) and the change against the row for the same period end one year earlier within 10 days, when that row is in the same read (`filings.prior_year_row`); blank otherwise | `pit.fundamentals_facts` (`metric_key = revenue`) |
| Runway | months = cash ÷ (quarterly burn ÷ 3) from the latest cash/burn fact; "cash-positive" when burn ≤ 0 (`workbench/scoring._runway`'s rule, the figure not the pips) | `pit.cash_burn_facts` |
| Overhang | the convert overhang percent, the dilution clock's own number | `signals.dilution_clock.overhang_pct` over `pit.dilution_facts` |
| Purity | the largest revenue-mix line, with its segment label on hover (`_purity`'s rule) | `pit.revenue_mix_facts` |
| Off 52w high | percent off the 52-week high | `range_52w` member (`pct_off_52w_high`) |
| 50d / 200d | above or below each average (two small marks), with the percent on hover | `sma_position` member |
| RVOL 20 | relative volume against the 20-bar baseline | `rvol` member (`rvol20`) |
| ADV 20d | 20-day average dollar volume (the liquidity cut `CLAUDE.md` asks for beside any cap slice) | `volume_regime` member (`adv_usd_20d`) |
| 30D vs SPY | the 30-trading-day return minus SPY's, in pp | `trailing_returns` member and SPY via `pit.benchmark_prices` (the Read-Throughs' `_spy` rule) |
| Insider buys 90d | open-market purchases in the member's 90-day window: count and dollars, as the member screens them | `insider_flow_90d` member (read its metric keys) |

A company whose data flags reach its price readings (`baskets.price_flags`) shows its price-derived cells (cap, 52w,
50d/200d, RVOL, ADV, vs SPY) grayed with the reason and stays in the table, named in the shared `FlaggedNote`.

**The market-cap bands are defined ONCE,** in `backend/domain/market_cap.py` (new): `CapTier` (nano < $50M · micro
$50M–$300M · small $300M–$2B · mid $2B–$10B · large ≥ $10B) and `cap_tier(usd) -> CapTier | None`, mirroring the
table in `CLAUDE.md` ("Event-outcome reports are sliced by market-cap tier") and `docs/BACKTEST.md`. A tier is a
display and slicing dimension only: never a call input, never sizing, never the retired size archetype (#4).

## Group rows

Each group (leaf, supersector, link or card) gets a header row with its aggregates, each saying its n: names; median
approximate cap; share above the 50d ("9 of 12 priced"); median 30D vs SPY ("median of 11 priced"); median YoY
("median of 6 with a prior year"); names with a runway figure. Medians only, no weighting, no score. A group with one
priced name shows the value with "1 name", never a median label.

## Sort, filters

Every column sorts; a blank sorts last in either direction; the default is the group order with names A to Z inside.
Filters (client side): cap tier (multi), "Counting names only", "only names with revenue", "only names with a
Workbench fact", a type-ahead over tickers and names. Component state only, nothing in the URL or browser storage.

## The quiet "show the work" line

The scope and the as-of; the clock; live cards counted / not yet created; companies in scope / rows / unresolved rows /
silent-only companies; per-column coverage (the header carries it too); flagged names; the cache state and
`compute_ms`; the roster note when today's roster stands in. Below it the honest notes: "approximate market cap: the
latest close × the latest shares fact (ADS ratio applied when known, withheld when unread)"; "Workbench facts exist
only where a name was marked for data; the blanks are the to-do list"; "insider buys as the Cockpit's column screens
them"; "medians, equal weight".

## Cache

`dashboards.cache.cached(conn, "peer-table", (scope_kind, scope_key, clock), asof, ...)` holds one scope's finished
rows; the scope lists (leaves, supersectors, cards with counts) are cached as `("scopes", clock)`. The cache clears on a
new run or a basket edit (rule 7).

## Backend and frontend

- `backend/domain/market_cap.py` (new, shared): the bands and `cap_tier`.
- `backend/dashboards/peer_table.py`, pure: the scope membership, each company once, the cell rules (every blank with
  its reason), the group rows and their n's, the sort keys.
- `backend/app/routers/dashboards/peer_table.py`: `GET /dashboards/peer-table/scopes?asof=` (the leaves, supersectors
  and cards present at the as-of, with counts) and `GET /dashboards/peer-table?asof=&scope=business_type|supersector|
  card|all&key=` (the table; 404 for a scope key not present at the as-of). `known_at, clock = serve_view(asof)` threads
  into every read. Wire models in the module (rule 8).
- `backend/tests/dashboards/test_peer_table.py`: the bands (every edge), the cap rule (known / unread / NULL ratio;
  awaiting price; awaiting shares), the YoY rule, runway (cash-positive), the once-per-company fold with chips, the
  group medians and n's on hand-computed numbers, the blank-sorts-last keys, a flagged name grayed and named; the
  router off one cached read with its 404; and, DB-backed, **a fact published after a past as-of never reaches a cell**
  (a shares fact and a revenue print with later `valid_from`, invisible at the earlier as-of, visible at the later).
- `frontend/src/dashboards/peer-table/`: `entry.ts` (slug `peer-table`, order 150, `asofAware: true`), `PeerTable.tsx`
  (the scope control, the table with a pinned Company column and sideways scroll, group header rows, the filters),
  `usePeerTable.ts`, `tableView.ts` (sort, filters, coverage), `peer-table.css`, tests. The Read-Throughs page is the
  table pattern to follow (single-line pills, pinned first column).
- **Layout:** the research set's document-like look; dense, tabular numbers, one line per row; on a phone the table
  scrolls sideways with the Company column pinned.

## Known limits (stated on the page too)

- **Approximate market cap** (above); an ADR with an unread ratio shows no cap by design.
- **Sparse Workbench facts:** runway, overhang and purity exist only where ratified or reached by an automated leg;
  the coverage counts say how sparse.
- **Revenue is the XBRL series only** (companyfacts); a name with no revenue facts shows blank, not zero.
- **Short interest is not stored** (no FINRA ingest yet): no column until it is.
- **The roster is each card's roster as of the as-of; business types and links are today's.**
- **Medians, equal weight, no score.**

## Out of scope

Any composite score or ranking beyond sorting by a column; short interest; a per-name drawer (the Cockpit name panel
is the drill-in); any write.

## Choices to confirm

The build lists here every choice it made that this plan does not spell out, each the operator's to confirm or change.
Pre-decided here for the operator's review: business type is the default scope; "All live cards" is offered with a
cold-load note rather than withheld; medians only in group rows; the band module lives in `domain/market_cap.py`.
