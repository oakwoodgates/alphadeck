# Peer Table

`peer-table` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

How the names across the cards compare, and where the data is thin: one sortable table of companies, each once,
grouped by what they do, every blank counted per column. One scope at a time: a business-type leaf
(the default), a supersector, one card, or every counted card. A group row gives medians with their n, never a
score. Workbench facts exist only where a name was marked for data or an automated leg reached it, so the blanks are
the to-do list.

## Who counts

- Every resolved roster row of the counted cards, Context and Bench included, one row per `security_id`. A
  "counting names only" toggle drops companies whose every row is silent; the server sends both readings' group
  figures.
- A card scope reads that card's rows only; a company in several links is listed once, under the first.
- A row with no resolved security is counted and listed on expand, never a row.
- A flagged price history keeps the row and blanks its price cells with the reason (`flaggedRule(...)`).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/peer_table.py` (rules in its docstring) |
| Route | `GET /dashboards/peer-table/scopes`; `GET /dashboards/peer-table` (`scope`, `key`; 422 without a key, 404 for one absent at the as-of) |
| Shared readers | `baskets`, `market_cap`, `filings.prior_year_row` |
| Cache key | `("scopes", clock)` and `(scope, key, clock)`; the warm-up reads the scopes, then the default scope and `all` |
| Frontend | `frontend/src/dashboards/peer-table/` |
| Tests | `backend/tests/dashboards/test_peer_table.py`, `peer-table/__tests__/` |

## As-of behavior

A fact published after a past as-of never reaches a cell (a DB-backed test pins it). Rosters are the card's at the
as-of; business types and a card's links are today's.

## Rules not visible in code

- Cap is the latest close times the latest shares fact: `workbench/scoring._market_cap`'s rule, copied into
  `cap_cell` because importing it pulls the call config. An ADS ratio `unread` withholds the cap. The bands are in
  `domain/market_cap.py`; a tier is display only (invariant 4).
- Blank means not stated, never zero; a cap blank says "awaiting price" or "awaiting shares", the mark-for-data list.
- A group with one stated value shows "1 name", never a median label. A name exactly on the 50-day average is
  priced, not above.
- Cash-positive runway sorts as the longest. Zero insider buys with Form 4s on file is a 0; none ingested is a dash.

## Known limits

- Revenue is the XBRL series only; short interest is not stored (no FINRA ingest).
