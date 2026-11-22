# Filing Stream

`filing-stream` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What one card's names filed and what the system stored of it: a dated stream, newest day first, each row opening
to its EDGAR source. The shared reader owns the row kinds, words, tags and source links; its contract is the module
docstring of [`filings.py`](../../backend/dashboards/filings.py).

## Who counts

- One card from `baskets.picker_cards`: every roster row at the as-of, Context and Bench included, each company once
  under the card's own ticker. A client toggle leaves the silent names out.
- A row with no resolved security carries no fact: counted and listed in the roster note, never in the stream.
- No price is read, so price flags do not apply.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/filing_stream.py` (`WINDOWS`, `ROUTINE_CAP`) |
| Route | `GET /dashboards/filing-stream/cards`, `GET .../{thesis_id}?days=` |
| Shared readers | `baskets`, `filings.read_filing_rows` |
| Cache key | `(str(thesis_id), clock)`; picker `("cards", clock)`; windows are sliced per request |
| Frontend | `frontend/src/dashboards/filing-stream/` |
| Tests | `backend/tests/dashboards/test_filing_stream.py`, frontend tests |

## As-of behavior

A past as-of reads the facts public by the end of that date on the public clock; the window runs back from it, both
ends included. The roster is the card's as of then (today's stands in before its first snapshot, and the page says
so); links are today's.

## Rules not visible in code

1. Routine rows fold into quiet lines (`filings.FOLD_GROUPS`); every count covers every row. Why: sales, grants and
   prospectus paper outnumber the exceptions; nothing may vanish from the numbers.
2. Per company and fold group, at most `ROUTINE_CAP` routine rows are listed per window; a non-routine row is never
   cut, and cut rows are counted (`not_listed`). Why: one shelf issuer files prospectus supplements by the thousand.
3. The stream never writes "open market": the Cockpit's insider screens need price bars and are not applied.
4. A 4/A is shown as a filing, never resolved against its original; its SELECT keeps the two-clock gate
   (`read_amendments`; invariant 1).
5. A fact is "ratified" only when a person set it; a machine-applied one is a "stored fact". A stored share count is
   dated by when it became public (`_share_count_public`). Why: an ingest batch must not read as ratifications.
6. `fmt_compact` and `compactUsd` (`streamView.ts`) copy one rounding rule; change both.

## Known limits

- The page states its own limits (`LIMITS` in `streamView.ts`): EDGAR's `recent` index window, 13D/G stakes as
  stored, no quoted passages.
- The warm-up reads the picker only: a card's first visit is cold.
