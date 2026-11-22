# Ticker Lookup

`ticker-lookup` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What the platform holds about one company at the as-of, on one screen. Each section is a reading another page
already makes, scoped to one `security_id`; nothing is new data or ranked. Seven sections, each with its n:
identity (the approximate cap and tier, the data flags on its price); cards (one row per card holding it, with the
name's place on the call in effect); the record's triggers on it; tape; filings; catalysts; the user's logged
decisions. The ticker rides the URL as `?t=`; Enter resolves an exact ticker first.

## Who counts

- Cards are the counted cards at the as-of (`baskets.cards_as_of`); a company in two links is one row. Context and
  Bench show tagged and read "not counted". Folds list archived cards that held it and counted cards that exclude
  it. A company on no counted card shows identity, tape and filings only.
- A flagged price history keeps the page: price figures gray, the cap withheld, a quiet line names the flag.
- Decisions come from the caller's own workspace cards only.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/ticker_lookup.py` |
| Route | `GET /dashboards/ticker-lookup/{security_id}`; `.../resolve?t=` |
| Shared readers | `baskets`, `filings`, `series.aligned`, `market_cap.cap_cell` (the shared cap rule) |
| Cache key | `(str(security_id), clock)`, plus the counted cards as `("cards", clock)`. Not warmed |
| Frontend | `frontend/src/dashboards/ticker-lookup/` |
| Tests | `test_ticker_lookup.py`, frontend `__tests__/` |

## As-of behavior

Every section reads at the as-of: roster snapshot, call in effect, public-clock tape, filings public by then. A
decision counts from `decision_date` and, on a past view, only once logged by that view's instant. A catalyst's
"passed" counts from the countdown origin.

## Rules not visible in code

- A trigger's identity is the Trigger Log's ([What a row is](trigger-log.md#what-a-row-is)), read only from cards
  the company sits on at the as-of (`trigger_id`). Why: the whole log is too many `calls` rows for one lookup.
- The place on a card: armed beats watch beats trigger-bearing beats none. A risk signal alone does not make a
  name trigger-bearing; it shows as a count.
- Decisions, card catalysts and exclusions are read on every request (`read_live_entries`). Why: they move no
  cache event (rule 7).

## Known limits

- At a past as-of the card catalysts and exclusions are today's.
- A trigger's key reads its kind (`turns_key`), not the revenue-acceleration exception the Trigger Log keeps by
  provenance; they agree under the default `revenue_accel_key`.
- A trigger that lapsed between recorded nights is absent.
