# Display signals

Read-only indicators computed from stored facts and shown beside the call: where a name sits against its
moving averages, its trailing returns, its volume, its insider flow, its fund flows, and two card-level
readings, basket breadth and the sector rollup. They are never an input to the call (invariant 4). The machine
computes them on each read; anyone signed in reads them for their own cards on the Board (the basket table, the
name panel, the rotation strip), the Scoreboard's episode view and the Workbench's fund rail. Read this before
you add a member or a column that reads one.

## Code

| Piece | Module |
|---|---|
| The contract and the data view | `signals/display/base.py` (`DisplaySignal`, `DisplayMember`, `DisplayPointInTimeData`) |
| Registration, in render order | `signals/display/__init__.py`; `signals/display/registry.py` |
| Members | one module each under `signals/display/` |
| Card-level readings | `signals/display/theme_breadth.py` (`breadth_for`); `signals/display/relative_strength.py` (`sector_rs_for`) |
| The route | `app/routers/theses.py` (`get_display_signals`, `read_display_signals`) |
| Read bounds | `signals.horizons.display_bounds` |

## How it works

### Off the call path by construction

- A `DisplaySignal` is not a `SignalEvent`: no role, no fired flag, no grade, no score, no window, so nothing
  downstream can read one as a trigger.
- The package imports none of the call path (`domain.signal`, `domain.config`, `signals.base`,
  `signals.registry`, `signals.common`, `calls`, `pipeline`, `repositories`, `db`, `psycopg`), and nothing in
  `pipeline/` or `calls/` reads the display registry.
  `tests/signals/display/test_registry.py::test_display_package_cannot_touch_the_call_path` pins both.
- Members read only `DisplayPointInTimeData`: `price_history`, `benchmark_prices`, `insider_txns`,
  `fund_shares` and `split_events`. It is a separate protocol from the detectors' view, so widening it never
  gives a detector a new input.
- Nothing is recorded. `record_if_changed` compares the whole `CallCard`, so a field that moves with price would
  append a `calls` row every night; display output rides its own compute-on-read route instead.

The data flags ("is this number real?": a split-night hold, a split awaiting review, a mixed-scale tape, a stopped
feed, a market cap on a pre-split count) follow the same rule on their own route, `GET /theses/{id}/data-flags`
(`domain/data_flags.py`, `pipeline/data_flags.py`; `tests/domain/test_data_flags.py`). They change how a cell
renders, never what a member computes.

### The wire

`GET /theses/{thesis_id}/display-signals?asof=` returns `DisplaySignalsResponse`: one entry per resolved basket
member (deduped, basket order, silent links included; an unresolved member is omitted), each holding every
member's `DisplaySignal`, plus the two card-level fields. A signal carries:

- `kind` and `label`;
- an optional `headline`: a one-glance state chip with a stable `key` a column can consume and a literal label
  built from the member's parameters; it states the tape, never a forecast;
- `metrics[]`: a `null` value is an honest gap, and its note says why;
- `events[]`: dated crosses the tape printed, stamped with the bar date;
- `series[]`: a fixed-slot shape, one slot per bar, newest last, left-padded with `null`; the client breaks the
  line on a gap and never interpolates, and nothing ranks on it;
- `basis`: the source table, every parameter used, the bars used, the window, and a staleness note when the last
  bar lags the as-of (invariant 6).

The payload is generic, so a metrics or events member changes no schema. A past as-of reads on the public clock
(`serve_view`; invariant 1). The route builds one point-in-time view per request with the basket as its prefetch
scope and `display_bounds()` as its read bounds, so each member trims its own window from one shared read.

### Members

| Member | Reads | Shows |
|---|---|---|
| `sma_position` | prices | the close against the fast and slow averages; crosses; the posture headline |
| `trailing_returns` | prices | returns over fixed bar counts (the `1d` to `1Y` ladder) |
| `range_52w` | prices | distance from the 52-week high and low, with print dates |
| `volume_regime` | prices | recent against prior volume; 20-day dollar volume |
| `rvol` | prices | one bar's volume against the prior bars: an 8-bar read matched to the call's breakout, and a 20-bar read that is not |
| `relative_strength` | prices, benchmark prices | the close against each benchmark; a fresh 13-week relative high |
| `insider_flow_90d` | Form 4 rows, day lows | open-market buys and sells, buyers, net dollars, over 90 days and a 30-day sub-window of the same rows |
| `etf_flow` | fund shares, closes | a fund's own creations and redemptions in dollars and as a share of its shares |
| `vcp` | prices | contraction of successive pullbacks; the loud `coiling` headline only when every condition holds |
| `price_path` | prices | a series: the last closes behind the sparkline |

