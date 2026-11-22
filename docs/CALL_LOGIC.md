# Call logic

How a thesis's signal events become a call: the state, the verdict, the grades, the two clocks (`exit_by`,
`arm_until`), setup strength and the counter-case. The machine computes every call; users read it on the Board
and act through decision capture. Read this before you change `calls/`, `pipeline/core.py` or what a detector
emits. What fires is [DETECTORS](DETECTORS.md); what each SEC filing stores is [FILINGS](FILINGS.md). Code cites
the section numbers below; they are stable.

None of the grade, `exit_by` (a signal-validity horizon, not a sell date), Managing (monitoring a thesis the
user entered) or the `confidence` wire field (setup strength) sizes a position, picks an instrument, routes an
order or manages risk (invariants 4 and 5).

## Code

| Piece | Module |
|---|---|
| The assembler (pure) | `calls/assembler.py` (`assemble_call`, `rank_members`) |
| Grades; setup strength; counter-case template | `calls/grading.py`; `calls/confidence.py`; `calls/counter_case.py` |
| The one seam every engine calls | `pipeline/core.py` (`assemble_from_pit`) |
| Dials | `domain/config.py` (`CallConfig`) |
| Card and event shapes | `domain/call.py` (`CallCard`, `MemberCall`, `TriggerRef`); `domain/signal.py` (`SignalEvent`) |
| Liveness, window end, sessions | `signals/common.py` (`entry_signal_is_live`, `window_end`); `domain/trading_days.py` |
| Split-night hold | `domain/split_hold.py`, `signals/split_hold.py`, `pipeline/split_jumps.py` |
| A card's counting catalyst entries | `signals/thesis_catalyst.py`; store `repositories/thesis_catalyst_repo.py` |
| Declared rule changes | `domain/rule_log.py` |

## How it works

### 0. Where the call is made

`pipeline.core.assemble_from_pit` is the one seam every engine shares (serve, nightly, backfill, replay,
editions, backtest). In order it removes the silent rows (`voting_thesis`, section 1), wraps the view with the
card's counting catalyst entries (section 3c), finds split-night holds (section 2b), runs every registered
detector on every voting member, broadcasts the theme conviction, runs the laggard rotation and calls
`assemble_call`. The assembler is pure: the same thesis, events, as-of, config and held lines give the same
card, with no database, network or clock (invariant 13). The `calls` table is the accountability record, never
the read path: every read recomputes the card, and a past as-of reads facts on the public clock (invariant 1).

**The through-line.** Each behavior keys on the property that drives it (invariant 14): call strength on the
grade, the signal-validity window on the conviction's horizon (`alpha_liveness_days`), the provisional
wording and the setup-strength cap on the entry grade (the weaker key), catalyst liveness on the agreement's
term. Never add an `if kind == ...` branch where a property already carries the signal; a new kind inherits
its behavior from its own properties.

### 1. Inputs

**The voting roster.** Every thesis ends with three fixed silent links, Context, ETFs and Bench
(`domain/thesis.py`). `voting_thesis` removes their rows at the seam, so a silent name stays visible but never
reaches a detector, the hold, the theme broadcast, the rotation reads or the assembler. Discovered votes. A
company is silent or voting, never both (the promote refuses both). ETFs holds the card's funds and only funds
(`fund_conflicts`), so a fund's own breakout never votes (rule `2026-11-funds-in-etfs-link`). Only the user
places a name there; the chain drafter never writes those labels. No `CallCard` field records it, and a history
view reads each row's link from the roster snapshot (invariant 1). Rule `2026-10-silent-links`. Naming guard:
never call a silent link "watch"; `CallCard.watch_members` means the confirmation-only members.

**A meta index card** (`thesis.meta_index`, migration 0075) has no voting names: `voting_thesis` returns an
empty basket, so the card stays Incubating and never warms, arms, notifies or enters the Decision Queue or the
Scoreboard; its links still feed dashboards. One writer, `PUT /theses/{id}/meta-index` (the promote never
writes it). The switch is card state, not versioned, so flipping it
changes re-runs of past nights; the editions engine freezes it with the card body. Rule
`2026-11-meta-index-card`.

