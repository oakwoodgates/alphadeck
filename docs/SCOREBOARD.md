# The Scoreboard — SCORE, the forward trust loop

The sixth stage: forward attribution over the platform's own record. Two tracks in v1 — **the
platform's calls** (the daily call-of-record, scored against realized prices on its own yardstick)
and **the operator's decisions** (the append-only decision log, joined to the episodes it answered).
The follow-blindly counterfactual track and its deltas are **v2** (additive on the same computation);
the immediate follow-up after v1 is surfacing **replay's historical episodes alongside** the live
record (clearly separated — the record stays clean). "The platform feeds itself" became true at M2;
*this* is what makes "validated forward" true.

Status: **SB1 (the scoring engine + CLI) built.** SB2 = `GET /scoreboard` + gated metrics; SB3 = the
operator-track join; SB4 = the FE view. This doc grows with the slices.

## The one rule everything hangs on

**The record is the scoring source — never a recompute.** The Scoreboard reads the immutable `calls`
log (what the platform actually said, when it said it) via `calls_repo.latest_for_thesis` (the
final card per as-of), and scores those cards. Re-deriving past calls with today's code/dials is
replay's job (`docs/REPLAY.md` — the historical twin); attribution's source is the record
(`docs/BOARD.md`). Consequences, all deliberate:

- **No backfill.** The record began when the daily cron first wrote (2026-07-10 in production);
  earlier history is replay's domain. An empty early Scoreboard is the honest launch state.
- **Censored starts.** An episode already armed on its thesis's *first recorded card* has an
  unknowable true arm date (`censored_start`) — shown in the ledger ("record began mid-arm"),
  **excluded from arm-anchored metrics**, never reconstructed.
- **Gaps are fine.** The log is dense per cron day (a new as-of always appends; `record_if_changed`
  dedups same-as-of only); weekends/downtime leave gaps, but episode boundaries stay exact because a
  membership change always recorded a row that day. `derive_episodes` consumes the gapped timeline
  as-is.

## The scoring unit and its flags

The unit is replay's **arm episode** (`replay/episodes.py::derive_episodes`, reused as-is): a
contiguous run of one basket member in `armed_members`, keyed `(thesis_id, security_id, arm_date)`,
scored by `replay/scoring.py::score_episode` over `[arm_date, exit_by]` — the system's own hold
horizon, the honest yardstick. The live additions (`scoreboard/schema.py`) are honesty about the
record, per episode:

| Flag | Meaning |
|---|---|
| `status` open/closed | open = still armed at the record edge ≤ asof (replay's `window_end`, read live); its return is a RUNNING return, not a verdict |
| `matured` | the episode's own `exit_by` has elapsed (≤ asof). **Metrics judge only matured, non-censored episodes** — a running return must never drift inside `false_arm_rate` before the claim's own deadline |
| `censored_start` | armed since before the record began (above) |
| `triggers_at_arm` | the arm-date card's member trigger evidence — the WHY rides every row (invariant #6) |

`Outcome.insufficient_prices` on a fresh arm means "no bar on/after the arm yet" (an arm recorded
Friday has no entry bar until the next trading close lands) — awaiting data, not an error.
`truncated` = the hold horizon ran past the available (asof-capped) bars: the running-return shape.

## Prices: the Postgres twin, asof-capped

`scoreboard/prices.py::PgRealizedPrices` is the Postgres twin of replay's DuckDB `RealizedPrices` —
the same three-method surface `score_episode` duck-types against, same latest-version-per-day dedup
and `recorded_at DESC, id DESC` tiebreak, plus two caps: `d <= asof` (the request as-of — scrubbing
the Scoreboard back can never see a later bar; open episodes' returns run to the last bar ≤ asof)
and `recorded_at <= known_at` (default now — a re-versioned/restated bar's latest version wins:
score against the corrected tape). A parity test (`tests/replay/test_pg_prices_parity.py`) pins the
two readers row-for-row equal, including the identical `Outcome`.

This required one 2-line enabler in replay: `replay/scoring.py`'s `import duckdb` moved under
`TYPE_CHECKING` (duckdb is the optional `.[replay]` extra, absent from the lean prod image; the
import was annotation-only). `tests/scoreboard/test_lean_import.py` pins that structurally.

## Reading it

```powershell
python -m scoreboard.run --asof 2026-07-11              # the human-readable ledger
python -m scoreboard.run --asof 2026-07-11 --json       # the full analytical dump
python -m scoreboard.run --asof 2026-07-11 --exclude-archived
```

**Archived theses are INCLUDED by default** — archiving stops accrual (the cron skips archived); it
never erases the record. `--exclude-archived` (and the endpoint param, SB2) is the explicit,
reversible filter. Compute-on-read: the whole path owns no tables and writes nothing
(`test_scoreboard_writes_nothing` counts the tables to prove it), no LLM anywhere.

A thesis with an unreadable historical card (the log outlives `CallCard` schema changes;
`DomainModel` is `extra="forbid"`) surfaces a per-thesis `error` and never blanks the board —
keep `CallCard` evolution **additive-only** so old cards stay loadable.

## Deliberately NOT here (v1)

Follow-blindly track + deltas (v2) · replay-history-alongside (the immediate follow-up) · a second
metrics-led view behind a toggle (v2, once n accrues) · charts · persistence/caching of scores ·
cron changes · notifications · the second recalibration (unlocked by this, not part of it) ·
a transaction-time (`known_at`) scrub parameter.
