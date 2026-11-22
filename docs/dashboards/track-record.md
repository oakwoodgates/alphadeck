# Track Record by Cap Tier

`track-record` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

What the tape did after each arm episode the Scoreboard records, by market-cap tier at the event: per cell the
horizon, comparison (IWM, SPY or raw), n and how often it held. It implements rule 6: what happened, never that a
signal works.

## Who counts

- Episodes are the Scoreboard's, unchanged (`derive_episodes`): the first night a member is in `armed_members`, keyed
  (card, name, `arm_date`). Archived cards count; reconstructed nights and episodes armed after the as-of do not.
- Left out of every cell and counted: `censored_start`, `ingest_flagged`, a flagged price history, a stopped feed on
  a company still trading. A company that ended (`tape_health.classify_stop` holds a `closed_at` by the as-of) with a
  stopped feed as its only flag is counted; horizons past its last bar read path ended.
- `edition` omitted is the Scoreboard's default (`_resolve_edition`, [SCOREBOARD](../SCOREBOARD.md)): the newest
  published edition, the record when none is published, `scoreboard_default_edition` honored. `newest_edition` in
  the reply is `editions_repo.newest`, not that pick: the selector offers "newest published".

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

Bars, share counts and flags read on the public clock (`serve_view`). A horizon not matured by the as-of is not
counted.

## Rules not visible in code

- Entry is the next session's close, not a fill (`entry_index`). Why: a night's call is first actionable next session. The
  arm-night toggle is the Scoreboard's entry, so `exit_by` reconciles with its `forward_return`.
- Horizons are `SESSION_HORIZONS` bars of the name's own tape, plus the last bar on or before `exit_by`; as traded,
  no dividends. Excess is the return minus the benchmark's over the same two dates, never a nearby bar. A hit is
  strictly above zero.
- A missing bar reads path ended only when the market has had that many sessions and the tape stops over
  `stale_days` behind; else maturing.
- Cap at the event: the as-traded close before `arm_date` times the share count public before it, on one split
  basis (`cap_cell`; bands in `domain/market_cap.py`). The count is the Workbench's, else the filing cover's
  (`shares_at_event`: 10-Q filers, within `COVER_MAX_AGE_DAYS`, over `COVER_MIN_SHARES`), else unpinned: counted,
  never guessed. Why: a later filing is lookahead.
- Liquidity: 20-session mean close times volume before `arm_date` (`LIQUIDITY_BANDS`); under 20 bars is unpinned.
- Rows: five tiers, unpinned, then pooled last (all counted, so rows add up). A cell under `MIN_N` prints n only.
- `cell_stats` and its mirror in `recordView.ts` run the same vectors. Why: the page re-tables with no request.

## Known limits

- The record is young: long horizons mostly read maturing.
- Cap and liquidity are approximate (a cover-count cap is not ratified); the bands are a first cut.
- No null comparison: IWM and SPY only.
