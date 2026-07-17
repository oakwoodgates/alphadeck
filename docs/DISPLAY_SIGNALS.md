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
| `sma_position` | `fact_price_eod` | close, sma50, sma200, pct_vs_sma50, pct_vs_sma200 | cross_sma50, cross_sma200, golden_cross/death_cross | fast=50, slow=200, lookback_days=600 |

**`sma_position` notes.** `LOOKBACK_DAYS=600` is *calendar* days (`price_history` trims by
calendar): ≈410 trading bars → ~210 SMA200-computable bars ≈ 10 months of 50×200 cross search. A
fresh name's initial 1y pull is honestly thinner — the basis (`bars_used` + the n/a notes) shows
exactly how much tape the reading stands on, and the daily cron's incremental ingest deepens
history over time. Flip detection is a sign state machine over `close − SMA` (and `SMA50 − SMA200`):
exact zeros are skipped — a close ON the line is not a cross (touch-and-return flips nothing; a
cross *through* the line stamps the first bar on the far side); the most recent flip wins.

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
