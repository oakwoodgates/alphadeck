# Replay

The replay engine re-runs the real call pipeline as of past dates over a Parquet mirror of the facts, turns
each thesis's call timeline into arm episodes, and scores them against realized prices in a separate pass.
It is the shared engine under the Scoreboard (episodes and scoring of the recorded calls), editions
(re-runs over a public-clock mirror) and the backtest (runs, nulls, sweeps). It has no page of its own; the
machine and the CLIs above it call it. Read this doc before you change `backend/replay/` or reason about
no-lookahead in research. What a run may claim, the clocks and the mirror hash are in
[BACKTEST](BACKTEST.md).

## Code

Paths are under `backend/`.

| Piece | Module |
|---|---|
| Export Postgres to the mirror, on the record or the public clock | `replay/export.py` (`export_snapshot`, `_EXPORT_ORDER`, `AUX_TABLES`, `HOLD_TABLES`) |
| As-of reader over the mirror | `replay/pit.py` (`ReplayPointInTimeData`, `connect_mirror`) |
| Replay loop and the per-session roster | `replay/harness.py` (`replay_all`, `RosterSource`, `trading_sessions`) |
| Arm episodes and close reasons | `replay/episodes.py` |
| Forward scorer | `replay/scoring.py` (`RealizedPrices`) |
| Metric set | `replay/metrics.py` (`MIN_N`) |
| Snapshot, episode and outcome models | `replay/schema.py` (`CallSnapshot`, `Episode`, `Outcome`) |
| Which key-1 detector turned an arm | `replay/key1.py` |
| Bare engine CLI | `replay/run.py` |
| A dial-variant comparer | `replay/compare.py`, imported only by `tests/replay/test_compare.py` |

Consumers: `scoreboard/record.py`, `scoreboard/assemble.py`, `scoreboard/diff.py`; `editions/engine.py`,
`editions/nightly.py`, `editions/preview.py`; `backtest/`. The engine needs the `.[replay]` extra (duckdb,
pyarrow).

## How it works

### The lookahead boundary

At as-of T the detectors see only facts with `valid_from <= T` and `recorded_at <= known_at`. The scorer
reads the forward window `(T, exit_by]`. No read path carries the scorer's data into an as-of call:

- `ReplayPointInTimeData` is as-of capped and constructor-bound: every accessor filters on its `asof` and
  `known_at`, latest version per identity, as `db.bitemporal` does live. No accessor can widen `asof`. It is
  the only reader the replay loop uses, and it feeds the same detectors and `assemble_call` as the live
  path (`pipeline.core.assemble_from_pit`).
- `RealizedPrices` is a different class with a disjoint method set, forward-windowed and uncapped. It is
  the only reader the scorer uses, and the scorer's signature takes no point-in-time reader.
- The two readers have opposite time semantics and no shared base.
  `tests/replay/test_scoring.py::test_lookahead_boundary_is_structural` reads the import graph: the scorer
  never imports `replay.pit`, and the loop never imports `RealizedPrices`.

### Parity is the integrity bar

The mirror is trusted only because tests hold it equal to the live read:

- `tests/replay/test_pit_parity.py`: the mirror's accessors equal the live `as_of` read row for row over the
  full column set, after a value normalizer, so a dropped column fails.
- `tests/replay/test_pit_lookahead.py`: the bitemporal no-lookahead tests, run against the mirror on both
  axes.
- `tests/replay/test_pit_first_knowability.py`: the fundamentals first-knowability annotation comes from one
  pure function on both engines (`db.bitemporal.first_knowable_versions`).
- `tests/replay/test_pit_insider_amendments.py`: Form 4/A resolution runs the one pure resolver
  (`domain.insider_amendments`) on both engines over the mirrored amendment tables. A mirror with none of
  those tables reads unresolved; a partial set refuses to open.
- `tests/replay/test_public_clock.py`: a public mirror is row for row the live public-clock read.
- `tests/replay/test_thesis_catalyst_mirror.py`: the counting catalysts on both clocks.

Parity and the two no-lookahead suites passing is the bar for any change to `replay/`.

### The mirror and the pin

`export_snapshot` writes a one-shot, truncate-and-rewrite snapshot of every fact table for the workspace and
the data tenant: rebuildable, never authoritative. On the record clock it keeps every row and version with
`recorded_at` untouched, in `_EXPORT_ORDER`; the public clock is in [BACKTEST](BACKTEST.md). A mirror
exported before a later fact table existed opens with that table empty (`pit._LATE_FACT_TABLES`).

The pin is the `known_at` ceiling, applied at read time, so one mirror reproduces the live `as_of` read for
any pin. The roster resolves per session at `known_at_for_asof(T, now=pin)`. On the record clock the facts
stay capped at the one pin, so the roster can be older knowledge than the facts, never newer. On the public
clock both are capped per session (`lockstep`).

