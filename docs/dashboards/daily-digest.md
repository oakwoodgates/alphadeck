# Daily Digest

`daily-digest` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

The morning page: six sections, each a cut of an existing page, read by that page's own pure function; a number
here is the number there. It adds no reading rule; its cross-row figures are counts and a top-N cut.

| Section | Source |
|---|---|
| Last night, P to N | [what-changed](what-changed.md), by card, Armed first |
| Positions | Managing cards' positions, clocks, risks, kill criteria |
| Due this week | [catalyst-calendar](catalyst-calendar.md) `this_week` |
| Insider buys | [insider-tracker](insider-tracker.md) purchases filed in (P, N] |
| The tape | [hot-board](hot-board.md) armed rows, closes of the last two sessions: `TOP_N` rises and falls |
| Forming | counts of Warming and Incubating cards |

## Who counts

- Cards counted at the as-of, each at its recorded call then; a card with no call is counted.
- Tape and insider sections count voting names only, never Context or Bench. A position's held name counts.
- A flagged price history keeps its row with the reason, out of n and the cut.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/daily_digest.py`, composing the source pages' computes |
| Route | `GET /dashboards/daily-digest` |
| Shared readers | `baskets`, `filings`, `nights`, `series` |
| Cache key | `(clock, origin)`: the heavy inputs (`read_digest`); warmed |
| Frontend | `frontend/src/dashboards/daily-digest/` (`digestView.ts`) |
| Tests | `backend/tests/dashboards/test_daily_digest.py`, `daily-digest/__tests__/` |

## As-of behavior

N is the newest recorded night on or before the as-of, P the one before. Before the nightly pass or on a weekend N
is an earlier night, and the header says so. With no P, sections 1 and 4 have no baseline. A fill logged after the
as-of is not a position then. Countdowns count from the countdown origin; ages stay on the as-of.

## Rules not visible in code

- The accent marks three lines: a card that entered Armed, a name newly armed, an entry window closing within
  `ENTRY_WINDOW_DAYS`. A card that left Armed (not into Managing) and an exit-by reached are warm (`tone_of`, pinned).
- Routine churn folds into one count line per card, client side, and never carries a tone. Why: nearly every
  Armed card churns nightly.
- `live_digest_inputs` reads positions, kill criteria and the week's catalysts on every request. Why: decision-log
  and card edits move no cache event.
- Insider filings are cut at N; the cluster rule sees `CLUSTER_WINDOW_DAYS` before P. Why: a later filer must not
  make a cluster.

## Known limits

- Catalysts and kill criteria have no history: a past morning reads today's.
- The tape reads closes only; heat and relative strength stay on the Hot Board.
