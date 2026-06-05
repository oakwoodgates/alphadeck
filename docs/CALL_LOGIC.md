# CALL_LOGIC.md — How a Call Is Made

> Repo path: `docs/CALL_LOGIC.md`. This is the platform's **brain** — the spec for how `SignalEvent`s
> become a lifecycle state, verdict, grade, expression, and exit-by. It is the "make the call and show
> its work" promise made concrete. It must be implemented as a **named, golden-tested component**
> (a call-assembler), never as an emergent side effect of the pipeline.
>
> **Legend:** `[PROPOSED]` = a starting default Claude drafted; confirm or change.
> `TODO(operator)` = needs the trader's judgment — this is where the edge lives; do **not** invent and present as decided.

---

## 0. Where this sits

```
SignalEvent[]  ──►  call-assembler (this spec)  ──►  CallCard
(from detectors)     pure f(thesis, events, asof)     (served by API, rendered in Cockpit)
```

The assembler is **pure and deterministic**: same thesis + same signal events + same `asof` → same CallCard. The LLM stub fills only `counter_case` and explanatory prose (citing existing evidence IDs); it never sets state, verdict, grade, or triggers. The `calls` table stores assembled CallCards as the **accountability record** (what the platform asserted, when) — it is **not** the read path. The API recomputes the CallCard live at the requested `asof`.

## 1. Inputs

Per `SignalEvent` (see `domain/signal.py`): `detector, security_id, role, kind, type, grade, score, fired, label, alpha_half_life_days, provenance, asof`.

**Signal taxonomy `[SPECIFIED]` (confirmed).** Three orthogonal fields:
- **`role`** — `entry_trigger` vs `risk_signal`. Only entry triggers can turn the two keys; risk signals feed `counter_case` / `kill_criteria` / confidence and never raise readiness.
- **`kind`** — what produced the signal: `insider | technical_breakout | laggard | squeeze | etf_launch | etf_flow | dilution_risk | …` (extensible).
- **`type`** — the catalyst nature where one applies: `regulatory | promoter_attention | clinical_readout | personnel | …`. Optional; many signals (e.g. a breakout) have a `kind` but no catalyst `type`.

So `insider_conviction` is `role=entry_trigger, kind=insider`; `dilution_clock` is `role=risk_signal, kind=dilution_risk`; a new ETF launch is `role=entry_trigger (low-grade), kind=etf_launch, type=…`; ETF flows are `kind=etf_flow`.

## 2. State-transition rules  `TODO(operator)`

The lifecycle is a **loop**, not a ratchet: `Incubating → Warming → Armed → Managing`, and Armed/flip can fall back to Incubating.

Fill the thresholds — these are the heart of the opinionated call:

| Transition | Condition (fill in) |
|---|---|
| → **Incubating** | Thesis parked; no entry trigger fired. *(default state)* |
| Incubating → **Warming** | `TODO(operator)`: e.g. "≥1 entry trigger fired but none at `core` grade," and/or attention/regulatory legs present. |
| Warming → **Armed** | `TODO(operator)`: e.g. "≥1 `core` entry trigger fired **with confirmation** (a second corroborating trigger, or a volume-confirmed breakout)." Define what 'confirmation' means. |
| any → **Managing** | Operator has logged a fill (position exists). |
| Armed/Warming → **Incubating** | `TODO(operator)`: e.g. "all fired triggers aged past their half-life with no entry," or a flip resolved. |

