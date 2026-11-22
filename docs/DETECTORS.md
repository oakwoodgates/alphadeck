# Detectors

Every signal detector: what fires it, what it reads, its grade, its window and fire date, the dials that tune
it, its module and the tests that pin it. Detectors produce the `SignalEvent`s the call is assembled from;
[CALL_LOGIC](CALL_LOGIC.md) owns what the call does with them (the keys, the states, the veto, the clocks).
What each SEC filing stores is [FILINGS](FILINGS.md). Read this before you change a detector or one of its
dials.

## Code

| Piece | Module |
|---|---|
| Registration, in trigger-list order | `signals/__init__.py`; `signals/registry.py` |
| The detector contract and the point-in-time view | `signals/base.py` (`Detector`, `SignalPointInTimeData`) |
| Shared helpers (liveness, windows, event builder) | `signals/common.py` |
| Read horizons and the Form 4 code set | `signals/horizons.py` |
| Thesis-level detectors | `signals/theme_conviction.py`, `signals/laggard.py`, run from `pipeline/core.py` |
| The replay mirror's view | `replay/pit.py` |
| Dials and switches | `domain/config.py` (`CallConfig`) |

## How it works

Every registered detector runs on every voting member of a card, on every call read and every nightly, through
`pipeline.core.assemble_from_pit`. Each is pure over the point-in-time view, so the live call and the replay
mirror get the same events. A detector emits at most one event per member per read. Its fire date (`asof`) is
when the market could first see the evidence (invariant 1); its window (`alpha_liveness_days`) starts there. A
risk carries no window: its detector stops emitting it once stale. Firing and grade always come from a
deterministic parse or a person's entry, never a model (invariant 3).

The theme conviction and the laggard run after the per-member loop because they read the members' events.

### Catalog

| Detector | Role and key | Kind | Grade | Fire date |
|---|---|---|---|---|
| `insider_conviction` | entry, Key 1 | `insider` | core or flip | night the cluster's latest Form 4 was public |
| `catalyst_conviction` | entry, Key 1 | `catalyst` | from the fact | the fact's `valid_from` |
| `volume_breakout` | entry, Key 2 | `technical_breakout` | core or flip | the breakout bar |
| `dilution_clock` | risk | `dilution_risk` | none | latest note's issue date |
| `revenue_acceleration` | entry, Key 1 | `catalyst` (type `earnings`) | `revenue_accel_grade` | first public filing of the number |
| `breakout_52w` | entry, Key 2 | `technical_breakout` | core | the breakout bar |
| `breakdown_core`, `breakdown_flip` | risk, de-arm | `breakdown` | de-arms core or flip | the cross bar; the latest bar |
| `insider_sell` | risk | `insider_sell` | none | the anchor sale's trade date |
| `corporate_catalyst` | entry, Key 1 | `catalyst` | from the item map | the 8-K's acceptance date |
| `corporate_risk` | risk | `corporate_risk` | none | the 8-K's acceptance date |
| `share_creep` | risk | `dilution_risk` | none | first public filing of the newest count |
| `activist_stake` | entry, Key 1 | `activist_stake` | core | the 13D's acceptance date |
| `theme_conviction` (thesis-level) | entry, Key 1 fallback | `theme_conviction` | flip | the entry's counting night |
| `laggard` (thesis-level) | entry, Key 2 | `laggard` | flip | the leader's breakout bar |

An acceptance date is the market-time date of SEC's trusted acceptance, else the filing date (rule
`2026-11-filings-dated-by-acceptance`; [FILINGS](FILINGS.md)). The card's "(filed ...)" text keeps SEC's filing
date. The catalyst kind is in Key 1 through `conviction_kinds`, except that the dormant `revenue_accel_key` can
move `revenue_acceleration` alone ([CALL_LOGIC](CALL_LOGIC.md), section 1).

### `insider_conviction`

