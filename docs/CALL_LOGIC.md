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

The assembler is **pure and deterministic**: same thesis + same signal events + same `asof` → same CallCard. The LLM stub fills only `counter_case` and explanatory prose (citing existing evidence IDs); it never sets state, verdict, grade, or triggers. The `calls` table stores assembled CallCards as the **accountability record** (what the platform asserted, when) — it is **not** the read path. The API recomputes the CallCard live at the requested `asof` — for a past `asof` on the PUBLIC clock, capped at the end of that market day (`serve_view`: each fact from when it became public — today's code and rules over what was public then; the roster point-in-time from 2026-09-15 — `INVARIANTS.md` #4 + §Known gaps).

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
> *breakdown* de-arms via the `breakdown` **risk-signal** detectors (`[built — sig]`, `breakdown_dearm_enabled`
> default ON since 2026-08-15): a close back below the 8-day breakout base (the **flip** exit) or below the
> 200-day base (the **core** exit). Grade-aware and **post-dating the arm** (a name never de-arms concurrent
> with its own arm); price-signal logic stays in detectors, never in the pure assembler. WHICH of the two runs
> is `breakdown_dearm_scope`, a **DORMANT** dial defaulting to `all` (both, as described) — `core_only` no-ops
> the fast flip exit for an H3 measurement. The two switches read as "whether" (`breakdown_dearm_enabled`) and
> "which" (`breakdown_dearm_scope`); the scope dial never means off.

> **The de-arm copy names the breakdown that de-armed it `[BUILT — Q12, operator-approved 2026-10-18]`.**
> The old single string ("De-armed on a structural break … Re-arms only on a fresh confirmation") was false
> on both counts — MEASURED (audit A1): all 30 recorded de-arm rows were fast breakdowns (28 purely, 2 mixed
> with a structural break), and 46 of 219 flip de-arms re-armed on the same breakout. The expression is now
> picked by the `dearm_grade` of every breakdown that **actually de-armed** a member (`_is_dearming` passes
> one only when it matches its member's entry grade, so this is the set of entry grades the broken-down
> names carried):
>
> | De-arming breakdowns | Expression (verbatim) |
> |---|---|
> | only fast (flip) | "De-armed on a fast breakdown — the close fell back below the 8-day breakout base, so the flip entry signal is no longer valid (a signal-validity event, not a sell). It re-arms if price reclaims that base while the breakout is still live, or on a fresh breakout." |
> | only structural (core) | "De-armed on a structural break — the close fell below the 200-day base, so the core entry signal is no longer valid (a signal-validity event, not a sell). It re-arms if price reclaims the 200-day base while the entry triggers are live, or on a fresh confirmation." |
> | both | "De-armed on a breakdown — armed names gave back their bases (see the counter-case); a signal-validity event, not a sell." |
>
> **Not the card's own `entry_grade`:** a de-armed card is graded across the whole live basket (no armed
> security to scope to), so it can read `core` while the only de-arm was a fast breakdown — 16 of the 28
> pure fast-breakdown rows on the record did. Each clause was checked against the code: the flip base is the
> 8-day breakout base (`breakdown.flip_score`, `breakout_base_window` = 8) and the break lifts when the close
> reclaims it or a fresh breakout resets it while Key 2 is live; the core base is the 200-day SMA
> (`breakdown.core_score`, `breakdown_core_sma_window` = 200) and the break lifts on a reclaim (no live
> downward cross) or once a fresh confirmation post-dates it (`_is_dearming`'s post-date gate). Both dials
> are pinned by a test, so a retune sends the copy back for review. **The re-arm routes the copy names are
> real but not exhaustive** — two more exist in the code: (1) *flip*: when the broken 8-day breakout ages out
> of its 10-day window while ANOTHER confirmation keeps Key 2 live (a 45-day 52-week breakout, a 15-day
> laggard), `_recent_flip_base` finds no live breakout, the flip breakdown stops firing, and the member re-arms
> with no reclaim and no fresh breakout; (2) *core*: a fresh **conviction** (a new insider buy, a ratified
> catalyst) that post-dates the break moves `_arm_date` past it just as a fresh confirmation does, so the
> old break stops counting while price is still below the 200-day base. Neither contradicts the copy (it
> never says "only"); both are listed so the record can be read honestly.

> **Theme menu = a ranked per-member view `[BUILT — M5a/M5b]`.** When several basket members are
> independently armed (a theme thesis), the call computes a call **per member** and **ranks** them —
> `calls/assembler.rank_members`, best-first `(is_fresh, entry-grade, is_own, runway, conviction_score, id)`:
> a freshness BAND (liveness runway) primary, entry grade within, then **own-above-theme** (`is_own`), then
> runway/score. The headline = the top-ranked **actionable** member (Board + Decision Queue show it + a `+N`
> depth hint — one name per thesis, anti-flooding); the rest are the ranked menu; confirmation-only names sit
> in a non-actionable **watch** tier. Because freshness is primary, **a fresh starter can out-rank a lapsing
> core** ("lapsing" here = the HOLD clock, `exit_by`, inside the freshness band — labeled "hold clock
> closing" in the UI, never the entry window; see §6) — a `core` arm three weeks from lapsing (LEU → 2026-06-30) does *not* auto-headline over a `starter`
> with years of runway (OKLO → 2029). **M5b** adds an operator-ratified, thesis-level **theme conviction**
> that supplies Key 1 as a *fallback* for a confirmed member with no own conviction (a capped, flagged
> `theme_armed` starter that ranks below own-conviction names within a band); see §3 + `docs/THEME_CONVICTION.md`.

> **Risk-veto rule `[SPECIFIED]` (confirmed).** A risk signal *reduces setup strength* (wire field `confidence`) and, when severe
> (e.g. critically short runway / imminent dilution), *blocks the Armed call* even if an entry trigger
> fired — a soft veto on **timing**. It never vetoes the **thesis** itself (that stays the operator's call).
> Severity threshold is `TODO(operator)` / calibrated; the block-vs-penalize behavior is fixed.

> **Risk roster — insider-sell cluster `[LIVE — Band 03 S1; master switch ON by default since the 2026-08-19 sig-lab pass]`.**
> `insider_sell` (`role=risk_signal, kind=insider_sell`) fires on a **clustered discretionary open-market
> sell episode**: ≥ `insider_sell_min_distinct` insiders (senior officer/director required by default)
> selling ≥ `insider_sell_min_usd` of code-`S` stock within one cohesion window, anchored on the most
> recent qualifying **episode** and fresh only inside `insider_sell_liveness_days` (risk freshness is
> detector-enforced — the assembler never ages risks). *Anchor selection:* every distinct kept-sale date
> is a candidate anchor, walked newest → oldest, and the first episode clearing freshness **and** every
> floor fires (there is no grade axis on a risk, so recency is the only preference). This is what stops
> one lone later sale from re-anchoring the episode onto itself, failing `insider_sell_min_distinct`, and
> silencing a still-live cluster — i.e. *more* insider selling reading *more* bullish. Screens, each named on the label/provenance:
> 10b5-1 **planned** sales (`aff_10b5_1 = True`) are screened out (near-noise; `NULL` = unknown is KEPT —
> absence never asserts either way); issuer **self-filings** and **implausible** rows are excluded;
> **below-day-low** sales are set aside as a different risk family (a discounted secondary, not open-market
> supply). Per the literature's asymmetry (sales are weak predictors — people sell for many reasons), its
> score is **capped at `insider_sell_max_score` (0.60), strictly below `risk_block_severity` (0.70)**
> (operator-ratified, 2026-08-16): it feeds the counter-case and a setup-strength haircut and can **never
> withhold an arm** in v1 — not a veto, not a de-arm (no `dearm_grade`). **Now LIVE (`insider_sell_enabled=True`,
> flipped 2026-08-19):** the sig-lab off-vs-on pass MEASURED it safe on real prod data (58 fires / 5 theses / 0
> arm-withholdings / 0 de-arms), so the `[PROPOSED]` dials held through the pass and the operator flipped the switch.

> **Risk roster — share-count creep / ATM detection `[LIVE — Band 03 S4; master switch ON by default since
> the 2026-08-19 sig-lab pass]`.** `share_creep` (`role=risk_signal, kind=dilution_risk` — the kind is **REUSED**: realized
> issuance beside `dilution_clock`'s potential convert overhang, two lenses on one phenomenon) fires on a
> **sustained quarterly share-count drip**: the trailing `share_creep_window_quarters` consecutive QoQ
> steps all strictly positive, cumulative rise ≥ `share_creep_cum_min_pct` — the signature of an
> at-the-market program quietly draining into the tape (ATM sales are registered off a shelf and file no
> per-sale 8-K; that is what makes the drip quiet). Reads the quarterly XBRL shares series
> (`fact_fundamentals`, three concepts stored `_xbrl`-keyed) through a fixed **availability ladder** —
> balance-sheet → dei cover → issued, first with a fresh consecutive chain; the verdict comes from that
> series alone (never concept-shopping past an honest "no creep", never mixing concepts). A single step ≥
> `share_creep_pair_ceiling_pct` is a split/recap/XBRL-scale artifact — a structural event, declined, not
> creep; a one-off jump with flat neighbors (a discrete explained raise) is declined by strict positivity.
> Freshness is detector-enforced on the newest print's `filed` (`share_creep_liveness_days`) — its FIRST
> knowability (D3a, the same `knowable_from` read as `revenue_acceleration`): every 10-Q re-reports the prior
> fiscal year-end balance sheet, so an unchanged count re-reported as a comparative neither refreshes the series
> nor re-dates its provenance (MEASURED at 2026-10-15: 87 → 87 events, 4 anchors re-dated earlier, 0 arms changed — the
> signal is sub-veto). Grade-blind
> like dilution (no grade, no `dearm_grade`); the flat `share_creep_score` (0.50) sits **strictly below
> `risk_block_severity` (0.70)** — counter-case + a setup-strength haircut, sub-veto, pinned by test.
> **Named recall holes (#9):** FPIs/ADRs (annual 20-F cadence), fresh IPOs, and dual-class filers whose
> per-class dei dimensions companyfacts drops (HIMS — whose convertibles remain `dilution_clock`'s
> territory) have no quarterly series; the detector declines on them, never fabricates. **Now LIVE
> (`share_creep_enabled=True`, flipped 2026-08-19):** the sig-lab off-vs-on pass MEASURED it safe (61 fires /
> 5 theses / 0 arm-withholdings — real dilution, e.g. UEC +13.4% QoQ), so the `[PROPOSED]` dials held and the
> operator flipped the switch.

## 2b. The split-night hold  `[BUILT — split plan S5, operator-approved 2026-10-22]`

**Why.** A vendor's first post-split bar can arrive on the new price scale while the stored history is still on
the old one (or a session early, or a night late). Read across that step, a 1-for-16 reverse split is a
+1,500% "breakout" and an unadjusted forward split is a crash. MEASURED (Q10, 2026-10-18): 17 honest armed rows
rested on such fake breakouts (GAME 10, NXL 5, MDCX 1, VRME 1); the T4 tape monitor can judge a lone cliff
only from bar 3. The split list (S1, S4) and the price leg (S2) put a LISTED split's history on one scale; the
hold covers the nights before a split is listed.

**When it holds** (`domain/split_hold.py`, pure). A member's tape carries a **split-shaped step** — two
consecutive stored bars whose close moves **x3 or more up, or to 1/3 or less down**, with the volume moving
the **other way** (`beta = -ln(volume ratio) / ln(price step)` in `(0, 3]`; both closes at or above T4's
two-cent tick floor) — whose first new-scale bar falls in the **last three sessions through the asof** (the
step day is session 1), and **no split in force on the asof** (confirmed, or announced once its first session
arrived — `domain.splits.is_applicable`) has its first session between the two bars or within two sessions
of the step bar. **Both directions** (operator-approved): an unadjusted forward split reads as a crash on
rising volume. A genuine big move comes on a volume surge in the SAME direction (JAGX 2026-09-22: x12.9 on
x383 volume) and is never held. The volume is read bar to bar, not as T4's flank-median level: a single bar's
volume is too noisy for T4's `BETA_MIN` (S4 MEASURED CMND's real 1-for-8 day at beta 0.041). Three sessions,
not "the newest bar": the vendor serves the re-scaled bar late (MDCX's 10-08 close arrived re-scaled on the
10-09 night) or early (NXL's 08-28 bar arrived on the 1-for-30 scale a session before the 08-31 split, so its
09-01 card read the step on the third-newest bar); sessions, not bar positions, so a missing bar or a stopped
feed never stretches a hold.

**What it holds.** That night, the member's **price-path triggers** — every detector that declares
`Detector.price_trigger` (the 8-day breakout, the 52-week breakout, the structural and fast breakdowns; a
registry guard fails a price reader that declares none) and the laggard rotation — read **only the bars
before the step** (`signals/split_hold.HeldPriceView`, applied in `pipeline.core.assemble_from_pit`). A
trigger that reads across the step cannot fire, so it cannot warm, arm or de-arm; a trigger wholly before the
step stands on its own clock (a breakout three sessions before the split keeps arming); a breakdown the
step's jump would have "cleared" stays (the last one-scale reading). The theme broadcast and the laggard read
the held stream, so a held breakout cues no co-member. The insider detectors are not held: they read the
tape only to screen a Form 4's price against its day low, and they fire on the filing.

**Held is never silent (#6).** Every open hold adds ONE line to the card's existing `missing[]` (no new card
field), after its own entries — so the counter-case lists it too — every night it holds:

| Case | Line (verbatim shape) |
|---|---|
| a list row one source stands on | "Breakout on BTOC held: possible unconfirmed 1-for-16 reverse split (SEC 8-K filed 10-09; waiting for a second source)" — or "(listed by Polygon; …)", or "(SEC 8-K filed 10-09 and Polygon disagree; waiting for your review)" |
| an announced split, the price early | "… held: the 1-for-30 reverse split is announced for 08-31, but the price stepped x29 on 08-28, before its first session" |
| nothing listed | "Breakout on GAME held: possible unconfirmed split (the price stepped x7.5 on 08-24 against the volume; no split filing found yet)" |
| a listed split set aside | "… (the price stepped …; the 1-for-10 reverse split listed for 07-30 was rejected)" |
| no trigger withheld | "Price triggers on AXG held: …" |
| co-members cued by a held price | "Laggard confirmation on ARM held: it reads the price of SMTK, held for a possible unconfirmed split" |

The line names a trigger only when one was WITHHELD (its identity — role, kind, grade, de-arm grade, fire
date — is not what the held view reads); a trigger re-read on the bars before the step (another close in its
label) is the reading held, not a trigger withheld. A co-member appears only when its laggard confirmation was
withheld (the leader cue was a held breakout, or the basket median moved it across the threshold); a laggard
merely re-labeled by the median adds no line. A hold changes the card, so `record_if_changed` records it when
it opens and when it clears (and when its line's words change — a list row arriving, a withheld trigger
appearing, or the vendor re-scaling a bar so the step moves a session).

**How it clears.**
- **Listed.** Once a split covering the step is in force on the list (S4 detects it nightly; the operator can
  confirm a needs-review row), S2 puts the history on one scale: no step, nothing held, and the triggers read
  corrected prices — a real move fires (one night late), a split-only jump fires nothing. **A legitimate split
  listed in time is never held** (GAME's, NXL's and VRME's filings came before their split days, and on the
  public clock a split counts from its own filing or first session).
- **Released.** After three sessions the hold lifts on its own. A step that NO list row of any status sits near
  then goes to the operator: the Admin Splits panel lists it as a "split-shaped jump with no split listed — is
  this real?" (the step's two bars and the bars after it; `pipeline.split_jumps`, the hold's own read on the
  public clock, last 30 days) and the nightly report prints ONE quiet line. **Never a page (#7).** Polygon's
  adjusted bars are not shown (no Polygon price client exists). **Named residual:** an unlisted real split,
  once released, reads across the step again until it is listed (T4 flags an up-step as a cliff from bar 3).

**Invariants.** #1 — computed from the view's own bars and split list (`price_history` / `split_events`): a
history view on the PUBLIC clock sees a split from its own publication, the live view what the list holds
now; no record-clock replay. #3 — deterministic, no model. #4 — timing only: it withholds the go-signal for a
few nights and never judges the thesis. #6 — on the card. **One implementation**: the nightly, the serve
path, `pipeline.backfill`, D3b and the replay mirror all assemble through `assemble_from_pit`
(`tests/signals/test_split_hold.py` pins the mirror's parity on a held night).

**Golden on real nights** (`tests/signals/test_split_hold.py`, every stored version of the real bars from a
read-only copy of prod; the split row is the only variable): GAME 08-25, NXL 09-01, MDCX 10-09 and VRME 10-01
are held with the split unlisted (and fire the +several-hundred-percent breakout without the hold) and not held
with it confirmed; PTIX 2025-05-19 and JAGX 09-22 / 09-23 are never held; ZCSH's real 3-for-1 forward split
(Polygon only, needs review) holds its fast breakdown and names Polygon's row once it is public; GAME's step is
on the operator's list three sessions on, and is not once listed.

**MEASURED on prod (a read-only copy, 2026-10-22; each card assembled with and without the hold over one
view).** Live (asof today, the record clock): **0 holds, 0 cards changed** across the 12 active theses.
History (the public clock): 180 cards (15 Fridays 07-10..10-16 x 12 theses) — **5 changed, each a card with a
hold open**; the split nights (13 nights x 12 theses) — the four golden nights are NOT held on today's list
(each split is listed and public by its night: one scale), 7 cards carry a hold elsewhere. Every change is the
hold's line; one moved an arm: AI Memory & Storage 08-21 drops ARM, whose laggard arm was cued by SMTK's fake
+3,288% breakout (SMTK's 1-for-50, one source on the list then); state and verdict changed nowhere. The holds:
real splits not yet in force (OFAL 1-for-10 and SMTK 1-for-50 — fake breakouts withheld; ZCSH's 3-for-1 forward
split — a fake fast breakdown withheld) and down-steps on heavy volume with no split listed (YYAI, WNRS, KXIN,
BTAI, AXG — most likely genuine crashes; WNRS's structural breakdown and YYAI's fast breakdown were held for up
to three sessions): the operator-approved cost of holding both directions. The released, unlisted list held 1
to 5 items on each of those Fridays, all but WNRS's 07-13 up-step being down-steps; today it holds 1 (AXG's
09-25 fall to 1/6.5).

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
  floor; else `flip`. Calibrated in `CallConfig`. **What counts as a code-`P` *open-market* buy:** SEC code
  `P` is "open market **or** private purchase", so a primary-market subscription (an IPO allocation / PIPE /
  placement at the **offer** price) files as `P` yet did not transact on the open market. The detector screens
  these out by cross-checking each buy's price against the security's **own EOD low that day** (`day_lows`,
  from the same point-in-time price view — no lookahead): a buy priced materially **below the day's low**
  (`insider_offmarket_below_low_frac`) is off-market and does not feed conviction, and a physically-impossible
  row (`usd` above `insider_max_plausible_txn_usd` — bad source data, e.g. a $100k/share price → a $2T buy) is
  dropped too. **Recall-safe (#9):** no price bar for the day → **kept** (a genuine open-market print is inside
  `[low, high]`, so this cannot exclude one — save a name that *reverse-split* between the buy and asof, a
  documented limitation). Excluded rows **stay** in `fact_insider_txn` + the display tape; only the call skips
  them. This is what stops an IPO-day insider subscription from arming a fake CORE (PBLS: RA Capital's $394M
  subscription at the $20 offer vs a $29.65–34.47 tape → was a half-billion CORE, now the honest ~$473k FLIP
  from the *real* post-IPO open-market buys).
  **Who counts as an insider (not only *what price*):** code `P` is filed by any acquirer, not just the
  officers/directors the open-market-purchase literature is about. The detector screens out a **self-filing** —
  the issuer filing a Form 4 on its own stock (reporting owner **==** issuer: KYOCERA-on-KYOCERA $690M @
  $21.75, Roivant-on-Roivant $350M @ $21 — a buyback/treasury/ADR mechanic, priced *at* the market so no
  price screen catches it), which is never personal insider conviction (#3). It recognizes the self-filing by
  **identity**, most-robust first: `rpt_owner_cik == issuer_cik` (both captured from the filing, migration
  0024; the CIKs also flow into replay via `SELECT *`), else the **filer name == the issuer name** (the row's
  captured `issuer_name`, or the security's `security_master` name for a row ingested before the capture — so
  the screen works on already-ingested rows with **no backfill**). **Recall-safe (#9):** only a self-filing has
  filer == issuer, and a missing CIK / name-format mismatch simply *keeps* the row; excluded rows **stay** in
  `fact_insider_txn` + the display tape, only the call skips them. *(Larger 10%-owner fund/affiliate blocks —
  Baker Bros' $190M — vs a genuine large activist/director buy — Paulson's $312M — are a **separate, deferred**
  call: no clean structured discriminator separates a noise block from smart-money, so they are left in for now
  pending a labeled sample; the captured CIKs set that pass up recall-safe.)*
  **The 10b5-1 planned-buy weight (Band 03 S2c — a DORMANT dial, `insider_10b5_1_buy_weight = 1.0`):**
  there is **no** buy-side 10b5-1 screen today — a planned buy counts fully — but the seam is piped so
  weighting one later is a **config flip, not a code change**. Semantics, stated once: the weight scales a
  KEPT planned buy's (`aff_10b5_1 IS TRUE`) **$ contribution** to the cluster total; the buy is **present**
  for the distinct-insider / senior / anchor logic **iff the weight > 0** (`0.0` = a full screen — the buy
  leaves the survivor set and the anchor moves). Tri-state honored: only an explicit `True` weighs; `None`
  (the pre-Dec-2022 norm) / `False` weigh 1.0 — unknown is never asserted "planned" (#9). At the 1.0
  default every decision golden is **byte-for-byte** today's (`x × 1.0` is IEEE-exact); flipping it is a
  later measured, operator-signed decision (the sell side's `insider_sell` precedent). The primary-market /
  self-filing excludes above stay **hard set-aside** (labeled on the Scoreboard ledger, not dial-able).
  **Which rows the insider detectors see — Form 4/A amendments and the other-issuer screen (R8-A,
  operator-approved 2026-10-17, Option A).** Every as-of read of `fact_insider_txn` (both engines: Postgres
  and the replay mirror, one pure resolver, `domain/insider_amendments.py`) is resolved against the stored
  4/As before any detector sees it. A 4/A is stored as filed and linked to the original it amends; what it
  does depends on its **disposition**: a clean **1:1 correction** (shares / price / code — including `A`→`P`
  — / date) is applied automatically, its rows replacing the original's; a **clerical** one (Table I
  identical AND no signal row's owner role / name / 10b5-1 flag moved) changes nothing and never
  double-counts; a **field change** (Table I identical, but the role / name / 10b5-1 flag on a code-`P`/`S`
  row changed — FRHC's CFO restated "Special Advisor to CEO" on a sale) is never "clerical"; a **void** (a
  zero-row amendment of an original with rows), **duplicates** (a 1→N void of filing-agent copies), a
  **partial** / **consolidation** / **addition** / **restatement** / **field change**, and an **ambiguous**
  link are the other kinds. **The filer's own correction is applied by default (R8-B, operator decision
  2026-10-20, revised 2026-10-21):** every amendment the linker resolves to a DEFINITE original — correction,
  void, duplicates, partial, consolidation, addition, restatement, field change — is RESOLVED BY THE SYSTEM. Why:
  a Form 4/A is the insider's OWN legal correction of their own filing, filed under penalty — deterministic
  data, not a model's recommendation — so applying it is data handling, not a decision taken from the operator
  (#3 / #10 are untouched; the operator was clicking "yes, believe the correction" on items with nothing to
  judge). Each resolution is an append-only decision whose basis names why — an `apply` (`system:filer-correction`,
  or `system:no-signal-effect` when its effect changes no row a call signal reads: the 2026-10-18 signal-effect
  test, kept — the `(code, date, shares, price, 10b5-1 flag, owner role, owner name)` of a row whose code is in
  the signal code set, compared before vs after as a multiset), or, for a void its words do not retract, a
  `keep` (`system:no-retraction`; `system:held` — owed) — written in the link's own transaction at ingest, or,
  for what a pre-R8-B ingest left pending, by the nightly SWEEP before the per-thesis loop (`recorded_at = now`,
  so a replay pinned earlier still reads the pending state: no rewrite of the past). Every one is listed on
  Admin ("Resolved automatically", with its remarks, footnotes, the filer's words that settled it and the
  original's rows beside the amendment's) with an exact Undo — an operator verdict stacked over the system's
  decision (the newest standing verdict wins), whose own Undo restores the system's resolution.
  **The row match (a correction removes a buy or sale only when it ACCOUNTS for that row):** an applied partial
  / consolidation / addition / restatement / field change replaces ONLY the original rows it accounts for
  (`domain.insider_amendments.match_rows`), and accounting is **exact shape only**, same owner first:
  identical; same date + code + shares (a price fix); same date + shares, another code (BAH's `S`→`F` recode);
  same code + shares, another date (a date fix); a sum on the same date + code (a consolidation — BHVN; one sale
  split into lots — BETR); then same date + code + price (a share fix). A bare "same date + same code" never
  matches (IBKR's 4/A adds an omitted sale beside two real same-day sales). An unaccounted original STANDS
  (MRCY's 4/A adds only an omitted grant — both its sales stand); an unmatched amendment row is an addition.
  **Leftovers** — an unaccounted original and an unmatched amendment row of the same owner AND code — are settled
  by **the filer's own words** (remarks + footnotes; deterministic phrase lists, `FEED_LOOP.md` §R8-B): FIX words
  → the amendment's rows replace them (MRAM, BKSY); ADD words → every original stays and the amendment's rows are
  added (IBKR); neither → **unresolved**: not applied, owed to the operator, the original standing meanwhile
  (YYAI); both → FIX when, in every leftover group, the amendment re-reports at least as many rows as the
  original (IEHC), else unresolved. The match is computed against the full stored rows when the decision is
  written and stored on it (`replaced_rows`, migration 0053: the keys replaced, `[]` = none); NULL — every
  pre-R8-B decision and the operator's *Use the correction* — replaces the whole original.
  **The voids:** a zero-row 4/A retracts its original ONLY when BOTH its own text carries explicit retraction
  language AND the original holds exactly ONE transaction, a code-`P`/`S` one — the words can then mean only
  that trade, and the system applies it (FLS's broker-canceled buy, AVEX, MNKD, DVLT ×2 — hidden). No
  retraction language → the original is KEPT as filed (a holdings / Table II / power-of-attorney fix: SMR,
  AZTA, VWAV — quiet). Retraction language against several trades → HELD: owed, the original standing meanwhile
  (FIGR's "did not in fact occur" beside a $7.43M sale). Words are never mapped to codes (FLS's text says "sale"
  for a code-`P` buy). Duplicates keep their handling (the later copies suppressed).
  **Only what the system cannot resolve is owed the operator** — an **AMBIGUOUS link** (several differing
  candidate originals — the system cannot know which filing it amends), an **unresolved** row match, or a
  **held** void — and only when it matters: it would change a code-`P`/`S` row (the content test) dated inside
  the **review horizon** — the dial `insider_amendment_review_horizon_days`, derived by default as the two-year
  backtest window (730 days — the member price tape's range, so no call is computed before it) plus the longest
  insider read horizon a detector declares (`P` 210) = **940 days** (`signals.horizons
  .insider_amendment_review_horizon`), because a row older than that is read by no call, live or replayed. Any
  of them outside it is listed quietly as "unresolved — historical (no call reads these rows)": the originals
  stand, it never pages, and the operator can still decide it. The operator has **three verdicts**, each with
  its exact Undo: *Use the correction* (the amendment's rows replace the whole original; for a void, it is
  hidden), *Keep the original*, *Add to the original* (every original row stays and the amendment's rows are
  added — `replaced_rows = []`, basis `operator:add`). The signal code set is derived in ONE place from the
  detectors' own declarations (`Detector.txn_codes` + `horizons()` → `signals.horizons.insider_signal_codes`:
  today `{P: 210 days, S: 120 days}`; a detector that reads `fact_insider_txn` without declaring codes raises).
  **The pending state** (an operator-linked amendment awaiting its verdict, one the operator took back by
  revoking the system's decision, or a pre-R8-B link the sweep has not reached): a cancellation (void /
  duplicates) SUPPRESSES the affected original rows — buys and sells — so a canceled buy never fires (the
  operator's condition); the other kinds have no effect until applied; an ambiguous link suppresses nothing.
  Separately, a row whose **issuer** (from the XML) is not this security — the member was the *reporting
  owner* of another company's stock (Honeywell buying Garrett Motion, Novartis selling Aileron) — is screened (keep-and-screen;
  the 147 such rows on prod are kept, not deleted). Every input is gated on its own `recorded_at` (the link,
  each decision): a replay pinned before them reads the original exactly as stored (#1). Examples on prod
  (MEASURED 2026-10-17, see `docs/FEED_LOOP.md`): FLS's cancelled $164k director buy stops being a CORE
  trigger (pending void); ATCH's five `A`→`P` recodes restore a missed CORE (5 insiders, $162,524); ARBE's
  50→50,000-share fix adds a FLIP; TDG's triplicated sells count once.
  **Joint reports — one trade several persons reported, counted ONCE (operator-approved 2026-10-21).** After the
  4/A resolution, in the same seam and for both engines (`domain/insider_joint_reports.py`), rows identical on
  (date, code, shares, price, **post-trade holding**) from ≥ 2 reporting owners in ≥ 2 SEPARATE filings are one
  trade: a fund and its partner (Maverick Capital + David Singer on INFQ), a spouse "By Spouse" (SMCI, OKLO), a
  family vehicle and its five filers (AMKR). The evidence is the filing's own `directOrIndirectOwnership` +
  `sharesOwnedFollowingTransaction` (migration 0054). **Never two direct holders** (two persons' own positions,
  however identical — ALAB's co-founders); a post-trade holding of 0 or == shares merges only between filings a
  non-trivial holding already linked; a row without the evidence NEVER merges (it can only stay double-counted).
  The survivor is the direct holder's row when there is one, else the first filer of that owner-set (the same
  filer for every trade of one joint report — the party counts once); it keeps its own name for the
  distinct-insider count and carries every co-reporter's role (seniority — counted once, never demoted) and
  filing (cited in the provenance), and the label names it: "…; counted once: A / B (joint report)". MEASURED
  2026-10-21 on prod (read-only): 0 thesis-level flips over the 666 honest call rows; INFQ's "$95.2M across 2
  insiders" (the 4/A pair -000507 / -000508) reads $47.6M by one party.
  **Internal transfers — a buy and a sale between LINKED filers count for nothing (operator-approved
  2026-10-21).** After the joint-report collapse, in the same seam and for both engines
  (`domain/insider_internal_transfers.py`), a code-P row and a code-S row on one security with the same (date,
  shares, price) are one INTERNAL TRANSFER when the legs' OWN filings link their owners: **(1) the same filer**
  (Pershing Square's funds "purchased and sold … among themselves"; ValueAct's "internal restructuring";
  Goldman's matched market-making fills), **(2) a shared holding** (a joint-report fingerprint —
  `filing_links` — links a buy-leg filing and a sale-leg filing), or **(3) a named holding** (a leg's filing
  reports INDIRECT shares whose `<natureOfOwnership>` names the other leg's owner — Susan Y. Kim's purchase
  filing holds "By James J. Kim 2024 GRAT"; migration 0055's `filing_natures`). Each buy pairs with ONE linked
  sale (a maximum matching, direct legs first); an INDIRECT leftover linked by a holding is the same shares
  reported again (Agnes Kim's "By Spouse" copy of her husband's sale) and goes with it; any other leftover
  (Goldman's second 63-share fill) stays counted. **Keep-and-screen:** every leg stays on the read, tagged; the
  conviction detector leaves a transfer buy out and the sell detector a transfer sale (a structural bucket, before
  price and plan), each NAMING the ones inside its chosen window ("…; 2 internal-transfer txns ($37,999,990)
  screened" — appended only when present, so no card without one re-records). **Not screened:** a private sale
  between UNRELATED people — a director buying the founder's block (FTHM), a Stock Purchase Agreement between two
  holders (RDNW), IAC buying MGM from Corvex — is real conviction (decision 3; the ledger's quiet note, below).
  **Safe on missing evidence:** a row without the 0055 capture links nothing by name. **Pair-local evidence
  only** (the legs' own filings), so every reader screens alike whatever its window: AMKR's 2021-12-30
  note-payment transfers are NOT screened — that day's filings state only generic natures ("By GRATs", "By
  trusts"); the natures naming the other side first appear in 2022+ filings. MEASURED 2026-10-21 on prod
  (read-only, the repair's own plan injected): the 12 active theses at asof today — 0 card diffs; the 666 honest
  call rows — 0 changes (no member holds a transfer leg inside the 240-day insider read of any of them); the 2025
  re-run (known_at now) — AMKR armed in AI Memory & Storage on 94 of 261 weekdays → 0 (an AMKR insider buy on the
  card 223 → 0 days; the 2025-02-25 "3 insiders … sold $38,119,047" cluster gone; the thesis state unchanged on
  every day), MRLN's Dec-2025 flip conviction $42,551 → $37,042 (23 detector-days, 0 member or thesis flips).
  **Private purchases (display only, never a call input).** A counted buy whose OWN cited footnote
  (`txn_footnotes`, 0055) says it was a private purchase — a deterministic phrase list, the passage located
  verbatim (`domain/insider_private_purchases.py`; never "purchased … ranging from $X", never one that says
  "open market" or a public offering) — carries a quiet Event Ledger note: "private purchase from Joshua Harley
  — not on the open market". The counterparty is named only when grounded (a same-day seller on the tape the
  passage names, or the issuer).
  **Not built (measured 2026-10-21, a proposal awaiting sign-off): the "by spouse" duplicate OUTSIDE a transfer**
  — an INDIRECT row whose own nature / footnote names a spouse and that matches (date, code, shares, price) a
  DIRECT row of another owner with a DIFFERENT post-trade holding (so the joint collapse leaves it). On prod's
  P/S tape: 237 such indirect–direct matches; 10 carry a spouse phrase (6 in the last two years, 0 inside the
  240-day read), and 2 of the 10 are definite false matches — MIAX 2025-08-13, Mark Massad's "By spouse" IPO-price
  purchase matching two UNRELATED directors' own identical purchases — plus 1 probable (AIXC 2016, a relative's
  separate purchase). Not near zero, so the narrow rule stays off. Inside a transfer the case is already handled
  (the co-report leg above — AMKR's Agnes Kim).
- **`catalyst_conviction` `[approved]` — grade-DECOUPLED liveness (option A).** Liveness = the agreement's own
  **relevance horizon** (its period of performance), independent of grade; grade = the **customer-vs-sponsor**
  nature of the commitment — a DOE **contract** (DOE *buys your product* = revenue) or a **loan / loan
  guarantee** (committed financing) = `core`; a grant / cooperative agreement / OTA (DOE *funds your
  development* = support) = `flip`. **By nature, never by obligation amount** (a $148M cooperative agreement
  is still `flip`; obligation magnitude may affect the trigger score and therefore setup strength within the
  grade, never position size). Full rule + precedent (LEU core, OKLO flip) in
  `docs/CATALYST_CONVICTION.md`.
  **What SETS that liveness, in one line:** the fact's own published `horizon_end` when it has one
  (`horizon_end − valid_from`, floored at a day), else `catalyst_default_horizon_days` (365) — and since
  A2 the published branch passes through an optional ceiling, `catalyst_max_horizon_days`, **`None` by
  default and therefore inert today**. The ceiling exists because the term is a fact about the
  *agreement* while the edge horizon is a policy question about *this platform*, and one DOE OTA can put
  1,238 days between them (OKLO, event 2026-02-09, term to 2029-07-01). It caps the published term ONLY;
  the no-term fallback keeps its own dial, because "we do not believe the edge outlives N days" and "we
  do not know this agreement's term" are different claims.
- **`theme_conviction` `[built — M5b]` — grade-DECOUPLED liveness, capped at flip.** An operator-ratified,
  **thesis-level** conviction (the basket-level analog of an insider buy / a name's catalyst), broadcast onto
  each eligible member as a Key-1 **fallback**. Always **`flip`** (capped at starter — belief never mints a
  core; it routes through the weaker-key path like any flip); liveness = the operator-set **horizon**
  (decoupled from grade, like a catalyst). It arms a member only when the member has its **own** live
  volume-backed (`core`) confirmation and **no** own live conviction (own wins; a lapsed own conviction falls
  back — the floor). Full rule in `docs/THEME_CONVICTION.md`.
- **`revenue_acceleration` `[built — sig]` — grade-FIXED core, structural (Key 1).** YoY revenue growth's
  second derivative turning positive (re-accelerating for the first time in N quarters) is the structural
  conviction behind the 5–10x — `core` by default (a fundamental inflection is durable). The grade WAS fixed in
  the detector; it now reads `revenue_accel_grade`, a **DORMANT** dial whose default `core` reproduces this rule
  byte-for-byte. That exists so the backtest can ASK whether a COMPUTED screen should supply a `core` Key 1 —
  it is the single largest source of arms on the record — without a code edit; nothing about the live rule has
  changed, and a flip needs a pre-registered pass plus sign-off (`docs/RECALIBRATION.md` part A).
  Two floors keep it honest: a YoY **level** floor (`revenue_accel_min_yoy` = 10%) and — ratified
  2026-08-15 — an absolute **acceleration** floor (`revenue_accel_min_accel` = **2pp**), so a sub-2pp wiggle
  (UNH re-accelerated ~0.4pp — noise, not a genuine re-acceleration) never mints a CORE. Knowability from the filing's
  `filed` date (#1); reads companyfacts via `fact_fundamentals` (migration 0036).
  **The series is the company's CURRENT revenue label (2026-10-20).** The ingest used to keep the FIRST
  `REVENUE_CONCEPTS` tag present (`Revenues`), and most filers moved to a `RevenueFromContractWithCustomer*` tag
  around ASC 606 — MEASURED on prod, 163 of 495 active companies with revenue were on a series that had stopped
  (AMD's at 2017-09-30), so this trigger could not fire on their real quarters. Now the tag whose newest quarter
  ends latest supplies the series (a tie goes to the tag with more quarters, then the list order), never joined
  with another tag (a join splices two definitions of revenue); two total-revenue tags are appended to the list
  (`RegulatedAndUnregulatedOperatingRevenue` for utilities, `RevenuesNetOfInterestExpense` for banks); where a
  stored old-tag number sits on a filing the current tag reports differently, the current tag's number is appended
  as a RE-PARSE of that same filing, naming the version it replaces (`ingest.fundamentals.plan_versions`; the
  `supersedes` chain, migration 0060), and the read takes a filing's latest parse. A **data-quality screen** keeps two row
  shapes no genuine quarter can have out of the series — counted and reported, never stored: a fiscal-year total
  filed under a quarter context (LHX, VICR) and a derived Q4 across a restatement (LFMD). The rule above is
  unchanged — it reads the right numbers. Re-ingest: `python -m pipeline.reingest_fundamentals`. Each revenue row
  now records its tag (`source = companyfacts:<concept>`), since two tags can share one accession (#6).
  **The rows the OLD ingest stored before the screen existed are SET ASIDE on read (A8, operator-approved
  2026-10-22; migration 0062).** `python -m pipeline.set_aside_fundamentals` runs the same screen over each
  company's cached companyfacts, under every revenue label, and names each stored version it rejects in the
  append-only `fundamentals_set_aside` table (MEASURED on the dev copy of prod with prod's cache, 2026-10-22: 79
  versions in 38 companies; a 1-row conflict, RDNW, that another label keeps as genuine, is left in the read).
  Every read skips an active set-aside — both clocks, the basket prefetch and the replay mirror — so the read
  falls back to the version it superseded, at every asof: the screen judges a row only against filings on or
  before its own, so this is exactly what the ingest screen would have refused the day the row was filed (no
  lookahead). Reversible: a `revoke` row restores it; the fact rows are never touched. Where the bad version was
  a period's only one, the period is simply absent (the detector declines across it, as the ingest screen would
  have left it). MEASURED: the read changes for 36 of the 38 companies (KOPN's and UUUU's rows were already
  outranked by a same-filing re-parse), and no card of the 12 theses changes at asof 10-21 or 10-22.
  **Hindsight caveat (a definition chosen today).** The tag is chosen from TODAY's companyfacts document, and
  under R17 the re-ingested versions — the same-filing supersessions included — are dated at their own filings.
  So every past as-of read on the public clock (the slider and scrub-back, `pipeline.backfill`, a D3b re-run,
  the Scoreboard re-run) now sees the current tag's number for those filings where it used to see the old tag's. No number
  becomes visible before its filing (no lookahead); what changed with hindsight is which public number counts
  as "revenue" — the same kind of caveat as the ratified tables' announcement-date clock.
  **Knowability is FIRST knowability (D3a, operator-approved 2026-10-16).** Every 10-Q re-reports the year-ago
  quarter as its comparative, and the ingest stores that as a new version dated at the LATER filing; dated by the
  latest version, a year-old inflection re-fired as fresh for another 180 days. The as-of read now annotates each
  fundamentals row with `knowable_from` / `knowable_accession` — the earliest knowable date (and filing) in the
  trailing run of value-identical versions ending at the picked one, counting only versions visible at
  `(asof, known_at)`; a version's own knowable date is its FILING date (`valid_from`), and the picked version is the
  latest filing visible — and the detector's fire date, liveness anchor and provenance accession all come from it.
  (Migration 0057, operator-approved 2026-10-21: the date is the PUBLIC filing date, never our store time — the
  ingest now stamps `recorded_at` honestly, as bookkeeping. The earlier `max(valid_from, market date of
  recorded_at)` would then have dated a name's whole history at the night it joined a basket — re-firing year-old
  inflections as fresh — and was identical to this rule on every row stored before 0057.) Equality is exact on
  the stored number, so a genuine restatement (a changed value) is new knowledge at its own filing and still fires from there;
  firing only on a filing's own newest quarter (rule (c)) is a separate, deferred policy call. When several live
  inflections share one knowability date, the NEWEST period is cited (tie-break (d)). Example — GEV: Q1-2025 revenue
  of $8,032M was first filed 2025-04-23 and re-reported unchanged in the 2026-04-22 10-Q; the 2026-10-01 Nuclear
  Buildout card cited "Q1 ending 2025-03-31" dated 2026-04-22. Now Q1-2025 dates at 2025-04-23 (long lapsed), and
  GEV stays armed on its genuine Q1-2026 inflection (YoY +16% after +4%), first filed in that same 2026-04-22 10-Q.
  MEASURED on prod at 2026-10-15: 101 → 74 live revenue triggers (27 lapse, 11 re-cite a genuine newer quarter, 3
  remaining fire off restated values — one of them, VREOD's 48,063,010 → 48,100,000, a rounding-only change: the
  named residual of exact equality); AI Memory & Storage (DELL/HPE/NLST) and Nuclear Buildout (CEG) leave Armed,
  and Rainbow Rush / The Everything Exchange each lose one armed member (ENSC / CME). Replay applies the identical
  rule (one shared pure function, parity-gated).
- **`volume_breakout` `[built]` — the CONFIRMATION grade (Key 2).** VOLUME grades it: volume-backed
  (vol ≥ `breakout_volume_mult` × base avg) = `core`; a momentum thrust on weak volume arms but `flip`.
  **Grade-down** (ratified 2026-08-15, `breakout_weak_close_grade_down`): a volume-backed breakout that
  **closes weak** (`close_strength` < `breakout_close_strength_min`, near the low of its day range) grades DOWN
  `core → flip` — a weak-close breakout is a quick-trade, not a structural hold (a failed next-day *hold* is
  score-only, no grade cut). This is why UNH — breakout `close_strength` 0.119 — arms a **starter** on its
  strong insider conviction, not a core.

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

**The two clock FLAGS on an armed member (Q14, 2026-10-18).** Each clock has its own flag on
`MemberCall`, and each is labeled for the clock it reads — they used to share one label, and it was the
wrong one:

| Flag (wire field) | Reads | On when | UI label |
|---|---|---|---|
| `lapsing` | the **hold clock**, `exit_by` | `exit_by − asof < headline_lapsing_soon_days` (45) — the same freshness band the menu ranking uses | **"hold clock closing"** |
| `entry_window_closing` | the **entry window**, `arm_until` | at most `entry_window_closing_sessions` (2) **trading sessions** remain after the card's `asof`, through `arm_until` inclusive | **"entry window closing"** |

`lapsing` was labeled "entry window closing" until Q14; MEASURED on the record (audit A3), 69% of
arm-window expiries never showed it — it tracks the 45-day hold band, not the confirmation clock that
actually ends an arm. Its meaning is unchanged; only its words are. The wire name stays `lapsing` on
purpose: `MemberCall` is a strict schema (`extra="forbid"`), so renaming the field would make every
recorded card unparseable and break the API contract, for no change in behavior.

`entry_window_closing` counts sessions with the cron's Mon-Fri calendar (`domain.trading_days.is_scheduled_day`
— the one definition `pipeline/schedule.py` also imports; `calls/` never imports `pipeline/`):
asof Mon with `arm_until` Wed = 2 left (Tue, Wed) → on; the Friday before = 3 → off; Thu with a Monday
close = 2 (Fri, Mon) → on, though it is four calendar days; a weekend `arm_until` (a calendar-day window
can end on a Saturday) adds no session, so Friday is the last. **Holidays are not modeled** — that calendar
fires the cron on market holidays too — so a holiday inside the last few days counts as a session and the
flag comes on **one session late** (Labor Day 2026: `arm_until` Mon 09-07 read 3 on Wed 09-02, where an
exchange calendar reads 2). The flag is computed from the card's own `asof` and the member's own
`arm_until` — no wall clock, so no lookahead — and only for an **armed** member: a watch member's
confirmation clock is a decay, not an entry, and a held (Managing) member is past its entry, so neither ever
carries it (#7). It is **display and timing context only (#4)**: computed after every decision and read by
none of them — state, verdict, grade, ranking and setup strength are identical at any dial value (golden-
tested). The Cockpit shows it on the Entry-by cell, the per-name panel and the CallCard's entry clock (the
headline member's), and the MemberMenu chips it; it replaced the Entry-by cell's old front-end "within 7
calendar days" tint, so there is one definition of "closing".

**The displayed trigger date (`event_date`).** Each fired trigger on the call card carries `event_date =
event.asof` (its fire date), rendered as a muted right-aligned date on the row and used to order the list
**newest-first** (display only — the assembled `triggers_fired` order is unchanged, so the Scoreboard's
arm-time snapshot stays stable). For a technical breakout that's the breakout bar date; for an **insider
cluster** it is the **most-recent buy in the CHOSEN cluster** (the anchor in §3/the detector), **not** the
earliest or the largest — so a cluster spanning e.g. Jan 30 → Feb 25 reads **Feb 25**. That is the freshness
anchor (when the conviction last strengthened) and is the same date the liveness/`exit_by` clocks key on, so
the row date and the clocks never disagree.

**Which cluster is chosen (the anchor walk).** The detector does **not** pin the anchor to the single
most-recent kept buy. Every distinct kept-buy date is a *candidate* anchor; each gathers the buys inside
`[anchor − insider_cluster_window_days, anchor]` (bounded on both sides, so a later buy never leaks into an
older cluster's total) and qualifies only if that cluster clears `insider_min_usd` **and** is still live for
**its own grade**. Among the qualifiers the strongest fires: **prefer `core`, then the most recent anchor** —
the same selection key `catalyst_conviction` uses. Pinning the newest buy unconditionally (the shape before
this) let one small, late, non-senior buy *shadow* an older still-live CORE: the new anchor's window excluded
the big buy, the grade fell to `flip`, and the flip's short liveness then expired the conviction outright —
so *more* insider buying read *less* bullish. To display the *earliest* buy (when the cluster began) or the
*largest* buy's date instead, change the chosen candidate's `asof` in
`backend/signals/insider_conviction.py` — `event_date`, `exit_by`, and the liveness window all follow it.

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

**The Cockpit rail does not render that string.** The `counter_case_fn` hook is unwired, so every
counter-case today is the deterministic template — and the template joins the SAME `active_risk` list
that becomes `card.risk_signals`, plus `missing[]`, both of which the rail already renders as their own
sections with the ticker, kind, event date and sources the prose drops. Measured across the 12 live
theses: 8 rebuild byte-for-byte from `risk_signals` + `missing`, and the remaining four's difference is
a kill-criteria restatement (its own Cockpit section) plus one caveat sentence already carried on the
Confirmation key tile. So the rail shows a COUNT of those sections ("123 active risk signals across 92
names · 4 still missing") rather than a ~7,900px restatement of them. The wire field is untouched — it
stays on the recorded CallCard as part of the accountability record, and wiring the LLM path later is
the point at which the rail should render prose again.

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
presented as decided — the dials are labeled as dials.
