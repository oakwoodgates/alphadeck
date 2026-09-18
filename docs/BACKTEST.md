# BACKTEST.md — the research surface: runs, the public clock, the nulls, and `/backtest`

> Repo path: `docs/BACKTEST.md`. Code: `backend/backtest/` (the RUN — identity, overlays, nulls, pooling,
> sweeps, the ledger) over `backend/replay/` (the ENGINE — the as-of harness, episodes, the forward scorer);
> `backend/app/routers/backtest.py` + `frontend/src/backtest/` (the surface). Install with
> `pip install -e ".[replay]"` (duckdb + pyarrow) — the lean prod image deliberately lacks it, which is why
> a run is written by a CLI and only READ by the app.
>
> `REPLAY.md` is the instrument. This is what we do with it, and what we are allowed to claim from it.

---

## What this is, and what it is not

Replaying the call algorithm over history, with editable dials, to answer ONE question: **globally, does the
timing algorithm behave sensibly?**

It is **not** a P&L simulator, not a strategy, not a promotion mechanism, and not a leaderboard. Nothing on
this surface ranks a thesis, and no dial moves in `domain/config.py` because of a run. Promotion is a
separate operator call made against run ids (the evidence/policy seam).

**No simulated row ever reaches `calls`.** A backtest writes only into its own run directory. The container's
mount of that directory is read-only; the route is structurally read-only (a test reads its import graph).

## The five permanent labels

Every response from `/backtest` carries these, backend-authored (`backtest/manifest.py::LABELS`), rendered
**verbatim** by the front end — it composes nothing, so the caveat on the page and the caveat in the artifact
cannot drift:

1. **Public clock** — facts enter when they became public, not when this system ingested them.
2. **Counterfactual universe** — the baskets were authored in 2026 over names that had already moved.
3. **Survivorship** — the roster is today's, not history's, wherever no `basket_snapshot` reaches back.
4. **Adjusted closes** — the tape is split/dividend adjusted.
5. **A recompute, never the record** — and, explicitly, an operator-ratified fact is modeled as public on its
   ANNOUNCEMENT date, so the ratification itself is hindsight (FLAG-H, below).

### FLAG-H — the ratification is hindsight, and the label says so

MEASURED: `fact_catalyst.valid_from` spans 2010-03-23 … 2026-02-09 while every row's `recorded_at` is
2026-06-10. A catalyst the operator ratified in 2026 is therefore modeled as knowable in 2010. The DATA axis
is right — the announcement date IS the public date — but the DECISION to record it is hindsight. Eight rows
total, so the impact is negligible; it is labeled rather than corrected because the honest fix (a ratification
clock) does not exist and pretending otherwise would be worse than saying it.

## A run is an immutable, addressable artifact

```
data/backtest/
  index.json                     the registry: one summary row per run, newest first
  sweep.json                     the latest sweep curve (see the asymmetry below)
  runs/<run_id>/
    manifest.json                what this run IS — code, dials, clock, pin, rosters, exclusions
    episodes.parquet             the arm episodes           } analysis
    outcomes.parquet             those episodes scored      }
    metrics.json                 the engine's claim-tied metric set, over ALL outcomes
    pooled.json                  the algorithm-level view with its two nulls
    episodes.json                the serving copy of the episodes (JSON: the lean image has no pyarrow)
    ledger.json                  the per-thesis drill-down, in the Scoreboard's own vocabulary
    *.parquet                    the frozen fact mirror (absent when a sweep SHARES one)
```

A run id is `<utc timestamp>-<clock>-<hypothesis slug>-<config short hash>` — sortable first (a directory
listing is a timeline), then legible (which axis, which experiment), then precise (which dials).

Two rules are the whole design. **A run directory is created fresh and never reused** (`mkdir(exist_ok=False)`),
so a run id in a PR description means exactly one set of numbers, forever. A collision raises `RunDirExists`
naming the id, the path, every component that had to agree for it to happen, and what to change. **The registry is rewritten
atomically**, because a listing truncated mid-write reads as "runs vanished" — the worst failure for an
artifact whose job is to count trials.

