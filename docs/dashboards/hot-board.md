# Hot Board

`hot-board` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Every name the record shows Armed, one row per card and name: its call, clocks since arming, return since then
against IWM, tape and approximate cap. Rows rank by heat, the mean of four percentiles among the rows shown
(`HEAT_PARTS`), recomputed when toggles or filters change the rows: a stated formula, never a verdict on an idea
(invariant 4).

## Who counts

- Each counted card's recorded call in effect at the as-of. Default rows: every Armed card's `armed_members`.
- Toggles, off by default, add Warming cards' names with a live entry trigger (saying which key), Armed cards'
  watch members, and Managing cards' held and armed members. Incubating and uncalled cards are only counted.
- A name on two cards is two rows, each naming the other cards.
- A flagged price history keeps its row and insider counts; price cells read the flag; no heat.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/hot_board.py` (rules in its docstring) |
| Route | `GET /dashboards/hot-board` (`rows=armed` by default, or `all`) |
| Shared readers | `baskets`, `series`, `market_cap`, `filings.source_url` |
| Cache key | `(rows, clock, origin)`; the warm-up reads `rows=armed` |
| Frontend | `frontend/src/dashboards/hot-board/` (`heat.ts` recomputes heat) |
| Tests | `backend/tests/dashboards/test_hot_board.py`, `hot-board/__tests__/` (both read `heat-fixtures.json`) |

## As-of behavior

The card in effect is its row recorded on or before the as-of; the arm-night walk reads rows up to it. A past
as-of reads the tape on the public clock. A card armed later has no rows; a member added later is not a row then.
Days to `exit_by` count from the countdown origin; session counts do not.

## Rules not visible in code

- "Leading" is the 30-day return minus IWM's, not `relative_strength.rs_iwm`. Why: `rs_iwm` is a price-level
  ratio, so ranking it would rank share price.
- Sessions left to `arm_until` use the call path's count (`domain.trading_days`), not IWM's bars. Why: a future
  session has no bar, and the count must agree with `entry_window_closing`.
- The arm night starts the member's current unbroken run of recorded nights, never a trigger's fire date. Why: the
  user could first see the name that night.
- The page asks for `rows=all` once, when a row-adding toggle is first checked. Why: the tape is the costly part,
  and the toggles start off.

## Known limits

- Heat ranks only the rows shown; it means nothing across days.
- The four heat parts weigh equally, untested against what followed.
- The tape is the display members' reading ([DISPLAY_SIGNALS](../DISPLAY_SIGNALS.md)): split-adjusted, no
  dividends.
