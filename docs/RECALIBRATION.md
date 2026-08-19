# RECALIBRATION.md — the one agenda for the post-MVP tuning pass

> Repo path: `docs/RECALIBRATION.md`. The brain's **structure** is built and reconciled (CALL_LOGIC). What
> remains is **calibration, not architecture**, plus a handful of deferred refinements and builds. This is
> the single list so the recalibration pass after the MVP has a real agenda instead of a scavenger hunt.
> Three parts: (A) value dials, (B) filed structural refinements, (C) deferred builds (roadmap).
>
> **Principle for the tuning pass:** these are *starting* values set conservatively / on the merits, **not**
> precision. Tune them against *real scored outcomes*, never to fit one name (the discipline we held on the
> insider liveness and the catalyst grade).

---

## A. Value dials — all in `backend/domain/config.py` (`CallConfig`)

| Dial | Current | Drives | Note |
|---|---|---|---|
| `warming_min_entry_triggers` | 1 | Incubating → Warming | |
| `risk_block_severity` | 0.70 | severe risk blocks the arm (timing veto) | block-vs-penalize is fixed; only the threshold tunes |
| `risk_penalty_per_signal` | 0.10 | setup-strength cut per active risk signal (`confidence` in code) | scaled by the signal's score |
| `dilution_overhang_severe_pct` | 25.0 | gross overhang that scales to a block | HIMS ~6% lands well below |
| **Insider conviction (grade-coupled liveness):** | | | |
| `insider_cluster_window_days` | 30 | buys within N days = one cluster | ~7–30d is the literature window |
| `insider_min_usd` | 10,000 | below this, no signal | |
| `insider_core_min_distinct` / `insider_core_min_usd` | 2 / 100,000 | the CORE cluster floor | |
| `insider_strong_single_usd` | 500,000 | single-buy CORE path | HIMS: one director ~$1.2M |
| `insider_core_alpha_liveness_days` | 180 | core signal-validity horizon | ~6mo, conservative low end (cluster buys persist longer) |
| `insider_flip_alpha_liveness_days` | 18 | flip signal-validity horizon | fast / mean-reverting |
| `insider_10b5_1_buy_weight` | 1.0 | **DORMANT (Band 03 S2c)**: scales a kept planned buy's (`aff_10b5_1=True`) $ contribution; present for distinct/senior/anchor iff > 0 (0.0 = full screen, anchor moves) | 1.0 IS today (byte-identical goldens — there is no buy-side 10b5-1 screen); flip only after a measured pass + operator sign-off (the `insider_sell` precedent); bounded [0,1]; tri-state — `None`/`False` always weigh 1.0 |
| **Insider-SELL cluster risk (Band 03 S1 — every default a `[PROPOSED]` prior; the sig-lab pass finalizes them BEFORE the switch flips):** | | | |
| `insider_sell_enabled` | False | the master switch — detect() no-ops until ON | flip only after the lab table (the breakdown precedent); replay's `--insider-sell` forces it on for the backtest |
| `insider_sell_cluster_window_days` | 30 | kept sales within N days of the anchor = one episode | a SEPARATE dial from the buy side's, so risk never couples to conviction calibration |
| `insider_sell_min_usd` | 250,000 | below this cluster total, no signal | selling is routine (comp-driven) — the buy side's $10k floor would fire everywhere (honest loudness); **the lab distribution picks the real value** |
| `insider_sell_min_distinct` | 2 | the cluster floor — "clustered" is the load-bearing word | no single-seller path in v1 (one big sale is the many-reasons case) |
| `insider_sell_require_senior` | True | ≥1 senior officer/director required in the cluster | reuses `insider_senior_role_keywords` (one definition of senior) |
| `insider_sell_liveness_days` | 90 | freshness window on the cluster anchor (detector-enforced — the assembler never ages risks) | sales' horizon is weaker/shorter than buys' 180d; ~one quarter |
| `insider_sell_max_score` | 0.60 | THE CEILING — strictly `< risk_block_severity` (0.70), so a sell cluster can never withhold an arm in v1 | operator-RATIFIED 2026-08-16; lifting it is a visible diff, made only with the lab's 0.70-crossing count measured first |
| **8-K item-code tape (Band 03 S3 — every default a `[PROPOSED]` prior; the sig-lab pass finalizes them BEFORE either switch flips; full doc `docs/CORPORATE_EVENTS.md`):** | | | |
| `corporate_catalyst_enabled` | False | the trigger-side master switch — detect() no-ops until ON | its OWN switch (it extends the LIVE catalyst family); replay's `--corporate-catalyst` forces it on for the lab |
| `corporate_risk_enabled` | False | the risk-side master switch | replay's `--corporate-risk`; two switches so the lab measures the sides independently |
| `corporate_event_items["1.01"]` | core / contract / 0.9 / 365d | material definitive agreement → Key-1 CORE catalyst | score = `_CORE_SCORE` parity; 365d = the catalyst default horizon (no agreement term on an item code) |
| `corporate_event_items["5.02"]` | flip / personnel / 0.5 / 90d | officer-director change → FLIP catalyst | direction is ambiguous (a departure and a marquee hire file identically) — the evidence link does the work |
| `corporate_event_items["3.01"]` | risk / 0.50 / 180d | listing-deficiency notice — moderate, sub-veto | 180d ≈ a real exchange cure period |
| `corporate_event_items["4.01"]` | risk / 0.50 / 180d | auditor change — moderate, sub-veto | a governance flag with a long tail |
| `corporate_event_items["4.02"]` | risk / 0.80 / 365d | non-reliance/restatement — SEVERE (≥ 0.70: withholds the NAME on timing) | = `breakdown_severity` parity; trust stays broken ~an annual cycle |
| `corporate_event_items["1.03"]` | risk / 0.90 / 365d | bankruptcy — SEVERE | the tape's loudest deterministic red flag (below dilution's 0.95 ceiling) |
| **Share-count creep / ATM detection (Band 03 S4 — every default a `[PROPOSED]` prior; the sig-lab pass finalizes them BEFORE the switch flips):** | | | |
| `share_creep_enabled` | False | the master switch — detect() no-ops until ON | flip only after the lab table; replay's `--share-creep` forces it on for the backtest |
| `share_creep_window_quarters` | 4 | the trailing consecutive-QoQ-pair window (the SUSTAINED-drip prior) | a persistent drip is the ATM tell; a one-off jump is usually an explained discrete raise (fork 1, operator-confirmed). Open lab question: a concentrated-but-nonzero window (one +29% pair among tiny ones) currently fires — measure whether a concentration guard is needed |
| `share_creep_cum_min_pct` | 10.0 | the cumulative % rise over the window that fires | routine SBC dilution ~2–5%/yr vs an active ATM ~10–30%/yr; measured: UEC +13.4% trailing-4 fires, MRAM's ~5%/yr SBC-scale drip does not (honest loudness) |
| `share_creep_pair_ceiling_pct` | 100.0 | a single QoQ step at/above this is NOT creep — the window declines | forward splits / recaps / XBRL scale artifacts (measured rampant: a literal 1-share row; thousands-vs-units errors reading +100,000%) |
| `share_creep_liveness_days` | 150 | freshness window on the newest print's `filed` (detector-enforced — the assembler never ages risks) | ~one filing cycle + the 10-K lag; a gone-dark series asserts nothing about today |
| `share_creep_score` | 0.50 | flat moderate — strictly `< risk_block_severity` (0.70), so creep can never withhold an arm in v1 | = the S3 moderate items' parity; the ceiling test pins the relation — lifting it is a visible diff, made only with the lab's crossing-count measured first |
| **SC 13D activist stake (Band 03 S5 — every default a `[PROPOSED]` prior; the sig-lab pass finalizes them BEFORE the switch flips; full doc `docs/ACTIVIST_STAKE.md`):** | | | |
| `activist_stake_enabled` | False | the master switch — detect() no-ops until ON | flip only after the lab table; replay's `--activist-stake` forces it on for the backtest |
| `activist_13d_score` | 0.9 | a 13D original's Key-1 CORE conviction score | = `catalyst_conviction._CORE_SCORE` parity: a filed >5%-with-intent stake is a binding, verifiable commitment. Grade itself is CORE **fixed in the detector** (structural — the R6/R9 precedent), not a dial |
| `activist_13d_liveness_days` | 180 | the edge window, anchored on the ORIGINAL 13D's filed date | the Brav/Jiang literature measures months post-filing; = insider-CORE parity, ≥ the 90d hold threshold (hold-and-build). 13G never fires (fork 3) and amendments never re-anchor (fork 4) — policy, not dials |
| **Catalyst conviction (horizon-decoupled, option A):** | | | |
| `catalyst_default_horizon_days` | 365 | liveness when no term is published | real term (period of performance) preferred |
| `doe_core_min_obligation_usd` | 10,000,000 | the DOE **contract** core floor | below it a contract is flip; assistance is flip regardless of size |
| `conviction_hold_threshold_days` | 90 | short-vs-long signal-window verdict wording (§4; legacy config name, not a sell rule) | the clean gap: insider-flip ~18d vs core/catalyst ≥180d |
| **Confirmation (volume breakout):** | | | |
| `breakout_lookback_days` / `base_window` / `min_base_bars` | 120 / 8 / 5 | new-closing-high check | the deliberately-minimal placeholder detector |
| `breakout_return_days` / `min_return` | 10 / 0.08 | momentum thrust | |
| `breakout_volume_mult` | 1.5 | vol ≥ mult × base avg → CORE (volume-backed) confirmation | below → flip (momentum-only) |
| `breakout_alpha_liveness_days` | 10 | the entry window (`arm_until`) | |
| **Setup strength (wire/config names retain `confidence`; caps compose `min-of`):** | | | |
| `single_detector_cap` | 0.80 | a one-detector call never reads "high" | |
| `starter_confidence_cap` | 0.55 | **any** starter (weak entry key) caps here — incl. a theme-armed member | the inverse-loudness ceiling |
| **Per-member ranking + theme conviction (M5):** | | | |
| `headline_lapsing_soon_days` | 45 | freshness band: runway < this = "lapsing-soon", ranks below any fresh member | **load-bearing for belief-vs-data (M5b):** also gates how readily a *fresh theme starter* leapfrogs a *lapsing own core* — tune with that in mind |
| `theme_conviction_default_horizon_days` | 365 | theme-conviction liveness when no `horizon_end` is ratified | ~12mo; also the upper-bound / re-ratification-cadence knob (it expires unless re-ratified) |
| within-band tiebreak (`is_own` placement) | own-above-theme, after grade | own-vs-theme ordering within a band | own-above-theme is *shape*; its placement/weight is the dial (M5b Q1) |
| **Workbench scoring meters (0–4 pip cutoffs — Slice 3; PRE-REGISTERED, not fit to the n=4 seed):** | | | |
| `purity_pip_pct` | (10, 25, 50, 80) | exposure-concentration % → 1/2/3/4 pips | 80 = the pure-play convention; 50 = majority; 25 = material (above the ~10% SEC segment-materiality floor); 10 = relevance floor. Seed check (separate): LEU 77→3, the three 100% names→4 |
| `runway_pip_months` | (6, 12, 18, 24) | cash-runway months → 1/2/3/4 pips | the financing cycle: raises land ~12–18mo out; <6 = distress; ≥24 = ~2 raise cycles. Cash-generative → top pip |
| `catalyst_pip_multi_count` / `catalyst_pip_dense_count` | 2 / 3 | live-catalyst count → 3 / 4 pips | + the core/flip grade: 1 live core = 2 pips, 1 flip = 1; 0 live = 0 (a real reading, not "—") |
| `dilution_pip_pct` (+ `dilution_overhang_severe_pct` as the 4-pip bar) | (2, 8, 15) + 25 | raw convert-overhang % → 1/2/3/4 pips | PRESSURE polarity; "—" when no convert fact (no fake zeros); the shared `dilution_clock.overhang_pct`, never backed out of severity |
| `cash_runway_basis` | "cash + equivalents + all marketable securities (current and noncurrent)" | the runway numerator rule | a RATIFY-TIME convention (documented), NOT scorer-enforced — the scorer trusts `cash_usd` as ratified |

