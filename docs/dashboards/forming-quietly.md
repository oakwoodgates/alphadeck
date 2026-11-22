# Forming Quietly

`forming-quietly` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

Which cards are forming and what each still needs, in the tape look ([Looks](../DASHBOARDS.md#looks)) at its quietest:
gray rows, no accent, no badge, no menu count (invariant 7). Per card: the recorded key that turned, the recorded
`missing` lines verbatim, risk signals, and the quiet signals on its counting roster (insider buying, coiling,
leading, theme breadth, adoption slope). It never ranks by promise or says "early", "setting up" or "about to"
(invariant 4).

## Who counts

- Cards counted at the as-of (`baskets.cards_as_of`), each read as its recorded call in effect.
- Rows: every Warming card and every Incubating card with a lit quiet signal; a toggle adds the other Incubating
  cards. Armed and Managing cards are never rows; they and cards with no recorded call are counted.
- Quiet signals read counting names only: Context and Bench never count.
- A flagged price history is left out of every reading, insider buying included, and counted and listed. Why: the
  insider screen reads the price tape.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/forming_quietly.py` (`MEMBERS`, `INSIDER_MIN_BUYS`) |
| Route | `GET /dashboards/forming-quietly` |
| Shared readers | `baskets`, `breadth.read_breadths`, `filings.source_url` |
| Cache key | `(clock,)`; the toggle, filters and sorts are the page's |
| Frontend | `frontend/src/dashboards/forming-quietly/` (`readsRecord`) |
| Tests | `backend/tests/dashboards/test_forming_quietly.py`, frontend tests (no accent; the words the page never says) |

## As-of behavior

The card in effect is its `calls` row with the greatest `(asof, seq)` on or before the as-of; the display members read
on the public clock; adoption months are those closed by the as-of.

## Rules not visible in code

1. Insider buying, coiling, leading and the theme-breadth member's `thrust` light an Incubating card. The breadth
   counts and the adoption slope never do. Why: no threshold is set for either.
2. "The night the key turned" is the first night of the card's current run of its recorded state (`run_start`; the
   highest `seq` wins a night, the Idea Timeline's rule). No per-key history is walked.
3. The adoption slope compares documents over the card's SIGNAL terms for the last `ADOPTION_SPAN` closed months
   against the span before. A month is closed only when its last day is before the as-of, and a term counts only with
   all six months stored.

## Known limits

- Quiet signals are display readings ([DISPLAY_SIGNALS](../DISPLAY_SIGNALS.md)), never call inputs.
- The adoption slope sums today's SIGNAL terms at any as-of (`term_set` has no history); a document matching two terms
  counts twice.