**A signal event** (`domain/signal.SignalEvent`) has a `role` (`entry_trigger` or `risk_signal`; only entry
triggers turn a key), a `kind` (what produced it), an optional catalyst `type`, a grade, a score, its window
(`alpha_liveness_days`), provenance and `asof`, its fire date.

**The two keys.** An entry trigger turns Key 1 (conviction) when its kind is in `CallConfig.conviction_kinds`
and Key 2 (confirmation) when its kind is in `confirmation_kinds`, with one exception: the dormant dial
`revenue_accel_key` assigns the `revenue_acceleration` detector's events by detector name (`_is_conviction`,
`_is_confirmation`). `own_conviction_kinds` is `conviction_kinds` without the theme conviction. Each
detector's kind and key are in [DETECTORS](DETECTORS.md).

**Moving a kind between keys is a history change.** A recorded `TriggerRef` does not carry its key, so the
Trigger Log and the Ticker Lookup read a past trigger's key from today's config. The kind sets have only grown,
so every past row reads right. A change that moves a kind, or flips `revenue_accel_key`, must either record
the key on the card (a `CallCard` change with a rule-log entry) or give those pages the config in force each
night. Today the Trigger Log follows `revenue_accel_key` and the Ticker Lookup does not.

### 2. State-transition rules

A trigger is live on night N only while the first session after N is inside its window (section 6). The
lifecycle is a loop (Incubating, Warming, Armed, Managing), and Armed and Warming fall back.
`assemble_call._state` checks in this order:

| State | When |
|---|---|
| Managing | The thesis has an open position as of the night (`opened_on` unset or on or before it), whatever the triggers |
| Armed | Some voting member is actionable: a live conviction and a live confirmation on the same security (`arming_requires_confirmation`), not withheld by a risk, not de-armed, and (under the dormant `computed_conviction_requires_core_confirmation`) not a computed-only conviction on a momentum-only confirmation |
| Warming | At least `warming_min_entry_triggers` live entry triggers anywhere on the card and no member actionable: keys on different names, one key only, or keys co-located on a member that a risk withholds or a breakdown de-arms |
| Incubating | Otherwise |

So Armed falls back to Warming when `arm_until` lapses or every armed member is withheld or de-armed, and to
Incubating when every live entry trigger ages out. The card's key states (`key_conviction.turned`,
`key_confirmation.turned`) are basket-wide, so a Warming card can show both keys on when they sit on
different names.

**Managing** comes from the decisions log (`operator_decision`): a take opens a position, a close closes it, a
void undoes an append. `decisions_repo.effective_position` reads it on both time axes (the decision date and
when it was recorded), so a replayed past night never sees a later fill. Alpha Deck then monitors the thesis;
it does not manage the position. A take logged on a name also attributes that member (section 4).

**The risk veto.** A risk signal with no `dearm_grade` and a score at or above `risk_block_severity` withholds
the member it is on, at any grade. It vetoes timing, never the thesis. Each risk that withholds or de-arms a
co-located member adds a "Risk must clear before arming" line to `missing[]`. Weaker risks feed the
counter-case and cut setup strength. The assembler never ages a risk: each detector stops emitting one past its
own freshness window.

**The breakdown de-arm.** A breakdown is a risk that carries a `dearm_grade` (`signals/breakdown.py`). It
de-arms a member only when its score clears `risk_block_severity`, its `dearm_grade` equals the member's entry
grade, and it fired strictly after the member's arm date, the latest fire date among the member's live entry
triggers (`_is_dearming`). So a fast breakdown never shakes a core entry, and a break already true when the
member armed never counts. `breakdown_dearm_enabled` says whether; the dormant `breakdown_dearm_scope` says
which. The de-arm expression is one of three recorded strings picked by the grades that de-armed
(`_DEARM_FLIP`, `_DEARM_CORE`, `_DEARM_MIXED`); it quotes two dials that
`tests/calls/test_expression_boundary.py` pins. Its re-arm routes are not the only ones: a flip member also
re-arms when the broken breakout ages out while another confirmation stays live, a core member when a fresh
conviction post-dates the break.

