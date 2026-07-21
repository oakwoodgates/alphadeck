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
- It has its own narrow `DisplayPointInTimeData` Protocol (only `price_history` + `insider_txns`);
  the detectors' `SignalPointInTimeData` stays exactly as #176 left it ("no future plugin surface").

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
| `range_52w` | `fact_price_eod` | pct_off_52w_high, pct_above_52w_low, high_52w, low_52w (print dates ride the notes) | — | lookback_days=380 |
| `volume_regime` | `fact_price_eod` | vol_ratio (20d ÷ prior 60d), adv_usd_20d | — | recent_bars=20, base_bars=60, lookback_days=150 |
| `insider_flow_90d` | `fact_insider_txn` (+ `fact_price_eod` day-lows) | buy/sell counts, distinct_buyers, buy/sell/net USD (open-market code-P buys, code-S sells) | last_buy, last_sell | window_days=90, offmarket_below_low_frac=0.10, max_plausible_txn_usd=2e9 |

**Member epistemics worth naming.** `insider_flow_90d` returns `None` for a name with **nothing
ingested** (nothing to say) but a **quiet zero** for an ingested name with no window activity (zero
is information); its basis note carries the "zero ingested ≠ proven-zero filings" caveat. Its
**headline** (`net_buying` / `net_selling` / `net_flat` — "net selling $3.4M (90d)", counts in the
detail) renders **only when the window has actual flow**: a quiet name adds no "no flow" line to
the panel's top strip (the strip marks the exception, #7); the section's zero metrics still carry
the quiet read.

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

`volume_regime` excludes bars without a volume and says how many. `range_52w` stamps tied
highs/lows on the most recent print and notes a sub-year window.

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
average moved. Board/basket-table surfaces are explicit follow-ups, not defaults.

**Perf note.** Each member does its own PIT read (2–3 price reads + 1 insider read per name per
request — the `/scored` cost profile). If latency ever shows on a big basket, memoize the PIT reads
per-request in the router; deliberately not built until it hurts.
