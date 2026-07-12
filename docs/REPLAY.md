# REPLAY.md — the replay / backtest harness (Phase 1, the trust instrument)

> Repo path: `docs/REPLAY.md`. Phase 1, **Step 1**: the instrument that makes the back half trustworthy
> before the front half is built. It is **not** the recalibration pass (Step 2, which *consumes* this to tune
> the dials) and **not** the production cut (Step 3). No new detectors, no new dials, no logic change — an
> instrument. Code lives in `backend/replay/`; install with `pip install -e ".[replay]"` (duckdb + pyarrow).

---

## What it does

Sweep an as-of date **T** across history; at each T run the **real** call pipeline to produce each thesis's
call; record the per-thesis call **timeline**; then — in a **strictly separate pass** — score the recorded
calls against **realized forward prices**. It is uniquely possible because the platform is deterministic and
Option-B: the call is a pure function of `(thesis, events, asof, cfg)` and events are a pure function of the
facts known as-of, so history replays honestly.

```
SoR (Postgres, bitemporal) --export--> Parquet mirror --DuckDB--> ReplayPointInTimeData(asof=T, known_at=PIN)
                                                                          │  (the SAME detectors + assemble_call)
                                                                          ▼
                                                              pipeline.core.assemble_from_pit  --> CallSnapshot
   per-thesis timeline ──> arm Episodes ──(separate pass)──> RealizedPrices(forward) ──> Outcomes ──> Metrics
```

## The integrity heart — the lookahead boundary (structural, not by convention)

At as-of **T** the detectors see **only** facts with `valid_from <= T AND recorded_at <= PIN`. The scorer,
separately, reads the **forward** window (`valid_from in (T, exit_by]`) to compute outcomes. There is **no read
path** by which the scorer's forward data can reach an as-of call, because:

- **`ReplayPointInTimeData`** (`replay/pit.py`) is **as-of-capped, constructor-bound**: every accessor query
  filters `valid_from <= asof AND recorded_at <= known_at` (mirroring `db.bitemporal._as_of`, latest-per-
  identity by `recorded_at DESC, id DESC`). No accessor can widen `asof`. It is the **only** reader the replay
  loop uses.
- **`RealizedPrices`** (`replay/scoring.py`) is a **different class** with a disjoint method set, **forward-
  windowed**, with **no** `asof`/`known_at` cap. It is the **only** reader the scorer uses. The scorer's
  signature takes no pit — one cannot be passed.
- The two readers have **opposite, non-overlapping time semantics** (`<= T` vs `> T`) and no shared base, so
  the boundary is a type-level fact, enforced by an **import-graph test** (`scoring.py` must not import
  `replay.pit`; the loop must not import `RealizedPrices`).

**The trust anchor — parity.** `tests/replay/test_pit_parity.py` asserts the DuckDB/Parquet mirror's accessors
equal the live Postgres `as_of` row-for-row (after a value normalizer — numeric→float, timestamptz→UTC, jsonb
`terms`→dict — over the **full** column set, so a dropped column *fails* the gate). Two no-lookahead mirror
tests (`test_pit_lookahead.py`) clone the bitemporal honesty tests against the mirror on both axes. Parity +
both no-lookahead tests green **is** the integrity bar.

## Determinism pin + the mirror