**The theme menu.** `rank_members` orders the actionable members best first by (fresh, entry grade, own
conviction, runway, conviction score, id). Fresh means at least `headline_lapsing_soon_days` left on the
member's `exit_by`, so a fresh starter can out-rank a core arm about to lapse. The headline is the top member;
the Decision Queue shows it with a `+N` count, one name per thesis. Confirmation-only members form the watch
tier, which carries no verdict.

### 2b. The split-night hold

A vendor's first post-split bar can arrive on the new scale while the history is on the old one, so a reverse
split reads as a huge breakout. The hold covers the nights before a split is listed.

- **When.** `domain/split_hold.py`: two consecutive bars step by `STEP_MIN` or more, up or down, with volume
  moving the other way (`BETA_MAX`; both closes above `MIN_CLOSE`), the new-scale bar falls in the last
  `HOLD_SESSIONS` sessions, and no split in force covers the step. A move on a same-direction volume surge is
  never held.
- **What.** That night every price-path trigger (a detector declaring `Detector.price_trigger`, and the
  laggard) reads only the bars before the step (`signals/split_hold.HeldPriceView`). The insider detectors are
  not held: they fire on the filing.
- **Never silent** (invariant 6). Each open hold adds one line to `missing[]`, naming a trigger only when one
  was withheld. The wording, including the vendor name from `domain/split_hold._sources`, is recorded card
  text; changing it re-records cards and needs a rule-log entry (rule `2026-11-massive-split-hold-label`).
