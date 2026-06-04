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

Per fired `SignalEvent` (see `domain/signal.py`): `detector, security_id, grade, type, score, fired, label, alpha_half_life_days, provenance, asof`.

Two classes of signal, treated differently:
- **Entry triggers** — push a thesis toward Armed (e.g. `insider_conviction`, future `technical_breakout`, `laggard`, `squeeze`).
- **Risk signals** — never push toward entry; feed `counter_case` / `kill_criteria` (e.g. `dilution_clock`). A risk signal firing should *lower* confidence or *block* an Armed call, never raise it.

> **Open modeling question (surface, don't silently resolve):** the current `TriggerType` enum
> `{regulatory, promoter_attention, technical_breakout, clinical_readout, squeeze, personnel}` has no
> value for insider-buying or for risk signals like dilution. Decide whether to (a) add an `insider`
> type + an `is_risk` flag, or (b) split `SignalEvent` into entry-trigger vs risk-signal subtypes.
> `TODO(operator)` to confirm direction before M3 schemas freeze.

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

> Guard: a **risk signal must be able to hold a thesis out of Armed** even if an entry trigger fired
> (e.g. critically short runway). `TODO(operator)`: define the veto/penalty rule.

## 3. Grade decision  `TODO(operator)`

Each fired entry trigger carries a `grade ∈ {flip, core}`. The **call's** grade = the highest-grade fired entry trigger.

- `flip` = fast, sentiment/attention-driven; mean-reverts; trade small and short-dated; do not hold.
- `core` = structural; build the position.

`TODO(operator)`: define per-detector grade rules. *Example strawman (replace):* `insider_conviction` →
`core` if (role ∈ {CEO, CFO}) **and** (≥2 distinct insiders) **and** (open-market code `P`) **and**
(dollar size ≥ threshold); else `flip`; else not fired.

## 4. Verdict mapping  `[PROPOSED]`

Verdict follows deterministically from state + grade (confirm the table):

| State | Condition | `Verdict` |
|---|---|---|
| Incubating | — | `watching` |
| Warming | no `core` fired | `not_yet` |
| Warming | a `flip` is live | `flip_only` |
| Armed | call grade `core` | `core_entry` |
| Armed | call grade `flip` | `flip_only` |
| Managing | position open | `managing` |

## 5. Expression  `[PROPOSED]`

Suggested expression follows the grade (confirm/refine):
- **flip** → small size, short-dated options, explicit "do not hold"; exit-by at/just past the catalyst.
- **core** → spot + options dated *past* exit-by; build into the leaders/shovels of the basket.

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
  `alpha_half_life_days=18, provenance→[form4:DEVCO:...]`  *(an **entry** trigger)*
- `dilution_clock` → `fired=true (risk), label="runway 14 mo, no recent shelf/ATM"`  *(a **risk** signal — feeds counter-case, not triggers_fired)*

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