Each module names its parameters as constants at the top, and `basis.params` echoes them.

**Insider flow agrees with the call.** The block is labeled open-market, so it applies the call's screens to each
code-P buy and gives it exactly one character (`_screen`; `BUY_SCREEN_CHARACTERS`): `open_market`, `self_filing`,
`primary_market` (below the day's low), `implausible`, or `internal_transfer` (a buy matched to a sale between
linked filers, `domain.insider_internal_transfers`). Only `open_market` buys count; the others are shown grayed
and labeled, never hidden. A filing-night row skips the below-the-day's-low test. The same characters label the
Scoreboard's per-buy chips (`InsiderBuyOut.character`); `tests/signals/display/test_insider_flow.py` pins the set
against the wire literal. A name with nothing ingested returns no block; an ingested name with no activity in
the window reads zero.

**Card-level readings** ride the same `DisplaySignal` shape:

- `breadth` (`theme_breadth.breadth_for`): the share of resolved members at or above their 50-day average, loud
  (`thrust`) when it crosses a majority and surges against 20 bars ago. A member with too little history is shown,
  not counted. `None` for an empty basket.
- `sector_rs` (`relative_strength.sector_rs_for`): members grouped by business supersector (resolved by the route
  from the company master; an unclassified member is grouped as "unclassified"), each group with how many lead the
  market at a fresh 13-week relative high. `None` with an empty basket or no benchmark tape.

### Surfaces

The call card's name panel shows a name's members as quiet chips, muted dated lines and fine print: indicators
are ambient context, never an alert (invariant 7). The Board's basket table reads these fields:

| Column | Member and field |
|---|---|
| SMA posture | `sma_position` headline |
| `Last close` | `sma_position` `close`, dated when it lags the basket |
| `1d` to `1Y` | `trailing_returns` (`1d` is the last close against the prior close; `1Y` is 252 bars) |
| `Path` | `price_path` series; breaks on a gap, "—" under two closes |
| `Off 52w high` | `range_52w` |
| `RVOL 8D`, `RVOL 20D` | `rvol`, each with its own loud threshold |
| `Ins 30d`, `Ins 90d` | `insider_flow_90d` buys and buyers, with an accent for a cluster of two or more buyers |
| `ADV 20d` | `volume_regime`, no threshold |
| `Tape` | marks only: `vcp` coiling, `relative_strength` leading, an `sma_position` cross within the mark window |

Columns sort within each call-state group, nulls last; `Path` and `Tape` have no scalar and do not sort. Each group
header shows a client-side 7-day median that feeds nothing. The surface itself is [BOARD](BOARD.md).

### Adding a member

A member is one module: pure parameter constants, a pure `compute`, a thin `display(pit, security_id, asof)` that
reads only the display view, and a `register_display_member` call at the bottom; then one import line in
`signals/display/__init__.py`, where order is render order. Update the pin in
`tests/signals/display/test_registry.py` and add the member's own tests. A new field shape (as `series[]` was)
regenerates the API contract pair in the same PR; a new data need widens `DisplayPointInTimeData`, never the
detectors' view.

## Contracts

- No display output reaches a `SignalEvent`, a call, the `calls` table or the nightly (import test above).
- The response is `DisplaySignalsResponse` in `app/schemas_api.py`; `backend/openapi.json` and
  `frontend/src/api/types.gen.ts` are generated from it.
- A gap is `null` with a note, never a made-up number (invariant 6).

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /theses/{id}/display-signals?asof=` | Anyone signed in, own workspace's cards | `require_user`; `get_thesis_or_404`; `tests/app/test_display_signals_api.py` |
| `GET /theses/{id}/data-flags` | Anyone signed in, own workspace's cards | the same; `tests/app/test_data_flags_api.py` |

## Settings

None: each member's parameters are module constants.

## Known limits

- The basket table's Tape marks and their mark window are display choices with no spec of their own.
- An insider-flow zero means nothing ingested in the window, not a proven absence of filings.

## Related

[CALL_LOGIC](CALL_LOGIC.md), [DETECTORS](DETECTORS.md), [BOARD](BOARD.md), [INVARIANTS](INVARIANTS.md) (1, 4, 6, 7).