## B. Filed structural refinements (small changes, deferred — not just value tweaks)

1. **Obligation magnitude through setup strength for flip catalysts.** A $148M cooperative agreement should
   read as a firmer setup than a $0 OTA — through experimental setup strength (`confidence` in code) within
   the flip grade, not a grade bump and never a position-size instruction. Grade is commitment nature /
   call-strength class, not obligation amount. Today flip catalyst score isn't scaled by obligation. *(catalyst
   `score` in `ingest/doe/feed` / the detector.)*
2. **Headline ranking weighs runway + freshness, not grade alone `[BUILT M5a; extended M5b]`.** Built in
   M5a (`calls/assembler.rank_members`): a freshness BAND (runway) is primary, grade within — so a `core` arm
   three weeks from lapsing (LEU → 2026-06-30) no longer auto-headlines over a fresh `starter` with years of
   runway (OKLO → 2029). M5b adds the `is_own` within-band tiebreak (own-above-theme) and makes
   `headline_lapsing_soon_days` **load-bearing for the belief-vs-data line** — it gates how readily a fresh
   theme starter leapfrogs a lapsing own core. What remains to tune is the *weighting* (the `is_own` placement
   + the lapsing threshold), per table A.
3. **Split the momentum-only cap back out (maybe).** The cap merge folded momentum-only into the starter cap
   (both 0.55, `min-of`). A near-conviction-less momentum call and a real provisional starter now share a
   ceiling; if we later want momentum-only to cap *harder*, re-introduce a separate (lower) momentum-only cap.
