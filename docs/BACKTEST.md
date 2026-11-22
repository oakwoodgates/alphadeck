# Backtest

The backtest re-runs today's call rules, or a dial overlay on them, over a frozen point-in-time mirror of
history and scores the arm episodes against realized prices. It answers one question: does the timing
algorithm behave sensibly across the whole book? It is not a P&L simulator, a strategy, a promotion
mechanism or a leaderboard. A CLI writes each run to its own directory; the steward reads runs and sweep
curves on the `/backtest` page. Read this doc before you change `backend/backtest/`, read a run, or quote a
number from one. The procedure for running a pass and writing it up is the `backtest` skill; the engine
underneath is [REPLAY](REPLAY.md).

## Code

Paths are under `backend/` unless they start with `frontend/`.

| Piece | Module |
|---|---|
| One run: export, replay, episodes, nulls, pooled report, ledger, manifest | `backtest/run.py` |
| Run identity, the five labels, the mirror hash | `backtest/manifest.py` (`LABELS`, `RUN_ID_PARTS`, `mirror_hash`) |
| Run directories and the registry | `backtest/store.py` |
| Timing and name-selection nulls | `backtest/nulls.py` |
| Pooled report | `backtest/pooled.py` (`SLICE_KEYS`, `HEADLINE_EXCESS`) |
| Sweeps, ladders, windows, agreement | `backtest/sweep.py`, `backtest/windows.py`, `backtest/config_overlay.py`, overlay files in `backtest/overlays/` |
| Parallel replay workers | `backtest/parallel.py` |
| Per-thesis ledger | `backtest/ledger.py` (built by `scoreboard/replay_snapshot.build_snapshot`) |
| Re-reads of finished runs | `backtest/rebench.py`, `backtest/pair.py`, `backtest/slice_nulls.py` |
| Which dials change the event stream | `backtest/dials.py` |
| Artifact reader for the routes | `backtest/artifact.py` |
| Routes and page | `app/routers/backtest.py`; `frontend/src/backtest/` |
| Engine and public clock | `backend/replay/`; `db/public_clock.py` |

Runs need the `.[replay]` extra (duckdb, pyarrow). The lean prod image lacks it, which is why a CLI writes
runs and the app only reads them.

## How it works

### What a run may claim

- No simulated row reaches `calls`. A run writes only its own directory; the routes read JSON artifacts and
  never write.
- The forward record is never tuned against. A sweep reads cross-window sign agreement as its stand-in for a
  hold-out.
- No dial moves in `domain/config.py` because of a run. Promotion is the operator's decision against run
  ids, by the rule in [CALL_LOGIC](CALL_LOGIC.md) for how a dial changes.
- A run replays one workspace's theses (`--workspace`, default the founding workspace) over the platform's
  facts.

### A run is an immutable artifact

The store layout is in the `backtest/store.py` docstring; a run directory also holds `pooled.json`,
`episodes.json` and `ledger.json` (JSON, because the lean image has no pyarrow), and the store root holds
`sweep.json` and `sweeps/`. The run id joins the parts named in `manifest.RUN_ID_PARTS` (timestamp, clock,
hypothesis slug, config hash, window start).

- A run directory is created fresh and never reused (`mkdir(exist_ok=False)`; a collision raises
  `RunDirExists`). Why: a run id in a write-up must mean one set of numbers forever.
- The registry (`index.json`) is rewritten atomically under a cross-process lock. Why: its job is counting
  trials, and a lost row reads as a run that never happened.
- `--config` (a dial overlay) requires `--hypothesis` and `--decision-rule`; the CLI refuses a dial moved
  without a pre-registered hypothesis.
- `GET /backtest/runs` derives `dial_trials` (how many runs touched each dial) from each row's
  `dials_moved`, and the manifest card shows it beside the dial: a result read without its trial count has
  lost its multiple-comparisons context.
- The manifest records the code sha, the dial blob and its hash, the clock, the pin, each thesis's roster
  (`ThesisEntry.member_ids`), what the export excluded, and timings.