The same mirror, pin, window and `cfg` give value-identical timelines and scores; byte identity across
Parquet writer versions is not promised. `cfg` is a parameter of every harness function, so a sweep varies
only the dials.

The mirror reader ports the live reader's per-request memo, basket prefetch and read bounds. An equivalence
test on this path must assert row counts per security, not that the read ran: the mirror stores uuid columns
as VARCHAR, so a `str` versus `UUID` key mismatch returns nothing and looks fast
(`tests/replay/test_pit_prefetch_equivalence.py`).

### The arm episode

Armed is sticky, so scoring per thesis and as-of would count one decision many times. The unit is the arm
episode: a contiguous run in which one basket member is armed, keyed `(thesis_id, security_id, arm_date)`.
It is per member, so name selection can be scored. An episode is measured over `[arm_date, exit_by]` on
realized closes; `exit_by` is the signal-validity horizon, not a sell instruction. A re-arm is a new
episode, and a thesis that never arms has none.

`episodes._close_reason` assigns one of `arm_until_lapsed`, `conviction_aged_out`, `managing`,
`window_end` and `dearmed_other`. A clock has run out on the de-arm night when that night's next session is
past it, the call path's own lapse test (`signals.common.entry_signal_is_live`). `managing` is expected to
be zero in pure replay: historical facts carry no user fills.

### The metric set

Each metric tests part of the product's claim: timing, preserving the early edge, name selection. There is
no "was the thesis right" metric, by design.

| Metric | Tests |
|---|---|
| `arm_timing_forward_return` | the return over the validity window from the arm |
| `early_vs_armed_delta` | warm-entry return minus arm-entry return: does the gate clip the early edge? |
| `grade_confidence_calibration` | do higher grades and setup strength track better outcomes? A hypothesis to test, never a probability |
| `name_selection_lift` | did the ranked headline beat the rest of the basket? |
| `false_arm_rate` | arms whose realized return was adverse |
| `withheld_arm_counterfactual` | the move during windows the gate withheld |
| `exit_by_vs_rollover` | does the edge persist to `exit_by` or decay earlier? |

Every metric carries `n` and `insufficient_n`. `MIN_N` is a presentation safeguard against over-reading tiny
aggregates, not an evidence threshold; clearing it validates nothing. Calibration needs `MIN_N` in each of
at least two grade buckets.

## Contracts

- `replay/schema.py` models are the Scoreboard's episode vocabulary; the Scoreboard and editions build
  their records from them.
- Every artifact is written on every run, an empty run included, with a schema declared from the models
  (`replay.run.arrow_schema`). A field type with no declared Arrow type raises, so changing an artifact's
  shape is a deliberate edit.
- Export files are written in a total order (`_EXPORT_ORDER`); the hash this enables is defined in
  [BACKTEST](BACKTEST.md).

## Who can do what

| CLI | Who | Enforced by |
|---|---|---|
| `python -m replay.run` (flags: `--help`) | anyone with the replay extra, from `backend/` or in the sig stack's backend container. It reads the database in `DATABASE_URL`, exports a record-clock mirror and writes only `--out` | nothing in the app; `db/session.py` defaults to the prod database, so name another. An agent opens no database connection outside pytest (`CLAUDE.md`) |

`replay.run` is the bare engine: a mirror, `episodes.parquet`, `outcomes.parquet` and `metrics.json`, and
no identity, nulls or registry row. Numbers to quote come from `backtest.run` and `backtest.sweep`.

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| Detector switches for a lab run (`--<switch>`, `--no-<switch>`) | the switch table in `replay/run.py`; defaults in `domain/config.py` | no; a bare run inherits the production defaults |

## Known limits

### Known limitation: the thesis definition is read as it is now

- The roster replays point-in-time: each session resolves the basket through `thesis_repo.get_asof`. The
  snapshot history (`basket_snapshot`) begins 2026-09-15; earlier sessions use the live roster, never an
  empty basket, and `RosterSource` reports each thesis's fallback days.
- The counting catalysts are a mirrored fact table (`fact_thesis_catalyst`), so they replay point-in-time.
- The narrative, the thesis's catalyst list (`Thesis.catalysts`, which feeds the card's catalyst surface),
  the kill criteria and the security master are read from the current store.

The canonical statement is in [INVARIANTS](INVARIANTS.md) "Known gaps".

## Related

- [BACKTEST](BACKTEST.md): runs, clocks, nulls, sweeps and the mirror hash.
- [SCOREBOARD](SCOREBOARD.md): the record scored with these models.
- [INVARIANTS](INVARIANTS.md): invariant 1 (no lookahead on the public clock).
- `db/public_clock.py`: the per-table public instant.