4. **Age-decay of setup strength (`confidence` in code).** `[Pass-001: this is the evidenced fix for the
   #44 window-end give-back]` Liveness is
   a binary gate, so a 5-month-old cluster arms at the same setup strength as a one-day-old one. Keep the arm
   gate binary; let only **setup strength** fade with conviction age. *(CALL_LOGIC §7 roadmap.)* Pass 001 proved the
   give-back **cannot** be fixed by trimming `insider_core_alpha_liveness_days` (that dial does double-duty as
   the co-location window — shortening it to 90d kills UNH's arm), so scoring through a window that ends ~35d
   past the realized peak and returns ~10% is a setup-strength-**shape** problem, not a liveness
   **magnitude**. Prioritized for a future
   shape pass (out of scope for the magnitudes-only recalibration).
5. **Runway for a revenue-positive name `[Slice-3 methodology, filed]`.** The Workbench runway meter is a
   pre-revenue-funding-risk gauge; for a revenue name (LEU) a single quarter's operating cash use is
   working-capital noise, not structural burn, so the literal months figure is "technically right but thin."
   MVP: the ≥24mo→4-pip cap keeps the **pip** honest (LEU 160mo → 4 = well-funded) plus a Slice-4 display cap
   on the figure. **The discriminator is SMR vs LEU, and a single quarter can't tell them apart:** LEU has
   real revenue (~$448.7M FY25) and its −$35.1M quarter is working-capital lumpiness — roughly self-funding,
   so a 4-pip "well-funded" read is right. **SMR also has revenue (~$31.5M/yr) but burns ~$50M/*quarter* — far
   more than its *annual* revenue — so it is a structural burner despite having revenue, and its 60-mo runway
   IS a real survival read.** Both show a negative operating quarter, so the structural fix — separating
   working-capital noise from structural burn — needs an **annual operating-cash-flow fact** we don't ingest
   yet. Filed. *(Reasoning: `docs/WORKBENCH_SCORING.md` "the recurring-vs-one-time burn pattern".)*
6. **Workbench dilution meter is inverted polarity `[Slice-4 display]`.** More pips = more risk, opposite the
   three goodness-meters. The scoring is correct; the UI needs a clear visual distinction so a full dilution
   meter never reads like a full purity meter. A display concern, not a scoring change.
7. **A varied demo name for the Workbench `[FILED, later]`.** The nuclear basket is homogeneous on these
   meters (purity 3-4, runway all 4, dilution all "—", catalysts 0-2) — honest, but it doesn't show the
   meters *discriminating*. Add a varied name (a lower-purity *adjacent*, a tight-runway one, or a
   convert-heavy one) so the demo actually demonstrates the scoring's value. Demo data, not a code change.

## C. Deferred builds (the sequenced plan is `docs/ROADMAP.md`)

> These are the deferred *builds* tracked alongside the dials; their **sequencing across phases lives in
> `docs/ROADMAP.md`** (the live plan). This list is the consolidated backlog, not the order.

1. **M5 — group / per-member ranked view `[BUILT]`.** M5 Part A (the per-member ranked menu — every armed
   name in the theme, ranked on a freshness band, B.2) and M5 Part B (theme/group arming — an operator-
   ratified theme conviction arms any confirmed member as a capped starter; `docs/THEME_CONVICTION.md`) are
   both shipped. The remaining work here is **calibration** (table A: the lapsing threshold + the `is_own`
   weighting), not architecture.
2. **DOE feed — loans award-type group.** The grade rule already maps loans/loan-guarantees → core, but the
   feed doesn't *query* the loans type group yet (note: loans need a sort field other than "Award Amount" —
   that combo 400s). Wire it so the first DOE loan guarantee is discovered, not just gradeable.
3. **More catalyst sources** (CATALYST_CONVICTION §3): the automated material-agreement 8-K detector; NRC
   license-action feed; ETF-launch-as-conviction (with the ETF radar).
4. **LLM counter-case** (M4b): the live Anthropic path behind the assembler's existing hook, deterministic
   template as the guaranteed fallback.
5. **Replay / backtest harness → ROADMAP Phase 1 (trust) `[BUILT — Step 1]`.** The instrument is shipped
   (`backend/replay/`, `docs/REPLAY.md`); it produces the scored outcomes this pass runs against. **ETF radar +
   umbrella/segment hierarchy → ROADMAP Phase 3 (breadth/depth).** *(These were one old-"M5+" bundle; they
   split across phases in the new plan.)*

## D. What the replay harness surfaced (Phase 1 Step 1 — SEED-ONLY, instrument-not-claim, log-not-fix)

The instrument's first run over the seed (n=19 episodes across UNH + nuclear; **far too small to claim** —
this is a question for Step 2 to confirm against real history, not a dial change now):

1. **`exit_by` / alpha-liveness horizons look too LONG for volatile names.** `exit_by_vs_rollover`: the median
   signal-validity endpoint sits **~35 days past its realized peak** and gives back **~37%** from peak to
   `exit_by`; that long scoring window turns peaked-positive arms into negative-at-`exit_by`, which is most
   of the metric's **89% false-arm-at-exit_by**. `exit_by` is the system's signal-window yardstick, not a
   mandatory trade exit.
   Meanwhile the **arm side looks sound**: calibration is monotonic (core −10% > flip −25% even here),
   `early_vs_armed` is only +6% (the wait clips little edge), and the withhold **dodged UNH's −12% summer
   slide** (2025-05-14→08-14). Net read: the **entry timing patches the flaw; the signal-validity horizon may be the
   miscalibration.** Candidate dials: `insider_core_alpha_liveness_days` (180), `catalyst_default_horizon_days`
   (365), `conviction_hold_threshold_days` (90) — and the age-decay-of-setup-strength refinement (B.4). **Do not
   tune on n=19** — Step 2 confirms on real history.

   **→ RESOLVED in Pass 001 (Step 2 — full item-by-item in §E below): all three candidate dials DEFER, ZERO
   changes.** Inspected `insider_core_alpha_liveness_days` via an old-vs-new replay (180 vs 90, UNH-core
   disaggregated): the give-back is real (UNH peaks +21.7%/+6.3%, exits +11.5%/−2.5%, ~35d past peak) **but the
   dial does double-duty** — it's also the co-location window, and shortening to 90d makes UNH **fail to arm**
   (the May cluster lapses before the Aug breakout; aggregate 19→17). So it can't be the lever (literature 6mo
   floor + actively harmful to shorten); the give-back is a **shape** problem → **B.4**. `catalyst_default_horizon_days`
   defers (unexercised — the nuclear catalysts carry explicit `horizon_end`); `conviction_hold_threshold_days`
   defers (mechanically not the signal-validity-window lever — it sets the verdict label, not `exit_by`).

## E. Pass 001 resolution (Phase 1 / Step 2 — the first recalibration pass)

The full decision record + evidence lives here (the standalone `RECALIBRATION_PASS_001.md` was retired into this
section + `ROADMAP.md`'s trust box + git history). **Outcome: ZERO dial-value changes** — the correct,
disciplined result on a 19-episode, mostly-UNH seed (in-sample, not validation). Item-by-item:

- **Inspected → DEFER:** `insider_core_alpha_liveness_days` (180) — the only seed-exercised dial; an old-vs-new
  replay showed the give-back is real but the dial does double-duty (shortening kills UNH's arm) → it's a shape
  problem (B.4), not a magnitude. Literature-grounded (6mo floor).
- **Deferred — unexercised (facts carry explicit horizons):** `catalyst_default_horizon_days`,
  `theme_conviction_default_horizon_days` (the seed's catalysts/theme conviction all ratify an explicit
  `horizon_end`; the default fires for zero live episodes).
- **Deferred — mechanically not the validity-window lever / wrong instrument:**
  `conviction_hold_threshold_days` (sets the verdict label, not `exit_by`).
- **Deferred — fires far from any boundary (no marginal case):** the insider firing thresholds
  (`insider_cluster_window_days`, `insider_min_usd`, `insider_core_min_distinct`/`_usd`,
  `insider_strong_single_usd`); `insider_flip_alpha_liveness_days` (no insider flip in the seed).
- **Deferred — no live episode straddles it:** `doe_core_min_obligation_usd` (the feed emits a $500K contract +
  a $148M grant either side of the $10M floor, but neither is live at any swept asof).
- **Deferred — no risk reaches the threshold / truncated:** `risk_block_severity`,
  `dilution_overhang_severe_pct` (HIMS ~6% ≪ 25%, no near-block); `risk_penalty_per_signal` (HIMS *arms* but its
  `exit_by` 2026-11-22 is truncated past the data end → no meaningfully-scored signal-window outcome; calibration
  insufficient regardless).
- **Deferred — overfitting risk / entry is sound:** the breakout firing params (`breakout_min_return`,
  `breakout_volume_mult`, the windows) — the finding shows the entry is sound; tuning the arming events on n=19
  re-litigates what works.
- **Deferred — metric per-bucket insufficient:** `single_detector_cap`, `starter_confidence_cap`
  (experimental setup-strength calibration);
  `headline_lapsing_soon_days` (name-selection n=1 + the freshness-wins shape interaction).
- **Not relevant (shape/build, out of scope):** `arming_requires_confirmation` (bool); B.1 (a score-computation
  change); B.3 (re-adds a dial); **B.4 (the evidenced fix for the give-back — strengthened above, executed
  later as a shape change)**; the `is_own` placement; all of §C (builds).