```bash
python -m backtest.run --start 2025-09-01 --end 2026-09-14
python -m backtest.run --start ... --end ... --config overlays/h5-horizon-090.json \
    --hypothesis "H5: the exit_by horizon is the timing lever" \
    --decision-rule "adopt only on a plateau with sign agreement in 2+ disjoint sub-windows"
```

`--config` REQUIRES both `--hypothesis` and `--decision-rule`. A dial moved without a pre-registered
hypothesis and decision rule is not a measurement, and the CLI refuses it.

### Count your trials

`index.json` carries `dials_moved` per run, and `/backtest/runs` derives `dial_trials` from it: how many runs
have touched each dial. A result read without knowing how many times its dial was swept is a result read
without its multiple-comparisons context, so the count sits on the manifest card beside the dial.

## The public clock

Three clocks exist: **event time** (`valid_from`), **public time** (when anyone could have known —
`accepted` / `filed` / the bar date), **system time** (`recorded_at`, when THIS system learned it). The live
gate keys on `recorded_at` and is never touched (INVARIANTS §4; `db/bitemporal.knowability_expr` is not
opened by any of this).

`clock=public` is a MIRROR-side rewrite in `replay/export.py`: the exported `recorded_at` is replaced by the
disclosure instant, so the reader, the gate and `knowability_expr` stay byte-identical in both modes. The
registry, VERIFIED on `alphadeck_dev`:

| PIT accessor | fact table | public-clock column | type | NULLs (raw rows) | NULLs (latest-version grain) |
|---|---|---|---|---|---|
| `price_history` | `fact_price_eod` | `d` | date | 0 / 369,039 | 0 |
| `fundamentals_facts` | `fact_fundamentals` | `valid_from` (there is no `filed`) | date | 0 / 120,626 | 0 |
| `corporate_event_facts` | `fact_corporate_event` | `filed` | date | 0 / 62,046 | 0 |
| `activist_stake_facts` | `fact_activist_stake` | `filed` | date | 0 / 24,090 | 0 |
| `insider_txns` | `fact_insider_txn` | `accepted` | timestamptz | **280,578 / 595,376 = 47.13 %** | **2,128 / 316,374 = 0.67 %** |
| `catalyst_facts` | `fact_catalyst` | `valid_from` | date | 0 / 6 | 0 |
| `dilution_facts` | `fact_dilution` | `valid_from` | date | 0 / 1 | 0 |
| `theme_conviction_facts` | `fact_theme_conviction` | `valid_from` | date | 0 / 1 | 0 |

A table with no declared clock is **excluded WHOLE** and named in the manifest together with the detectors it
thereby blinds. A ROW whose clock column is NULL is excluded and COUNTED. Both counts ride the manifest and
the surface renders them under "what this run could not see" — the size of the hole is part of the result.

Supporting measurements worth keeping: `accepted` never precedes `recorded_at` (0 of 314,798 non-null rows);
insider disclosure lag on the latest grain since 2024 is p50 **2 d**, p90 **5 d**, p99 **83 d**, max 474 d;
`recorded_at::date > d` for 98.6 % of price rows, so the public clock does real work on the price axis; and
`clock=public` is a **no-op for fundamentals** (`recorded_at == valid_from == filed` on all 120,626 rows),
which matters because H1 is a fundamentals screen.

**The version pile-up, and the random tiebreak it used to hide (FLAG-A/B).** For insider and price, EVERY
re-version of a fact carries the SAME public clock (our own repairs and refetches, not new filings):
`fact_insider_txn` 44,982 identity-groups tie on `(identity, clock)`, `fact_price_eod` 17,373 — and 8,947 of
the price ties **differ on `close`**. Under a naive public-clock rewrite the reader's
`ORDER BY recorded_at DESC, id DESC` would have been decided by a random UUID. The export therefore resolves
versions at export time, partitioned by `(identity, clock_value)`: every distinct public-clock version
survives (a restatement appears at its own `filed`) while pure re-parses collapse to our best one, chosen by
the honest system clock. After that, no tie is left for `id DESC` to break.

## The two nulls, and why an absolute return is not evidence

The baskets were authored in 2026 over names that had already moved. Forward returns on that universe are
biased upward by selection and **no clock fixes that**. So every pooled metric reports three things beside it:

