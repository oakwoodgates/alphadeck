# Chain Wave

`chain-wave` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

One card: its links in chain order down the side, calendar weeks across, each cell that row's weekly return minus
SPY's in percentage points, so a move along the chain reads as a diagonal. Below the chain: the card's counting
names as one row, and one row per context role set (`ROLE_ORDER`, payers first). With payers, one line gives
payers' and counting names' return minus SPY over the last `READOUT_WEEKS` weeks: numbers only.

## Who counts

- One card (`baskets.picker_cards`). A link's row holds its roster companies, each once; old links and No link follow.
- Context and Bench are out of the chain unless `include_silent`. Counting names are every company outside them;
  role rows hold Context and Bench names whatever the toggle says.
- Every row shows, with "priced / names". A name is priced when it contributed a daily return in the window; under
  `THIN_BELOW` priced names a row is `thin`, drawn faint, never hidden.
- A flagged price history never contributes a return or shapes the calendar. It counts in names, never as priced,
  and is named with its reason (`flaggedRule(...)`).

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/chain_wave.py` (`wave_inputs` builds all `WEEKS` once; `chain_wave` slices per request) |
| Route | `GET /dashboards/chain-wave/cards`, `.../{thesis_id}?weeks=&include_silent=` (404: card not counted) |
| Shared readers | `baskets`, `series` |
| Cache key | `(str(thesis_id), clock)`, Context and Bench included; `("cards", clock)` for the picker (warmed) |
| Frontend | `frontend/src/dashboards/chain-wave/` (`waveView.ts`) |
| Tests | `backend/tests/dashboards/test_chain_wave.py`, `chain-wave/__tests__/` |

## As-of behavior

A live view reads the current roster; a past view the latest snapshot by `known_at`, today's standing in before the
first (noted). A past as-of reads closes and SPY on the public clock
(`test_a_price_published_after_a_past_asof_never_reaches_a_cell`). The last column is the as-of's week.

## Rules not visible in code

- A "—" cell carries its reason, checked in order: `no_session`, `not_started`, `no_return`, `no_spy`. A flat week is
  `no_return`, never 0%. Why: zero would claim a reading.
- The readout ignores window and toggle; counting excludes Context and Bench, payers includes them.
- The picker reads no as-of rosters; link counts are today's. Why: loading every card's roster at a past
  as-of is the heavy read.

## Known limits

- The as-of roster applies to every week: hindsight selection. Links and chain order are today's.
- Equal weight, rebalanced daily (`series.chain`): our own measure, not a published method.
- "To date" tests the as-of's weekday against Friday, not the session calendar, so a Thursday before Good Friday
  still reads "to date".
