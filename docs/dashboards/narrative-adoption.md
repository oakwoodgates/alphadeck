# Narrative Adoption Curve

`narrative-adoption` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

How many filings mention a card's SIGNAL terms each month, one card at a time, on a 36-month axis ending at the
as-of's month: documents by form group, filers and new filers, and the card's basket line against SPY, then a term
table. It shows which forms carry the terms and whether the filer count widens; never "early" or "crowded"
(invariant 4).

## Who counts

- The card's current SIGNAL terms (`signal_terms`), today's at any as-of: a term set has no history. BROAD terms are
  collision-prone and never counted; a stored term no longer in the set is ignored. Both are reported on the quiet
  line.
- The overlay follows the Narrative Index: the roster at the as-of, Context and Bench left out, flagged names left
  out and named (`flaggedRule(...)`).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/narrative_adoption.py` (rules in its docstring) |
| Route | `GET /dashboards/narrative-adoption/cards`; `GET .../{thesis_id}` (404 if not counted) |
| Shared readers | `baskets` (`picker_cards`), `series`; `PointInTimeData.term_mentions` |
| Cache key | `(str(thesis_id), clock, run_identity(run))`; the picker is `("cards", clock)`, the warm-up's read |
| Job | `backend/pipeline/term_mentions.py` writes `fact_term_mentions` (a workspace table) weekly; its docstring is the contract, [CRON_SIDECAR](../CRON_SIDECAR.md) the schedule |
| Frontend | `frontend/src/dashboards/narrative-adoption/` |
| Tests | `backend/tests/dashboards/test_narrative_adoption.py`, frontend tests |

The job is a dry run unless given `--apply`. The page imports only its pure helpers and `latest_run`, never its
writer (rule 1).

## As-of behavior

A month counts from its last day: a past as-of ends the curve at the last month closed by then, and the as-of's own
month is absent until it closes. A row read before its month closed is marked partial until the weekly run re-reads
it.

## Rules not visible in code

- A month with no stored count is absent, never a zero. A month stored for only some terms reads as a floor.
- A document matching two terms counts twice in the card's sum; the term table counts each term alone.
- Filers read "at least" where any contributing term-month was sampled (`filers_sampled`); a month with no new-filer
  count is a gap.
- The job's run is in the cache key. Why: the cache clears on the nightly and card edits, not the weekly job.
- The overlay widens only its own price bound (`PRICE_LOOKBACK_DAYS`): the display bound would end the tape before 36
  months.

## Known limits

Counts are SEC full-text matches: an exhibit and its filing may both count, a risk-factor mention like an MD&A one.
