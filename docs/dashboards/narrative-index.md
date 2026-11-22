# Narrative Index

`narrative-index` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Is the market agreeing with a card's idea yet, and since when: each counted card's basket as one equal-weighted
line against SPY and IWM, over a 1Y, 90D or 30D window of trading sessions, with the creation day marked. Per card:
the window return, that minus SPY's, the return since creation (only when the creation day falls in the window),
names priced of names in the basket, and sessions carried flat. A card with no priced name shows no return, never
0%. The state chip is the card's call in effect, so the page shows the shared `RecordStartNote`.

## Who counts

- Each card's roster at the as-of (`baskets.cards_as_of`), applied over the whole window. Context and Bench only
  with `include_silent`.
- Each company once per card (`security_id`). A row with no resolved security counts as a name, never priced.
- A flagged price history is left out of every window; it stays in the card's names, never in priced, and the card
  lists it (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/narrative_index.py` (rules in its docstring); line math in `dashboards/series.py` |
| Route | `GET /dashboards/narrative-index` (`window`, `include_silent`); `GET .../{thesis_id}` is one card, 404 if not counted at the as-of |
| Shared readers | `baskets`, `series` |
| Cache key | `(clock, include_silent)`; each card's 1Y series, windows derived per request; the warm-up reads `window=1y` |
| Frontend | `frontend/src/dashboards/narrative-index/` |
| Tests | `backend/tests/dashboards/test_narrative_index.py`, frontend tests, `RecordStartNote.test.tsx` |

## As-of behavior

A past as-of reads only bars public by the end of that day (rule 2); the sessions end at the as-of. The state chip
is the `calls` row with the greatest `(asof, seq)` on or before it, a reconstructed row included; none reads "no
recorded call".

## Rules not visible in code

- The basket is a look back: the roster at the as-of, not the basket as it stood each day. The page says so. Why:
  roster snapshots exist only from 2026-09-15.
- Since creation runs from the close of the last session on or before the creation day in market time.
- One session calendar serves every card, so a benchmark gap carries flat as a card's does.
- A tile shows flat sessions only when there are some. Why: a zero on every tile is noise (invariant 7).

## Known limits

- A flag is read once per as-of for the 1Y read, so a scale break 200 sessions back also leaves the name out of
  the 30D line.
