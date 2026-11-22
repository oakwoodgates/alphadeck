# Fundamentals Multiples

`fundamentals-multiples` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Is a link's revenue inflecting, or one name's: one card's quarterly revenue, name by name and link by link. Each name
is a small chart of bars on its own scale (derived quarters hatched) with year-over-year dots above; each link and the
card carry a median line. It only draws the series the revenue-acceleration detector reads.

## Who counts

- One card (`baskets.picker_cards`), one row per real link in chain order, then old links, "No link", Context and
  Bench when occupied.
- Every roster company is drawn once per row it sits in. A name with no series, a series ending before the axis or no
  resolved security draws an empty frame and is counted. Fewer than `THIN_BELOW` bars is drawn and marked thin.
- Context and Bench sit behind a toggle and never enter a median. A company in two links counts once on the card line.
- No price is read.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/fundamentals_multiples.py` (`QUARTERS`, `THIN_BELOW`, `MIN_MEDIAN_NAMES`) |
| Route | `GET /dashboards/fundamentals-multiples/cards`, `.../{thesis_id}` |
| Shared readers | `baskets`, `filings.prior_year_row`; `fundamentals_set_aside_repo` (counts) |
| Cache key | `(str(thesis_id), clock)`; picker `("cards", clock)`. The toggle is client side, so the read holds every roster name |
| Frontend | `frontend/src/dashboards/fundamentals-multiples/` |
| Tests | `backend/tests/dashboards/test_fundamentals_multiples.py`, frontend tests |

## As-of behavior

Facts come through the Cockpit's point-in-time view on the public clock, so a chart ends at the last quarter filed by
the as-of. A bar is placed by period end and dated by its filing. An active set-aside falls back to the version it
superseded. The roster is the card's as of then; links are today's.

## Rules not visible in code

1. The axis is shared: `QUARTERS` calendar quarters ending at the newest quarter any roster name's latest print falls
   in, Context and Bench included. Why: fiscal calendars differ, so per-name windows would widen it, and the toggle
   must not move it.
2. A dot compares with the same period end a year earlier (`filings.prior_year_row`); no such row, or one at zero or
   less, gives no dot.
3. Dots sit on one fixed scale, `YOY_CAP` either side; a larger change is pinned and ringed. Why: one +900% must not
   flatten the rest.
4. A median is drawn only where `MIN_MEDIAN_NAMES` names contribute (a row with one counting name shows its dots):
   equal weight, no score.

## Known limits

- Revenue only: the XBRL series, quarterly, Q4 derived from the annual where no quarterly fact exists.