### The clocks

`backtest.run` and `backtest.sweep` default to the record clock. Pass `--clock public` for any run you
intend to quote. The clock is a property of the mirror: a run given `--mirror-dir` inherits the mirror's
clock and refuses a `--clock` that disagrees (`MirrorClockMismatch`, raised before the run directory
exists). `known_at_mode` is derived from the clock, never a flag: `lockstep` on public (facts and roster
capped at each session's night), `pin` on record (facts capped at the run's one pin).

- A record mirror holds every row and version with `recorded_at` untouched, written in `(recorded_at, id)`
  order.
- A public mirror runs the same SQL as the live history read (`db/public_clock.public_versions_sql`,
  through `replay/export.export_snapshot(clock="public")`). Each table's public instant is defined once in
  `PUBLIC_TIME`, and the `db/public_clock.py` module docstring is its table; invariant 1 states the rule.
  The exported `recorded_at` is the public instant, with the ingest instant as `ingested_at`.
  `tests/replay/test_public_clock.py::test_the_public_mirror_is_row_for_row_the_live_public_read` pins the
  parity.
- A public export resolves versions per `(identity, public instant)`: a restatement survives at its own
  instant, and re-parses of one filing collapse to one row, chosen by `recorded_at`.
- The Form 4/A tables (`replay.export.AUX_TABLES`) and the held insider rows with the steward's decisions
  (`HOLD_TABLES`) export whole; on a public mirror their rows carry the `public_at` the reader gates on. A
  mirror exported before such a table existed reads none of it; re-export to read under the newer rule.
- A table with no declared clock is excluded whole and named in the manifest with the detectors it
  blinds. A row whose clock is NULL is excluded and counted (`manifest.TableCounts`). A Form 4 with no
  acceptance on any version is excluded and counted, the one difference from the live history read, which
  counts it from when we first held it. The page shows these counts; a caveat about the swept population is
  counted on the mirror, never on the source database.
- A mirror's manifest does not record which public-clock definition built it. Re-export rather than compare
  runs across a change to `db/public_clock.py`.

### Episodes and rosters

The unit is the arm episode ([REPLAY](REPLAY.md) owns it). Episodes are not independent: members of one
basket arm together, so the effective n is below the episode count. Each episode carries `co_arm_count`
(others newly armed that night) and `armed_count_that_night` (all armed, new and sticky), and
`co_arm_bucket` is a pooled slice.

The roster is point-in-time from 2026-09-15, when `basket_snapshot` history begins. Earlier sessions replay
today's basket; the manifest reports each thesis's fallback days (`harness.RosterSource`), and both nulls
draw from that same roster.

### The two nulls and the headline

The baskets were authored in 2026 over names that had already moved, so an absolute forward return is
biased upward and no clock fixes that. Every pooled metric therefore carries three comparisons:

- Excess over the thesis basket in the same window, two ways over one priced population: the equal-weight
  mean (`excess`, what a basket position earned) and the median (`excess_vs_basket_median`, what the
  typical member did). The median is the headline (`pooled.HEADLINE_EXCESS`, stored on the report as
  `headline_excess`). Why: a thematic basket is right-skewed by construction; one runner moves the mean
  and leaves the typical member where it was.
- The timing null: the same name entered on a randomly drawn session of the run.
- The name-selection null: another member of the same roster on the same day.

`--null-draws` sets K (`nulls.DEFAULT_DRAWS`). K is part of a pass's pre-registration; changing it breaks
comparability. Draws are without replacement, or the whole population when it is smaller than K. The seed
defaults to the run id and is the pass id in a sweep; each episode's sub-seed comes from its identity
(`nulls.episode_seed`), so adding an episode never reshuffles another's draws. The null phase runs serially;
`--workers` parallelizes the replay only.

The short-window trap: on a window under about 30 sessions, a `vs_timing` close to `actual` means there was
no comparison, not that there is no edge. The page says so (`timingNullCaveat` in `PooledPanel.tsx`). The
threshold is not tuned to make the caveat fire: that would fit the instrument to the answer.

