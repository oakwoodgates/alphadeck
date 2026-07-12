# THEME_CONVICTION.md — the theme conviction key (M5 Part B)

> Repo path: `docs/THEME_CONVICTION.md`. The **basket-level fallback source of Key 1**, alongside
> `insider_conviction` (single-name) and `catalyst_conviction` (name-specific). It extends
> `docs/CALL_LOGIC.md` (the two-key model) and parallels `docs/CATALYST_CONVICTION.md`; read those first.
>
> **Status: BUILT** (M5 Part B). An operator-ratified, **thesis-level** conviction supplies Key 1 as a
> *fallback* for a basket member that has no name-specific conviction of its own. It relaxes **only where
> Key 1 comes from** — nothing else about arming changes.
>
> **Legend:** `[BUILT]` shipped · `[APPROVED]` operator-confirmed rule · `[DEFERRED]` filed, not built.

---

## 0. The problem it solves

A real theme has names the operator believes in that **break out but carry no name-specific conviction**
(no insider buy, no DOE award of their own). Before M5b the platform left them in the **watch tier**
("moving, no conviction yet") — correct, but incomplete when the operator's *thesis-level* conviction is
the real "why now." M5b lets that thesis-level belief arm such a name — as a disciplined **starter**, never
a core — when the name **also** confirms on its own volume-backed breakout.

> It is the theme analog of *"I believe in this sector"* — graded, horizon'd, provenanced, and capped, so
> belief can support a provisional timing call without masquerading as a name-specific, structural
> conviction. The grade/verdict is call strength, not position size.

## 1. The mechanic

Today a member arms only when conviction (Key 1) and confirmation (Key 2) **co-locate** on the same
security. M5b lets an operator-ratified **theme conviction** supply Key 1 **as a fallback** for a member
with no live own conviction. **Key 2 stays the member's own volume-backed breakout; both keys must be live
together at the as-of.** Nothing else about arming changes.

## 2. The seven settled rules `[BUILT]`

1. **First-class ratified fact.** `fact_theme_conviction` (bitemporal, append-only, provenanced), ratified
   via `ingest/theme_conviction.py` (mirrors `ingest_catalyst`), keyed by **thesis**, not security. Arming
   re-derives from it on read.
2. **Grade capped at starter.** The broadcast emits the conviction event at **`flip`** → `entry_grade =
   weaker_grade(flip, core) = flip` → the existing setup-strength cap (`starter_confidence_cap`) applies.
   **Belief can never mint a core call-strength class** — the load-bearing discipline. Neither label sizes
   a trade.
3. **Operator horizon + expiry.** Liveness = `horizon_end − valid_from`, else
   `cfg.theme_conviction_default_horizon_days` (365). The conviction expires past its horizon unless
   re-ratified — no zombie narratives. Same liveness rule as a catalyst (decoupled from grade).
