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

Two rules are the whole design. **A run directory is created fresh and never reused** (`mkdir(exist_ok=False)`),
so a run id in a PR description means exactly one set of numbers, forever. **The registry is rewritten
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

**The short-window trap, surfaced rather than left silent.** `timing_candidate_sessions` rides the firing
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

1. **Benchmark the memo first.** The basket prefetch is the whole win — 2,935 queries/session → 8. It is
   done (B5a).
2. **The event-layer cache second, and it is now SECOND-ORDER.** At 8.3 ms per member-session the detector
   pass is cheap enough that a full re-run is minutes. The cache earns its keep on the NULLS and on
   multi-variant sweeps, not on the base run — and the nulls are the current bottleneck: **269 s vs 18 s of
   replay** on the measured run. Benchmark the memo against the nulls before building the cache.

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

- **Neither honest-clock axis is exposed by `backtest.run`.** Both were BUILT and are tested, and neither is
  reachable from the run CLI:
  - `replay/export.py::export_snapshot(clock="public")` exists, `BacktestManifest.clock` accepts `"public"`,
    but `execute()` never passes it and the CLI's `--clock` accepts only `record`.
  - `replay/harness.py` and `backtest/parallel.py` both accept `known_at_mode="lockstep"` (cap the facts at
    the end of each session T rather than at one global pin), but `execute()` leaves it at `"pin"` and there
    is no flag.

  Every run on this path is therefore system-clock + global-pin, and the manifest says so honestly. This is
  the first thing B8's pre-registered pass hits. It is NOT a one-line pass-through: under a SHARED mirror
  (a sweep) `execute()` skips the export entirely, so a `--clock` argument would silently do nothing for
  every point but the first — the clock belongs to the MIRROR, not to the run, and the sweep's shared-mirror
  path needs a decision before the flag is added.
- **Sweeps are latest-only** (above).
- **The event-layer cache is unbuilt** (above) — deliberately, and the benchmark order is recorded so the
  decision can be revisited with numbers rather than re-argued.
- **Baskets are only partially point-in-time.** `basket_snapshot` history begins 2026-09-15; earlier windows
  replay on today's basket. The manifest reports this per thesis and QUANTITATIVELY (fallback days out of
  total days), because a thesis that fell back for 2 sessions of 300 is a different artifact from one that
  fell back for all 300. The nulls draw from the SAME counterfactual roster the real arm drew from, which is
  the symmetric and honest choice.
