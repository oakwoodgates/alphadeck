# Trigger Log

`trigger-log` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What fired lately, and has it already run: every entry trigger and risk signal the recorded calls show across the
counted cards in a window, newest first. It never aggregates: outcome statistics belong to the
[Track Record](track-record.md) (rule 6).

### What a row is

- One trigger on one card, by role, security, kind and fire date (`nights.copies`): the same trigger on twelve
  nights is one row, labelled by its first night (`label_changes` counts the others).
- Its recorded night is the first night in the window the record shows it. One already on the row before the
  window is no row; `carried_live` counts those still live.
- A fire date before the recorded night reads "recorded N days after". An insider trigger's fire date is the night
  its latest Form 4 was public.
- Live means still on the card in effect at the as-of. Since is the as-traded return after the recorded night,
  beside IWM's.

## Who counts

- Cards counted at the as-of (`baskets.cards_as_of`); role `entry` (default), `risk` or `both`. Context and
  Bench need no filter: a recorded call never carries their triggers.
- No bar since reads "unpriced"; a flagged price history reads "flagged". Both keep the row (rule 5).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/trigger_log.py` (`collect`, `trigger_log`) |
| Route | `GET /dashboards/trigger-log` (`days`, `role`) |
| Shared readers | `baskets`, `nights`, `series`, `filings.source_url` |
| Cache key | `(window, role, clock)`; warmed for `role=entry` |
| Frontend | `frontend/src/dashboards/trigger-log/` |
| Tests | `test_trigger_log.py`, frontend `__tests__/` |

## As-of behavior

The window is `[as-of - days, as-of]`, inclusive; nights recorded after the as-of are never read, and bars end there
on the public clock. Reconstructed nights count and are marked.

## Rules not visible in code

- The label is not in the identity. Why: labels carry that night's numbers, so a continuing signal would read as new
  nightly.
- The role loads per request. Why: risk signals are most of the record; both together were too large to render.
- The window also reads the row before it. Why: the log appends only on change, so that row holds the earlier state.
- The key reads the kind against today's `DEFAULT_CONFIG`; the revenue-acceleration exception is found by
  provenance (`REVENUE_SOURCE`), as the detector name never reaches the wire.

## Known limits

- Live triggers only: one that lapsed between two recorded nights is not here.
- A trigger first recorded before the window is not listed, however live.
