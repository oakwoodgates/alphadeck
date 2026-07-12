# CATALYST_CONVICTION.md — the catalyst conviction key (#10)

> Repo path: `docs/CATALYST_CONVICTION.md`. The **conviction key for theme / catalyst-driven theses** —
> the second source of Key 1, alongside `insider_conviction`. It extends `docs/CALL_LOGIC.md` (the two-key
> model); read that first.
>
> **Status: BUILT** (was a design pass; now shipped). Two sources of catalyst facts exist: the
> **operator-ratified bridge** (`ingest/catalyst.py`, `pipeline/ratify_catalyst.py`) and the **automated
> DOE/USASpending feed** (`ingest/doe/`, merged #37). The design sections (§1–§11) below are kept as the
> rationale of record; the **Sign-off** block is the source of truth for what's built vs deferred.
>
> **Legend:** `[BUILT]` shipped · `[APPROVED]` operator-confirmed rule · `[DEFERRED]` filed, not built.

---

## Sign-off — what's built vs deferred

**Built `[BUILT]`:**
- **Operator-ratified bridge** — `ingest/catalyst.py` + `pipeline/ratify_catalyst.py`: a human ratifies a
  specific event once (source URL + date + grade + horizon) → a `fact_catalyst` row. Still the path for
  catalysts no automated feed covers.
- **Automated DOE/USASpending feed** (#37) — `ingest/doe/`: the first automated catalyst source. Discovers
  DOE awards for a hand-curated entity allowlist, resolves them **exactly by `recipient_id`** (never fuzzy),
  fetches award detail, and derives grade + horizon deterministically. The broadened spike (§9) chose DOE
  over the 8-K deal-keyword path (a polarity trap — surfaces financings/dilution, not power deals). Full
  data-source detail + the entity traps in `docs/DATA_SOURCES.md`.
- **Signal shape** (§2): `role=entry_trigger`, **one** `kind=catalyst` + a `type` discriminator
  (`gov_funding` for DOE), fires on the **subject security** (name-specific co-location, §5), real provenance.
- **Liveness ≠ grade (option A).** A catalyst's liveness is its relevance **horizon** — the agreement's
  period of performance from the structured record, else a 365d default — DECOUPLED from grade. Grade does
  one job: categorical **call strength**. It never sets position size or expression. (Insider stays
  grade-coupled — there grade and horizon coincide. See the
  through-line in CALL_LOGIC.)
- **Verdict keyed on horizon, not kind (CALL_LOGIC §4).** A provisional (`flip`) conviction with a *long*
  signal-validity horizon → `starter_entry`; *short* horizon → `flip_only`. These are call-strength/readiness
  labels, not sizing or exit instructions; the next kind inherits correct behaviour from its own horizon
  (no `if-kind` branch).
- **Invariant #3:** firing + grade are deterministic-parse or operator-ratified — **never** the model.
- **Append-only / bitemporal storage:** `fact_catalyst` (+ `horizon_end`); a correction is a new row;
  `tenant_id` per row → production is a **fresh tenant**, never a destructive wipe.

**Deferred `[DEFERRED]`:** theme/group arming (a theme-conviction arms any confirmed member) is now
`[BUILT — M5b, see docs/THEME_CONVICTION.md]`; the automated material-agreement 8-K detector; NRC license-action feed; ETF-launch-as-conviction; the
loans award-type group in the DOE feed (the grade rule already handles loans → core, but the query group isn't
wired — see RECALIBRATION). The age-decay-of-setup-strength refinement (`confidence` in code) stays filed
(CALL_LOGIC §7).

## Grade rule `[APPROVED]` — customer vs sponsor

Grade is the **nature/call-strength class of the commitment, never its obligation amount and never a position
size**. Obligation magnitude may affect the trigger score and experimental setup strength (`confidence` on
the wire) within a grade. The principle: is the funder your **customer** or your **sponsor**?
- **`core`** — DOE-as-customer/financier: a procurement **contract** (DOE buys your product = contracted
  revenue) of real size, or a **loan / loan guarantee** (committed financing).
- **`flip`** — DOE-as-sponsor: a grant / cooperative agreement / OTA / authorization pathway (funds your
  development = support, not revenue). Provisional / fast setup; no sizing or instrument instruction.

**Award type is the proxy; customer-vs-sponsor is the principle.** Precedent: LEU's $317M HALEU production
**contract** → `core`; OKLO's $0 reactor-pilot **OTA** → `flip`; a $148M cooperative agreement stays `flip`
(obligation magnitude, not nature — its trigger score may affect setup strength within the flip grade, but
never position size). Deterministic implementation in
`ingest/doe/feed._derive_grade` (the `$10M` contract floor is `cfg.doe_core_min_obligation_usd`); ratified
catalysts set grade by the same principle at ratification time.

**Calibration dials** (RECALIBRATION.md): the `$10M` contract floor; the `365d` default horizon (when no term
is published); the legacy-named `90d` hold threshold (a short-vs-long signal-window verdict cutoff, not a
mandatory trade-exit rule; the clean gap between insider-flip ~18d and core/catalyst ≥180d).

**Seeded demo state — two real DOE catalysts, on opposite ends of the grade.** Both ratified via the
bridge from USAspending records, both co-located with a live 2026-06-02 breakout:
- **OKLO** — DOE Reactor Pilot Program **OTA** (`DENE0009589`), **flip** (authorization pathway, $0 DOE
  obligation), horizon → 2029. Alone, it arms OKLO as a disciplined **starter**.
- **LEU** (Centrus) — DOE **HALEU production contract** (`89243223CNE000030`), **core** (~$317M obligated
  *and exercised* = binding contracted revenue), horizon = the contract's **base term → 2026-06-30** (the
  ~$1.1B all-options ceiling to 2028 is DOE-discretion, **not** folded in). It arms LEU as a real
  **core_entry** call-strength verdict, with the signal-validity window ending on 2026-06-30 — a
  near-the-edge renewal cliff, not an open-ended core signal. Entity:
  `AMERICAN CENTRIFUGE OPERATING, LLC → Centrus → LEU` is the **first row** of the
  curated awardee→ticker table the automated feed will reuse.

With both seeded, the theme **headlines the binding name** (LEU `core_entry`), because the assembler arms a
thesis on its **strongest** member — a binding revenue contract correctly out-ranks a provisional
authorization. OKLO's starter is still computed beneath; true per-member side-by-side is the **M5** group
view. The **setup-strength cap** (`starter_confidence_cap`) generalized with this work: it now caps **any**
starter (the weaker *entry
grade*), so OKLO's provisional starter no longer reads loud just because its breakout is strong — same
"key on the weaker key, not the kind" generalization as the verdict (CALL_LOGIC §7).

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
  license, a DOE loan guarantee) — capital or contracted revenue is now real; the setup is structural.