**Fires** on a cluster of code-P open-market buys by insiders of the member. **Reads** `fact_insider_txn`, after
the shared read resolves Form 4/As, held rows, joint reports and internal transfers ([FILINGS](FILINGS.md),
"Form 4/A: stored apart, resolved at read" and "Joint reports and internal transfers"), plus the day's price
bars. **Screens** (each row stays stored; only the call skips it): a buy priced below the day's low by
`insider_offmarket_below_low_frac` (an offer-price subscription; a row with no bar is kept, a filing-night row
skips the test), a row above `insider_max_plausible_txn_usd`, an issuer filing on its own stock (by CIK, else by
name), a row whose issuer is another company, and an internal-transfer buy, named in the label. A planned
(10b5-1) buy counts at `insider_10b5_1_buy_weight`, a dormant dial; only an explicit true flag is planned.

**The walk.** Every kept-buy date is a candidate anchor; its cluster is the buys in
`[anchor - insider_cluster_window_days, anchor]`. A candidate qualifies when its total clears `insider_min_usd`
and it is live for its own grade; the strongest fires, core first, then the most recent anchor. Pinning the
newest buy instead let a small late buy shadow a live core cluster.

**Grade** (`_cluster_at`): core when a senior insider (`insider_senior_role_keywords`) is in the cluster and either
`insider_core_min_distinct` distinct buyers clear `insider_core_min_usd` or the cluster total clears
`insider_strong_single_usd`; else flip. **Window**: grade-coupled, `insider_core_alpha_liveness_days` or
`insider_flip_alpha_liveness_days`. **Fire date** (`_public_night`, rule `2026-11-insider-clock-from-filing`): the
latest `edgar_night` of the chosen cluster's filing acceptances; a buy with no stored acceptance counts from its
trade date; `recorded_at` is never read; a 4/A-corrected buy counts from the 4/A. The label keeps the trade dates.
A trade older than the longest graded window is never a candidate, so `horizons()` stays exact.

Tests: `tests/signals/test_insider_conviction.py`, `test_insider_internal_transfer_screens.py`,
`test_insider_holds_signals.py`, `test_insider_split_scale.py`, `test_insider_signal_codes.py`.

### `catalyst_conviction`

**Fires** on the strongest live catalyst fact on the member: core first, then the later night, then a platform
fact over a card's own counting entry on a full tie. The strongest live fact of the other origin rides as a
second source marked also live, not counted ([CALL_LOGIC](CALL_LOGIC.md), section 3c). **Reads**
`fact_catalyst` (written by the DOE feed and the demo seed) and the card's counting entries naming the member.
**Grade** comes from the fact. For DOE awards, `ingest/doe/feed._derive_grade` applies customer versus sponsor:
a loan or loan guarantee is core, a contract at or above `doe_core_min_obligation_usd` is core, and a grant,
cooperative agreement, OTA or smaller contract is flip whatever its size. Size moves the score within a grade,
never the grade. **Window** (`liveness`): the published term (`horizon_end - valid_from`, at least a day), capped
by `catalyst_max_horizon_days` when set; with no term, `catalyst_default_horizon_days`. A term is a fact about the
agreement and the cap a policy about the edge, so each has its own dial. **Fire date**: `valid_from`, for a DOE
award its record date (`feed._event_date`, rule `2026-11-catalysts-dated-by-record-date`): award DENE0009589 was
executed in February 2026, announced 2026-03-17 and recorded 2026-05-18. For an award nobody announced, the
record may precede public visibility by a few days. When the two dates differ the label names both.

Arming still needs a co-located confirmation. Tests: `tests/signals/test_catalyst_conviction.py`,
`tests/ingest/test_doe_feed.py`.

### `volume_breakout`

**Fires** on the most recent bar still inside its window whose close is a new `breakout_base_window`-day closing
high and up at least `breakout_min_return` over `breakout_return_days` sessions, dated at that bar, so it stays
on through a consolidation and re-anchors on a fresher breakout. **Reads** `fact_price_eod` for
`breakout_lookback_days`. **Grade**: core when volume is at least `breakout_volume_mult` times the base average,
else flip; a volume-backed breakout that closes weak (`breakout_close_strength_min`) grades down to flip under
`breakout_weak_close_grade_down`. A weak close and a failed next-day hold also cut the score
(`breakout_weak_close_penalty`, `breakout_failed_hold_penalty`). **Window**: `breakout_alpha_liveness_days`. A price-path trigger, held on a
split night. Tests: `tests/signals/test_volume_breakout.py`.

