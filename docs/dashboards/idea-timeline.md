# Idea Timeline

`idea-timeline` · a "now" view (no dial) · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Where each live card has been: its recorded state over time on one shared date axis, as segments (one run of the same
state), with its dated catalysts, your logged decisions (take, close, pass) and the latest call's `exit_by` and
`arm_until`. The time axis is the history, so there is no dial.

## Who counts

Every live card of the workspace; archived cards are left out (`thesis_repo.list_all`). A card with no recorded call
keeps its row ("no call yet") and is counted on the work line. The page reads cards, not names, so Context and Bench
do not apply.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/idea_timeline.py` (`FUTURE_CAP_DAYS`, `DECISION_MARKS`) |
| Route | `GET /dashboards/idea-timeline` (no as-of) |
| Shared readers | none: reads `calls` (no card blob) and `operator_decision` |
| Cache key | none |
| Frontend | `frontend/src/dashboards/idea-timeline/` (`readsRecord`) |
| Tests | `backend/tests/dashboards/test_idea_timeline.py`, frontend `__tests__/` |

## As-of behavior

A "now" view: the endpoint takes no as-of and `today` is `market_today()`, passed into the compute. The page reads the
original `calls` record only, so the past-as-of test of the add checklist does not apply.

## Rules not visible in code

1. A night reads its last row (highest `seq`), because `calls_repo.record_if_changed` appends a same-night re-run only
   when the card changed. A state holds until the next recorded night.
2. A reconstructed night (rebuilt by `pipeline.backfill`) is read off its winning row and drawn hatched: reported,
   never scored.
3. Days are calendar days: `end - start`, `end` being the next run's first night or today.
4. Times armed counts armed runs, the first recorded night included.
5. A void decision row draws nothing; the row it names stays, grayed.
6. `exit_by` is a signal-validity horizon, not a trade exit. Both clocks come from the latest call.
7. The axis ends at the latest future marker, capped at today plus `FUTURE_CAP_DAYS`; a marker past the cap is counted
   on its row, never dropped.
8. Managing is the state the call path gives a card with a position (`calls.assembler._state`); the page reads the
   recorded state, never a position.

## Known limits

- A night with no row reads as the previous state carried. The page cannot tell a missed pass or a withheld recording
  gate from a night an older rule skipped.
- Card lineage (a split card carrying its original's date) is not built; each card starts at its own first recorded
  call.
- Edition re-runs are not shown.