### Pooled, never per thesis

The unit is the algorithm. Slicing outcomes by thesis ranks ideas, which invariant 4 forbids.

- `PooledReport` has no thesis field. Its slices are `SLICE_KEYS` only (key-1 source, confirmation grade,
  co-arm bucket, close reason). `tests/backtest/test_pooled.py` walks the payload for a thesis identifier,
  and `tests/app/test_backtest_routes.py` walks the wire payload.
- The per-thesis view exists only as the run's ledger: a drill-down that opens collapsed below the pooled
  panel, in name order fixed by the writer (`tests/backtest/test_ledger.py`). It reuses the Scoreboard's
  wire shape (`ScoreboardReplayThesisOut`) and components; nothing flows back to the Scoreboard.
- The ledger serves the Scoreboard's eligible metric set (matured, uncensored). `metrics.json`, which scores
  every outcome, stays in the run directory unserved, so the page never shows two differently gated numbers.

### Sweeps: a curve, never a winner

`backtest.sweep` runs one point per dial setting over one frozen mirror exported once for the pass, so a
delta is the dial's and not the tape's. It reports a curve, a plateau band and per-window agreement; it
never names a best point, and the points render in dial order.

- A pass tiles its span into disjoint, gapless windows (`windows.DEFAULT_WINDOW_DAYS`) and runs every point
  once per window; the point's metric pools across them. The `backtest/windows.py` docstring gives the
  reasons: windows are separate measurements to agree across, a dead job costs one window, and memory stays
  bounded. `--concurrency` buys a fraction, not a factor.
- Each point carries `window_deltas`, `window_status` (`moved_up`, `moved_down`, `unchanged`,
  `unmeasurable`) and three agreement fields. `sign_agreement` is the original rule, read as registered
  and never rewritten. `strict_sign_agreement` needs every window to move strictly the same way.
  `strict_measurable_agreement` asks the same of the windows that held an episode the dial could touch (at
  least two), and reports `n_unmeasurable`. An unchanged window withholds agreement; an unmeasurable one is
  excluded and counted. The band keys on `--plateau-rule` (`sweep.DEFAULT_PLATEAU_RULE`); a pass may
  pre-register another rule before launch, never after.
- The baseline runs once per window and is shared: variants deduplicate by `config_hash` across every
  curve of the pass, and shared points carry `runs_shared`.
- `--ladder dial=v1,v2` gives one curve per dial in one pass; `--grid` gives one cartesian curve, and the
  two are refused together. `--overlay-ladder FILE` gives one curve of overlays for dials that move
  together; `config_overlay.load_overlay_ladder` validates every point before the export.
- Runs carry the pass's `--hypothesis` and `--decision-rule`; each curve carries its own, overridable with
  `--ladder-hypothesis` and `--ladder-decision-rule`. An override naming a dial with no ladder is refused.
- Every run carries the `pass_id`. Each curve is kept at `sweeps/<pass_id>-<dials>[-<slice>].json`;
  `sweep.json` holds the last curve written.
- A record sweep and a public sweep are two experiments, never one curve. The mirror directory and the run
  id both carry the clock, so the two tapes sit side by side.

**Slices.** A dial that touches a small family cannot move a median over the whole pool; a flat pooled curve
for it is arithmetic, not evidence. `--metric-slice ATTR=VALUE` (or `--ladder-metric-slice` per curve)
reads a curve on one family, and then every figure on that curve is the slice's. Only `SLICE_KEYS` may be
sliced; the guard is on `Ladder` (`MetricSliceError`), so a library caller cannot skip it. A slice that
cannot be computed raises instead of returning an empty, flat-looking curve. `--metric-slice` with
`--ladder` is refused. A sliced curve is a new pre-registered hypothesis; re-reading a finished pass on a
slice is a labeled diagnostic.