- **Excess over basket** — the same window measured against the thesis basket's equal-weight close-to-close
  move, from the same mirror. The theme's own drift, removed.
- **The timing null** — the SAME name entered on a randomly drawn session from the run's own session list.
  Isolates timing.
- **The name-selection null** — a randomly drawn OTHER member of the same roster on the same day. Isolates
  name selection.

`K = --null-draws` (default 50) draws per episode per null. The **seed defaults to the run_id**, so a run
reproduces its own draws and two runs never share them; both ride the manifest. When fewer than K candidates
exist the whole population is used — reporting three peers of a four-name basket is honest where resampling
to K would manufacture confidence.

**The short-window trap, surfaced rather than left silent — and six-week windows sit ON the boundary.**
A 42-day window holds roughly 30 trading sessions, which is exactly where the caveat's threshold is. Any
write-up of a windowed pass must carry the sentence whether or not the caveat fired: **a `vs_timing` close
to `actual` on a ~30-session window means there was no comparison, NOT that there is no edge.** The
threshold is deliberately left where it is rather than tuned so that it always fires — moving a threshold
to make a caveat appear is fitting the instrument to the answer. `timing_candidate_sessions` rides the firing
diagnostics: on a four-session run there is roughly one alternative entry date per episode, every draw prices
a near-empty forward window, and `vs_timing` comes back on top of the actual — which READS like "the
algorithm ties with chance" and MEANS "there was no chance to compare against". Under 30 sessions the surface
says so in words.

**Episodes are not independent.** MEASURED on the record: 82 % of arm episodes arrived alongside a co-member
the same thesis-session. The co-arm slice is what makes that visible rather than assumed, and the effective n
is far below the episode count. Two counts ride every episode, because one cannot tell the cases apart:
`co_arm_count` (how many OTHERS newly armed — the decision made that night) and `armed_count_that_night`
(how many were armed in total, new and sticky alike — the loudness the operator saw).

## Pooled, never per thesis — the no-leaderboard rule

The unit is the **ALGORITHM**. Pooling across theses tests the algorithm; slicing outcomes by thesis tests the
IDEA, which is the leaderboard trap and an invariant #4 violation (opinionated on timing, deferential on
thesis).

This is enforced structurally, not by convention:

- `PooledReport` has **no thesis field of any kind**; the slices are algorithm-only (Key-1 source ×
  confirmation grade × co-arm bucket × close reason).
- A backend test walks the serialized payload looking for any thesis identifier, and a route test walks the
  same payload on the WIRE.
- The per-thesis view exists in exactly one place — the run's own LEDGER, a drill-down that opens collapsed,
  below the pooled panel, ordered by NAME. A drill-down is how a reader checks a pooled number against its
  rows; an outcome-ordered one would be a ranking. The order is fixed by the writer and a test pins it.

## `/backtest` — the surface

Three read-only routes, artifact-served: `GET /backtest/runs` (the registry + `dial_trials`),
`GET /backtest/runs/{run_id}` (manifest, pooled, episodes, ledger, labels), `GET /backtest/sweep` (the latest
curve). **`available: false` is a legitimate 200**, never a 500 and never a 404 page: a missing store, a
missing run, a half-written directory and an unreadable file all collapse to it.

**Dev/sig-only BY DATA AVAILABILITY, with no build flag.** On prod the store directory does not exist, so the
page renders one quiet line. There is no environment variable anyone can get wrong. The nav tab always
renders — the `available:false` idiom hides SECTIONS, not tabs, and gating the tab would cost a request on
every page load to answer a question the page answers itself.

The page's reading order IS the design: the five labels, then the run picker and the manifest card (what
produced these numbers), then the POOLED panel, then — folded away — the per-thesis ledger, then the sweep.
The ledger renders through the Scoreboard's own components (`LedgerHead`, `EpisodeRow`, `EpisodeScorecard`,
`MetricsStrip`) because its rows genuinely are replayed, scored arm episodes and the backend serves them in
that exact wire shape; `ledger.json` is built by `scoreboard/replay_snapshot.build_snapshot`, so the two
surfaces cannot disagree about which episodes are eligible. The drawer mounts the scorecard **without an
as-of**, which is what keeps today's live price window, display signals and scored members out of a run that
swept a frozen mirror.