4. **Curated basket members, each on its OWN volume-backed confirmation.** Broadcast iterates the basket
   only (can't reach outside) and arms a member **iff** it has a live confirmation-kind event of grade
   **`core`** (volume-backed). Momentum-only (flip) breakouts are excluded; confirmation is never borrowed.
5. **Stronger of {own, theme}.** A member with its own live conviction is **not** theme-broadcast — own
   wins; the theme is a pure fallback for names that have none.
6. **Gated by the M5a ranking + a visible flag.** Theme-armed members are starters, so they rank below
   cores and — within a band + grade — below own-conviction names (the `is_own` tiebreak). Each carries a
   `theme_armed` flag so the basis is visible (armed on the theme vs on its own signal). No new cap
   subsystem; surfacing rides M5a's `rank_members`.
7. **The floor.** A member whose own conviction has **lapsed** is absent from the event stream (its
   detector returns `None` when not live), so it **falls back to a theme-armed starter** — if it's still in
   the basket, still has a live volume-backed confirmation, and the theme is still live — clearly
   downgraded + flagged, rather than dropping to the watch tier.

## 3. Architecture `[BUILT]` — broadcast-as-event

The theme conviction is a thesis-level bitemporal fact. A thin detector
(`signals/theme_conviction.py`) reads it (`detect_fact`, the only DB-touching step) and **broadcasts a
flip-graded conviction `SignalEvent` onto each eligible member's `security_id`** (`broadcast`, pure). From
there it is **just another conviction event**: adding `Kind.THEME_CONVICTION` to `cfg.conviction_kinds`
makes the existing co-location (`conv_secs & conf_secs`), ranking, setup-strength cap
(`starter_confidence_cap`), watch-tier subtraction
(`conf_secs − conv_secs`), and the per-member risk veto all work **with no change to the guarded
`assemble_call`**. The broadcast runs in `pipeline/call_for_thesis.py` after the per-member detector loop
(eligibility reads the assembled member stream). The entire M5b eligibility discipline (rules 4/5/7) lives
in `broadcast`; the assembler stays generic.

*Why broadcast over a special-cased `thesis.theme_conviction` field in the assembler:* it reuses the
persistence pattern (no new subsystem), keeps the assembler free of any `if kind == …` branch (the
through-line), gives provenance + replay for free (the event rides `triggers_fired`), and keeps the golden
assembler tests pure (theme arming is exercised by passing a `THEME_CONVICTION` event directly).

## 4. Ranking `[APPROVED]` — freshness wins (Q1)

The freshness BAND stays primary (M5a doctrine). `is_own` is a **within-band tiebreak after grade**:
within the same band + grade, an own-conviction name outranks a theme-armed one. A **fresh** theme starter
**can** still outrank a **lapsing** own core (consistent with the M5a OKLO-over-lapsing-LEU order) — `is_own`
does not lift a lapsing core over a fresh theme name. Tuple, best-first:
`(is_fresh, grade_rank, is_own, runway_days, conviction_score, id)`. The within-band weighting/placement is
a RECALIBRATION dial; `headline_lapsing_soon_days` is now load-bearing for this belief-vs-data line (how
readily a fresh theme starter leapfrogs a lapsing own core).

## 5. Through-line audit (invariants)

- **Theme conviction is the weaker key** (emitted `flip`) → it caps the call's setup strength via the
  **grade property**, never an `if kind == THEME_CONVICTION` behavior branch. Grade is the categorical
  call-strength class, not sizing or expression guidance. The only legitimate reads of the theme kind are:
  set membership in `conviction_kinds`; the `own_conviction_kinds` exclusion (`conviction_kinds −
  {THEME_CONVICTION}`, the seam that keeps a future conviction kind inheriting "own" automatically); the
  `theme_armed` **display flag**; the `is_own` **ranking tiebreak** (provenance-based, not behavioral).
- **No model-sourced numbers (#3):** grade + horizon are operator inputs on the fact, never the LLM's.
- **Provenance (#6):** every theme-armed call records the ratified `source_ref` (it rides `triggers_fired`).
- **Bitemporal / Option B:** the fact is append-only; arming re-derives on read (`as_of_thesis` + `known_at`
  keep replay honest). Production is a fresh tenant; never a destructive wipe.

## 6. Seeded demo state `[BUILT]`

`seed_nuclear_theme_conviction` ratifies a **flip** theme conviction on the nuclear thesis (the ADVANCE Act
+ DOE HALEU/reactor-pilot programs + AI/datacenter power demand; ~12-month operator horizon → live at the
2026-06-05 demo). Result at 2026-06-05, alongside the DOE catalysts (OKLO flip OTA, LEU core contract):

- **SMR** — a 2026-06-02 **volume-backed (CORE, ~1.96×)** breakout + the theme fallback → a capped,
  **theme-armed STARTER**. The lone theme-armed name.
- **NNE** — a **momentum-only (flip, ~1.19× < 1.5×)** breakout → rule 4 correctly **excludes** it → NNE
  stays in **watch**. One theme-armed name is the right outcome (the volume gate working) — *do not* weaken
  rule 4 to fill the empty slot.
- **Ranking** (freshness-primary): **OKLO** headlines (fresh own flip), **SMR** is #2 (a fresh theme
  starter, above the lapsing own core), **LEU** is #3 (own core, lapsing → 2026-06-30). own-above-theme is
  only a within-band tiebreak — it does not lift the lapsing LEU core over the fresh SMR theme starter.

> Data note: SMR faded 13.95 → 10.50 by 06-05, but its 06-02 CORE breakout is still inside the 10-day Key-2
> liveness window, so it arms on a name down ~25%. That is **pre-existing** breakout-liveness behavior (not
> introduced by M5b) and illustrates exactly why the starter cap exists; the demo as-of stays honest at 06-05.

## 7. Dials (RECALIBRATION.md)

`theme_conviction_default_horizon_days` (365, the horizon when no `horizon_end` is ratified — also the
upper-bound / re-ratification-cadence knob); the within-band tiebreak weighting (own-above-theme placement
is shape, the weight is the dial); `headline_lapsing_soon_days` (now load-bearing for belief-vs-data, §4).
The theme-armed setup-strength cap is the existing `starter_confidence_cap`; the volume-backed bar is the
existing breakout params. A **hard surface cap** on how many theme-armed names appear is `[DEFERRED]` —
rely on ranking; add only if Phase-1 replay shows flooding.

## 8. Out of scope / deferred `[DEFERRED]`

A bespoke **revoke** endpoint (v1 revoke = ratify a superseding row with `horizon_end = today` → liveness
≈ 0 → the detector drops it). The **hard surface cap** (above). The **purity / archetype bar** (don't let
the theme arm an off-thesis basket name) — Phase 2, needs the Workbench's purity scores; the curated basket
is the membership bar for now. The **Workbench** as the ratification front-door — the Cockpit/CLI host it
now; the fact is surface-agnostic. Automated (non-ratified) theme-conviction sources; per-member theme
overrides; any non-grade/non-horizon "theme strength" number.