### `dilution_clock`

**Fires** while the member has outstanding convertible notes (matured notes drop out). **Reads** `fact_dilution`,
the convert terms parsed from the 8-K, and the shares-outstanding basis. **Score** scales with the gross
overhang as a share of shares outstanding and saturates at `dilution_severe_score` from
`dilution_overhang_severe_pct` up; that score sits above `risk_block_severity`, so a severe overhang withholds
the member. Tests:
`tests/signals/test_dilution_clock.py`.

### `revenue_acceleration`

**Fires** when year-over-year revenue growth re-accelerates (`signals/revenue_acceleration.py` defines it): the
quarter's acceleration turns positive after a non-positive one, clears `revenue_accel_min_accel`, and growth
clears `revenue_accel_min_yoy`. The cited quarter must have ended no more than `revenue_accel_max_quarter_age_days`
before its number first became knowable (`_fresh_at_knowability`), so a restated old comparative never fires.
**Reads** the as-of revenue series from `fact_fundamentals`: the company's current revenue tag, never spliced
across tags, with set-aside versions skipped. **Fire date**: the first public filing of the cited number
(`knowable_from`); an unchanged number re-reported later keeps its first date (invariant 1). **Grade**:
`revenue_accel_grade`, a dormant dial at core. **Window**: `revenue_accel_alpha_liveness_days`. Tests:
`tests/signals/test_revenue_acceleration.py`.

### `breakout_52w`

**Fires** on the most recent live bar whose close is a fresh 52-week closing high (`breakout_52w_base_bars`) with
volume at least `breakout_52w_volume_mult` times the average over `breakout_52w_vol_base_bars`; declines below
`breakout_52w_min_base_bars`. **Grade**: core, fixed. It shares the `technical_breakout` kind, and the
confirmation grade is the strongest co-firing one, so a flip 8-day breakout never drags it down. **Window**:
`breakout_52w_alpha_liveness_days`. A price-path trigger. Tests: `tests/signals/test_breakout_52w.py`.

### `breakdown_core` and `breakdown_flip`

Two risks that carry a `dearm_grade`, scored `breakdown_severity` (set above `risk_block_severity`, so raising the
veto dial above it turns the de-arm off). **Core** (`core_score`): the close is below the
`breakdown_core_sma_window`-day average and got there by a downward cross, so a chronic downtrend never fires;
dated at the cross bar; declines with fewer than `breakdown_core_min_bars` bars. **Flip** (`flip_score`): a live
8-day breakout has been given back (the close is under the base it cleared); dated at the latest bar. Whether
they de-arm and which: `breakdown_dearm_enabled`, `breakdown_dearm_scope` ([CALL_LOGIC](CALL_LOGIC.md), section
2). Price-path triggers. Tests: `tests/signals/test_breakdown.py`, `tests/calls/test_breakdown_dearm.py`,
`tests/calls/test_dearm_copy.py`.

### `insider_sell`

**Fires** on a clustered discretionary code-S sell episode: at least `insider_sell_min_distinct` insiders (a senior
one when `insider_sell_require_senior`) selling at least `insider_sell_min_usd` inside
`insider_sell_cluster_window_days`. Candidate anchors are walked newest first; the first that is fresh
(`insider_sell_liveness_days`) and clears every floor fires, so a lone later sale cannot silence a live cluster.
**Screens**, named in the label: planned (10b5-1) sales (an unknown flag is kept), issuer self-filings,
implausible rows, internal-transfer sales, and sales below the day's low (a discounted secondary, not
open-market supply; a filing-night row skips the test). **Score** is capped at `insider_sell_max_score`, below
`risk_block_severity`: sales are weak predictors, so it feeds the counter-case and setup strength and never
withholds. Switch `insider_sell_enabled`. Tests: `tests/signals/test_insider_sell.py`.

