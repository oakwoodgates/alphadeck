# CATALYST_CONVICTION.md — design pass (#10)

> Repo path: `docs/CATALYST_CONVICTION.md`. The **conviction key for theme / catalyst-driven theses** —
> the second source of Key 1, alongside `insider_conviction`. This is a **design pass: no code** until
> the operator signs off on the marked decisions. It extends `docs/CALL_LOGIC.md` (the two-key model);
> read that first.
>
> **Legend:** `[PROPOSED]` = a default Claude drafted — confirm or change. `TODO(operator)` = your
> judgment; this is where the edge lives (CLAUDE.md), so it is **not** decided here.

---

## 0. The problem it solves

Today the only conviction trigger (Key 1) is `insider_conviction` (Form 4 open-market buys). That fits
single-name insider plays (HIMS, UNH) but **not** theme / catalyst-driven theses. The small-scale-nuclear
basket has **no insider buys** — its "why now is real" is regulatory / demand / commercial catalysts. So
nuclear warms on confirmation (the sector breakout) but can **never arm** (no conviction key). That is the
gap this closes: a deterministic catalyst supplies the conviction key, so a theme can arm when a real
catalyst fires **and** the market confirms.

> It is the theme analog of *"insiders put real money in."* Same role in the two-key model; different source.

## 1. Organizing principle  `[PROPOSED]`

A **catalyst-conviction is a deterministic, verifiable, *fundamental commitment* that changes the
trajectory** — the structural analog of an open-market insider buy. The grade follows directly:

- **core** = a *binding, fundamental* commitment (a signed multi-year power-offtake / PPA, an NRC operating
  license, a DOE loan guarantee) — capital or contracted revenue is now real; *build the position*.
- **flip** = a *soft / sentiment* event (a non-binding MOU/LOI, promoter attention, a thematic ETF launch) —
  fast, mean-reverting; *small and short-dated*.

This keeps the grade **deterministic** (it falls out of the parsed/ratified facts — filing item code,
counterparty, structured terms), never the model's read. Conviction is the most important signal in the
system; anchoring it to *verifiable commitment* (not narrative) is the whole point.

## 2. Signal shape  `[PROPOSED]` (fits the existing `SignalEvent` contract — no new core schema)

