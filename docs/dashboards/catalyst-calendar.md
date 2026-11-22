# Catalyst Calendar

`catalyst-calendar` · a "now" view (no as-of dial) · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

The dates the user wrote, across every live card on one calendar: each dated catalyst, plus the two deadlines of
the card's latest recorded call (`arm_until`, worded "entry window ends", and `exit_by`). Upcoming items fall in
five buckets from today (`bucket_bounds`; `NEXT_DAYS` and `FAR_DAYS` set the 30- and 90-day edges; a week starts
Monday). Below them: the passed catalysts, newest first, to prune; then the undated catalysts per card, the card
with the most first.

## Who counts

- Every live card of the caller's workspace, in any state (`thesis_repo.list_all`; archived cards are out).
- A card with no recorded call keeps its catalysts, with no state and no deadlines. A card with no catalysts is
  counted on the work line, never dropped silently.
- No names, so no Context or Bench; no prices, so no flagged histories.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/catalyst_calendar.py` (`catalyst_calendar`, pure; the reading rules are in its docstring) |
| Route | `GET /dashboards/catalyst-calendar` (no params; the router reads the live cards and `read_latest_calls`) |
| Shared readers | none |
| Cache key | none (a request is cheap and the page is not in `warm_dashboards.PAGES`) |
| Frontend | `frontend/src/dashboards/catalyst-calendar/` (`calendar.ts` holds the filters) |
| Tests | `backend/tests/dashboards/test_catalyst_calendar.py`, `catalyst-calendar/__tests__/` |

## As-of behavior

`asofAware: false`: no dial, and the route takes no as-of. The router passes `market_today()` into the compute as
`today`, and every "days away" counts from it ([DASHBOARDS](../DASHBOARDS.md) rule 2). Catalysts are edited in
place with no history, so there is no past view and no past-as-of test. A card's state and deadlines are read from
its latest recorded `calls` row, not recomputed. The page carries the shared record-start note (`readsRecord`).

## Rules not visible in code

- A fuzzy `when_label` ("~Q3") is never turned into a date; the catalyst stays undated. Why: a guessed date would
  put an invented day on the calendar.
- A passed deadline is not listed. Why: the entry window and exit-by are the call's own clocks, not authored
  catalysts, so there is nothing to prune.
- A passed catalyst stays on the Board until the user removes it in the card's editor; this page only lists it.
  Why: a dashboard never writes (rule 1).
- An empty `kind` reads as no kind, and the kind filter never touches a deadline.

## Known limits

- "Now" only. Earlier versions of a catalyst's date are not kept.
- A deadline is as fresh as the card's latest recorded call.