**Pairing.** `backtest.pair` pairs a point's episodes with the baseline's on `(thesis_id, security_id,
arm_date)` and reports the shared set, how many the dial moved (`n_changed`), the median change among those
and what the dropped and added episodes were worth. The same fields ride every curve point. Read
`n_changed` beside the paired median: an untouched majority can hold the median at zero while the dial moved
many episodes. Pairing fixes the entry, not the horizon: a liveness dial also moves `exit_by`, so a shared
episode can be measured over different forward windows. The paired block is a diagnostic; the decision rule
keys on the pooled delta and its agreement.

**Which dials a horizon sweep touches.** `exit_by` comes from the conviction events' liveness horizons, and
`arm_until` from the confirmation events' (`calls/assembler.py`; the dials are in `domain/config.py`). Sweep
them as two families. A short horizon can stop a known arm from happening, so a sweep whose low end asserts
that episodes exist may fail for a correct reason.

### Mirror reuse and resume

- A fresh pass refuses to export over an existing mirror (`MirrorExists`) before writing anything. Why: the
  mirror path derives from pin, window and clock, and a second export would leave the first pass's runs
  citing a hash their directory no longer has.
- `--mirror-dir` sweeps an existing mirror: no export, the clock comes from the tape, the hash is recorded
  on every run and curve, and the report says `mirror_reused`. A directory without a mirror manifest is
  refused.
- `--resume PASS_ID` skips every `(config, window)` already registered with readable outcomes, re-runs the
  rest and assembles the curves from the union. It never re-exports. It refuses when a completed run cites
  another mirror hash, when the mirror vanished after anything completed, when the requested windows do not
  cover the windows already run, or when the clock differs. The completed runs of a dead pass already count
  as trials.
- One pass runs from one venv. The Parquet writer stamps its pyarrow build into every file, so rows that
  are identical can hash differently across venvs.

### Rebench and slice nulls

- `backtest.rebench` recomputes both excess figures from a run's own artifacts and mirror. It writes
  nothing, and refuses a run whose mirror hash differs, whose roster cannot be rebuilt, or whose recompute
  does not reproduce the stored numbers. `--no-db` works for manifests that carry `member_ids`.
- `backtest.slice_nulls` re-draws one family's timing and name nulls off the frozen mirror and refuses any
  run whose whole-pool re-draw does not reproduce its stored `pooled.json`. It reads no database.

### Re-reading a pass after a correction

A data or code correction leaves every earlier conclusion measured on the uncorrected inputs. The re-read
re-runs the same pre-registered measurement (tiling, pin, clock, K, seed, dials); it is a re-measurement,
not a new trial. Separate the correction from a growing tape by crossing old and new code with old and new
mirrors, and quote the two effects separately. A recent window is a current reading: episodes whose
`exit_by` runs past the tape's edge are re-measured as the tape grows.

## Sliced by market cap

`PooledReport` carries no market-cap slice (`SLICE_KEYS`). A market-cap tier is a property of the company
at the event, so a report sliced by it still pools across theses and ranks no thesis (invariant 4).
Event-outcome reports follow the rule and the bands in `CLAUDE.md` (`domain/market_cap.py` defines the
bands), with presentation in [DASHBOARDS](DASHBOARDS.md) rule 6.

## What the mirror hash promises

`mirror_hash` is a sha256 over the sorted `(file name, size, sha256)` of every `*.parquet` in the mirror
folder (`backtest/manifest.py`).

- The same stored rows written by the same pyarrow build give the same hash, whatever plan Postgres picks.
  Every export file is written in a total order: record files by `replay/export.py::_EXPORT_ORDER`, public
  fact files one row per `(identity, public instant)` sorted on it
  (`tests/replay/test_export_determinism.py`). The same hash on another machine is not promised: pyarrow is
  not pinned.
- Record mirrors exported before 2026-11-13 do not re-export to the same hash: their files came out in
  plan order. Their stored hashes still verify their stored files, so `rebench`, `slice_nulls` and a
  resume accept them. Do not re-export or re-hash an old mirror, and do not read a record curve from before
  that date and one from after it as one tape. Public mirrors were already ordered and did not change.
