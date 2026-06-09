# RECALIBRATION_PASS_001.md — Phase 1 / Step 2, the first recalibration pass

> The decision record for the first dial-tuning pass against the replay harness (`backend/replay/`, #44). The
> deliverable is dial-VALUE changes + evidence + this record. **Outcome: ZERO dial changes — every backlog
> item deferred with an episode-level reason, and the one give-back finding routed to its correct (out-of-
> scope) shape refinement (B.4 age-decay).** On a 19-episode, mostly-UNH seed that is the *correct* result.

## ⚠️ In-sample, not validation (read this first)

This pass tuned **direction from the literature + the mechanism**; the replay only **illustrates** — it never
**selects** a value. The seed is **n=19 episodes, and only UNH is a long forward arc**; nothing here is a
calibration claim. The honest claim is: *"the dials are grounded in replayed history, the one obvious lever was
inspected, and the give-back was routed to its correct refinement"* — **NOT** "the system is validated."
Validation is the forward Scoreboard living with real calls, later (Phase 3, parked).

## Method

`cfg` is a swept parameter, so each comparison is an **old-cfg-vs-new-cfg replay at the same pinned snapshot +
window + PIN, varying only the dial under test** (window `2025-04-01 → 2026-06-30`, PIN `2027-01-01`). The thin
`replay/compare.py` exports the Parquet mirror **once** and replays each variant over that frozen snapshot
(it touches none of export/pit/harness/episodes/scoring/metrics). The harness is value-identical, so each delta
is real, not noise.

## The one inspected dial — `insider_core_alpha_liveness_days` (180), the #44 exit-too-long finding

**Hypothesis:** UNH's core insider arm holds too long past its realized peak (its `exit_by` = the mid-May-2025
cluster fire + 180d ≈ 2025-11-12 — the #44 give-back). **Disaggregated to the UNH-core subset only** (the n=19
aggregate is contaminated by the nuclear arms, whose `exit_by` is un-tunable ratified data).

| cfg | UNH-core episodes | arm → exit_by | peak | exit_by return | days exit past peak |
|---|---|---|---|---|---|
| **180 (base)** | 2 | 08-15 → 11-12 | **+21.7%** | **+11.5%** | 35 |
| | | 09-09 → 11-12 | +6.3% | −2.5% | 35 |
| **90 (illustrative shorter)** | **0** | — | — | — | — (UNH never arms) |

**The give-back is real** (peaks +21.7% / +6.3%, exits +11.5% / −2.5%, ~35 days past the realized peak each).
**But the dial cannot be the lever — it does DOUBLE DUTY:** `insider_core_alpha_liveness_days` is *both* the
hold horizon (`exit_by`) *and* the co-location window (how late a breakout may confirm a still-live conviction;
`config.py` says so explicitly — it "doubles as the cap so a conviction can't arm on a breakout >6mo later"). At
**90d the conviction lapses (~mid-Aug) before the 08-15 breakout co-locates → UNH produces ZERO armed episodes**
(the aggregate drops 19→17, losing exactly the two UNH arms). That is the **"right but early" case the 180d
window exists to catch** (the CEO-led May cluster, confirmed by the August breakout ~3 months later). And the
peak sits ~146d post-cluster, so *any* value short of ~146d exits *before* the peak (worse), while ~146d would
be **fitting the single peak date** (the overfit the discipline forbids). **No shorter value helps without
either killing the arm or overfitting n=1.**

**Decision: DEFER at 180.** Grounded in (a) the insider-purchase literature — 180d = the conservative **6-month
low end** of the 6–12mo abnormal-return horizon, where *multi-insider cluster buys* (exactly UNH) persist
*longest* (Lakonishok & Lee 2001; Cohen, Malloy & Pomorski 2012, *"Decoding Inside Information"*; cited in
`config.py`) — and (b) the mechanism above: shortening is *actively harmful* (it breaks co-location), not merely
unsupported. **The give-back is a SHAPE problem, not a magnitude:** a binary liveness window cannot express
"confidence should fade as a call ages past its peak." → Routed to **RECALIBRATION B.4 (age-decay of
confidence)**, a shape change, out of scope for this magnitudes-only pass.

## The HIMS truncation check (the risk_penalty defer basis)

The triage's risk-penalty defer hinged on whether HIMS reaches a *scored* episode. Confirmed empirically: **HIMS
arms** (a `starter_entry` — CORE insider conviction + a momentum-only breakout → flip entry — arm **2026-06-01**,
the demo call), but its `exit_by` = **2026-11-22** runs ~5 months past the 06-30 data end → **`truncated=True`**.
The forward return (+0.9%) is arm-to-last-bar (~3 sessions), **not** the 180d hold. So the dilution risk penalty
never reaches a meaningfully-scored hold outcome → **`risk_penalty_per_signal` defers** (and the confidence-vs-
outcome metric, calibration, is per-bucket insufficient regardless). *(Had HIMS's exit_by landed ≤ 06-30, this
would have moved to bucket (a); it did not.)*

## Backlog resolution (full per-item list in `docs/RECALIBRATION.md`)

- **Changed with evidence:** none. (Zero dial-value changes — the correct outcome at n=19.)
- **Deferred with reason:** `insider_core_alpha_liveness_days` (above — double-duty + literature + n=1, routed
  to B.4); every other §A dial (each with its episode-level reason in RECALIBRATION §A/§D — unexercised
  explicit-horizon dials, fire-far-from-boundary thresholds, mechanically-wrong-lever `conviction_hold_threshold_days`,
  not-live DOE awards straddling the $10M floor, per-bucket-insufficient confidence caps, n=1 ranking dials).
- **Strengthened (filed for later):** **B.4 age-decay of confidence** — now the evidenced fix for the exit
  give-back (the dial can't be trimmed without breaking co-location; the give-back is a confidence-fade shape).
- **Not relevant (shape/build):** the §B/§C items + `arming_requires_confirmation` + the `is_own` placement.

## What this pass establishes (and what it does not)

**Establishes:** the dials are now grounded in a real replay over history, not intuition; the one obvious
miscalibration (the exit give-back) was inspected with a real comparison and shown to be a *shape* problem the
liveness *magnitude* cannot fix; and every other dial has a recorded, episode-level reason it was left alone.
**Does not establish:** that any dial value is *right* — that needs forward data at scale (the Scoreboard). A
pass that ships zero changes here, with this evidence, is the disciplined result, not a shortfall.
