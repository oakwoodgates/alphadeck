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
| `risk_penalty_per_signal` | 0.10 | confidence cut per active risk signal | scaled by the signal's score |
| `dilution_block_runway_months` | 6.0 | dilution severity → block | |
| `dilution_overhang_severe_pct` | 25.0 | gross overhang that scales to a block | HIMS ~6% lands well below |
| **Insider conviction (grade-coupled liveness):** | | | |
| `insider_cluster_window_days` | 30 | buys within N days = one cluster | ~7–30d is the literature window |
| `insider_min_usd` | 10,000 | below this, no signal | |
| `insider_core_min_distinct` / `insider_core_min_usd` | 2 / 100,000 | the CORE cluster floor | |
| `insider_strong_single_usd` | 500,000 | single-buy CORE path | HIMS: one director ~$1.2M |
| `insider_core_alpha_liveness_days` | 180 | core hold horizon | ~6mo, conservative low end (cluster buys persist longer) |
| `insider_flip_alpha_liveness_days` | 18 | flip hold horizon | fast / mean-reverting |
| **Catalyst conviction (horizon-decoupled, option A):** | | | |
| `catalyst_default_horizon_days` | 365 | liveness when no term is published | real term (period of performance) preferred |
| `doe_core_min_obligation_usd` | 10,000,000 | the DOE **contract** core floor | below it a contract is flip; assistance is flip regardless of size |
| `conviction_hold_threshold_days` | 90 | hold-or-don't (verdict §4) | the clean gap: insider-flip ~18d vs core/catalyst ≥180d |
| **Confirmation (volume breakout):** | | | |
| `breakout_lookback_days` / `base_window` / `min_base_bars` | 120 / 8 / 5 | new-closing-high check | the deliberately-minimal placeholder detector |
| `breakout_return_days` / `min_return` | 10 / 0.08 | momentum thrust | |
| `breakout_volume_mult` | 1.5 | vol ≥ mult × base avg → CORE (volume-backed) confirmation | below → flip (momentum-only) |
| `breakout_alpha_liveness_days` | 10 | the entry window (`arm_until`) | |
| **Confidence (caps compose `min-of`):** | | | |
| `single_detector_cap` | 0.80 | a one-detector call never reads "high" | |
| `starter_confidence_cap` | 0.55 | **any** starter (weak entry key) caps here | the inverse-loudness ceiling |

## B. Filed structural refinements (small changes, deferred — not just value tweaks)

1. **Size-through-confidence for flip catalysts.** A $148M cooperative agreement should read as a *bigger,
   firmer starter* than a $0 OTA — but as **size flowing through confidence within the flip grade**, not a
   grade bump (grade is nature, not size). Today flip catalyst score isn't scaled by obligation. *(catalyst
   `score` in `ingest/doe/feed` / the detector.)*
2. **Headline rule weighs runway + freshness, not grade alone.** The theme headline is strongest-by-entry-
   grade; but a `core` arm three weeks from lapsing (LEU → 2026-06-30) isn't always better to *act on* than a
   `starter` with years of runway (OKLO → 2029). *(CALL_LOGIC §2; `calls/assembler._arming_security`.)*
3. **Split the momentum-only cap back out (maybe).** The cap merge folded momentum-only into the starter cap
   (both 0.55, `min-of`). A near-conviction-less momentum call and a real provisional starter now share a
   ceiling; if we later want momentum-only to cap *harder*, re-introduce a separate (lower) momentum-only cap.
4. **Age-decay of confidence.** Liveness is a binary gate, so a 5-month-old cluster arms at the same
   confidence as a one-day-old one. Keep the arm gate binary; let only **confidence** fade with conviction
   age. *(CALL_LOGIC §7 roadmap.)*

## C. Deferred builds (roadmap)

1. **M5 — group / per-member ranked view (NEXT).** A theme currently collapses to a single headline; the
   menu — every armed name in the theme, **ranked**, with its setup — is what serves name-selection (the
   operator's flaw). Demanded by real data (LEU core next to OKLO starter). Also: theme/group arming (a
   theme-conviction arms any confirmed member) lives here.
2. **DOE feed — loans award-type group.** The grade rule already maps loans/loan-guarantees → core, but the
   feed doesn't *query* the loans type group yet (note: loans need a sort field other than "Award Amount" —
   that combo 400s). Wire it so the first DOE loan guarantee is discovered, not just gradeable.
3. **More catalyst sources** (CATALYST_CONVICTION §3): the automated material-agreement 8-K detector; NRC
   license-action feed; ETF-launch-as-conviction (with the ETF radar).
4. **LLM counter-case** (M4b): the live Anthropic path behind the assembler's existing hook, deterministic
   template as the guaranteed fallback.
5. **ETF radar, replay harness, umbrella/segment hierarchy** (M5+, per the build plan).
