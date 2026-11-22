# Overlap Map

`overlap-map` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Where the cards touch, as structure only: no prices, no calls. Three tabs read one payload. Map (the default): a
bubble per counted card, its area the card's companies, joined by a line when two cards share at least N companies.
Venn: two or three sets (a card, a link on a card, a supersector, a business type), each region with its exact count
and companies. Sectors: cards by supersector or business type. Never a ranking, a "related" label or a merge
suggestion (invariant 4).

## Who counts

- The cards `baskets.cards_as_of` counts. Counting names only by default (Context names are the most shared and
  would join nearly every card in one group); the Include Context, ETFs and Bench toggle adds the silent rows,
  decided by `baskets.counted_rows` through `is_silent`, never by label.
- Matching is by `security_id`: a company in several links of one card counts once for it; two share classes are two
  companies. A row with no resolved security is counted on the quiet line, never matched.
- A card sharing no company has no position and sits in a "Shares no names" strip, never dropped.
- Sector and business type come from today's master; a company with neither reads Unclassified, never dropped.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/overlap_map.py` (rules in its docstring) |
| Route | `GET /dashboards/overlap-map` (`include_silent`) |
| Shared readers | `baskets`, `master.identity_for` |
| Cache key | `(clock, include_silent)`; the whole payload, layout included; not in `warm_dashboards.PAGES` |
| Frontend | `frontend/src/dashboards/overlap-map/` (`vennLayout.ts`, `sectorGrid.ts`) |
| Tests | `backend/tests/dashboards/test_overlap_map.py`, `overlap-map/__tests__/` |

## As-of behavior

No price or fact is read. A past as-of reads each card's roster then: a card or roster row added after it is absent
(a DB-backed test pins it). A card with no roster snapshot reads today's roster, with a quiet note.

## Rules not visible in code

- The layout is deterministic (`layout_bubbles`): each card starts from a hash of its id (`hashlib`, never Python's
  per-process `hash()`). Why: the map looks the same on every visit and a new card moves the others little. The
  threshold only hides lines; bubbles never move.
- Two Venn sets are exact (the lens is solved to the shared count); three are approximate and the page says so.
  Counts are exact either way.
- Sectors uses the supersector and business-type rollup, never the raw SEC industry text.

## Known limits

- Position is our own layout: closer means more shared names, not a measured distance.