- **How it clears.** A split listed in time puts the history on one scale ([FILINGS](FILINGS.md), "Split
  filings"). Otherwise the hold releases after its sessions, and an unexplained step goes to the steward's Admin
  Splits panel and one quiet nightly line (`pipeline.split_jumps`), never a page (invariant 7).

Golden: `tests/signals/test_split_hold.py` (real split nights, and the replay mirror's parity on a held
night); `tests/calls/test_split_hold_missing.py`.

### 3. Grade decision

Each fired entry trigger carries a grade, `flip` (fast, attention-driven, likelier to mean-revert) or `core`
(structural, durable). A set of triggers grades at its highest (`calls/grading.call_grade`). The card carries
three: the conviction grade, the confirmation grade, and the entry grade, the weaker of the two
(`weaker_grade`). When a member is actionable all three are graded on the headline member; otherwise across
every live trigger on the card, so a Warming card still shows a core conviction. The grade is a call-strength
class. It never sets size, instrument or expression, and it never sets liveness: the horizon does, and the two
coincide only for insider buys.

How each detector grades is in [DETECTORS](DETECTORS.md). Which Form 4 rows the insider detectors see (4/A
resolution, held rows, joint reports, internal transfers) is in [FILINGS](FILINGS.md), "Form 4/A: stored
apart, resolved at read". Firing and grade always come from a deterministic parse or a person's entry, never
a model (invariant 3).

### 3c. Thesis catalysts: a user's catalyst on the card

A card's catalyst list is the one place a user enters a catalyst. An entry is a calendar item until the user
turns on "counts toward the call" with a grade and a citation (a link or an SEC accession; recorded, not
verified), through `PUT /theses/{id}/catalysts/{entry_id}/counting`. No module under `llm/` or `agent_tools/`
reaches the store's writer (`tests/repositories/test_thesis_catalyst_writers.py`). Rule
`2026-11-v2-thesis-catalysts-count`.

- **Naming members:** a Key-1 catalyst conviction on each named member, on this card only
  (`signals.thesis_catalyst.ThesisCatalystView`), scored and timed as a platform catalyst. It is an own
  conviction, so the theme broadcast skips that member. It never reaches another card or workspace.
- **Naming none:** the card's theme conviction, flip only ([DETECTORS](DETECTORS.md), "theme_conviction").
- **A member in a silent link, or a meta index card:** nothing; `voting_thesis` has removed the row.

**It counts from** the night it was saved (`edgar_night` of the save instant), never the typed date. Turning it
on, or changing its grade or members, counts from that edit; editing its label, citation or relevant-until date
keeps its night; turning it off stops it from that night. Every history view reads the version saved by the
night it shows. The one named exception: the theme conviction seeded before V2 on the "Small-scale nuclear
(system)" card keeps its stated date, 2026-01-15 (migration 0213).

**It stays live** to its relevant-until date (capped by `catalyst_max_horizon_days` when that is set), else
`catalyst_default_horizon_days`; a whole-card entry uses `theme_conviction_default_horizon_days`, uncapped.
**Against a platform catalyst on the same member** the stronger counts (core over flip, then the later night; a
full tie goes to the platform), and the other rides as a second source marked also live, not counted
(`detail.also_live`). **Provenance:** source `thesis_catalyst`, the citation as ref, `{entry_id, counts_from}`
in the detail; the API derives `origin` (`operator` or `platform`) on the response. Tests:
`tests/pipeline/test_thesis_catalyst_rule.py`, `tests/signals/test_thesis_catalyst.py`.

### 4. Verdict mapping

`assemble_call._verdict`. A conviction is holdable when any live conviction trigger in scope has
`alpha_liveness_days` at or above `conviction_hold_threshold_days` (a legacy name: it separates short from long
signal windows and is never a sell rule).

| State | Condition | Verdict |
|---|---|---|
| Incubating | | `watching` |
| Warming | flip conviction, none holdable | `flip_only` |
| Warming | anything else: a holdable flip, a core conviction of any window, or no conviction | `not_yet` |
| Armed | flip conviction, none holdable | `flip_only` |
| Armed | flip conviction, holdable | `starter_entry` |
| Armed | core conviction, entry grade flip | `starter_entry` |
| Armed | core conviction, entry grade core | `core_entry` |
| Managing | | `managing` |

The two `starter_entry` rows mirror each other: a provisional conviction with a strong confirmation, and a core
conviction with a momentum-only confirmation. Both mean the setup is provisional; what could firm it lives in
the expression and the counter-case. A verdict is readiness, never a position size.

**Per-member Managing.** When the open position carries a `security_id` (a take logged on a name), the held
member's `MemberCall` is computed like any member's and then takes `verdict = managing`, `confidence = None` and
no entry-window flag; it leads `armed_members` and leaves the watch tier. The risk veto cannot un-hold it. A
thesis-level take attributes no member: attribution is exact or absent.

### 5. Expression context and external handoff

The card's `expression` is advisory research context: why the setup is fast or structural and what would
strengthen it, never a size, an option tenor or an order. A fund sleeve may be surfaced with its internals;
Alpha Deck never allocates to it. The user's own systems govern the trade (invariant 5;
`tests/calls/test_expression_boundary.py`).

### 6. Signal-validity horizon (`exit_by`) and clocks

Both clocks are anchored to each trigger's fire date, so they do not slide as the as-of advances (`_clock`):

- `exit_by`, the hold clock, is the latest window end over the live conviction triggers in scope: the headline
  member's when some member is actionable, else every live conviction trigger on the card. It is the
  signal-validity horizon and the scoring yardstick after a fill, never a sell date.
- `arm_until`, the entry window, is the same over the live confirmation triggers in scope.
- `catalyst_surface` is the card's dated catalysts on or before `exit_by`; an undated one is shown, not
  surfaced.

