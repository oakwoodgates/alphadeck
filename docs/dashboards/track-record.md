# Track Record by Cap Tier

`track-record` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What the tape did after each arm episode the Scoreboard records, by market-cap tier at the event: per cell the
horizon, comparison, n and how often it held. Rule 6: what happened, never that a signal works.

## Who counts

- Episodes are the Scoreboard's, unchanged ([SCOREBOARD](../SCOREBOARD.md)): `derive_episodes` over `armed_members`.
  Archived cards count; reconstructed nights and episodes armed after the as-of do not.
- Left out of every cell and counted: `censored_start`, `ingest_flagged`, a flagged price history, a stopped feed on
  a company still trading. A company that ended (`tape_health.classify_stop`) with a stopped feed as its only flag
  is counted.
- `edition` omitted is the Scoreboard's default (`_resolve_edition`); `newest_edition` in the reply is
  `editions_repo.newest`, not that pick.

## Data and code

| Piece | Where |
|---|---|
| Compute | `backend/dashboards/track_record.py` |
| Route | `GET /dashboards/track-record` (`edition`, `include_late`) |
| Shared readers | `market_cap`, `baskets` price flags, `scoreboard_records` |
| Cache key | `(edition number, not exclude_late, clock)`; the warm-up names no edition |
| Frontend | `frontend/src/dashboards/track-record/` |
| Tests | `backend/tests/dashboards/test_track_record.py`, frontend `__tests__/` |

It reads the caller's workspace and imports only what the Scoreboard route imports (the lean prod image has no
`duckdb`).

## As-of behavior

Bars, share counts and flags read on the public clock (`serve_view`). An unmatured horizon is not counted.

## Rules not visible in code

- Entry is the next session's close, not a fill (`entry_index`): a night's call is first actionable then. The
  arm-night toggle is the Scoreboard's entry, so `exit_by` reconciles with its `forward_return`.
- Horizons are `SESSION_HORIZONS` bars of the name's own tape plus the last bar on or before `exit_by`, as traded with
  no dividends. Excess is the return minus the benchmark's over the same two dates, never a nearby bar; a hit is
  strictly above zero.
- A missing bar reads path ended only when the market has had that many sessions and the tape stops over
  `stale_days` behind; else maturing.
- Cap at the event: the as-traded close before `arm_date` times the share count public before it, on one split
  basis (`cap_cell`, `domain/market_cap.py`). The count is the Workbench's, else the filing cover's
  (`shares_at_event`), else unpinned: counted, never guessed. Why: a later filing is lookahead.
- Liquidity: 20-session mean close times volume before `arm_date` (`LIQUIDITY_BANDS`); under 20 bars is unpinned.
- `cell_stats` and its `recordView.ts` mirror run the same vectors (the page re-tables with no request).

## Known limits

- Cap and liquidity are approximate (a cover-count cap is not ratified).
- No null comparison: IWM and SPY only.
