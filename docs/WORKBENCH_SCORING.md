# WORKBENCH_SCORING.md — the Workbench's scored read (Phase 2, Slice 3)

> Repo path: `docs/WORKBENCH_SCORING.md`. How the Workbench turns a basket name into **scores** — the
> four 0-4 "pip" meters + the market-cap figure shown per name, **re-derived on read, never persisted**.
> Siblings: `CALL_LOGIC.md` (the back-half brain), `RECALIBRATION.md` (the cutoffs as tunable dials),
> `INVARIANTS.md` (the load-bearing rules this slice added), `PRODUCTION_TENANT.md` (the tenant resolver),
> `ROADMAP.md` (Phase-2 sequencing). The DATA behind the scores is the three ratify bridges
> (`fact_revenue_mix` / `fact_shares_outstanding` / `fact_cash_burn`) + the dilution clock — produced by the
> **extract → ratify hybrid** (`WORKBENCH_EXTRACTION.md`, the production side; this doc is the consumption side).
>
> **Status: BUILT** (Slice 3, PR #50), and **unchanged by every slice since** — the UI (#52), the extract →
> ratify hybrid (#55–#57), the broadener (#58), and the flag-explanation drafter (#59) all FEED or DISPLAY
> these meters without touching the scoring math. Engine: `backend/workbench/scoring.py`.
>
> **Legend:** `[BUILT]` shipped · `[FILED]` deferred, recorded in `RECALIBRATION.md`.

---

## The model — four meters + a figure, re-derived on read (Option B)

The Workbench scores each basket name from the **point-in-time facts**; the engine is a pure function
`score_member(pit, member, cfg) -> ScoredMember`. Two non-negotiables:

- **Scores NEVER persist — they re-derive on read (Option B).** A chain reopened months later shows *current*
  numbers, never a stale snapshot. Only the chain **structure** persists (operationally, on the thesis spine
  — see `ROADMAP.md` "the persistence seam"); the scores are recomputed every read.
- **Every figure comes from a tenant-filtered as-of read** (`PointInTimeData`), so no-lookahead and tenant
  isolation hold for free (`INVARIANTS.md` #4, #5).

Each name carries **four 0-4 pip METERS** — `purity`, `runway`, `catalysts`, `dilution` — plus **market cap
as a FIGURE** (a number, not a meter; `pips` stays null). **`—` (no data) is distinct from `0`** — a meter
with no underlying fact reads "—", never a fake zero.

## Each meter's exact derivation

| meter | formula | fact (PIT accessor) |
|---|---|---|
| **purity** | the largest revenue-line `mix_pct` for the name | `fact_revenue_mix` (`revenue_mix_facts`) |
| **runway** | `cash_usd / (quarterly_burn_usd / 3)` months; cash-generative (burn ≤ 0) → top pip | `fact_cash_burn` (`cash_burn_facts`) |
| **catalysts** | count of **live** catalysts (live = within the catalyst's liveness horizon) + the core/flip grade | `fact_catalyst` (`catalyst_facts`) |
| **dilution** | the raw convert-overhang % | `fact_dilution` via `dilution_clock.overhang_pct` |
| **market cap** | latest `close` × latest `shares` (A+B total-economic) | `fact_price_eod` × `fact_shares_outstanding` |

**Reuse, not rebuild** (the back half already computes two of these):
- **dilution** calls the shared `dilution_clock.overhang_pct` — the **single source of overhang**, read by
  both the risk-veto and the meter. See the single-source rule below + `INVARIANTS.md` #8.
- **catalysts** reuse `catalyst_conviction.liveness` (promoted from `_liveness` to public) — a name's catalyst
  is "live" in the *same* sense for the meter and the back-half conviction key.

## The pre-registration DISCIPLINE (the heart)

**The cutoffs are grounded in what each metric MEANS in absolute terms — NOT fit to the seed.** This is the
same discipline the recalibration pass holds (`RECALIBRATION.md`): the four nuclear seed names cluster (three
at ~100% purity, all with long runways), so setting a cutoff *so a seed name lands on a given pip* would be
overfitting to n=4. Instead:

1. Every cutoff is justified by an **absolute-meaning rationale stated up front** (below), drawn from a
   convention, a first principle, or an existing dial — never from a seed value.
2. The seed names' scores are a **sanity check on the result, shown separately — never the source** of the
   numbers. (E.g. purity 80 = the pure-play convention; LEU lands at 3 because it's 77%, *honestly* not a
   pure-play — the bar wasn't moved to make LEU look any particular way.)
3. All cutoffs live in `CallConfig` (`backend/domain/config.py`), never hardcoded — enforced by a
   **behavioral** magic-number test (a changed cutoff changes a pip) **and** a **lexical** float-literal scan
   of the scorer (`INVARIANTS.md` #7; `tests/workbench/test_scoring.py`).

## The cutoffs, with their grounding

(Tunable dials — the live values + tuning notes are in `RECALIBRATION.md` Table A. The *grounding* is here.)

- **purity** `(10, 25, 50, 80)` % — exposure **concentration** (the % of revenue/business that *is* the
  theme). 80 = the conventional **pure-play** bar; 50 = majority; 25 = a clearly-material segment (above the
  ~10% SEC reportable-segment-materiality floor); 10 = the floor of relevance.
- **runway** `(6, 12, 18, 24)` months — a **funding-risk** gauge grounded in the **financing cycle**: raises
  typically land ~12-18 months out; < 6 months signals distress / an imminent raise; ≥ 24 months is ~two
  raise cycles of cushion. A cash-generative name reads the top pip.
- **catalysts** — live **count** + **grade**: 0 live → 0; 1 live *flip* → 1, 1 live *core* → 2 (a binding
  catalyst is denser than a provisional one); ≥ `catalyst_pip_multi_count` (2) → 3; ≥ `…_dense_count` (3) → 4
  (a rich surface). Deliberately strict — a lone DOE award is not a dense surface.
- **dilution** `(2, 8, 15)` % + the severe dial — raw overhang %; the 4-pip "severe" bar **reuses the
  existing `dilution_overhang_severe_pct` (25%)** the back-half risk-veto already grades against (one place).
  A name with converts below the 1-pip bar reads **0** (a clean, low reading); no convert fact reads **—**.

## The deterministic fit label

A pure function of the pips (NOT prose — the auto-drafted *thesis-fit paragraph* is the LLM's job, Slice 5):
purity drives the base read (`pure-play` / `core exposure` / `partial exposure` / `peripheral` / `off-thesis`
/ `unrated` when purity is "—"), and a **risk flag** is appended — `· funding risk` if runway ≤ 1 pip,
`· dilution risk` if dilution ≥ 3 pips.

## Provenance chips ("behind the scores")

Every meter traces to a fact or a computation (`INVARIANTS.md` #3): **purity** → the `fact_revenue_mix`
`source`/`source_ref` (10-K) + `note`; **runway** → the `fact_cash_burn` `source_ref` (10-Q) + `note`;
**catalysts** → each live `fact_catalyst.source_ref`; **dilution** → the convert accession + the computed
overhang; **market cap** → the price ref + the shares `source_ref`. Filing refs resolve to clickable EDGAR
URLs (`edgar_url`).

## The risk axis — dilution is the only implemented risk signal

`dilution_risk` is the **sole implemented** `Role.RISK_SIGNAL` today (`squeeze` etc. are reserved in the
`Kind` enum but unbuilt). So **dilution IS the risk axis** — its meter is the risk read; there is no separate
fifth "risk" meter. When a second risk signal lands, it becomes its own meter (or feeds this slot); the
four-meter row already reserves the place.

> **Dilution meter polarity (a Slice-4 display concern, `[FILED]`):** dilution is a *pressure* meter — more
> pips = more risk, **opposite** the three goodness-meters. The scoring is correct; the UI needs a clear
> visual distinction so a full dilution meter never reads like a full purity meter. A display problem, not a
> scoring change.

---

## The crown-jewel rules — the *why* that must survive

These are the decisions whose reasoning is easy to lose. State the why next to the what.

### Purity is exposure CONCENTRATION, not safety
A pre-revenue pure-play reads ~**100%** purity on the **business-description** basis — it is *maximally
exposed* to the thesis, which is the honest reading. The funding risk of being pre-revenue lives in
**runway** and **dilution**, NOT in purity. **Never discount purity for being pre-revenue.** The **basis**
rides the fact's `source` field and is **kept distinct, never flattened**: `10-k-segment` (a real
revenue-segment %, e.g. LEU 77% enrichment, SMR 100% single nuclear segment — *revenue-backed*) vs
`10-k-business-description` (a pure-play read off Item 1, e.g. OKLO / NNE 100% — *pre-revenue /
pre-commercial-revenue*). A revenue-backed 100% and a pre-revenue 100% must never read as the same thing.

### The uniform cash rule (the runway numerator)
`cash_usd = cash + equivalents + ALL marketable securities (current AND noncurrent)`. These are liquid
Treasuries regardless of balance-sheet classification, so all-marketable is the honest runway basis. **The
rule is applied at RATIFY time** (when `cash_usd` is ratified) **as a documented convention — the
`cash_runway_basis` constant in `CallConfig` — NOT enforced in the scorer**, which trusts `cash_usd` as
ratified. Every future cash ratification follows the one stated rule. *(Origin: the seed draft mixed bases —
one name's figure included noncurrent marketable securities, another's was current-only — so the rule was
made uniform + documented.)*

### The recurring-vs-one-time burn provenance pattern (the ENTRA1 lesson)
**A cash-burn provenance note MUST separate one-time outflows from recurring burn** — the runway meter is
only honest on the *recurring* figure. The worked example: **NuScale (SMR)** seeds a recurring burn of
**$50.483M/qtr = the reported $314.678M operating cash use LESS the $264.195M one-time ENTRA1 Milestone
Contribution** (a $507.4M strategic-partner obligation under the Partnership Milestones Agreement, recognized
as FY2025 G&A, paid to ENTRA1 in tranches). Seeding the reported $314.678M would have shown a *false ~3-month
runway*; the recurring $50.483M gives an honest ~60 months. **The durable lesson is the pattern, not the
number:** getting this right took **three independent passes** (the build agent, an independent fact-check,
and a web search against the filings) that **each got part of it wrong** — so **no single pass is gospel** on
a sourced financial figure; the provenance note records what was backed out and why, and the operator
ratifies against the filing.

### The dilution overhang single-source rule
`dilution_clock.overhang_pct(facts, sid, asof)` is the **one** raw-overhang computation, shared by the
risk-veto (`score`) and the Workbench meter. **The meter buckets on this raw % and NEVER backs it out of the
clamped risk `severity`** — because severity *saturates* at the severe threshold (`min(overhang /
severe_pct, 1) × risk_block_severity`), so a number recovered from severity is wrong above the severe bar. A
future reader will be tempted to derive the meter from severity; that is the trap. See `INVARIANTS.md` #8.

### The LEU A+B (total-economic shares) lesson
Share counts are the **A+B total-economic** count — Class B is **economic** common stock (the A/B split is
*voting*, not economics), so it counts toward market cap. LEU (Centrus) = Class A 18,953,594 + Class B
719,200 = **19,672,794**; NuScale's Up-C A+B is its total economic share count too. Market cap uses the A+B
count, not Class A alone.

### The through-line on the scorer
Each meter is factored on **its own driving property** — purity on revenue-mix %, runway on cash/burn,
catalysts on live-count + grade, dilution on overhang % — with **no `if kind ==` branching anywhere in the
engine**. This is the back-half through-line (`INVARIANTS.md` #7) applied to the Workbench: behavior is keyed
on the property that drives it, never on a kind literal.
