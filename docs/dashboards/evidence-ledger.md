# Evidence Ledger

`evidence-ledger` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

One card's provenance as one dated list, each row marked by who put it there: the recorded call's triggers and risk
signals, the ratified and stored facts the shared filing reader holds (kinds `ratified`, `catalyst`), the card's
catalysts, kill criteria and evidence links, and its Context roles. Undated rows stand above the dated list.

The authority class labels who put a row there and never judges it (`fact_authority`): `operator` (your purity, a
Context role), `steward` (a shared value or company catalyst the steward set, or carried over from V1), `system` (a
computation on public data), `automatic` (stored by ingest or the machine), `card` (the card's own entry).

## Who counts

One card from `baskets.picker_cards`. Every roster company is read for facts, Context and Bench tagged. A roster row
with no resolved security is counted and carries no fact. No price is read.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/evidence_ledger.py` |
| Route | `GET /dashboards/evidence-ledger/cards`, `.../{thesis_id}` |
| Shared readers | `baskets`, `filings` |
| Cache key | `(str(thesis_id), clock)`; picker `("cards", clock)` |
| Frontend | `frontend/src/dashboards/evidence-ledger/` (`readsRecord`) |
| Tests | `backend/tests/dashboards/test_evidence_ledger.py`, frontend tests |

## As-of behavior

The recorded call is the `calls` row with the greatest `(asof, seq)` on or before the as-of, a reconstructed night
counts under its night and is marked. A fact ratified after the as-of is not a row. Roles are dated by snapshots taken
by then. The card's catalysts, kill criteria and evidence links are always today's.

## Rules not visible in code

1. Card entries load live; the cache holds the rest. Why: editing them moves no cache event, so a cached ledger would
   show a stale entry.
2. An accession links through the issuer's CIK, never its own prefix (the filing agent's).
3. Evidence links are never dated. Why: `date_label` is free text.
4. A role is dated by the first snapshot of the unbroken run carrying its current role (`role_starts`). Why: a role
   that changed or came back would take an earlier role's date.
5. `by` is a kind (`you`, `steward`, `carried`), never an email; private purity is never relabeled steward.
6. A filter hides rows from the page, never from the tallies.

## Known limits

- Card entries are today's at any as-of; a fix needs a history of the card's lists.
- Located passages are not stored: a fact links to its filing, not its passage.
- Facts read back `READ_DAYS` only.