**The next-session rule** (rule `2026-11-entry-window-next-session`). A trigger is live on night N only while
the first session after N is on or before its window end (`signals.common.entry_signal_is_live`): the call is
recorded after the close, so the user can act no earlier than the next session. One function serves both keys
and every risk's freshness; no window is stretched.

**Window ends** are computed once (`signals.common.window_end`), in calendar days. The bar-driven families
(the 8-day breakout with the flip breakdown, the 52-week breakout, the laggard) carry dormant dials
(`<family>_alpha_liveness_unit`, `_sessions`) that would count in sessions instead.

**The session calendar** is Monday to Friday minus the NYSE's full-day closures
(`domain.trading_days.is_scheduled_day`; rule `2026-11-market-holiday-calendar`). The closure list is kept by
hand through 2028, and `tests/domain/test_trading_days.py` fails 180 days before it runs out.

**Two flags on an armed member**, display only: state, verdict, grade, ranking and setup strength never read
them (invariant 4).

| Flag | Reads | On when |
|---|---|---|
| `lapsing` ("hold clock closing") | `exit_by` | fewer than `headline_lapsing_soon_days` calendar days left |
| `entry_window_closing` | `arm_until` | at most `entry_window_closing_sessions` sessions left |

`lapsing` keeps its wire name because `MemberCall` is a strict schema (`extra="forbid"`). The Board's day
countdowns count calendar days ([BOARD](BOARD.md)).

**The trigger date.** Each trigger row shows `event_date`, its fire date, the same date the clocks key on. Each
detector's fire date is in [DETECTORS](DETECTORS.md).

### 7. Setup strength

`confidence` is the Armed card's setup-strength bar: a relative reading of the trigger composition, not a
probability, a win rate or a sizing input (`calls/confidence.py`). The card computes it only when Armed, over
the headline member's live triggers and risks; each armed member carries its own.

- A noisy-OR over the entry triggers, one score per kind (its strongest), so two reads of one move count once.
- Capped at `single_detector_cap` when one kind fired, and at `starter_confidence_cap` for any starter (entry
  grade flip, whichever key is weak), so a provisional call never reads loud; the caps compose by minimum.
- Minus `risk_penalty_per_signal` times the strongest score of each active risk kind.

It stays experimental until matured forward outcomes support calibration; the Scoreboard's n ≥ 5 gate is a
display safeguard, not evidence ([SCOREBOARD](SCOREBOARD.md), "Setup strength and the n >= 5 gate").

### 8. Counter-case

`counter_case` is the deterministic template (`calls/counter_case.deterministic_counter_case`): the caveats (a
momentum-only confirmation), the active risks, the kill criteria and `missing[]`. It never alters state,
verdict, grade or triggers, and no model supplies any part of it. The `counter_case_fn` hook for model prose is
not wired. The call card shows a count of the risk and still-missing rows instead of the text, which restates
them ([BOARD](BOARD.md)); the field stays on the recorded card.

### 9. Worked example (HIMS)

`tests/calls/test_hims_armed_call.py` runs the real fixtures: director David Wells' $1.17M open-market buy,
traded 2026-05-26, with its Form 4 acceptance stated by the test as 17:00 ET on 05-28 (the committed XML carries
none), and the HIMS price bars.

- **2026-05-28.** The insider cluster fires core with 180 days of liveness, dated the 05-28 public night; its
  label keeps "(traded 2026-05-26)". No breakout. The card is Warming, verdict `not_yet`.
- **2026-06-01.** A breakout prints on weak volume: a flip confirmation with a 10-day window. Both keys are
  live on HIMS: Armed, conviction grade core, entry grade flip, verdict `starter_entry`. `exit_by` is
  2026-11-24 (05-28 plus 180 days); `arm_until` is 2026-06-11. Setup strength is capped at
  `starter_confidence_cap`, and the counter-case leads with the momentum-only caveat.
- **06-02 and 06-03.** The 06-01 breakout is still live, so the card stays Armed; by the next-session rule it
  lapses to Warming on the night of 06-11 unless a fresh breakout re-arms it.