A run pins `known_at = PIN` (the `recorded_at` ceiling). The Parquet export is a **one-shot, truncate-and-
rewrite** snapshot of the SoR (all columns, all rows for the tenant — rebuildable, **never authoritative**; the
PIN is a read-time filter, so the mirror reproduces the SoR's `as_of` for any `known_at`). Same
`(snapshot, PIN, window, cfg)` → **value-identical** timeline + scores (the honest, achievable form of
"byte-reproducible"; Parquet byte-identity across writer versions is brittle and not the point). `cfg` is a
**swept parameter** of the harness functions, so Step 2 can compare outcomes across dial settings.

## The scoring unit — the arm episode

Armed is **sticky**, so per-`(thesis, asof)` would multi-count one decision. The unit is the **arm episode**:
a contiguous run in which one basket **member** is in `armed_members`, keyed `(thesis_id, security_id,
arm_date)` — **per member** (not just the headline), so name-selection is scorable. Measured over
`[arm_date, exit_by]` (the system's own **signal-validity horizon** — an honest scoring yardstick, not a
mandatory trade exit) on realized closes. Re-arm = a new episode; never-armed theses → **0 episodes**
(Warming-forever is a non-event). Close reasons:
`arm_until_lapsed` · `conviction_aged_out` · `managing` · `window_end` · `dearmed_other`. **`managing` is
expected-zero in pure replay** (no operator fills exist in historical facts); it denotes an
operator-entered position being monitored, not portfolio risk management.

## The metric set — tied to the claim (not generic hit-rate)

The claim: **opinionated on timing, deferential on thesis; preserve the edge (early narrative), patch the flaw
(timing + name-selection).** There is deliberately **no** "was the thesis right" metric. Each carries `n` +
`insufficient_n`. `MIN_N = 5` is a presentation safeguard against over-reading tiny aggregates, not an
evidence threshold; clearing it does not validate a metric or turn setup strength into a probability.

| Metric | Tests |
|---|---|
| `arm_timing_forward_return` | **Timing** (the flaw patched): realized return over the signal-validity window from the arm. |
| `early_vs_armed_delta` | **Preserve the edge**: warm-return − arm-return; large positive ⇒ the gate clips the early edge. |
| `grade_confidence_calibration` | **Discrimination** (legacy metric slug): do higher-grade / higher-setup-strength (`confidence` wire field) arms track better outcomes monotonically? This tests a future calibration hypothesis; it does not treat setup strength as probability. |
| `name_selection_lift` | **Name-selection** (the flaw patched): did the ranked headline beat the rest of the basket? |
| `false_arm_rate` | **Timing precision**: arms whose realized return was adverse (the gate firing wrongly). |
| `withheld_arm_counterfactual` | **Timing's false-negative side**: the move during windows the gate withheld. |
| `exit_by_vs_rollover` | **Signal-window persistence**: does the edge persist to the `exit_by` validity endpoint, or decay earlier? (the liveness dials; not a sell instruction). |

**Instrument, not a claim.** On the seed only **UNH** is a long forward arc (the mid-May-2025 CEO-led insider
cluster → the Aug-2025 volume-backed breakout → aged out by 2026). The deliverable is the instrument + UNH as
the worked example; metrics flagged `insufficient_n` (calibration, name-selection at N≈1) are scaffold for
Step 2, which runs against real history at scale.

## ⚠️ KNOWN LIMITATION — thesis definitions are NOT replayed bitemporally

Thesis definitions and `security_master` are read from the **current** operational SoR, not replayed: a replay
at T uses **today's** basket membership, only the **facts** are as-of T. This is harmless while baskets are
static (the seed), **but it is a real lookahead vector the moment a thesis's membership changes over the
window** — a member added after T would still be replayed at T. **Bitemporal thesis definitions must be
addressed before the backtest can be trusted on evolving theses.** Out of scope for Step 1 (flagged loudly so
Step 2 and anyone after know the boundary); the harness output carries the same warning.

## Run it

```powershell
# from backend\, venv active, infra Postgres up + seeded
python -m replay.run --start 2025-04-01 --end 2026-06-30 --pin 2027-01-01 --out ..\.replay-out
# writes: <out>/{fact_*.parquet (mirror), outcomes.parquet, episodes.parquet, metrics.json}; prints the metrics
```

Tests (`backend/tests/replay/`): `pytest tests/replay` — the parity gate, both no-lookahead mirror tests, the
UNH arc end-to-end, episode derivation, the scorer + the import-graph boundary guard, reproducibility, and the
cfg-sweep. Parity + the two no-lookahead tests are the gate.

## Out of scope (later)

Step 2 (recalibration — tuning the dials); Step 3 (the production-tenant cut). The live **Scoreboard** is now
built from the reusable record/scoring models (`replay/schema.py`: `CallSnapshot`, `Episode`, `Outcome`), but
its implementation is not part of this replay harness. Bitemporal thesis-definition versioning (the
limitation above). A `recorded_at`-staggered correction dataset beyond the one test fixture; multi-PIN
comparison runs.
