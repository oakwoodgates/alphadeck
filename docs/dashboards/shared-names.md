# Shared Names

`shared-names` · a "now" view · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Which companies sit on more than one of the workspace's live cards, and which cards share them:

- **Companies on 2+ cards**: ticker, the master's name, card count and one chip per card with that card's link or
  links for it (no link reads "Unsegmented", the Cockpit's word), its context role and a quiet Context or Bench
  tag. Most cards first, then ticker.
- **Card pairs**: every pair of cards sharing a company, with the shared tickers, most shared first. A pair narrows
  the company table to its names.

The "opposite sides" mark and filter render only when a row qualifies.

## Who counts

- Live cards of the caller's workspace (`thesis_repo.list_all(workspace=...)`); archived cards are out.
- Matched by `security_id`, never ticker. A company with several rows on one card counts once for it, its links on
  one chip. A pair counts each shared company once.
- Context and Bench are in by default and tagged. `counting_only=true` drops them before both sections are
  computed, so the rule lives server-side only, and counts them as left out.
- A basket row with no resolved security is counted on the work line, never matched. A company with no master name
  keeps its row. No price is read, so rule 5's price flags do not apply.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/shared_names.py` (`shared_names`, pure) |
| Route | `GET /dashboards/shared-names` (`counting_only`) |
| Shared readers | none (`thesis_repo.list_all`, `securities.master.names_for`) |
| Cache key | none: one read of the live cards and names per request |
| Frontend | `frontend/src/dashboards/shared-names/` |
| Tests | `backend/tests/dashboards/test_shared_names.py`, `shared-names/__tests__/` |

## As-of behavior

None. The endpoint takes no as-of and reads today's baskets, so rule 2's past-as-of case does not apply.

## Rules not visible in code

- "Opposite sides": the company counts on one card (any non-silent row) and carries the context role
  `counter_case` on a different card (`_opposite_sides`). Why: one card leans on a name another argues against.
  With `counting_only` a counter-case tag leaves with its silent row.
- A row's ticker is the one most cards carry, alphabetical on a tie. Why: cards can carry different tickers for
  one security; each chip keeps its card's own, which opens the Cockpit on that name.

## Known limits

- Matching is by security, so two share classes of one company are two companies.
- No history: the page reads today's baskets, not `baskets.cards_as_of`'s past rosters.
- Tables only. [Overlap Map](overlap-map.md) draws the same overlap as bubbles, a Venn and a sector grid.