- **flip** = a *soft / sentiment* event (a non-binding MOU/LOI, promoter attention, a thematic ETF launch) —
  fast and more likely to mean-revert.

These grades classify call strength/nature only. They do not select position size, instrument, or expression.

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
| `score` | from the parsed terms (obligation magnitude / bindingness), bounded — same spirit as `insider_conviction._score`; it contributes to setup strength, not position sizing. |
| `asof` | the catalyst's **event date** (filing/award date), not the query asof — anchors liveness to when conviction formed, exactly like the insider cluster. |

## 3. Data sources  `[ranked]` — deterministic or operator-ratified (invariant #3)

> **Status:** #5 operator-ratified bridge `[BUILT]` · #2 USASpending DOE feed `[BUILT]` (#37, see
> `docs/DATA_SOURCES.md` for the entity allowlist + traps) · #1 8-K, #3 NRC, #4 ETF-launch `[DEFERRED]`.
> The spike chose to ship #2 *before* #1 — the 8-K deal-keyword path is a polarity trap (financings, not
> power deals). The ranking below is the original rationale of record.

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

## 6. Grade rule  `[APPROVED — see the Grade rule section above]`

Resolved as **customer vs sponsor** (contract / loan-guarantee = `core`; grant / cooperative-agreement /
OTA = `flip`; by commitment nature, not obligation amount or position size). The authoritative statement +
precedent is the **Grade rule** section
near the top. (This section's original instinct — binding=core, soft=flip — was right; customer-vs-sponsor
made it precise and added the loan-guarantee=core edge.)

## 7. Alpha-liveness  `[SUPERSEDED by option A]`

⚠️ This section proposed a *graded* catalyst liveness (core≈180d / flip≈21d) — **option A reversed that.** A
catalyst's liveness is **NOT** grade-coupled; it is the agreement's **relevance horizon** (its period of
performance from the structured record, else the `365d` default), independent of grade. Grade classifies
call strength only; it does not size a position. (Insider liveness *stays* grade-coupled — CALL_LOGIC §3 +
the through-line.) Kept to mark the change.

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
