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

Per `SignalEvent` (see `domain/signal.py`): `detector, security_id, role, kind, type, grade, score, fired, label, alpha_liveness_days, provenance, asof`.

**Signal taxonomy `[SPECIFIED]` (confirmed).** Three orthogonal fields:
- **`role`** — `entry_trigger` vs `risk_signal`. Only entry triggers can turn the two keys; risk signals feed `counter_case` / `kill_criteria` / confidence and never raise readiness.
- **`kind`** — what produced the signal: `insider | technical_breakout | laggard | squeeze | etf_launch | etf_flow | dilution_risk | …` (extensible).
- **`type`** — the catalyst nature where one applies: `regulatory | promoter_attention | clinical_readout | personnel | …`. Optional; many signals (e.g. a breakout) have a `kind` but no catalyst `type`.

So `insider_conviction` is `role=entry_trigger, kind=insider`; `dilution_clock` is `role=risk_signal, kind=dilution_risk`; a new ETF launch is `role=entry_trigger (low-grade), kind=etf_launch, type=…`; ETF flows are `kind=etf_flow`.

## 2. State-transition rules  `[PINNED]` (STARTING calibration)

The lifecycle is a **loop**, not a ratchet: `Incubating → Warming → Armed → Managing`, and Armed/Warming can fall back. A fired entry trigger is **live** only while inside its alpha-liveness window (`asof ≤ fire_date + alpha_liveness_days`); aged-out triggers stop counting. The numbers live in `CallConfig` (STARTING calibration), not here.