| field | value |
|---|---|
| `role` | `entry_trigger` — a **conviction** kind, added to `cfg.conviction_kinds` (alongside `insider`). So a catalyst **warms**; a breakout (confirmation) **arms**. |
| `kind` | new `catalyst` *(one kind; the specifics ride `type`)* — `TODO(operator)`: one kind vs several (`power_offtake \| regulatory_clearance \| gov_award \| etf_launch`). One-kind-plus-`type` keeps the taxonomy small; I lean that way. |
| `type` | the catalyst nature (existing field): `regulatory \| commercial \| gov_funding \| emergence \| …`. |
| `grade` | `core \| flip` per §1. |
| `alpha_liveness_days` | **graded** (reuses the #30/#32 machinery): core = months, flip = weeks (§7). |
| `security_id` | the **subject** of the catalyst — the 8-K filer / the awardee. Name-specific → co-locates with *that name's* breakout via the existing per-security model (§5). |
| `provenance` | the filing accession / dataset record **+ the parsed terms** (counterparty, $ size, term) — show the work, like the converts detector. |
| `score` | from the parsed terms (size / bindingness), bounded — same spirit as `insider_conviction._score`. |
| `asof` | the catalyst's **event date** (filing/award date), not the query asof — anchors liveness to when conviction formed, exactly like the insider cluster. |

## 3. Data sources  `[PROPOSED, ranked]` — deterministic or operator-ratified (invariant #3)

Every trigger traces to a computation against data **or** a one-time human ratification. The LLM may draft
the *explanation* (citing the source) but **never fires the trigger, sets the grade, or invents a number.**

1. **Material-agreement 8-K (Item 1.01)** — power-purchase / offtake / hyperscaler-datacenter deals. **MVP
   first brick.** Reuses the proven EDGAR 8-K brick (the converts detector already does fetch → clean →
   regex-parse). Deterministic: Item 1.01 **+** a configured counterparty/keyword signature → parse
   counterparty + term → fire on the **filer**. Directly arms the real nuclear story (the 2024-25 nuclear
   ↔ hyperscaler power deals). `core` (signed multi-year offtake) vs `flip` (MOU/LOI).
2. **Federal awards — USAspending.gov API** — DOE loan guarantees / grants. A *structured* federal-award
   API (deterministic, no parsing guesswork), mapped to the awardee. Strong, clean second source.
3. **NRC license actions** — reactor design approval / combined operating license. NRC public data
   (ADAMS / licensing-status pages). **Feasibility TBD** — assess whether there's a structured feed during
   the spike; deterministic if so, else route through (5).
4. **N-1A / 485 thematic ETF launch** — a new theme ETF = an *emergence* signal (institutions now express
   the theme). **Theme-level, not name-specific** → low-grade and a *group* signal → belongs with the ETF
   radar + the M5 group-arming mode, **not** the single-name arm. Deferred.
5. **Operator-ratified catalyst (the human bridge)** — for catalysts the parser can't yet capture, the
   operator ratifies a specific event **once** (source URL + date + grade), stored as a fact with
   provenance. **Human-sourced, not model-sourced** → fully honors invariant #3, and it arms a theme
   *immediately* while the deterministic detectors (1-3) mature. The Workbench (deferred) is its eventual
   home; for now a CLI / seed entry.

## 4. Invariant compliance (#3 — the LLM augments, never sources)

- **Firing + grade** come from a deterministic parse (1-4) **or** an operator ratification (5) — *never*
  the model. "Is this 8-K a core power deal?" is answered by the item code + counterparty signature +
  structured terms, or by the operator — not by the LLM's judgment.
- The LLM only **drafts the call-card explanation**, citing the accession/record. It cannot classify,
  fire, or number.

## 5. Co-location  `[PROPOSED]`

Catalysts are **name-specific** (the filer/awardee), so they slot into the existing per-security
co-location with **no new mechanism**: a catalyst on `OKLO` + a breakout on `OKLO` → `OKLO` arms. A
catalyst alone still only **warms** (no confirmation), and a breakout alone still only **warms** (no
conviction — today's honest nuclear state). **Theme-wide** catalysts (a sector DOE program, an ETF launch)
need a *theme-conviction-arms-any-confirmed-member* mode — that is the **M5 group-arming** work, explicitly
deferred (and already flagged in the assembler).

## 6. Grade rule  `TODO(operator)`

Proposal (refine — this is the edge): **core** = binding fundamental commitment (signed multi-year
PPA/offtake; NRC operating license; DOE loan guarantee). **flip** = soft (MOU/LOI; promoter attention;
ETF launch). Which catalysts, if any, would you grade differently?

## 7. Alpha-liveness by grade  `TODO(operator)`

Proposal (reuses the graded `alpha_liveness_days` from #30/#32): **core catalyst ≈ 180d** — a structural
re-rating plays out over ~6 months, same horizon as an insider cluster (you could argue *longer* for a
multi-year contracted offtake). **flip ≈ 21-30d.** Set the core horizon on the merits (the catalyst's real
edge horizon), not to fit any one name — same discipline as the insider liveness call.

## 8. How it composes — the unlock

```
nuclear today:    confirmation (breakout) only            -> Warming forever (no conviction key)
nuclear with #10: a name signs a hyperscaler PPA (core    -> that name ARMS as core_entry
                  catalyst) AND breaks out (confirmation)     when both keys co-locate
```

The discipline is unchanged — it's the *same two-key model*, now reachable for themes: catalyst alone =
not yet; breakout alone = not yet; both, co-located = arm. No new arming logic; just a second conviction
source feeding Key 1.

## 9. MVP plan — after sign-off (then code, smallest first)

1. Add the `catalyst` kind (+ `type`s) to the enums; add it to `cfg.conviction_kinds`; add graded
   `catalyst_*_alpha_liveness_days` to config.
2. Build **one** detector — the **material-agreement 8-K** detector (deterministic, reuses the EDGAR
   brick) — **plus** the **operator-ratified** bridge (5).
3. **Spike first (like UNH):** does a real nuclear basket name (SMR/OKLO/NNE/LEU) have a parseable
   material-agreement 8-K (a power/offtake deal) at a date that **co-locates** with its breakout → does
   nuclear **ARM as core_entry** on real data? Report the finding **before** building the seed/UI.
4. If the deterministic 8-K path is thin for these names, the **operator-ratified** bridge arms nuclear
   immediately (honest, human-sourced) while (1-3) mature.

## 10. Open questions — sign-off before any code

1. **First source:** start with the **material-agreement 8-K** detector + the **operator-ratified** bridge
   (my recommendation — reuses the EDGAR brick, directly arms nuclear, deterministic)? Or lead with
   USAspending/DOE awards, or NRC?
2. **Grade rule** (§6) — confirm core-vs-flip, or adjust.
3. **Core liveness** (§7) — 180d, or a different merits-based horizon (longer for multi-year offtakes)?
4. **Co-location** (§5) — confirm name-specific now, theme/group arming deferred to M5.
5. **Kind granularity** (§2) — one `catalyst` kind (+`type`) vs several kinds.

## 11. Out of scope / deferred

Theme / group arming (M5); ETF-launch-as-conviction (ETF radar + M5); NRC if no clean feed (→ ratified);
the LLM-written counter-case/explanation (M4b); the Workbench UI for ratifying catalysts (later — CLI/seed
for now).
