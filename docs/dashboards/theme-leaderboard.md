# Theme Leaderboard

`theme-leaderboard` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Every counted card ranked by one stated formula with its parts beside it: what the data shows, never a judgment of
a thesis (invariant 4). The score is the mean of five percentiles times 100, each the card's rank among the
cards shown:

| Part | Reads |
|---|---|
| tape | median 30-day return of the priced counting names minus IWM's |
| breadth | share of counting names above their 50-day SMA |
| keys | keys turned on the call in effect; Managing counts as both |
| adoption | term-mention documents, last `RECENT_MONTHS` closed months against the `PRIOR_MONTHS` before |
| insiders | distinct open-market buyers in 30 days across the counting names |

A card missing any part has no score and sorts last. The page recomputes the score for the cards it shows
(`score.ts`), so it means nothing across days. Look: [trading set](../DASHBOARDS.md#looks), no accent.

## Who counts

- Cards counted at the as-of (`baskets.cards_as_of`), whatever their state; with no recorded call a card keeps its
  row, keys blank.
- Only counting names feed the parts, never Context or Bench. A flagged price history is left out of every tape
  reading and counted per card (rule 5); its Form 4 count stays.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/theme_leaderboard.py` |
| Route | `GET /dashboards/theme-leaderboard` |
| Shared readers | `baskets`, `breadth.read_breadths`, `market_cap` |
| Cache key | `(clock,)`; warmed. Score, filters and sorts derive on the page |
| Frontend | `frontend/src/dashboards/theme-leaderboard/` |
| Tests | `test_theme_leaderboard.py`, frontend `__tests__/`; both scores run `score-fixtures.json` |

## As-of behavior

Every part reads at the as-of: roster snapshot in force, recorded call in effect, display members on the public
clock, term months closed by then. A past as-of counts today's SIGNAL terms.

## Rules not visible in code

- Adoption sums only terms with all six closed months stored; one added since the last weekly run is left out and
  counted. A document matching two terms counts twice. When the earlier months total zero the part is missing. Why:
  a zero-to-one jump would otherwise outrank a real trend.
- A name with no Form 4 ingested is not zero; a card where none has one has no insiders part.
- The percentile rule (`percentiles`) is the Hot Board's, copied. Why: each dashboard deletes cleanly.

## Known limits

- Equal weights, untested against what followed.
- Keys ties heavily: one key turned by any name counts, while Armed needs both on one name.
- The parts never feed a call; a high score is not one.
