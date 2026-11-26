# Cockpit

`cockpit` · follows the as-of dial · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows

One thesis card in full: a top thesis switcher with steppers, its narrative, open position summary when held, thesis pulse, rotation strip, grouped basket table with reversible view filters, fund launches, evidence, catalyst calendar, kill criteria and the live call rail. Full behavior and contracts live in [BOARD](../BOARD.md).

## Who counts

Every active thesis in the caller's workspace appears in the top switcher in workspace list order (never ranked). Every basket member of the selected thesis lands in one group; Context, ETFs and Bench never count toward the call and render last.

## Data and code

| Piece | Where |
|---|---|
| Compute | App thesis endpoints (`/theses`, `/theses/{id}`, `/call`, `/display-signals`, `/scored`, `/data-flags`, `/fund-launches`, `/decisions`) |
| Route | `/dashboards/cockpit` (and `/thesis/:thesisId` deep links) |
| Shared readers | None (reads the app's own thesis endpoints) |
| Cache key | None (per-thesis reads use `app/call_cache.py` and TanStack Query) |
| Frontend | `frontend/src/dashboards/cockpit/`, `frontend/src/cockpit/` |
| Tests | `frontend/src/cockpit/__tests__/`, `frontend/src/__tests__/App.routes.test.tsx`, `frontend/src/dashboards/__tests__/registry.test.ts` |

## As-of behavior

Follows the app's as-of dial (`?asof=`). The call, display signals, scored read and data flags all read the selected as-of on the same clock (`serve_view`). Countdowns (entry window, hold exit-by, catalysts) count from the action day on the live night and from the as-of on a past date.

## Rules not visible in code

- Default thesis selection picks `?thesis=` when present, else the first active thesis in workspace list order, never by rank (invariant 4).
- Cached lifecycle dots in the switcher read only warm TanStack Query entries and trigger zero extra `/call` requests.
- View filters, risk marks and pulse counts are display-only and reversible in one click (WB#1, WB#2, WB#3).

## Known limits

See [BOARD](../BOARD.md#known-limits) for the full list of Cockpit limits.