**Nothing links into it.** Board, Cockpit and Scoreboard do not know it exists, and it never notifies.

Compose gives sig and dev a READ-ONLY bind of `./data/backtest` at `/data/backtest` (the
`scoreboard_replay` idiom). Prod's base file does not carry it at all.

### Two metric sets, deliberately not merged

`metrics.json` scores EVERY outcome; the ledger's own metric set scores the ELIGIBLE ones (matured +
non-censored — the Scoreboard's rule) and is the set whose rows are on screen beside it. Only the ledger's is
served, because putting two differently-gated numbers on one page with nothing to tell them apart is how a
reader quotes the wrong one. `metrics.json` stays in the run directory for analysis.

## Cost — MEASURED, and the follow-ups in measured order

The full-run cost was `>5.5 h and still running`. The investigation found it was **query count**, not query
complexity: 14,676 DuckDB queries over 5 sessions = 14.98 per member-session, `price_history` alone called
~7× per member-session and re-reading each security's entire history unbounded.

| leg | s/session (196-name basket) | queries (3 sessions) | rows materialized | identical to baseline |
|---|---|---|---|---|
| A — baseline | **42.12** | 8,805 | 2,666,554 | — |
| B — + per-PIT memo | 16.27–22.91 | 3,993 | 664,699 | yes |
| D — + registry bounds | 15.25–24.10 | 3,993 | 337,540 | yes |
| E — + basket prefetch | **1.65** | **8/session** | 337,540 | yes |