### `corporate_catalyst` and `corporate_risk`

Both read `fact_corporate_event` ([FILINGS](FILINGS.md), "8-K items") through `signals/corporate_events.event_facts`,
which skips succession reports: their items describe the reorganization itself (`ingest/edgar/succession.py`).
`CallConfig.corporate_event_items` routes each item to the trigger or the risk side with its grade, type, score
and window, applied on read. The SEC's item code is the classification (invariant 3).

- **`corporate_catalyst`** fires the strongest live trigger item, core first, then the most recent. Today's map
  has one trigger item, the officer or director change, graded flip. Material agreements fire nothing: most are
  financings, and the role, not the grade, decides whether a card arms. Switch `corporate_catalyst_enabled`.
- **`corporate_risk`** fires once per member at the most severe live risk item; the others are named in the label
  and the provenance, so co-live moderate items cost one setup-strength cut. An item scored at or above
  `risk_block_severity` withholds the member. Unregistered equity sales route here as dilution. Switch
  `corporate_risk_enabled`.

An 8-K/A is its own row; the reads take existence and latest, never a sum. Tests:
`tests/signals/test_corporate_catalyst.py`, `test_corporate_risk.py`, `test_corporate_succession.py`.

### `share_creep`

**Fires** on a sustained quarterly share-count drip, the signature of an at-the-market program: the last
`share_creep_window_quarters` quarter-over-quarter steps all strictly positive and the cumulative rise at least
`share_creep_cum_min_pct`. **Reads** the XBRL share series in `fact_fundamentals` through a fixed ladder
(balance sheet, then cover, then issued), judging only the first concept with a fresh consecutive chain. A single
step at or above `share_creep_pair_ceiling_pct` is a split or scale artifact and declines. **Fire date**: the
newest count's first public filing; fresh for `share_creep_liveness_days`. **Score**: `share_creep_score`, below
the veto. It shares the `dilution_risk` kind with `dilution_clock` (realized versus potential dilution). Switch
`share_creep_enabled`. Tests: `tests/signals/test_share_creep.py`.

### `activist_stake`

**Fires** on the most recent live 13D-family original about the member: an outside party crossed 5% with intent.
**Reads** `fact_activist_stake` ([FILINGS](FILINGS.md), "SC 13D/G"), the member's CIK, and the member's Form 4
rows for roles only (`Detector.reads_insider_roles`). **Grade**: core, fixed. **Window**:
`activist_13d_liveness_days`; score `activist_13d_score`. **Fire date**: the original's `valid_from`, its SEC
acceptance date.

- The 13G family fires nothing: passive crossings are mostly index-fund paperwork.
- Amendments never anchor a fire (a 13D/A reads the same for an increase and an exit); they ride the
  provenance. The fired filer's own amendment reporting under 5% ends the fire (`activist_exit_terminates`).
- **Misattribution screen** (`_is_misattributed`): a filer equal to the subject, or a stated percent under 5,
  never anchors. A missing value is kept.
- **Holder screen** (`activist_affiliate_screen`): a filing never anchors when any filer row reports at least
  `activist_control_pct`, or a filer is an officer or director of the member on a Form 4 within
  `activist_insider_lookback_days` before the 13D (`activist_insider_role_keywords`; a 10% owner alone does not
  screen). The Form 4 counts only once public. A screened filing stays on the tape and is named in the label.
- **The 13G-to-13D switch** (`_switch_from_13g`): a same-filer 13G at least `activist_switch_min_gap_days` earlier
  enriches the label and provenance only.

Switch `activist_stake_enabled`. Tests: `tests/signals/test_activist_stake.py`,
`test_activist_holder_screen_pit.py`.

### `theme_conviction`

A card's whole-card counting entry (a catalyst entry naming no member; [CALL_LOGIC](CALL_LOGIC.md), section 3c),
read by `detect_fact` and broadcast by `broadcast` onto eligible members as a Key-1 fallback. The rules:

1. Its source is the card's counting entry. The call never reads `fact_theme_conviction`; only `replay/pit.py`
   converts an old mirror's rows the way migration 0213 did.
2. Always flip, so the member's entry grade is flip and setup strength is capped: belief never mints a core.
3. Window: the entry's relevant-until date, else `theme_conviction_default_horizon_days`; past it the conviction
   lapses unless the user enters it again.
4. A member qualifies only with its own live core confirmation; a momentum-only breakout never qualifies, and the
   broadcast never reaches outside the voting basket.
5. A member with its own live conviction (`own_conviction_kinds`) is skipped: own wins.
6. Theme-armed members rank below own-conviction members within a band and grade and carry `theme_armed`.
7. A member whose own conviction lapsed falls back to a theme-armed starter while rules 3 and 4 hold.

Tests: `tests/signals/test_theme_conviction.py`, `tests/pipeline/test_theme_arm.py`.

### `laggard`

**Fires** while a member has a live `volume_breakout` (the leader): each other voting member whose return over
`laggard_return_days` trails the basket median by at least `laggard_lag_pts` and whose close is at or above its
`laggard_trend_sma_window`-day average gets a flip confirmation. A leader is never its own laggard. **Window**:
`laggard_alpha_liveness_days` from the latest leader's breakout bar. Read over `laggard_lookback_days`; a
price-path trigger. It confirms only; arming still needs the member's own conviction. Tests:
`tests/signals/test_laggard.py`.

## Contracts

- Registration order is the order of a card's trigger lists; `tests/signals/test_registry.py` pins the list.
- Every detector declares its read horizons (`horizons()`); one without a declaration fails
  `tests/signals/test_horizons.py`. A reader of `fact_insider_txn` declares `txn_codes` or
  `reads_insider_roles`, and the Form 4/A review derives its code set from them
  (`signals.horizons.insider_signal_codes`; `tests/signals/test_insider_signal_codes.py`).
- A price-path detector declares `Detector.price_trigger`; that is what the split-night hold withholds.
- A switch gates `detect` only; the pure `score` stays testable with the switch off.
- The replay mirror reads through the same detectors over `replay/pit.py`; the replay parity tests hold the two
  engines alike.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Run the detectors | Machine (every call read and the nightly) | `pipeline.core.assemble_from_pit` |
| Enter a counting catalyst | Anyone signed in, own card | [CALL_LOGIC](CALL_LOGIC.md), "Who can do what" |
| Decide a held Form 4 row or a Form 4/A | Steward | [FILINGS](FILINGS.md), "Who can do what" |
| Change a dial or a switch | Operator, by PR with a rule-log entry | `domain/rule_log.py`; the editions cut |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| Every dial and switch named here | `domain/config.py` (`CallConfig`) | No |

## Known limits

- The insider clock reads the acceptance on the version the read picks; a newest version without one, where an
  older version has it, counts from the trade date.
- A planned (10b5-1) buy counts fully while `insider_10b5_1_buy_weight` stays at its default.
- Large fund or affiliate code-P blocks are counted; no structured field separates them from smart-money buys.
- The below-the-day's-low screen keeps a buy on a name that reverse-split between the buy and the as-of.
- The 13D tape stores a filing's lead reporting person only, so the holder screen cannot see a joint filing's
  majority member when it is not the lead.
- An increase reported on a 13D/A never re-fires `activist_stake`.
- `share_creep` cannot see foreign filers, fresh listings or dual-class filers with no quarterly series.
- The DOE feed does not query the loans award group, so no loan catalyst reaches the tape.
- `corporate_catalyst`'s 8-K catalysts do not feed the Workbench catalyst-density meter, which reads
  `fact_catalyst`.

## Related

[CALL_LOGIC](CALL_LOGIC.md), [FILINGS](FILINGS.md), [DATA_SOURCES](DATA_SOURCES.md) ("USASpending (DOE awards)"),
[INVARIANTS](INVARIANTS.md) (1, 3, 9), [REPLAY](REPLAY.md).