> **Risk-veto rule `[SPECIFIED]` (confirmed).** A risk signal *penalizes confidence* and, when severe
> (e.g. critically short runway / imminent dilution), *blocks the Armed call* even if an entry trigger
> fired — a soft veto on **timing**. It never vetoes the **thesis** itself (that stays the operator's call).
> Severity threshold is `TODO(operator)` / calibrated; the block-vs-penalize behavior is fixed.

## 3. Grade decision  `TODO(operator)`

Each fired entry trigger carries a `grade ∈ {flip, core}`. The **call's** grade = the highest-grade fired entry trigger.

- `flip` = fast, sentiment/attention-driven; mean-reverts; trade small and short-dated; do not hold.
- `core` = structural; build the position.

`TODO(operator)`: define per-detector grade rules. *Example strawman (replace):* `insider_conviction` →
`core` if (role ∈ {CEO, CFO}) **and** (≥2 distinct insiders) **and** (open-market code `P`) **and**
(dollar size ≥ threshold); else `flip`; else not fired.

## 4. Verdict mapping  `[PINNED]`

Two grades are kept distinct: the **conviction grade** (the conviction key — the *thesis* quality) and
the **entry grade** = the *weaker* of the two keys (the *action* to take). The verdict the operator acts
on is driven by the **entry grade**, so a core thesis whose confirmation hasn't volume-confirmed reads
as a **starter**, never a bare `core_entry` (which invites over-committing — the operator's documented
flaw). The conviction grade is shown separately so the thesis's core quality isn't lost; a starter is
the upgrade path to a full core entry.

| State | Condition | `Verdict` |
|---|---|---|
| Incubating | — | `watching` |
| Warming | conviction `core`, no confirmation | `not_yet` |
| Warming | conviction `flip` live | `flip_only` |
| Armed | conviction `flip` | `flip_only` (small, short-dated, do-not-hold) |
| Armed | conviction `core`, entry `core` (volume-backed confirmation) | `core_entry` (build to core size) |
| Armed | conviction `core`, entry `flip` (momentum-only confirmation) | `starter_entry` (core thesis, starter entry; upgrades to core when volume confirms) |
| Managing | position open | `managing` |

A `starter_entry` is also surfaced as reduced confidence (§7), a volume-gap counter-case (§8), and a
cautious "start small; build to core when volume confirms" expression (§5).

## 5. Expression  `[PROPOSED]`

Suggested expression follows the grade (confirm/refine):
- **flip** → small size, short-dated options, explicit "do not hold"; exit-by at/just past the catalyst.
- **core** → spot + options dated *past* exit-by; build into the leaders/shovels of the basket.
- **ETF / safe sleeve** → for durable, long-duration exposure to the *whole* theme (usually offered at the umbrella/thesis level, not per Armed segment): a thematic ETF from the ETF radar. Lower torque — gives up the leader/lotto upside for duration and diversification. Always presented with fund internals (holdings, weights, expense ratio, AUM, liquidity) so the operator sees whether the ETF actually expresses the thesis. This is the floor, not the alpha; it can run *alongside* the single-name expressions, not instead of the call.

## 6. Exit-by & catalyst surface  `[SPECIFIED]`

```
exit_by        = asof + max(alpha_half_life_days over fired ENTRY triggers)
catalyst_surface = [ c for c in thesis.catalysts if c.when_date is not None and c.when_date <= exit_by ]
```
Undated/fuzzy catalysts (no `when_date`) are shown for context but excluded from the surface filter.
The Cockpit flags any binary event in `catalyst_surface` as risk crossed before exit.

## 7. Confidence  `TODO(operator)`

`confidence ∈ [0,1]`, rendered as the Armed card's bar. Must be **calibrated**, not loud — a marginal
2-of-N setup reads low. Risk signals reduce it.

`TODO(operator)`: define the function. *Strawman (replace):* a saturating function of
`(count of fired entry triggers, their scores, cross-detector agreement)` minus a penalty per active
risk signal, capped so a single-detector call never reads "high."

## 8. Counter-case  (LLM prose, not a computed field)

The `counter_case` is **prose from the LLM stub**, assembled from: the thesis `kill_criteria`, any active
risk signals, and the `missing[]` triggers. It cites existing evidence IDs only and **cannot** alter
state/verdict/grade/triggers. If the LLM is unavailable, fall back to a deterministic template listing
kill-criteria + missing triggers.

---

## 9. Worked example — the shape of a correct Armed call

> Demonstrates the *flow and output shape*. Bracketed thresholds are placeholders that §2–§3/§7 will pin down.

**Setup.** `asof = 2026-06-02`. Thesis **Psychedelic Therapy** (Warming). Basket member `DEVCO` has a
cached cluster of Form 4 open-market buys. The pipeline runs detectors as-of and emits:

- `insider_conviction` → `fired=true, grade=core, score=0.82,`
  `label="3 insiders incl. CEO+CFO bought $2.1M open-market (code P), 9d pre-earnings",`
  `alpha_half_life_days=18, provenance→[form4:DEVCO:...]`  *(`role=entry_trigger, kind=insider`)*
- `dilution_clock` → `fired=true, label="runway 14 mo, no recent shelf/ATM"`  *(`role=risk_signal, kind=dilution_risk` — feeds counter-case + confidence, not triggers_fired)*

**Assembly.**
- **State:** one `core` entry trigger fired + [confirmation rule §2] satisfied → **Armed**.
- **Grade:** `core` (highest fired entry grade).
- **Verdict:** `core_entry` (§4).
- **Expression:** `[PROPOSED]` "spot + 6–8wk calls on DEVCO; size as core" (§5).
- **exit_by:** `2026-06-02 + 18d = 2026-06-20` (§6).
- **catalyst_surface:** earnings `2026-06-11` ≤ exit-by → **crossed** (flag it).
- **confidence:** `0.82 → ~0.78` after the single-detector cap and a small dilution penalty (§7).
- **triggers_fired:** `[insider_conviction → ↗ Form 4 provenance]`. **missing:** `[technical_breakout]`.
- **counter_case:** *(LLM prose)* "Single-detector conviction; no volume-confirmed breakout yet; runway
  is adequate but unconfirmed; an earnings print falls inside the hold window." cites the dilution + Form 4 evidence.

**Resulting CallCard** → renders **Armed / "The Call"**: verdict `core_entry`, confidence bar ~78%, the
insider trigger with a working ↗ source link, the counter-case, and `Act / Override / Snooze`.

This is the loop the north star requires a pass to demonstrate **on real data** — not a Warming readiness
card, and not static demo data.

---

## What still needs you

Everything marked `TODO(operator)`: the state-transition thresholds (§2), per-detector grade rules (§3),
the confidence function (§7), and the trigger-type taxonomy decision (§1). Those encode your trading
judgment and shouldn't be guessed. The `[PROPOSED]` and `[SPECIFIED]` parts are ready to build against.
