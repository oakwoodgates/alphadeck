# Cross Radar

`cross-radar` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Golden and death crosses (the 50-day SMA crossing the 200-day) by group, over every card: clusters (at least
`CLUSTER_MIN_NAMES` names and `CLUSTER_MIN_SHARE_PCT`% of a group's priced names crossing the same way within
`RECENT_SESSIONS`), first movers (exactly one name crossed), the near-cross watch (gap within `NEAR_BAND_PCT` and
narrowing), and a timeline of every cross in the last `HISTORY_SESSIONS`, with each group's posture bar. The tape,
never a forecast.

## Who counts

- Groups (link, business type, sector, card), the Context and Bench toggle and the roster note are the Rotation
  Map's ([rotation-map](rotation-map.md)), through `baskets.buckets`.
- A name is priced with bars for a 200-day SMA and a last bar within `sma.STALE_GAP_DAYS` of the as-of. Any other is
  counted as not priced in its group, never dropped.
- A flagged price history is left out of everything (rule 5): counted in its group's names, not in not priced, and
  named with its reason. Rows with no resolved security are counted, never grouped.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/cross_radar.py` (`company_crosses` per company; `cross_radar` groups per request) |
| Route | `GET /dashboards/cross-radar` (`group_by`, `include_silent`, `price_200d`) |
| Shared readers | `baskets`, `master.identity_for`, `signals.display.sma` |
| Cache key | `(clock,)`: one read of every company; grouping and toggles derive per request (warmed: `group_by=link`) |
| Frontend | `frontend/src/dashboards/cross-radar/` (`crossView.ts`) |
| Tests | `backend/tests/dashboards/test_cross_radar.py`, `cross-radar/__tests__/` |

## As-of behavior

A past as-of reads what was public by the end of that date. Cards created after it are out; a card with no roster
snapshot by then keeps today's roster, and the page counts it. Business type and sector read today's master.

## Rules not visible in code

- A cross is stamped on the first bar on the far side; an exact zero is a touch, not a cross. A name counts by its
  latest cross in the window, so never in a golden and a death count at once.
- The SMAs come from `signals.display.sma` (`_sma_series`), not a copy. Why: the radar's crosses must never drift
  from the Cockpit's `sma_position`.
- A short or stale tape is "not priced", never "no cross". Why: an old cross must not read as recent.
- The price x 200d ticks (`price_200d`) are timeline only, never counted in a cluster, first mover or near-cross.

## Known limits

- SMA crosses lag by construction.
- A name under 200 + `HISTORY_SESSIONS` bars reads a shorter history, which its line says.
- Nothing here says what followed a cluster; an event-outcome read needs rule 6.
