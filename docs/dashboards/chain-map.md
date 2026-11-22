# Chain Map

`chain-map` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

One card as a picture, in the research set's look ([DASHBOARDS](../DASHBOARDS.md) Looks). Its real links, in chain
order, are columns; its companies are nodes in them, each with business type, side of the 50-day average and the age
of its newest SEC filing. Companies with a context role ([WORKBENCH](../WORKBENCH.md) "Context roles") stand on
two rails beside the chain, one edge each to the chain's bracket. A layout, not a model: no weights or inferred edges.

## Who counts

- One card (`baskets.picker_cards`); all its roster rows, Context and Bench included.
- One node per company per column. A role-tagged company with no row in a real link stands on its rail; one in a
  link stays in its column with a role tag. Untagged Context and Bench names sit behind a client-side toggle.
- A roster link no longer on the card, and rows with no link, get their own columns. A row with no resolved security
  is a plain ticker, counted.
- A flagged price history keeps its node, grayed with the reason (`flaggedRule(...)`); only its 50-day side is
  unread.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/chain_map.py` (`chain_map`; placement rules in its docstring) |
| Route | `GET /dashboards/chain-map/cards`, `GET /dashboards/chain-map/{thesis_id}` (404: card not counted) |
| Shared readers | `baskets`, `filings.read_filing_rows`, `master.identity_for` |
| Cache key | `(str(thesis_id), clock)`; `("cards", clock)` for the picker (warmed) |
| Frontend | `frontend/src/dashboards/chain-map/` (`mapLayout.ts`: pure layout, inline SVG) |
| Tests | `backend/tests/dashboards/test_chain_map.py`, `chain-map/__tests__/` |

## As-of behavior

A live view reads the current roster; a past view the latest snapshot by `known_at` (`thesis_repo.get_asof`),
today's standing in before the first (`roster_fallback`, noted on the page). The 50-day side and filings read on
the public clock; a filing dated after the as-of is skipped; ages count from the as-of. A card created after the
as-of is a 404.

## Rules not visible in code

- The last-filing glyph considers only `SEC_FILING_KINDS`. Why: a split, dilution fact, catalyst or stored fact is
  not a filing.
- Every edge lands on the chain as a whole, never a link or a name. Why: a role names a part the company plays; no role
  target exists.
- `roster_rows` pairs `memberships_of` with the basket row for row (a strict zip). Why: memberships carry link and
  role, not the thesis-fit prose.

## Known limits

- Links, descriptors, business types and prose are today's; only the roster is snapshotted.
- The 50-day side and filing age are display context, never a call input ([DISPLAY_SIGNALS](../DISPLAY_SIGNALS.md)).