| Transition | Condition |
|---|---|
| → **Incubating** | No *live* entry trigger. *(default state)* |
| Incubating → **Warming** | ≥ `warming_min_entry_triggers` live entry triggers, but the two keys are **not** co-located (e.g. a conviction with no confirmation on the same security). |
| Warming → **Armed** | A **conviction** key and a **confirmation** key are *live and co-located on the same security* (`arming_requires_confirmation`), and no severe risk signal is blocking. |
| any → **Managing** | Operator has logged a fill (`position` exists, `opened_on ≤ asof`). |
| Armed → **Warming** | The **confirmation** key ages past its liveness window (the *entry window* `arm_until` lapses) with no fill — re-arming needs a fresh confirmation. A mild consolidation (a dip that doesn't age out the firing) is **not** a lapse. |
| Armed/Warming → **Incubating** | All live entry triggers age out (past the *hold* horizon `exit_by`). |

> **Two clocks (sticky-on-confirmation).** The arm is sticky on the **confirmation's** clock — the *entry window* (`arm_until`, §6); the **conviction's** clock is the *hold* horizon (`exit_by`, §6) that governs once a fill is logged. A genuine *breakdown* (close back below the breakout base) de-arms only via a `breakdown` **risk-signal** detector (M4a) — price-signal logic stays in detectors, never in the pure assembler.

> **Risk-veto rule `[SPECIFIED]` (confirmed).** A risk signal *penalizes confidence* and, when severe
> (e.g. critically short runway / imminent dilution), *blocks the Armed call* even if an entry trigger
> fired — a soft veto on **timing**. It never vetoes the **thesis** itself (that stays the operator's call).
> Severity threshold is `TODO(operator)` / calibrated; the block-vs-penalize behavior is fixed.

## 3. Grade decision  `TODO(operator)`

Each fired entry trigger carries a `grade ∈ {flip, core}`. The **call's** grade = the highest-grade fired entry trigger.

- `flip` = fast, sentiment/attention-driven; mean-reverts; trade small and short-dated; do not hold.
- `core` = structural; build the position.

The grade also sets the conviction **alpha-liveness window** (`alpha_liveness_days`, §6, STARTING calibration): a `core` insider cluster carries a multi-month hold horizon (the insider open-market-purchase literature measures abnormal returns over ~6 months, with multi-insider *cluster* buys the most persistent — so core ≈ 180d, the conservative low end), while a `flip` lasts only weeks. This is the fix for the *"right but early"* case: a core conviction stays live long enough to arm when confirmation finally prints (e.g. the UNH CEO-led cluster — conviction in May, the volume-backed breakout confirms in August, ~3 months later). It is a **liveness window** — a hard cutoff (full weight until it expires), *not* an exponential 50%-decay point — so it is set to the full edge-persistence horizon, and it doubles as the cap so a conviction can't arm on an unrelated breakout half a year later.

`TODO(operator)`: define per-detector grade rules. *Example strawman (replace):* `insider_conviction` →
`core` if (role ∈ {CEO, CFO}) **and** (≥2 distinct insiders) **and** (open-market code `P`) **and**
(dollar size ≥ threshold); else `flip`; else not fired.

## 4. Verdict mapping  `[PINNED]`

Three things are kept distinct so **grade isn't overloaded** (it used to silently carry all three, which
mis-fit catalysts):
- **Entry size** ← the **grade** (`flip` = small / provisional, `core` = full / binding).
- **Hold-or-not** ← the conviction's **horizon** (its `alpha_liveness_days`): a long horizon is
  hold-and-build, a short one is sentiment ("do not hold"). Keyed on **horizon, not kind**
  (`conviction_hold_threshold_days`), so a provisional-but-long-horizon catalyst *holds* while a fast
  insider flip does *not* — and the next signal kind inherits correct behaviour from its own horizon
  rather than an `if-kind` branch.
- **Build-to-full vs starter** ← the **entry grade** (the *weaker* key): a core thesis whose confirmation
  isn't volume-backed reads as a **starter**, never a bare `core_entry` (which invites over-committing —
  the operator's documented flaw).

| State | Condition | `Verdict` |
|---|---|---|
| Incubating | — | `watching` |
| Warming | conviction live, hold-worthy (long horizon, or `core`), no confirmation | `not_yet` |
| Warming | conviction live, short horizon (sentiment) | `flip_only` |
| Armed | small (`flip`) conviction, **short** horizon | `flip_only` (do not hold; exit at the catalyst) |
| Armed | small (`flip`) conviction, **long** horizon | `starter_entry` (enter small; build as it firms) |
| Armed | `core` conviction, entry `flip` (weak/momentum confirmation) | `starter_entry` (build to core when volume confirms) |
| Armed | `core` conviction, entry `core` (volume-backed confirmation) | `core_entry` (build to core size) |
| Managing | position open | `managing` |

The two `starter_entry` rows are the **mirror** — provisional-conviction + strong-confirmation, and
core-conviction + weak-confirmation, both mean *"enter small, build."* The only difference is what you
build into (more catalysts firming vs volume confirming); that lives in the expression / show-your-work
(§5, §8) and confidence (§7), not a separate verdict. A `starter_entry` carries reduced confidence and a
cautious expression.

## 5. Expression  `[PROPOSED]`

Suggested expression follows size (grade) **and hold (horizon)** — confirm/refine:
- **flip, short horizon** → small size, short-dated options, explicit "do not hold"; exit-by at/just past the catalyst.
- **flip, long horizon** (a provisional but durable catalyst) → **STARTER**: enter small; build as the conviction firms (a binding deal / more catalysts), not max size off one early step.
- **core** → spot + options dated *past* exit-by; build into the leaders/shovels of the basket.
- **ETF / safe sleeve** → for durable, long-duration exposure to the *whole* theme (usually offered at the umbrella/thesis level, not per Armed segment): a thematic ETF from the ETF radar. Lower torque — gives up the leader/lotto upside for duration and diversification. Always presented with fund internals (holdings, weights, expense ratio, AUM, liquidity) so the operator sees whether the ETF actually expresses the thesis. This is the floor, not the alpha; it can run *alongside* the single-name expressions, not instead of the call.

## 6. Exit-by & catalyst surface  `[SPECIFIED]`

Two clocks, each **anchored to the trigger's fire date** (`event.asof`), so they are stable under recompute — they do **not** slide as the query `asof` advances:

```
exit_by   = max(fire_date + alpha_liveness_days  over LIVE conviction   triggers)   # the HOLD horizon
arm_until = max(fire_date + alpha_liveness_days  over LIVE confirmation triggers)   # the ENTRY window
catalyst_surface = [ c for c in thesis.catalysts if c.when_date is not None and c.when_date <= exit_by ]
```
Both are `null` when no live trigger of that kind exists. `exit_by` (the conviction / hold clock) drives the
catalyst surface and the post-fill hold; `arm_until` (the confirmation / entry clock) is the window in which the
Armed call is live — when `asof` passes it, the arm lapses (§2). A trigger is **live** only inside its liveness window
(`asof ≤ fire_date + alpha_liveness_days`). The conviction (insider) liveness window is **graded** (§3) — a `core` cluster's
horizon is multi-month, a `flip`'s is short — so the hold clock scales with the strength of the conviction (and the
detector's lookback reaches at least as far, or a still-live cluster would drop from the re-derived stream early). Undated/fuzzy catalysts (no `when_date`) are shown for context but
excluded from the surface filter. The Cockpit flags any binary event in `catalyst_surface` as risk crossed before exit.

## 7. Confidence  `TODO(operator)`

`confidence ∈ [0,1]`, rendered as the Armed card's bar. Must be **calibrated**, not loud — a marginal
2-of-N setup reads low. Risk signals reduce it.

`TODO(operator)`: define the function. *Strawman (replace):* a saturating function of
`(count of fired entry triggers, their scores, cross-detector agreement)` minus a penalty per active
risk signal, capped so a single-detector call never reads "high."

**Roadmap (filed, not built):** *decay the conviction's confidence contribution across its
alpha-liveness window.* Liveness is a binary gate (full weight until it expires), so today a 5-month-old
cluster arms at the same confidence as a one-day-old one — which isn't true to the edge. Keep the
arm / no-arm gate binary; let only the **confidence** fade with the conviction's age. (This is also what
would make a literal "half-life" honest, if that decay were ever wanted.)

