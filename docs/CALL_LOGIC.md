# CALL_LOGIC.md — How a Call Is Made

> Repo path: `docs/CALL_LOGIC.md`. This is the platform's **brain** — the spec for how `SignalEvent`s
> become a lifecycle state, verdict, grade, expression, and exit-by. It is the "make the call and show
> its work" promise made concrete. It must be implemented as a **named, golden-tested component**
> (a call-assembler), never as an emergent side effect of the pipeline.
>
> **Legend:** `[PROPOSED]` = a starting default Claude drafted; confirm or change.
> `TODO(operator)` = needs the trader's judgment — this is where the edge lives; do **not** invent and present as decided.
>
> **Product boundary:** this call is research-and-monitoring output. Grade is a categorical call-strength
> class; `exit_by` is a signal-validity horizon; Managing means monitoring an operator-entered thesis; and the
> `confidence` wire field is displayed/documented as experimental **setup strength**. None of these fields
> sizes a position, selects an instrument, routes an order, or manages portfolio risk.

---

## 0. Where this sits

```
SignalEvent[]  ──►  call-assembler (this spec)  ──►  CallCard
(from detectors)     pure f(thesis, events, asof)     (served by API, rendered in Cockpit)
```

The assembler is **pure and deterministic**: same thesis + same signal events + same `asof` → same CallCard. The LLM stub fills only `counter_case` and explanatory prose (citing existing evidence IDs); it never sets state, verdict, grade, or triggers. The `calls` table stores assembled CallCards as the **accountability record** (what the platform asserted, when) — it is **not** the read path. The API recomputes the CallCard live at the requested `asof`.

## The through-line — factor behavior on the property that drives it  `[PRINCIPLE]`

> The single most load-bearing design decision in the brain. Read it before changing any rule below.

The original model overloaded **grade** with position size, instrument/expression guidance, and a mandatory
hold-or-exit instruction, then also used **signal kind** as a proxy for the alpha-liveness horizon. Those
couplings overclaimed what a research call can decide. The corrected design **unbundles each property** and
keeps trade construction outside Alpha Deck:

| behavior | keyed on (the property that drives it) | NOT on |
|---|---|---|
| categorical **call strength** | the **grade** (`flip` vs `core`) | position size, instrument, or expression |
| signal-validity window | the conviction's **horizon** (`alpha_liveness_days`; `conviction_hold_threshold_days` is the legacy-named verdict cutoff) | a mandatory trade exit or a generic grade/signal-**kind** mapping (the insider detector's explicit grade-coupling is §3) |
| provisional-vs-strong call wording, and the **setup-strength cap** | the **entry grade** = the **weaker key** | position size or the *stronger* key (§4, §7) |
| catalyst **liveness** | the agreement's **relevance horizon** (period of performance) | grade — insider *stays* grade-coupled; §3 |
| sizing, instrument selection, execution, and portfolio risk | the operator + firm's OMS / execution / risk systems | any CallCard field |

**Standing rule: never re-couple these.** Do not add an `if kind == …` branch where a property (the horizon,
the weaker key, the term) already carries the signal. A new signal kind must inherit correct behavior from its
own properties, not from a special case. If you find yourself special-casing a kind, the behavior you want is
almost certainly a property you should be reading instead.

## 1. Inputs

Per `SignalEvent` (see `domain/signal.py`): `detector, security_id, role, kind, type, grade, score, fired, label, alpha_liveness_days, provenance, asof`.

**Signal taxonomy `[SPECIFIED]` (confirmed).** Three orthogonal fields:
- **`role`** — `entry_trigger` vs `risk_signal`. Only entry triggers can turn the two keys; risk signals feed `counter_case` / `kill_criteria` / setup strength (wire field `confidence`) and never raise readiness.
- **`kind`** — what produced the signal: `insider | catalyst | technical_breakout | laggard | squeeze | etf_launch | etf_flow | dilution_risk | …` (extensible).
- **`type`** — the catalyst nature where one applies: `gov_funding | regulatory | commercial | emergence | promoter_attention | clinical_readout | personnel | …`. Optional; many signals (e.g. a breakout) have a `kind` but no catalyst `type`.