The card's actions are act, pass, override and exit, each a logged decision ([BOARD](BOARD.md), "Decision
capture").

## Changing a rule or a dial

The call logic changes only with the operator's sign-off. A change that can move a recorded card (a rule, a
dial value, recorded wording) appends one `RuleEntry` to `domain/rule_log.py` in the same PR, or the editions
cut reads "unexplained change" and pages ([EDITIONS](EDITIONS.md), "The automatic cut"). Dial values live in
`domain/config.py`; they are priors, and none has been fitted to outcomes.

A hypothesis dial lands at today's value, byte-identical on every golden (`tests/calls/test_hypothesis_dials.py`),
and moves only when a backtest pass registered before launch meets its own decision rule ([BACKTEST](BACKTEST.md))
and the operator signs off. Adding or reshaping a `CallConfig` field moves `config_hash` but never re-records a
card: the hash rides beside the card, not in it.

Why `insider_core_alpha_liveness_days` is not shortened: it is also the co-location window, so shortening it
stops a conviction arming on a later breakout (at 90 days, UNH's cluster lapsed before its breakout). The
give-back after a peak is a setup-strength question, not this dial's.

## Contracts

- `CallCard` and `MemberCall` are strict schemas recorded in `calls` and frozen in editions: a renamed field
  breaks every recorded card; a new field or changed wording re-records cards (`record_if_changed`).
- `assemble_call` is pure (invariant 13; `tests/calls/test_assembler_golden.py`), and every engine assembles
  through `assemble_from_pit`.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Read a call (`GET /theses/{id}/call?asof=`) | Anyone signed in, own workspace's cards | `require_user`; `get_thesis_or_404`; `tests/app/test_workspace_isolation.py` |
| Counting switch (`PUT /theses/{id}/catalysts/{entry_id}/counting`) | Anyone signed in, own card | the same; `tests/app/test_thesis_catalyst_counting.py` |
| Meta index switch (`PUT /theses/{id}/meta-index`) | Anyone signed in, own card | the same; `tests/app/test_meta_index_api.py` |
| Take, close, pass or void (`POST /theses/{id}/decisions`) | Anyone signed in, own card | the same; `tests/app/test_decisions_api.py` |
| Record the nightly call | Machine (nightly pass) | `pipeline/daily.py` |
| Change a rule or a dial | Operator, by PR | `domain/rule_log.py`; the editions cut |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| Every dial named here | `domain/config.py` (`CallConfig`) | No (config, not environment) |

## Known limits

- **Known defect: a company in several links counts once per link.** Its basket rows share one `security_id`,
  and the detector loop and the theme broadcast walk rows, so its events enter the stream once per link. Today
  only the lists repeat (triggers, risks, `missing[]`, the counter-case); state, grades, clocks and ranking take
  a maximum or a set. `warming_min_entry_triggers` counts raw events, so raising it above 1 would let one
  trigger in two links warm a card. The fix (dedupe by `security_id` at the seam) re-records cards.
- A held name moved into a silent link stays Managing, but its own triggers and risks no longer reach the card.
  Whether to exempt it is open.
- The reads around the call see the whole roster, silent names included: the ingest holds, the Scoreboard's
  roster count, the backtest's basket benchmark and name nulls, the data flags.
- An unlisted real split, once its hold releases, reads across the step until it is listed (section 2b).
- `revenue_accel_key` cannot be flipped before the record-versus-config question in section 1 is settled.
- Setup strength does not fade with the conviction's age.

## Related

[DETECTORS](DETECTORS.md), [FILINGS](FILINGS.md), [INVARIANTS](INVARIANTS.md) (1, 3, 4, 5, 6, 13, 14),
[BOARD](BOARD.md), [SCOREBOARD](SCOREBOARD.md), [EDITIONS](EDITIONS.md), [BACKTEST](BACKTEST.md),
[REPLAY](REPLAY.md).