## 8. Counter-case  (LLM prose, not a computed field)

The `counter_case` is **prose from the LLM stub**, assembled from: the thesis `kill_criteria`, any active
risk signals, and the `missing[]` triggers. It cites existing evidence IDs only and **cannot** alter
state/verdict/grade/triggers. If the LLM is unavailable, fall back to a deterministic template listing
kill-criteria + missing triggers.

---

## 9. Worked example — the shape of a correct Armed call (the real HIMS case)

> Demonstrates the *flow and output shape* on the live M3 target. This is what the seeded HIMS thesis
> actually computes (`pipeline.seed` → `GET /theses/{id}/call?asof=`), so the numbers are real, not a
> mock. The read path re-derives the dated signal stream from the bitemporal facts at each `asof`.

**Setup.** Basket member `HIMS` has a real Form 4 (director David Wells, ~$1.17M open-market, code P,
late May) and real EOD bars.

**Before confirmation — `asof = 2026-05-28`.** Only the conviction key is live:
- `insider_conviction` → `fired=true, grade=core` (one strong senior buy clears the high-USD floor —
  STARTING calibration), `alpha_liveness_days=180` (graded: the multi-month core-conviction horizon),
  `event_date=2026-05-26`, provenance → the real Form 4 accession.  *(`role=entry_trigger, kind=insider`)*
- `volume_breakout` → no breakout in its freshness window → no event.

→ **State: Warming** (conviction warms; arming needs a *co-located* confirmation). **Verdict: `not_yet`.**
`exit_by` = `2026-05-26 + 180d` (the conviction / hold clock — graded core horizon); `arm_until` = none. **missing: `[volume-confirmed breakout]`.**

**At confirmation — `asof = 2026-06-01`.** The breakout prints, but on ~0.9× volume:
- `volume_breakout` → `fired=true, grade=flip` (momentum-only: a new closing high + thrust fired, but
  volume did not back it), `alpha_liveness_days=10`, `event_date=2026-06-01`, provenance →
  `price:HIMS:2026-06-01` + the computation detail.  *(`role=entry_trigger, kind=technical_breakout`)*

**Assembly (06-01).**
- **State:** conviction + confirmation are live and **co-located on HIMS** → **Armed**.
- **Two grades, kept distinct (§4):** conviction `core` (the thesis quality); confirmation `flip`
  (momentum-only); **entry grade = the weaker = `flip`**.
- **Verdict:** `starter_entry` — a core *thesis* but a starter *entry*, because volume hasn't
  confirmed. (A volume-backed breakout would make the entry `core` → `core_entry`.)
- **Two clocks (§6):** `exit_by` (hold) = `2026-05-26 + 180d` (graded core-conviction horizon);
  `arm_until` (entry window) = `2026-06-01 + 10d = 2026-06-11` — the call stays Armed through a
  consolidation until 06-11, then lapses to Warming unless a fresh breakout re-arms it.
- **catalyst_surface:** any dated catalyst ≤ `exit_by` is flagged as crossed before exit.
- **confidence:** capped at `momentum_only_confidence_cap` (≈0.55) — the volume gap reads as lower
  confidence (§7).
- **triggers_fired:** `[insider_conviction → ↗ Form 4, volume_breakout → price detail]`. **missing: `[]`.**
- **counter_case:** the deterministic template leads with the volume-gap caveat ("confirmation is
  momentum-only, not volume-backed…") plus kill-criteria; the LLM (M4b) rewrites it as prose, citing
  existing evidence only.

**Resulting CallCard** → renders **Armed / "The Call"**: verdict `starter_entry`, a ~55% confidence
bar, both keys lit, the insider trigger with a working ↗ Form 4 link, the volume-gap counter-case, and
`Act / Override / Snooze`.

This is the loop the north star required — and as of M3a it is wired end to end **on real data**: real
EDGAR + EOD → detectors → assembler → `GET /theses/{id}/call?asof=`.

---

## What still needs you

Everything marked `TODO(operator)`: the state-transition thresholds (§2), per-detector grade rules (§3),
the confidence function (§7), and the trigger-type taxonomy decision (§1). Those encode your trading
judgment and shouldn't be guessed. The `[PROPOSED]` and `[SPECIFIED]` parts are ready to build against.
