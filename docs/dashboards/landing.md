# Price & Volume Landing

`landing` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Where price closed in each window's range, and whether volume backed the move, by group. Per group and per window of
trading bars (`WINDOWS`): the median range position (the close within the window's own high-low range), the split of
names by return sign and volume (the quadrants) and the median up/down-day volume. Rows are links (default), business
types, sectors or cards.

## Who counts

- Cards and groupings are the Rotation Map's (`baskets`); Context and Bench are left out unless `include_silent`.
- In the Link view a company in two links counts in both; in the other views once per group.
- A name with too little tape reads "—" for that window and is left out of its medians, but counted. A group under
  `MIN_PRICED` names shows "—" for its medians.
- A missing or stale tape counts as not priced, never dropped.
- A company with flagged price history is left out of every aggregate but counted in its group's names and listed
  grayed with the reason (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/landing.py` (`RISING_MULT`, `MIN_PRICED`, `LOOKBACK_DAYS`) |
| Route | `GET /dashboards/landing` (`group_by`, `include_silent`) |
| Shared readers | `baskets`, `master.identity_for` |
| Cache key | `(clock,)`: the per-company readings; groupings and toggles derive per request |
| Frontend | `frontend/src/dashboards/landing/` |
| Tests | `backend/tests/dashboards/test_landing.py` (a bar published after a past as-of stays invisible), frontend tests |

## As-of behavior

A past as-of reads the bars public by the end of that date on the public clock, and each card's roster as of then
(today's stands in where no snapshot exists, and the page says so). Business type and sector come from today's master
at any as-of.

## Rules not visible in code

1. A window is the company's last N bars, the as-of's own bar first; the prior window is the N bars before. Why: the
   Cockpit's own ladder, so the numbers agree.
2. Volume is rising when the window's total is above `RISING_MULT` times the prior window's, so a tape under 2N bars
   reads "—" and exactly the multiple is not rising.
3. The quadrant split is shown as counts, never one verdict label; an unchanged close is flat whatever volume did.
4. `LOOKBACK_DAYS` must cover the 90-day window and its prior window; a test holds it inside the display view's price
   bound.

## Known limits

- 1D up/down-day volume is degenerate by construction: one bar is an up day (no down days, "—") or a down day (0.00x).
