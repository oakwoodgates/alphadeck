# Display signals — read-only indicators, structurally off the call path

**What this is.** The Workbench/Cockpit surfaces are fed by two very different kinds of computation.
The **detectors** (`backend/signals/`, PR #176's registry) are the call path: they emit
`SignalEvent`s that arm, veto, and grade. **Display signals** (`backend/signals/display/`) are the
other thing the operator kept wanting: quiet per-name tape context — *where does this name sit vs
its 50/200-day SMA, and when did it flip?* — computed deterministically from facts already
ingested, shown beside the call, and **never an input to it**. TA-as-*prediction* stays parked
(`ROADMAP.md` non-goals); this is deterministic arithmetic over the stored tape, display-only.

## The bound is structural, not policy

A display signal **cannot** become a trigger, and the guarantee is import-shaped (the explain-seam
idiom), pinned by `tests/signals/display/test_registry.py::test_display_package_cannot_touch_the_call_path`:

- `DisplaySignal` is **not** a `SignalEvent`: no `role`, no `fired`, no `grade`, no `score`, no
  `alpha_liveness_days`. Nothing downstream can mistake one for something that fired.
- The package imports **none of**: `domain.signal`, `domain.config` (`CallConfig`), `signals.base`,
  `signals.registry`, `signals.common`, `calls`, `pipeline`, `repositories`, `db`, `psycopg`.
  A member is a pure function of the point-in-time view it is handed; it cannot open a connection,
  read a call dial, or persist anything.
- Nothing in `pipeline/` or `calls/` consumes the display registry — `assemble_from_pit` physically
  cannot see a display output.
- It has its own narrow `DisplayPointInTimeData` Protocol (only `price_history` + `insider_txns` +
  `fund_shares`); the detectors' `SignalPointInTimeData` stays exactly as #176 left it ("no future
  plugin surface") — `fund_shares` was widened HERE, deliberately not there, so no detector can
  quietly grow a dependency on the fund-flow basis.

**Why display output is never recorded (the trap that shaped this design).** The daily cron's
`record_if_changed` (`repositories/calls_repo.py`) canonicalizes `model_dump()` of the **entire**
domain `CallCard`. Any day-varying display field on the card (an SMA distance moves whenever price
moves) would make `_canonical` differ every night → one appended `calls` row per day → the cron's
idempotency gone and the call-of-record / Scoreboard polluted. So indicators ride their own
**compute-on-read** endpoint and are never persisted, never on the cron.

## The wire

`GET /theses/{thesis_id}/display-signals?asof=` → `DisplaySignalsResponse` — per resolved basket
member (deduped, basket order; unresolved members omitted — the Workbench-scored rule), the list of
each registered member's `DisplaySignal`:

- `kind` (= the registered member name) · `label`
- `headline` (optional) — the member's **one-glance state chip**, rendered at the top of its block:
  `{key, label, glyph: up|down|turn_up|turn_down|flat, detail}`. `key` is a STABLE machine state a
  future Board column / basket cell can consume; `label` is the literal statement, always derived
  from the member's params (never a hardcoded window or MA type); the FE tints the **glyph only**
  (rising-family positive, falling-family negative — the chip stays mono, #7). A headline states
  the tape, never a forecast (#4). Any member may send one (a quiet tape, a net-selling flow, …).
- `metrics[]` — `{key, label, value: float|null, unit: pct|usd|price|count|ratio, note}`.
  A `null` value is an **honest gap** and the note says why (`"n/a: 140/200 bars"`) — never a fake
  number (#6/#7).
- `events[]` — `{key, label, date, direction}`: dated flips/crosses the tape actually printed,
  stamped with the **bar date**, never the query asof.
- `basis` — show-the-work (#6): `source` (the fact table), `params` (every dial the member used),
  `bars_used`, `window_start/window_end` (the exact tape the reading stands on), and a staleness
  `note` when the last bar lags the asof (the delisted/halted tell).

The payload is **generic on purpose**: adding a member changes zero wire schema (no
`openapi.json` / `types.gen.ts` diff, no FE change) and one panel section renders every member
uniformly. Because every read is the bitemporal as-of, an old `asof` time-travels the tape for free
(#1). A member with nothing computable returns `signals: []` — an honest empty, never a dropped row.

## Member catalog

| member (kind) | reads | metrics | events | params |
|---|---|---|---|---|
| `sma_position` | `fact_price_eod` | close, ma_fast, ma_slow, pct_vs_fast, pct_vs_slow | cross_sma50, cross_sma200, golden_cross/death_cross | fast=50, slow=200, lookback_days=600, slope_bars=5 |
| `trailing_returns` | `fact_price_eod` | ret_1d, ret_7d, ret_30d, ret_90d, ret_1y (% return over N trading BARS back; 1Y = 252) | — | windows_trading_days=[1,7,30,90,252], lookback_days=420 |
| `range_52w` | `fact_price_eod` | pct_off_52w_high, pct_above_52w_low, high_52w, low_52w (print dates ride the notes) | — | lookback_days=380 |
| `volume_regime` | `fact_price_eod` | vol_ratio (20d ÷ prior 60d), adv_usd_20d | — | recent_bars=20, base_bars=60, lookback_days=150 |
| `rvol` | `fact_price_eod` | rvol (as-of vol ÷ mean of the prior **8** bars — call-matched), rvol20 (÷ the prior **20** bars — trader convention, call-decoupled) | — | baseline_bars=8, loud_mult=1.5, baseline_bars_20=20, loud_mult_20=1.5, lookback_days=55 |
| `insider_flow_90d` | `fact_insider_txn` (+ `fact_price_eod` day-lows) | buy/sell counts, distinct_buyers, **buy_count_30d / distinct_buyers_30d** (a 30d sub-window off the SAME screened buys), buy/sell/net USD (open-market code-P buys, code-S sells) | last_buy, last_sell | window_days=90, window_days_short=30, offmarket_below_low_frac=0.10, max_plausible_txn_usd=2e9 |
| `etf_flow` | `fact_fund_shares` (+ `fact_price_eod` closes) | flow_1w_usd, flow_1w_pct_of_shares, flow_1m_usd, flow_1m_pct_of_shares | — | window_1w_days=7, window_1m_days=30 |

**Member epistemics worth naming.** `insider_flow_90d` returns `None` for a name with **nothing
ingested** (nothing to say) but a **quiet zero** for an ingested name with no window activity (zero
is information); its basis note carries the "zero ingested ≠ proven-zero filings" caveat. Its
**headline** (`net_buying` / `net_selling` / `net_flat` — "net selling $3.4M (90d)", counts in the
detail) renders **only when the window has actual flow**: a quiet name adds no "no flow" line to
the panel's top strip (the strip marks the exception, #7); the section's zero metrics still carry
the quiet read.

**The 30d sub-window (the `Ins 30d` / `Ins 90d` basket columns).** Alongside the 90d metrics the
member emits `buy_count_30d` / `distinct_buyers_30d` — the subset of the **already-screened**
open-market buys whose `valid_from` sits in the tighter trailing window `(asof-30, asof]`. It is a
**pure filter on the same rows** (no second screen, no lookahead — the 90d rows are already
`<= asof`), and its boundary mirrors the 90d convention (day 29 in, day 30 out, exactly as the 90d
window includes day 89 and excludes day 90). The Cockpit basket surfaces both windows as the
**`Ins 30d` / `Ins 90d`** columns (short before long, matching the return ladder), each rendering
`{open-market buys}/{distinct buyers}` — a muted "—" on zero buys (the common case, #7) and a
leader-blue **cluster** accent when `distinct_buyers ≥ 2` (breadth is the stronger insider tell; a
lone buyer shows un-accented). Both windows ride the generic `metrics[]` list — zero wire/OpenAPI
change.

**The open-market screen (agreeing with the call).** Because the block is LABELED "open-market", its
code-P buys are screened the **same way `backend/signals/insider_conviction.py` screens the call** —
SEC code `P` is "open market **or private** purchase", so an offer-price primary-market subscription
(an IPO allocation / PIPE / placement) files as code P yet never traded on the open market. A buy
priced `offmarket_below_low_frac` (10%) or more **below the security's own EOD low that day** is such
a subscription, and a row above `max_plausible_txn_usd` ($2B) is bad source data; both drop out of the
buy total, and the **set-aside subscription $ is named in the basis note** (never silently dropped, #9
/ show-the-work #6). This is what stops the NamePanel from reading "net buying ~$434M" next to the
call's honest "~$473K FLIP" (PBLS: RA Capital's $394M IPO subscription at the $20 offer vs a
$29.65–34.47 tape). **Recall-safe:** no price bar for the day → **kept** (a genuine open-market print
sits inside `[low, high]`, so this cannot exclude a real one — save a name that reverse-split between
the buy and asof, a documented limitation shared with the call). The two dials are **display module
constants**, deliberately **not `CallConfig`** — the display seam cannot import the call's dial set
(`base.py` + the `test_registry.py` pin) — so they intentionally *mirror* the call's and are re-tuned
by hand if it recalibrates. **Only buys are screened**: the offer-price conflation is a buy-side
phenomenon; sells are the raw code-S tape.

**The buy-character taxonomy (Band 03 S2c — the Scoreboard's per-buy labels).** The module also owns
`_screen(txn, day_lows, issuer_name)` — the CHARACTER attribution behind the Scoreboard drawer's
per-buy chips / event-ledger rows (`scoreboard/overlays.episode_insider_buys` →
`InsiderBuyOut.character`). Each code-P row lands in exactly ONE character, ordered most-structural
first (mirroring `insider_sell._screen`): **`implausible`** (the $ ceiling) → **`self_filing`** (the
issuer filing on itself — `_is_issuer_self`/`_norm_entity`, DUPLICATED from
`signals/insider_conviction.py` with a pointer, the seam's pattern: CIK equality canonical, name
fallback for pre-capture rows, missing identity never "self") → **`primary_market`** (below the
day's low) → **`open_market`**. `open_market` means "passed the AVAILABLE screens", never "proven
discretionary" — a no-day-low buy stays `open_market`; the tri-state `aff_10b5_1` flag is **not**
read by `_screen` (a planned buy is still open-market; the flag rides beside the character and
renders only on an explicit `true`). Set-aside characters (`primary_market`/`implausible`) surface
**greyed + labeled, never hidden** (WB #2 / #9). The panel's net-flow screen
(`_is_open_market_buy`) composes the SAME predicates but is **deliberately identity-blind**: a
`self_filing` is labeled yet still counts in the 90d net-flow — that re-base is DEFERRED (operator
decision 3, 2026-08-18), the one place the tape and the call knowingly disagree.

`volume_regime` excludes bars without a volume and says how many. `range_52w` stamps tied
highs/lows on the most recent print and notes a sub-year window.

**`rvol` vs `volume_regime` — different volume reads, both quiet.** `rvol` is a SINGLE-BAR relative
volume — the **as-of bar's volume ÷ the mean volume of the N bars before it** — answering "is
*today's* move volume-backed?", now over TWO base windows off ONE fetch: **8 bars** (`rvol`,
mirroring the breakout detector — the call-matched read) and **20 bars** (`rvol20`, the trader
"unusually active vs its *month*?" convention, deliberately call-decoupled). `volume_regime.vol_ratio`
is a different shape again — a **20-bar mean ÷ the prior 60-bar mean** — answering "is participation
*rising* vs its own base?" (its 20 is a trailing MEAN, not `rvol20`'s single anchor bar). All render
as panel chips (and `rvol` / `rvol20` are the basket table's **RVOL|8 / RVOL|20** columns); none are
redundant.

**`rvol` (the 8-bar) mirrors the call; `rvol20` (the 20-bar) is deliberately decoupled.** The 8-bar's
`baseline_bars` (8) and `loud_mult` (1.5) are **display module constants that mirror
`CallConfig.breakout_base_window` / `breakout_volume_mult`** — the seam cannot import `CallConfig`
(`base.py` + the `test_registry` import-ban), so they are hand-kept equal and
`test_rvol.py::test_dials_mirror_the_call_config_exactly` catches a drift. Because the window and
threshold match, the **RVOL|8** column and the breakout trigger never **contradict** — but the
breakout computes its `vol_ratio` at the **breakout bar's date** while `rvol` computes at the **as-of
bar**, so on a non-breakout day the two legitimately differ (a different anchor bar), and an as-of bar
with no volume reads an honest "—", never a stale bar's ratio. The **20-bar `rvol20` mirrors NO call
dial**: 20 is not the call's window, so `baseline_bars_20` / `loud_mult_20` are standalone display
constants — **not `CallConfig`-mirrored and deliberately NOT drift-guarded** (there is nothing to
drift against), a display-only trader convention. Each window handles its OWN gaps (a name with only
9–20 bars reads a real `rvol` but an honest "—" + the bar shortfall on `rvol20`); a volumeless as-of
bar or a zero base sum blanks both. Both windows' **loud accent is FE-derived** (`value >=
basis.params.loud_mult` for the 8-bar, `loud_mult_20` for the 20-bar) and renders a **warm 'hot'**,
never the return-green `pos`/`neg` tone — so each threshold lives in exactly one place, this
module (#7).

**`etf_flow` (the fund sleeve's inflow/outflow read).** Flow is the fund's OWN shares-outstanding
change, priced: Δshares between consecutive sampled counts (`fact_fund_shares` — the daily ingest's
fund-shares leg samples the issuer page, aggregator fallback), each delta priced at the close
on/before its date, summed over trailing 1w/1m windows. Positive Δshares = net creations = INFLOW;
negative = redemptions = OUTFLOW. **The three traps its goldens pin: price appreciation alone,
volume churn alone, and an AUM rise without Δshares are all ZERO flow** — AUM moves when price
moves; flow only moves when shares do. A window states a value only when fully knowable (a baseline
sample on/before the window start + a fresh sample inside it); a younger series reads
`"n/a: 3/30 sampled days"` and a stalled sampler reads `"n/a: no sample in the last 7d (…)"` —
both DISTINCT from a true zero (two equal real samples ⇒ `0.0`, glyph `flat`). Zero samples (every
non-ETF member; an ETF before its first sample) returns `None` — nothing renders. The headline
prefers the 1m read and falls back to 1w while 1m accrues; the basis carries `sample_count`, the
adapter(s) (with the aggregator's ~10k-share-rounded caveat when it sampled), and the latest
sample's exact page URL. Surfaced on the Workbench sleeve dossier (`SleeveRail`) as the fund-flow
chip beside the AUM internals — the pairing that makes the trap visible: AUM up + flow flat = just
price. Promoting flow to a call input is F4 — a separate, operator-signed slice; this member is
structurally unable to be one.

**`sma_position` notes.** `LOOKBACK_DAYS=600` is *calendar* days (`price_history` trims by
calendar): ≈410 trading bars → ~210 SMA200-computable bars ≈ 10 months of 50×200 cross search. A
fresh name's initial 1y pull is honestly thinner — the basis (`bars_used` + the n/a notes) shows
exactly how much tape the reading stands on, and the daily cron's incremental ingest deepens
history over time. Flip detection is a sign state machine over `close − SMA` (and `SMA50 − SMA200`):
exact zeros are skipped — a close ON the line is not a cross (touch-and-return flips nothing; a
cross *through* the line stamps the first bar on the far side); the most recent flip wins.

**The posture headline (the operator's 2×2).** `sma_position`'s headline states
(fast over/under slow) × (fast rising/falling), literally: `↑ 50d over 200d · rising` /
`↘ … falling` / `↗ 50d under 200d · rising` / `↓ … falling`; the muted `detail` carries the
secondary read (`price above both · rising`). *Rising/falling* = the line now vs `SLOPE_BARS=5`
bars back (an exact tie reads `flat` — never a guessed direction). Stable keys: `above_rising`,
`above_falling`, `below_rising`, `below_falling`, `level_*`, and `partial_*` when the slow line
lacks bars (the chip degrades to the half it can say: `↑ 50d rising · 200d n/a`). Metric keys are
window-agnostic (`ma_fast`/`ma_slow`) and every label derives from params, so changing FAST/SLOW —
or adding an EMA sibling that reuses `_headline` on its own two series — never churns the contract.

## Adding a member (the append-one-module checklist)

1. New module in `backend/signals/display/`: named param constants at top → a pure
   `compute(rows, asof) -> DisplaySignal | None` → a thin `display(pit, security_id, asof)` reading
   only `DisplayPointInTimeData` accessors → module-bottom
   `MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))`.
2. Add the import line to `signals/display/__init__.py` (`# isort: off` block — registration order
   is the panel's render order and must stay behavior-stable).
3. Update the registry pin in `tests/signals/display/test_registry.py` + add the member's own pure
   tests (hand-computed values, the honest-degrade notes, event stamping).
4. Add a catalog row above. That's the whole diff — no wire, no FE, no OpenAPI regen.

If a member ever needs a new PIT accessor, widen `DisplayPointInTimeData` (not the detectors'
protocol); if one ever needs clickable filing provenance, add an `*Out` mirror with
`_provenance_out` in `schemas_api.py` then — not before.

## Surfaces & loudness

The NamePanel's **"Indicators · this name"** section (S2) renders metrics as quiet chips, events as
muted dated lines, and the basis as fine print — inverse loudness (#7): indicators are ambient
context, never an alert, and an Incubating name's panel must not get louder because a moving
average moved. The **Cockpit basket table** now surfaces a subset as columns — the SMA posture,
the trailing-return ladder (`1d/7d/30d/90d/1Y`), `RVOL|8` / `RVOL|20`, and `Ins 30d` / `Ins 90d` —
each bridged onto its row by `security_id` and holding the same discipline: a muted "—" is the
default, an accent marks the exception (#7). The columns are individually sortable **within** each
call-state group (nulls-last; the call hierarchy never moves) — the surface detail lives in
`docs/BOARD.md`.

**Perf note (built — Board/Cockpit perf PR-1b).** Each member still does its own PIT read, but the
display route builds ONE `PointInTimeData` per request with the resolved basket as its **prefetch scope**
and `signals.horizons.display_bounds()` as its **read bounds**: each fact table is loaded for the whole
basket in one `as_of_many` query, memoized per `(table, security_id)`, and every member trims its own
`LOOKBACK_DAYS` window from that shared read (the memo rule — a per-call window is never a memo key; the
SPY/IWM benchmark tape, never a basket member, comes through the per-security fallback, memoized once).
The price bound is DERIVED from the members' declarations (`DisplayMember.horizons`, a REQUIRED field —
the max is `sma.py`'s 600 d, + `MARGIN_DAYS`); the insider read stays **unbounded** because
`insider_flow_90d` declares `None`: its "no rows at all" (→ the panel's `—`) vs "rows, none in the
window" (→ `0/0`) semantics would collapse under a floor for a name whose only Form 4s are older than it
(a batched existence check is the deferred follow-up that would let display take the call's insider bound).
A member that reads a table it has not declared, or asks for a longer window than it declared, fails
`tests/signals/test_horizons.py` — never a truncated read. MEASURED on the 190-name Modern Defense thesis
(dev, host venv → dev DB, 2026-09-03): the `/display-signals` body 16.3 s → 3.1 s and `/call` 10.9 s → 2.1 s (2,851 and
2,934 per-security queries → one batch query per table), output byte-identical on all eleven theses at two as-ofs. See `docs/INVARIANTS.md` §4.