So `insider_conviction` is `role=entry_trigger, kind=insider`; a DOE award is `role=entry_trigger, kind=catalyst, type=gov_funding` (the second conviction source — §3); a `theme_conviction` is `role=entry_trigger, kind=theme_conviction` (the basket-level fallback, M5b — §3); `dilution_clock` is `role=risk_signal, kind=dilution_risk`; a new ETF launch is `role=entry_trigger (low-grade), kind=etf_launch, type=…`; ETF flows are `kind=etf_flow`. `insider`, `catalyst`, and `theme_conviction` are all **conviction** kinds (`cfg.conviction_kinds`) — any WARMS; a breakout (confirmation) ARMS. `cfg.own_conviction_kinds` (= `conviction_kinds − {theme_conviction}`) marks the name-sourced ("own") convictions, distinct from the theme fallback.

## 2. State-transition rules  `[PINNED]` (STARTING calibration)

The lifecycle is a **loop**, not a ratchet: `Incubating → Warming → Armed → Managing`, and Armed/Warming can fall back. A fired entry trigger is **live** only while inside its alpha-liveness window (`asof ≤ fire_date + alpha_liveness_days`); aged-out triggers stop counting. The numbers live in `CallConfig` (STARTING calibration), not here.

| Transition | Condition |
|---|---|
| → **Incubating** | No *live* entry trigger. *(default state)* |
| Incubating → **Warming** | ≥ `warming_min_entry_triggers` live entry triggers, but the two keys are **not** co-located (e.g. a conviction with no confirmation on the same security). |
| Warming → **Armed** | A **conviction** key and a **confirmation** key are *live and co-located on the same security* (`arming_requires_confirmation`), and no severe risk signal is blocking. |
| any → **Managing** | Operator has logged a fill (`position` exists, `opened_on ≤ asof`). Alpha Deck now **monitors the entered thesis**; it does not manage the position or portfolio risk. The position DERIVES from the **operator-decisions log** (`operator_decision` — decision capture): `take` opens, `close` closes, `void` un-does an append; read as-of BOTH time axes (`decision_date` = valid, `recorded_at` = transaction — a replayed past call never sees a later-logged fill), fed once at the `call_for_thesis` funnel (`decisions_repo.effective_position`; any log rows beat the seed-era `thesis.position_*` columns, including net-closed). The same log is the Scoreboard's operator column and the gate's override record (a take rides with the platform's stance at logging time — logged, never blocked, #5). A take logged **on a name** also attributes the held member on the menu — per-member Managing, §4. |
| Armed → **Warming** | The **confirmation** key ages past its liveness window (the *entry window* `arm_until` lapses) with no fill — re-arming needs a fresh confirmation. A mild consolidation (a dip that doesn't age out the firing) is **not** a lapse. |
| Armed/Warming → **Incubating** | All live entry triggers age out (past the signal-validity horizon `exit_by`). |

> **Two clocks (sticky-on-confirmation).** The arm is sticky on the **confirmation's** clock — the *entry
> window* (`arm_until`, §6); the **conviction's** clock is the signal-validity horizon (`exit_by`, §6), which
> remains the thesis-monitoring and scoring yardstick after a fill. It is not a sell deadline. A genuine
> *breakdown* (close back below the breakout base) de-arms only via a `breakdown` **risk-signal** detector (M4a)
> — price-signal logic stays in detectors, never in the pure assembler.

> **Theme menu = a ranked per-member view `[BUILT — M5a/M5b]`.** When several basket members are
> independently armed (a theme thesis), the call computes a call **per member** and **ranks** them —
> `calls/assembler.rank_members`, best-first `(is_fresh, entry-grade, is_own, runway, conviction_score, id)`:
> a freshness BAND (liveness runway) primary, entry grade within, then **own-above-theme** (`is_own`), then
> runway/score. The headline = the top-ranked **actionable** member (Board + Decision Queue show it + a `+N`
> depth hint — one name per thesis, anti-flooding); the rest are the ranked menu; confirmation-only names sit
> in a non-actionable **watch** tier. Because freshness is primary, **a fresh starter can out-rank a lapsing
> core** — a `core` arm three weeks from lapsing (LEU → 2026-06-30) does *not* auto-headline over a `starter`
> with years of runway (OKLO → 2029). **M5b** adds an operator-ratified, thesis-level **theme conviction**
> that supplies Key 1 as a *fallback* for a confirmed member with no own conviction (a capped, flagged
> `theme_armed` starter that ranks below own-conviction names within a band); see §3 + `docs/THEME_CONVICTION.md`.

> **Risk-veto rule `[SPECIFIED]` (confirmed).** A risk signal *reduces setup strength* (wire field `confidence`) and, when severe
> (e.g. critically short runway / imminent dilution), *blocks the Armed call* even if an entry trigger
> fired — a soft veto on **timing**. It never vetoes the **thesis** itself (that stays the operator's call).
> Severity threshold is `TODO(operator)` / calibrated; the block-vs-penalize behavior is fixed.

## 3. Grade decision  `[built — insider]` · `[approved — catalyst]`

Each fired entry trigger carries a `grade ∈ {flip, core}`; the **call's** grade = the highest-grade fired
entry trigger. Grade is a categorical **call-strength class**, not a trade-construction instruction.

- `flip` = fast, sentiment/attention-driven, more likely to mean-revert.
- `core` = structural and more durable.

**Grade does not set position size, instrument, or expression.** It classifies the setup's deterministic
signal strength/nature. Signal validity comes from the conviction's **horizon**, not its grade (§4) — they
only *coincide* for insider buys, which is exactly why the two conviction sources set liveness differently:

- **`insider_conviction` `[built]` — grade-COUPLED liveness.** For an open-market buy, strength and
  edge-horizon genuinely move together, so grade sets the `alpha_liveness_days` window: a `core` cluster ≈
  **180d** (the insider-purchase literature measures abnormal returns over ~6 months, multi-insider *cluster*
  buys the most persistent — the conservative low end), a `flip` ≈ short weeks. It is a hard liveness window
  (full weight until it expires, not a 50%-decay point) and doubles as the cap so a conviction can't arm on an
  unrelated breakout half a year later — the fix for the *"right but early"* case (UNH: CEO-led cluster in May,
  the volume-backed breakout confirms ~3 months later, still inside the core window). **Built rule:** `core` if
  a senior cluster (≥2 distinct, code `P`, CEO/CFO/director) **or** a single high-USD senior buy clears the
  floor; else `flip`. Calibrated in `CallConfig`.
- **`catalyst_conviction` `[approved]` — grade-DECOUPLED liveness (option A).** Liveness = the agreement's own
  **relevance horizon** (its period of performance), independent of grade; grade = the **customer-vs-sponsor**
  nature of the commitment — a DOE **contract** (DOE *buys your product* = revenue) or a **loan / loan
  guarantee** (committed financing) = `core`; a grant / cooperative agreement / OTA (DOE *funds your
  development* = support) = `flip`. **By nature, never by obligation amount** (a $148M cooperative agreement
  is still `flip`; obligation magnitude may affect the trigger score and therefore setup strength within the
  grade, never position size). Full rule + precedent (LEU core, OKLO flip) in
  `docs/CATALYST_CONVICTION.md`.
- **`theme_conviction` `[built — M5b]` — grade-DECOUPLED liveness, capped at flip.** An operator-ratified,
  **thesis-level** conviction (the basket-level analog of an insider buy / a name's catalyst), broadcast onto
  each eligible member as a Key-1 **fallback**. Always **`flip`** (capped at starter — belief never mints a
  core; it routes through the weaker-key path like any flip); liveness = the operator-set **horizon**
  (decoupled from grade, like a catalyst). It arms a member only when the member has its **own** live
  volume-backed (`core`) confirmation and **no** own live conviction (own wins; a lapsed own conviction falls
  back — the floor). Full rule in `docs/THEME_CONVICTION.md`.

Firing + grade are always a **deterministic parse or an operator ratification — never the LLM** (invariant #3).

## 4. Verdict mapping  `[PINNED]`

Three things are kept distinct so **grade isn't overloaded**:
- **Call-strength class** ← the **grade** (`flip` = fast/provisional setup, `core` = structural/durable setup).
- **Signal-validity horizon** ← the conviction's `alpha_liveness_days`. The legacy-named
  `conviction_hold_threshold_days` separates short- from long-lived verdict wording, but neither value tells
  the operator when to sell. The next signal kind inherits this behavior from its own horizon rather than an
  `if-kind` branch.
- **Provisional vs strongest call wording** ← the **entry grade** (the *weaker* key): a core-grade conviction
  whose confirmation isn't volume-backed reads as a `starter_entry`, never a bare `core_entry`. These are
  readiness/call-strength verdicts, not position sizes.

| State | Condition | `Verdict` |
|---|---|---|
| Incubating | — | `watching` |
| Warming | conviction live with a long signal window, no confirmation | `not_yet` |
| Warming | conviction live with a short signal window | `flip_only` |
| Armed | `flip` conviction, **short** signal window | `flip_only` (fast setup; validity ends around the catalyst/window boundary) |
| Armed | `flip` conviction, **long** signal window | `starter_entry` (provisional call with a durable window) |
| Armed | `core` conviction, entry `flip` (weak/momentum confirmation) | `starter_entry` (provisional until volume confirms) |
| Armed | `core` conviction, entry `core` (volume-backed confirmation) | `core_entry` (strongest call classification) |
| Managing | operator-entered position open; platform monitors thesis | `managing` |

The two `starter_entry` rows are the **mirror** — provisional-conviction + strong-confirmation, and
core-conviction + weak-confirmation. Both mean *"the setup is provisional."* The difference is why it could
strengthen (more catalysts firming vs volume confirming); that lives in the show-your-work context (§5, §8)
and setup strength (§7), not a separate verdict. A `starter_entry` carries reduced setup strength; it does
not prescribe a starter-sized position.

**Per-member Managing attribution `[SPECIFIED]` (confirmed — ratified with #155).** The table's Managing row is thesis-level;
per-member it applies to exactly **one** name — the held one. When the open position carries a
`security_id` (the position derives from a `take` logged **on a name**; `Position.security_id` rides the
derived position), the held member's `MemberCall` is computed by the **same scoped helper as every member**
(its live grades, clocks, and triggers ride along — computed facts, unchanged) with the two ACTION fields
overridden: `verdict = managing` (the platform action is "monitor the entered thesis", not "enter") and
`confidence = None` (the setup-strength bar describes entry readiness, not ongoing position risk; the
thesis-level Managing rule, applied per-member). It **leads `armed_members`** (the
held name is a Managing thesis's per-name headline; the entry ranking follows beneath, unchanged — safe
because the Decision Queue's `armed_members[0]` headline renders only for `state=armed`, which an open
position precludes) and it never sits in the watch tier (watch stays verdict-less by contract) — so the
Cockpit's grouped basket files it under **Managing** instead of Quiet even after its triggers age out. The
**risk veto cannot un-hold a held name** (it gates entry timing, §2; the position already exists — the risk
still rides `risk_signals` for the counter-case). A position with **no** `security_id` (a thesis-level
take; the seed-era `thesis.position_*` fallback, which stores no name) attributes nothing per-member —
attribution is honest or absent, never guessed. No-lookahead is inherited from the position feed (§2): the
position derives as-of both time axes, so a future-dated or later-recorded fill neither flips the state nor
attributes a member.

## 5. Expression context and external handoff  `[BOUNDARY]`

The wire still carries an `expression` string, but its product meaning is **advisory research context**:
why the setup is fast vs structural, which basket names or fund sleeves expose the thesis, and what evidence
would strengthen it. Grade never maps to position size, options tenor, or an instrument/order instruction.
The operator and the firm's OMS / execution / sizing / portfolio-risk systems decide and govern the actual
trade.

An **ETF / safe sleeve** may be surfaced as a lower-torque research candidate for whole-theme exposure,
with fund internals (holdings, weights, expense ratio, AUM, liquidity) so the operator can evaluate whether
it expresses the thesis. Alpha Deck does not allocate to it or route an order.

## 6. Signal-validity horizon (`exit_by`) & catalyst surface  `[SPECIFIED]`

Two clocks, each **anchored to the trigger's fire date** (`event.asof`), so they are stable under recompute — they do **not** slide as the query `asof` advances:

```
exit_by   = max(fire_date + alpha_liveness_days  over LIVE conviction   triggers)   # signal-validity horizon
arm_until = max(fire_date + alpha_liveness_days  over LIVE confirmation triggers)   # the ENTRY window
catalyst_surface = [ c for c in thesis.catalysts if c.when_date is not None and c.when_date <= exit_by ]
```
Both are `null` when no live trigger of that kind exists. `exit_by` is the conviction signal's
**valid-through horizon**: how long the thesis window behind the arm remains live. It drives the catalyst
surface and supplies a post-fill monitoring/scoring yardstick; it is **not a mandatory trade exit, sell-by
date, stop, or order instruction**. `arm_until` (the confirmation / entry clock) is the window in which the
Armed call is live — when `asof` passes it, the arm lapses (§2). A trigger is **live** only inside its liveness
window (`asof ≤ fire_date + alpha_liveness_days`). The conviction (insider) liveness window is **graded** (§3)
— a `core` cluster's horizon is multi-month, a `flip`'s is short — so the validity window scales with the
strength of the conviction (and the detector's lookback reaches at least as far, or a still-live cluster
would drop from the re-derived stream early). Undated/fuzzy catalysts (no `when_date`) are shown for context
but excluded from the surface filter. The Cockpit flags binary events that fall within `catalyst_surface`;
that inclusion is not an instruction to close the trade at the event or window end.

**The displayed trigger date (`event_date`).** Each fired trigger on the call card carries `event_date =
event.asof` (its fire date), rendered as a muted right-aligned date on the row and used to order the list
**newest-first** (display only — the assembled `triggers_fired` order is unchanged, so the Scoreboard's
arm-time snapshot stays stable). For a technical breakout that's the breakout bar date; for an **insider
cluster** it is the **most-recent** open-market buy in the cluster (`max(valid_from)`, the anchor in §3/the
detector), **not** the earliest or the largest — so a cluster spanning e.g. Jan 30 → Feb 25 reads **Feb 25**.
That is the freshness anchor (when the conviction last strengthened) and is the same date the
liveness/`exit_by` clocks key on, so the row date and the clocks never disagree. **This anchor is a
deliberate choice**: to instead show the *earliest* buy (when the cluster began) or the *largest* buy's date,
change the single `anchor =` line in `backend/signals/insider_conviction.py` — `event_date`, `exit_by`, and
the liveness window all follow it.

## 7. Setup strength  `[built]` (wire field: `confidence`; values experimental)

`confidence ∈ [0,1]` is rendered as the Armed card's **setup-strength** bar. The scale is a relative read of
the current trigger composition — **not a probability of success, calibrated win rate, sizing input, or
endorsement of the thesis**. Risk signals reduce it. It is scoped to the **armed security** (not basket-wide).

**Calibration boundary:** setup strength remains experimental until the Scoreboard's matured forward outcomes
support calibration. The Scoreboard's `n ≥ 5` aggregate-metric gate is a UI safeguard against over-reading
early summaries, not an evidence threshold; crossing it does not make this value a probability. The
`confidence` name remains on the wire/config for compatibility in this docs-only terminology pass.

**Built function** (the *structure* is fixed; the values are experimental dials, see RECALIBRATION.md): a
saturating (noisy-OR) combine of `(fired entry-trigger scores → more agreeing detectors saturates higher)`
minus a penalty per active risk signal, with two ceilings **composed `min-of`** (a call tripping both takes
the lower, never double-capped):
- **single-detector cap** — a one-detector call never reads "high."
- **starter cap** (`starter_confidence_cap`, ≈0.55) — **any** `starter` (entry grade = `flip`, i.e.
  *either* key is weak: an unconfirmed/momentum-only breakout **or** a provisional conviction) is capped,
  no matter how strong the *other* key is. Without this, noisy-OR lets the one strong key float an
  explicitly provisional call to a loud number — the inverse-loudness trap (a provisional card out-shouting
  a steadier one in the Decision Queue). The cap is keyed on the **entry grade**, so it fires for a weak
  breakout *and* a provisional-but-durable catalyst alike — the same generalization as the verdict (§4):
  one rule on the weaker key, not an `if-kind` branch. (Superseded the narrower `momentum_only` cap, which
  only caught the weak-confirmation half.)

**Roadmap (filed, not built):** *decay the conviction's setup-strength contribution (`confidence` in code) across its
alpha-liveness window.* Liveness is a binary gate (full weight until it expires), so today a 5-month-old
cluster arms at the same setup strength as a one-day-old one — which isn't true to the edge. Keep the
arm / no-arm gate binary; let only the **setup strength** fade with the conviction's age. (This is also what
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
`exit_by` = `2026-05-26 + 180d` (the conviction signal-validity horizon — graded core window);
`arm_until` = none. **missing: `[volume-confirmed breakout]`.**

**At confirmation — `asof = 2026-06-01`.** The breakout prints, but on ~0.9× volume:
- `volume_breakout` → `fired=true, grade=flip` (momentum-only: a new closing high + thrust fired, but
  volume did not back it), `alpha_liveness_days=10`, `event_date=2026-06-01`, provenance →
  `price:HIMS:2026-06-01` + the computation detail.  *(`role=entry_trigger, kind=technical_breakout`)*

**Assembly (06-01).**
- **State:** conviction + confirmation are live and **co-located on HIMS** → **Armed**.
- **Two grades, kept distinct (§4):** conviction `core` (the conviction trigger's call-strength class); confirmation `flip`
  (momentum-only); **entry grade = the weaker = `flip`**.
- **Verdict:** `starter_entry` — a structural conviction but a provisional call, because volume hasn't
  confirmed. (A volume-backed breakout would make the entry grade `core` → `core_entry`.) The label does not
  size a trade.
- **Two clocks (§6):** `exit_by` (signal validity) = `2026-05-26 + 180d` (graded core-conviction horizon);
  `arm_until` (entry window) = `2026-06-01 + 10d = 2026-06-11` — the call stays Armed through a
  consolidation until 06-11, then lapses to Warming unless a fresh breakout re-arms it.
- **catalyst_surface:** any dated catalyst ≤ `exit_by` is flagged as falling within the live thesis window.
- **setup strength (`confidence` on the wire):** capped at `starter_confidence_cap` (≈0.55) — HIMS is a
  `starter` (entry `flip`), so the
  weak-confirmation key holds it down even though the conviction is strong (§7).
- **triggers_fired:** `[insider_conviction → ↗ Form 4, volume_breakout → price detail]`. **missing: `[]`.**
- **counter_case:** the deterministic template leads with the volume-gap caveat ("confirmation is
  momentum-only, not volume-backed…") plus kill-criteria; the LLM (M4b) rewrites it as prose, citing
  existing evidence only.

**Resulting CallCard** → renders **Armed / "The Call"**: verdict `starter_entry`, setup strength at ~0.55
(55% display scale, **not** win probability), both keys lit, the insider trigger with a working ↗ Form 4 link,
the volume-gap counter-case, and
`Act / Override / Snooze`.

This is the loop the north star required — and as of M3a it is wired end to end **on real data**: real
EDGAR + EOD → detectors → assembler → `GET /theses/{id}/call?asof=`.

---

## What still needs you

The *structure* of the brain is built and reconciled with the code (states §2, grade §3, verdict §4,
clocks §6, setup strength §7). What remains is **forward calibration, not architecture** — the threshold *values* the
operator tunes against real outcomes once the MVP has run. Those are consolidated into one agenda in
**`docs/RECALIBRATION.md`** (liveness windows, grade boundaries + the $10M DOE threshold, the cap values, the
momentum-only-vs-starter split, and the filed refinements). The deferred build items (age-decay of setup
strength (`confidence` in code), the loans query group) live there too. Nothing here is a guessed number
presented as decided — the dials are labelled as dials.