- When a run exports its own mirror, the mirror folder is the run folder and the hash is taken after
  `episodes.parquet` and `outcomes.parquet` are written, so it covers the results too. A shared mirror
  (a sweep, `--mirror-dir`) holds no such files, so its hash covers the facts alone. This stays as built:
  changing what is hashed would make `rebench` and `slice_nulls` refuse every existing self-exported run.

## Contracts

- A run directory never changes after it is written.
- The five labels are backend-authored (`manifest.LABELS`), ride every `/backtest` response and render
  verbatim (`BacktestLabels.tsx`); the page composes no caveat of its own.
- A missing store, a missing run, a half-written directory, an unreadable file and a selection that matches
  nothing all answer `available: false` with a 200, never a 404 or a 500.
- The router writes nothing and imports no writer
  (`tests/app/test_backtest_routes.py::test_the_backtest_router_never_writes`), and it imports without the
  replay extra (`test_the_backtest_serving_path_imports_without_the_replay_extra`).
- The scorecard drawer mounts without an as-of, so no live price window or display signal enters a run.
- Board, Cockpit and Scoreboard do not link to `/backtest`, and it never notifies.
- Wire shapes: the `Backtest*` models in `backend/openapi.json`.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /backtest/runs`, `/runs/{run_id}`, `/sweeps`, `/sweep` | steward; a plain user gets 403 | `require_super_admin` on the router (`_STEWARD`, `app/main.py`); `tests/app/test_auth_gate.py::test_steward_routes_carry_require_super_admin` |
| The `/backtest` page and nav tab | steward; a plain user sees one line | `BacktestRoute` in `frontend/src/App.tsx`; the `steward` nav entry in `AppHeader.tsx` |
| `backtest.run`, `backtest.sweep` | whoever runs the CLI with the replay extra; reads the database in `DATABASE_URL`, writes only the store | nothing in the app; `db/session.py` defaults to prod, so name dev. Agents: the `backtest` skill |
| `backtest.rebench` | same; the database only for manifests without `member_ids` | `--no-db` refuses those |
| `backtest.pair`, `backtest.slice_nulls` | anyone with the store; artifacts only | none needed |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| Store root | `backtest.store.DEFAULT_ROOT`; `--out-root` (run, sweep, pair), `--root` (rebench, slice_nulls) | not a setting; the backend sees a store only through a read-only bind: `./data/backtest` (dev, sig overlays), the frozen archive `./data/backtest_prod` (`docker-compose.prod.yml`), none in the base file |
| Code sha on the manifest | `Settings.image_sha`, else the checkout's git HEAD (`manifest.resolve_code_sha`) | set `ALPHADECK_IMAGE_SHA` in the shell that runs the CLI |

## Known limits

- Every run is labeled "public clock" (`LABELS[0]` is fixed) even on the record clock, the CLI default.
  The manifest card shows the run's real clock. Listed for the operator.
- Rosters are point-in-time only from 2026-09-15; the rest of the thesis definition and the security master
  are read as they are now ([REPLAY](REPLAY.md)).
- A ratified catalyst counts from its `valid_from`; the decision to record it is hindsight (the second
  label). A DOE award counts from its record date (`ingest/doe/feed.py`), late by design: the source
  carries no announcement date.
- The mirror manifest and the run manifest share the name `manifest.json`; when a run exports its own
  mirror both land in one folder, and `replay.export.read_mirror_manifest` reads either shape.

## Related

- [REPLAY](REPLAY.md): the engine, the mirror and the episode unit.
- [INVARIANTS](INVARIANTS.md): 1 (the public clock) and 4 (no thesis ranking).
- [CALL_LOGIC](CALL_LOGIC.md): how a dial changes.
- [SCOREBOARD](SCOREBOARD.md): the record and the shared ledger components.
- [DASHBOARDS](DASHBOARDS.md) rule 6: how event-outcome reports are presented.
- The `backtest` skill: run, resume, reuse a mirror, write up a pass.