**25.5× MEASURED, byte-identical** (leg E over 20 sessions, last 3 sessions' snapshots compared against leg
A's). That is B5a: a port of the live PIT's memo + basket prefetch + bounds, an already-argued mechanism
rather than a new one.

**The follow-ups, in the order the measurements put them:**

1. **The PIT's basket prefetch first.** 2,935 queries/session → 8, MEASURED 25.5×. Done (B5a).
2. **The `RealizedPrices` TAPE MEMO second** — done (M1). This was recorded here as "the benchmark memo",
   and that name was wrong against the code: `_BasketBenchmark` *already* memoizes on
   `(thesis, entry, exit)`, and it can never hit under the timing null, because that null draws a
   **different random entry date on every draw** — so every call is a fresh key and a fresh miss. The cost
   was one level down, in `RealizedPrices`, which had no cache at all and issued **two DuckDB queries per
   priced window**. A null draw prices the name once and then its whole basket for the benchmark, so the
   per-episode count is `2·K·M + 4K + 2M` (K draws, basket size M) — about **2,374 queries for one
   episode** at K=5 on a 196-name basket, which is why the nulls took **658 s at K=5 on a two-week run**
   against seconds of scoring. Caching each security's tape once and slicing it by bisect makes a whole
   pass cost **one query per security touched**. MEASURED on the M1 fixture: **662 → 8 tape reads, 83×
   fewer** at 6 episodes, K=5, basket 8 — and the ratio grows with M, so that is a floor. Byte-identical
   artifacts by test, against the pre-memo reader kept verbatim as the oracle.
3. **The event-layer cache third, and it is still SECOND-ORDER.** At 8.3 ms per member-session the detector
   pass is cheap enough that a full re-run is minutes. The cache earns its keep on multi-variant sweeps
   rather than on the base run. Re-measure after M1 before building it — the term it would attack was
   never the dominant one.

### MEASURED end to end, on real data — one six-week point, before and after the memo

12 theses, public clock / lockstep, 6 workers, K=5, window 2026-08-03 → 2026-09-14, **386 episodes**, on
the dev copy. The two runs were given the SAME `--null-seed` so their draws are identical and the
comparison is about the reader alone:

| | pre-memo `20260917T224817Z-public-default-f6bfc4cd` | post-memo `20260917T234923Z-public-default-f6bfc4cd` |
|---|---|---|
| **wall** | **59 m 59 s** | **2 m 26 s** |
| `nulls_s` | **3,456.34** (96% of the run) | **7.36** — a **470×** collapse |
| `score_s` | 9.55 | 1.74 |
| `replay_s` (6 workers) | 74.1 | 76.03 |
| `export_s` | 55.59 | 57.33 |

**The identity check, on 386 real episodes rather than a fixture.** `pooled.json`, `episodes.json` and
`metrics.json` are byte-identical. `episodes.parquet` and `outcomes.parquet` are identical ROW FOR ROW in
order with equal schemas; their file bytes differ only in the Parquet writer's `created_by` string,
because the two runs were launched from venvs carrying different pyarrow builds. `ledger.json` differs
only at `generated_at` and `code_sha`, and the manifest at `run_id`, `created_at`, `code_sha`, `timings`
and the mirror hash (a fresh export, same writer-string reason). **Nothing the memo touches differs.**

That writer string is also a rule for any pass: **run every window and every point of one pass from ONE
venv**, or artifacts that are identical row for row will differ byte for byte and the mirror hash with
them, for no reason anybody can see later. It is stated in the launcher's docstring.

**The dominant term moved.** After the memo a six-week run is replay (76 s at 6 workers) plus a one-time
export (57 s, paid ONCE per pass under a shared mirror); the nulls are noise. That inverts the reason the
window split existed — see below.

**The null phase is SERIAL, and `--workers` does not touch it.** MEASURED on the live six-week run's
process tree — the mirror export finished at 22:49:12Z and what remained was ONE python process with no
pool children, the `ProcessPoolExecutor` in `backtest/parallel.py` having already come and gone — and
confirmed by reading `backtest/run.py` (the `draw_nulls` call is on the main process, with no pool and no
workers argument). The year run is INFERRED to have behaved the same way, from the same code rather than
from its own process tree. So the wall clock of one run is `export + replay/workers + nulls(serial)`, and before M1 the last
term dominated. Parallelizing the nulls is possible — the seed is already derived per episode from
`(seed, thesis, security, arm_date)` precisely so that adding an episode cannot reshuffle another's draws —
but it should partition **by thesis**, not by episode, or each worker re-pays `_BasketBenchmark`'s
per-member cost. It is the second lever and bounded by cores (MEASURED ~2.8 usable on this box); the memo
was worth more than an order of magnitude more.

**An honesty note on a discarded number.** The first prefetch leg reported *35×* and a snapshot mismatch. The
cause was the probe, not the design: `replay/export.py` writes `uuid` columns as VARCHAR, so the prefetch
keyed its memo by `str` while lookups used `UUID` — every lookup hit an empty list and the leg was measuring
"read nothing". Fixed with a `{str(sid): sid}` map, re-measured at 25.5× with byte-identical output. The
lesson is now a build rule: **an equivalence test asserts row COUNTS per security, not that it ran.**

Projected wall clocks (PROPOSED, arithmetic from the MEASURED per-member-session costs; ~185,000
member-sessions): baseline ≈ 11.0 h · memo only ≈ 4.5 h · memo+bounds+prefetch ≈ **26 min** · + 6-way
per-thesis parallelism over one frozen mirror ≈ **8–10 min** · + member-sharding the two biggest theses ≈
5–6 min.

## Sweeps — a curve, never a winner

`python -m backtest.sweep` runs one point per dial setting over ONE shared frozen mirror (so a metric delta is
attributable to the dial rather than to the tape moving underneath it), and reports:

- the **plateau** — the widest contiguous band of settings that behave alike, as indices into the points. A
  band one point wide is the sweep finding NOTHING, and the surface says exactly that rather than
  highlighting a high point.
- **sub-period sign agreement** — the same delta recomputed on each disjoint sub-window. A pooled number that
  cannot survive cutting the window in half has not found anything.
- Never an argmax, never a sort by outcome, never a "best". The points render in dial order.

### The window is the unit of work (S1)

A pass no longer runs one long window. `[start, end]` is tiled into **disjoint, gapless six-week windows**
(`backtest/windows.py`), every point is run once per window, and the point's metric is the POOLED read
across them. `backtest.sweep` exports ONE mirror for the whole pass and every job inherits it — the mirror
is the whole tape regardless of window (`export_snapshot` takes no date bound), so one export serves every
window of every point, which is also what keeps a delta attributable to the dial rather than to a second
snapshot of a moving database.

**The per-window deltas replaced the sub-window split of one run, and they ask a stronger question.** The
windows are separate measurements, not slices of one, so a dial that helps in one six-week window and
hurts in the next is visibly unstable. One window reports NO agreement rather than a vacuous yes: a single
window has nothing to agree with.

**The baseline is run once per window and shared.** Variants are deduplicated by `config_hash` before
anything launches, so every ladder containing the production default cites the same baseline runs instead
of re-measuring them. Points that share runs are marked `runs_shared` on the curve — one measurement cited
twice is not two.

**Why the split, honestly.** It was designed when the null phase was serial and 96% of a run, and separate
window PROCESSES were the only way to parallelize it. After the tape memo that reason is gone. What
remains, and is why it stays: cross-window agreement between genuinely separate measurements (the
scientific point all along), a blast radius of ~90 s rather than an hour when a job dies, and bounded
memory per job. **Concurrency now buys a fraction, not a factor** — one job's wall clock is its LARGEST
thesis so its workers idle near the end and a second job fills that tail, but the box is ~2.8 usable cores
either way. The default is 2, never 6.

**Two things the split forced, and both were latent bugs.** The run id gained the WINDOW START, because a
point's window runs launch concurrently and otherwise differ only by a second-resolution timestamp. And
`store.register_run`'s read-modify-write is now under a cross-process lock: `_write_index` was already
atomic, so no reader ever saw a truncated file, but two concurrent runs could each read the index, each
append their own row, and each write — silently losing one. The registry's whole job is counting trials.

**A pass id groups a curve.** Every run of one sweep carries the same `pass_id` on its manifest and its
registry row, so the grouping survives without `sweep.json` — which is latest-only. The sweep runner also
copies each curve to `sweeps/<pass_id>-<dials>.json` on the way out.

### One pass, many ladders (S2)

A phase is six dials, and running it as six separate sweeps re-ran the production baseline six times:
**MEASURED, 45 redundant runs and five redundant exports — about 1.1 h of a 6.5 h pass.** `backtest.sweep`
therefore takes a repeatable **`--ladder dial=v1,v2,...`**: one curve per ladder, ONE mirror export, one
`pass_id`, and variants deduplicated by `config_hash` **across** ladders — so the production default, which
every ladder contains, is measured once per window and cited by all six curves. Points that share runs
carry `runs_shared`, because one measurement cited six times is not six.

`--ladder` is distinct from `--grid`, which stays a **cartesian** variant set producing ONE curve — H3's
three arms live on two dials and need exactly that. Passing both is refused rather than resolved.

**Pre-registration.** The RUNS carry the pass's `--hypothesis` / `--decision-rule`, and they have to: the
baseline run belongs to every curve at once and cannot carry six different texts. Each CURVE carries its
own on its report, overridable per ladder with `--ladder-hypothesis dial=…` / `--ladder-decision-rule
dial=…`, and the curve file is the artifact a dial's result is quoted from. A per-ladder override naming a
dial with no `--ladder` is refused — a typo must not silently substitute the pass's text.

Each curve is kept at `sweeps/<pass_id>-<dial>.json`; `sweep.json` keeps its shape and holds the last
curve of the pass, so the `/backtest` sweep view renders unchanged. Every report lists the pass's other
curves in `pass_curves`.

### Separating timing from composition — `backtest.pair`

A point's `delta_vs_baseline` answers two questions at once. Moving a liveness dial re-times the episodes
the baseline also armed (TIMING) and changes which episodes arm at all (COMPOSITION), and a pooled median
cannot tell them apart. `python -m backtest.pair --pass-id … --dial … --values 60,90` pairs a point against
its curve's baseline on the episode's own identity — `(thesis_id, security_id, arm_date)` — and reports,
per window and pooled: the shared set and how many of it the dial actually MOVED, the median change among
those, and what the dropped and added episodes were worth. It reads **artifacts only**, so a finished pass
can be re-interrogated for the cost of reading its own bytes. The same fields ride every curve point
(`paired_delta_vs_baseline`, `n_shared`, `n_changed`, …) through the same implementation, so the CLI and the
curve cannot drift.

**The reading trap it exists to prevent, MEASURED on phase 1.** `revenue_accel` at 60 d has a paired median
of exactly **+0.00%** — and moved **561 of 1931** shared episodes by a median **+8.35%**. The paired median
is zero because the untouched 70% majority decides it; reporting it alone would say "the dial does nothing
to shared episodes", which is the opposite of what happened. **Always read `n_changed` beside it.**

The paired block is a reported **diagnostic**. The decision rule still keys on the pooled delta and its
cross-window sign agreement, and the curve's banner says so.

### `--resume <pass_id>` — the recovery path

A window job that dies takes the pass down (deliberately: a curve missing a point it believes it measured
is worse than a pass that stopped), and the runs that finished stay registered. `--resume` skips every
`(config, window)` already registered under that pass id **with readable outcomes**, re-runs the rest, and
assembles the curves from the union. Registration alone does not count as done — a job killed mid-write can
leave a row and a directory, and reusing it would pool a point over a truncated run.

**The nulls draw identically on a resume, by construction: the seed IS the pass id.** A resumed pass
continues the same pass rather than minting a new one, and a test pins that the assembled curve equals the
one an uninterrupted pass produces.

**A resume does NOT re-export, and refuses rather than guesses.** A second export is a second snapshot of a
database a human may well have touched in between — and a resume is exactly when that is likely — so the
re-run jobs would sweep a different tape from the completed ones while the curve reported a single
`mirror_hash`. Three rules, each checked BEFORE any job launches, because by the time a job has run the
damage is on disk:

- the existing mirror is REUSED, and any completed run whose manifest cites a different mirror hash
  refuses the pass (the tape changed underneath it);
- a mirror that has vanished refuses when anything completed — there is nothing left to re-run the
  survivors against — but is simply exported when nothing has, which is a fresh pass wearing an old id;
- the requested WINDOWS must contain every window the pass already ran, and the requested CLOCK must be
  the one it ran on. Nothing about a pass id says what it measured: without these, a resume with different
  windows finds no matching pairs and runs everything under the old id, and a resume on a different clock
  would put two fact axes inside one curve.

**The trials caveat.** A dead pass's completed runs already count as trials in the registry — `dial_trials`
includes them, and nothing retracts a measurement that was really made. Resume does not mint new ones,
which is exactly why it is the cheaper recovery; re-running from scratch would double-count that dial.
Record a dead pass id in the write-up so the trial count can be read correctly.

**A record sweep and a public sweep are two curves, never one.** They are different experiments: the clock
is exported into the one shared mirror, every point inherits it, and `SweepReport.clock` records it. The
mirror directory is named for the clock too, so the same window at the same pin on both axes exports two
tapes side by side rather than one over the other — otherwise the earlier sweep's points would go on citing
a mirror hash that no longer described the tape they swept. The run id carries the clock for the same
reason: on a short window a point finishes inside the timestamp's one-second resolution, so without it the
second pass collides with the first.

**Known asymmetry (follow-up).** Runs are immutable and addressable; **sweeps are not**. `backtest.sweep`
writes ONE `sweep.json` at the store root, so a second sweep overwrites the first. Each point cites its own
`run_id`, so the underlying evidence survives — it is the curve that does not. Making sweeps addressable
(`sweeps/<id>.json`, with a registry of their own) is the obvious fix and is not done.

## H5 — the `exit_by` dial set, and the second family the spec omitted

`calls/assembler.py` computes `exit_by = _clock(conviction_events)`, so the dials that move it are exactly the
conviction-side liveness horizons:

| dial | default | detector |
|---|---|---|
| `insider_core_alpha_liveness_days` | 180 | `insider_conviction` |
| `insider_flip_alpha_liveness_days` | 18 | `insider_conviction` |
| `revenue_accel_alpha_liveness_days` | 180 | `revenue_acceleration` |
| `catalyst_default_horizon_days` | (config) | `catalyst_conviction` |
| `theme_conviction_default_horizon_days` | 365 | `theme_conviction` |
| `activist_13d_liveness_days` | 180 | `activist_stake` |
| `corporate_event_items["5.02"].liveness_days` | 90 | `corporate_catalyst` |

**There is a SECOND family**, and it was not in the spec's list: `arm_until = _clock(confirmation_events)`
moves episode LENGTH and therefore the `arm_until_lapsed` close reason —
`breakout_alpha_liveness_days` (10), `breakout_52w_alpha_liveness_days`, `laggard_alpha_liveness_days`, plus
`conviction_hold_threshold_days` (90) on the policy side. Sweep the conviction family first (it is `exit_by`
proper) and report the confirmation family as a second grid. A pinning test should derive this set
mechanically — perturb each dial on a fixture and record which ones change `exit_by` — so a new horizon dial
is review-visible rather than trusted to this table.

One measured trap for anyone shortening a horizon: at 90 days UNH legitimately fails to arm, so a sweep whose
low end asserts "episodes exist" will fail for a correct reason.

## Known gaps

- **Both honest-clock axes are wired (CW).** `backtest.run --clock {record,public}` exports its own mirror
  in that mode; `backtest.sweep --clock` exports the ONE shared mirror and every point inherits; a run given
  a mirror inherits its clock and refuses loudly (`MirrorClockMismatch`, before the run directory exists) if
  handed one that disagrees. `known_at_mode` is DERIVED from the clock (`lockstep` on public, `pin` on
  record), never a second flag that could disagree with it. The run manifest records the axis and the
  mirror's own counts and exclusions.
- **The mirror manifest and the run manifest share a filename** — both are `manifest.json`, and a run that
  exports its own mirror writes them into one directory, run-manifest last. `replay.export
  .read_mirror_manifest` reads both shapes (a run manifest is recognized by its `run_id` and carries the
  same exclusion list under `mirror`), so `connect_mirror` on a FINISHED public-clock run still knows what
  was left out instead of silently seeing nothing excluded and then failing on a missing file. Accepted as
  handled rather than renamed: the two names are load-bearing in B2's tests and in the store's own layout.
- **Sweeps are latest-only** (above).
- **The event-layer cache is unbuilt** (above) — deliberately, and the measurement order is recorded so the
  decision can be revisited with numbers rather than re-argued. The nulls pool is likewise unbuilt, and
  deliberately second: see the note under the cost follow-ups.
- **A 1-year, 12-thesis, public-clock run at 6 workers / K=5 did not finish in 3h40m** (MEASURED, before
  M1 — a lower bound, not a timing; the run was killed, not crashed). The six-week point that replaced it
  measured **60 minutes** pre-memo and **2 m 26 s** post-memo, so the cost model
  (`export + replay/workers + nulls`) is confirmed and the nulls are no longer its dominant term.
- **Caveats are counted on the MIRROR, not on the source database.** The public clock drops every row it
  cannot date, so the population a pass actually swept is the exported Parquet and nothing else. The worked
  example: a count of impossible-date insider facts taken from dev returned 14, but one of them (GS-PA
  `0001900188-25-000010`) had no `accepted` date on any of its 16 source rows, so the export dropped it as
  one of the 2,162 null-clock drops and it never reached the mirror. The pass's caveat is 13.
- **The run picker lists every run.** A phase-1 pass writes ~270 rows into a registry that used to hold a
  handful of standalone experiments. The `pass_id` on every row is what makes them separable; grouping the
  picker by pass is deferred until the operator has seen the need for it.
- **`K` is pinned at 5 across the first pass for comparability**, which was the right call when the nulls
  cost an hour a run. At 7 s they cost nothing, and K=20 would make the timing null far less coarse over a
  window of ~30 sessions. Raising it is a pre-registration change and breaks comparability with runs
  already made, so it is an operator decision, not a build one.
- **Baskets are only partially point-in-time.** `basket_snapshot` history begins 2026-09-15; earlier windows
  replay on today's basket. The manifest reports this per thesis and QUANTITATIVELY (fallback days out of
  total days), because a thesis that fell back for 2 sessions of 300 is a different artifact from one that
  fell back for all 300. The nulls draw from the SAME counterfactual roster the real arm drew from, which is
  the symmetric and honest choice.
