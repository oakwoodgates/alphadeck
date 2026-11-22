# Read-Throughs

`read-through` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What the payers, the rivals and the direct reads just said: every company tagged with a context role (`ROLE_ORDER`)
on a counted card, one row per (company, role), with its card chips, business type, last filing, next dated catalyst
and 7 and 30 trading-day return minus SPY's. A role is display metadata, never written by the model (invariant 3).
The page says what was filed and when, never what it means.

## Who counts

- Every roster row with a role on the cards `baskets.cards_as_of` counts, Context and Bench included (most roles live
  there). The same role on two cards is one row with two chips.
- A role row with no resolved security is counted in the work line, never a row.
- A flagged price history keeps the row; both tape cells read "flagged" with no number sent (`flaggedRule(...)`).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/read_through.py` (rules in its docstring) |
| Route | `GET /dashboards/read-through` |
| Shared readers | `baskets`, `filings` (`read_filing_rows`, `SEC_FILING_KINDS`) |
| Cache key | `(clock,)` over `read_inputs` with `catalysts=()`; the origin is not in it; the warm-up sends no params |
| Frontend | `frontend/src/dashboards/read-through/` |
| Tests | `backend/tests/dashboards/test_read_through.py`, frontend tests |

Catalysts are read live on every request (`read_live_catalysts`): they move no cache event (rule 7).

## As-of behavior

A past as-of reads the public clock (rule 2). A role set after the as-of does not show (a DB-backed test pins it).
The last filing is the newest within `FILING_DAYS` of the as-of, aged from it. The next catalyst counts down from
`countdown_origin` (the action day live, the as-of on a past view), and the origin day counts. Catalysts and
business types are today's.

## Rules not visible in code

- Last filing is an SEC filing only (`SEC_FILING_KINDS`); a split, a dilution fact, a catalyst or a stored fact is
  never picked: they are rows the system holds, not filings. Ties go by `KIND_ORDER`, then accession, form,
  headline: the pick never depends on arrival order.
- The tape windows are the `trailing_returns` member's 7 and 30 trading days (`TAPE_WINDOWS`), matching the Cockpit
  and Leaders. A cell is blank when the company's last close is not SPY's (`ends_differ`): the two returns would end
  on different days.
- A past or undated catalyst is ignored; ties by card name, then label.

## Known limits

- Last filing carries the Filing Stream's limits ([Filing Stream](filing-stream.md)).
- The cold read is dominated by a year of filings per role company.
