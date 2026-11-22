# Entry Windows

`entry-windows` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Where in the window the as-of sits. One row per Armed card: its entry window (to `arm_until`) and hold horizon
(to `exit_by`) from the arm night on one date axis, sessions into and left, days to `exit_by`, catalysts inside, and
the armed names' median return since the arm night against IWM and SPY. Tape look
([DASHBOARDS](../DASHBOARDS.md) Looks); one accent: `entry_window_closing`.

## Who counts

- The cards counted at the as-of, each at its recorded call in effect then. An Armed card is a row; a Managing card
  joins behind a toggle. Warming, Incubating and no-call cards are counted, never rows.
- A Managing card with no Armed night is counted, not drawn; an Armed card with no armed name is a row without chips.
- A name with no bar on or after the arm night, or a flagged price history, is counted but out of the median.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/entry_windows.py` |
| Route | `GET /dashboards/entry-windows` |
| Shared readers | `baskets`, `series`, `filings.source_url` |
| Cache key | `(clock, origin)`; one read holds every row, the toggle and sorts are client side (warmed) |
| Frontend | `frontend/src/dashboards/entry-windows/` |
| Tests | `backend/tests/dashboards/test_entry_windows.py`, `entry-windows/__tests__/` |

## As-of behavior

The card in effect is its `calls` row on or before the as-of, and the run is walked on rows up to it: a card
armed later is not a row. Days to `exit_by` and a catalyst's passed flag count from the countdown origin;
sessions stay on the as-of.

## Rules not visible in code

- The arm night starts the card's current recorded Armed run, never a trigger's fire date. Why: that is when the user
  could first see it Armed. A Managing card walks back through armed and managing nights.
- Sessions into the window count on IWM's bars; sessions left use the call path's count (`domain.trading_days`). Why:
  no bar exists after the as-of, and the number must agree with `entry_window_closing`
  ([CALL_LOGIC](../CALL_LOGIC.md)). A window is open while the next session falls on or before `arm_until`.
- The accent is true when any armed member's own flag is, so `member_clocks` carry each flagged member's own clock.

## Known limits

- The arm-night walk is copied in the Hot Board ([DASHBOARDS](../DASHBOARDS.md) Known limits).
- Since-arm returns start at the arm night's own close (the Scoreboard's rule), which the user could not have taken;
  the Track Record also shows the next session's.
- Returns are as traded, no dividends; sessions are IWM's bars.
