# Market Map

`market-map` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Where the money is moving across everything on the cards, as a nested treemap heat map. A tile is a company: area is
its weight (approximate market cap by default, 20-day dollar volume, or equal), color its return over a window (1d to
1Y, or YTD), optionally minus SPY's or IWM's. Group boxes are card then link (the default), card, sector, business
type, cap band or all companies, each headed by its median. The payload ships readings, so every control is client
side. Not a whole-market screener (invariant 2).

## Who counts

- The cards `baskets.cards_as_of` counts. Context and Bench ride the payload marked `silent`; a toggle shows them.
- A company with no price bars is a hatched no-reading tile. A flagged price history is left off the map and out of
  every median; `FlaggedNote` lists it (rule 5).
- A company is drawn once per group it is in, so in the card views area is the cards' structure, not the market. One
  with no weight is drawn dashed at the smallest weight.
- A group median needs `MIN_PRICED` priced names (`mapView.ts`).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/market_map.py` (rules in its docstring) |
| Route | `GET /dashboards/market-map` |
| Shared readers | `baskets`, `market_cap` (`cap_cell`, `pick_shares`) |
| Cache key | `(clock,)` |
| Frontend | `frontend/src/dashboards/market-map/` (`squarify.ts`, `mapView.ts`) |
| Tests | `backend/tests/dashboards/test_market_map.py`, frontend tests |

## As-of behavior

A past as-of reads the public clock (rule 2). YTD runs from the last close on or before December 31 of the prior
year. A card with no roster snapshot by then reads today's roster, and the page says so. Business type and sector
are today's.

## Rules not visible in code

- The cap is `cap_cell`'s (`dashboards/market_cap.py`), shared with the Peer Table. With no Workbench share count,
  this page stands in the latest filing-cover count (`cover_shares`): a 10-Q filer only, the latest row only, filed
  within `COVER_MAX_AGE_DAYS` of the as-of (a company that stops tagging the cover keeps an old value: Visa's is from
  2010) and at least `COVER_MIN_SHARES`. Other `cap_cell` pages show "awaiting shares" instead; Track Record applies
  the cover rule at the event's date.
- Color ends are fixed per window (`COLOR_END`). Why: a color means the same move from one day to the next.
- Dollar volume needs 20 sessions, each with a volume; a gap is stated, never a partial mean.

## Known limits

- Not in `warm_dashboards.PAGES`: the first visit after each nightly run computes cold.
