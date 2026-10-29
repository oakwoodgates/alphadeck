# Dashboards: experimental views across the cards

## What a dashboard is

The Board shows one card per thesis. A **dashboard** is an experimental, self-contained view *across*
the cards: one page that reads many theses (or many names) at once and shows what the data says about
them together.

This is a brainstorming phase. Some dashboards will be improved and some will be cut, so each one is
built to stand alone and to delete cleanly. Every dashboard carries a **status**, shown as the same
quiet tag beside its title and on the index: `experimental` for a new view (it may be cut), `core` for
a standing page of the app (today only the Board).

Where they live in the app: the top nav's **Dashboards ▾** menu (right after Home) lists
"All dashboards" and then each dashboard; `/dashboards` is the index; `/dashboards/<slug>` is one
dashboard. Every link carries `?asof=` like the rest of the app.

## The Board is the home dashboard (operator decision, 2026-10-29)

The Board is a registry entry like any other, the FIRST one: slug `board`, status `core`, as-of aware,
`layout: "fill"`. Its body is a thin wrapper (`frontend/src/dashboards/board/BoardDashboard.tsx`) around
the unchanged Board in `frontend/src/board/`, which keeps its own tests. It has no backend files under
`dashboards/`: it reads through the app's existing endpoints (`GET /theses`, `GET /theses/{id}/call`,
the data flags). Its plan is `docs/BOARD.md`.

- **Home.** `HOME_DASHBOARD` in `frontend/src/dashboards/registry.ts` names the dashboard `/` renders.
  Changing the home page is that one line. `/` renders the home dashboard's shell in place, never a
  redirect, so `/` bookmarks and `?asof=` links keep working; `/dashboards/<slug>` renders the same
  dashboard. The top nav's first tab is **Home** (`/`), lit only at `/`; the same dashboard at
  `/dashboards/<slug>` lights the Dashboards menu instead. A `HOME_DASHBOARD` the registry does not name
  lands on the index.
- **`layout`** (optional, default `"page"`). `"page"` is the title row over a padded body that grows with
  its content. `"fill"` draws no title row and gives the body the rest of the viewport (a fixed-height
  100vh column under the top bar), unpadded: for a page that scrolls inside itself, like the Board's
  columns.
- **Cockpit links are unchanged.** Every dashboard opens the Cockpit through the shell's
  `onSelectThesis`: `/thesis/:id?name=<key>&asof=…` with `state.from` set to the current URL, so the
  Cockpit's Back returns to the page the click came from (`/` or `/dashboards/<slug>`), or to Home when
  there is no `state.from` (a fresh-tab link).

## The rules (operator decisions, 2026-10-28)

1. **Display only.** A dashboard never feeds a call, never changes a card's state, never writes to the
   database, never sends a notification and never calls the LLM. It may READ anything, including the
   recorded calls (the `calls` log) and the live call. Reading the live call goes through
   `call_for_thesis(..., record=False)`, exactly as the Board's read does: its default is
   `record=True`, which appends the card to the `calls` log (the nightly's write, never a dashboard's).
   Three tests in `backend/tests/dashboards/test_isolation.py` enforce this, each a source scan over
   the real tree:
   - `test_leaf_rule_nothing_outside_app_dashboards_and_tests_imports_a_dashboard` (with its twin
     `test_leaf_rule_inside_app_only_main_mounts_dashboards_and_only_their_routers_import_them`):
     a dashboard is a leaf, so the call path, the pipeline, signals, calls and editions can never
     depend on one.
   - `test_no_dashboard_imports_the_llm_or_notify`: no `llm`, `notify` or `anthropic` import, and
     no LLM client from `app.deps`.
   - `test_no_dashboard_writes_to_the_database`: no SQL write (`INSERT INTO`, `UPDATE … SET`,
     `DELETE FROM`, `TRUNCATE`, `ALTER`, `CREATE`, `DROP`, `MERGE INTO`) in the dashboard code's
     strings, and no `call_for_thesis` without an explicit `record=False`.

   The scans are a floor, not a proof: a write through an imported writer function (a `repositories`
   save) is not caught by them, so review checks every import a dashboard adds.

   **Amendment (operator decision, 2026-10-29): a dashboard PAGE may use an operator action the app
   already exposes elsewhere.** Today that is one action: archiving and restoring a card through the
   existing theses endpoints (`POST /theses/{id}/archive` and `/unarchive`), which the Board does. The
   rest of the rule stands: dashboard BACKEND code still never writes, the three isolation tests above
   are unchanged, and a dashboard never adds a write of its own, in the frontend or the backend.

2. **As-of.** A dashboard follows the app's as-of dial unless its plan makes it a "now" view (like
   Admin and Radar). The registry entry's `asofAware` flag decides: `true` shows the dial and hands
   the body the app's as-of; `false` shows no dial and hands the body today's date. At a past as-of a
   dashboard reads only what was public by the end of that date: the same public-clock read the
   Board's `GET /theses/{id}/call?asof=` uses. `app/routers/theses.py` takes
   `known_at, clock = serve_view(asof)` (`domain.market_time.serve_view`) and threads both into the
   read, `call_for_thesis(conn, thesis.id, asof, known_at=known_at, clock=clock, record=False)`; its
   display-signals route hands the same pair to `PointInTimeData`. A live as-of gets
   `(None, "record")`, the unchanged live read. A past one gets `(known_at_for_asof(asof), "public")`:
   each fact counts from when it became public, up to the end of that market day (invariant #1). A
   dashboard calls `serve_view` once per request and threads that pair into every read; it never
   builds its own cutoff.

3. **Rankings and sorts are allowed** when they are computed by code from data, never sourced from an
   LLM, and their wording stays neutral. Say what the numbers show ("largest 1W move", "most names
   above the 50d"), never an instruction or a verdict ("buy", "must watch", "best idea"). A dashboard
   ranks by what the data shows; it never judges whether an idea is good (invariant #4).

4. **Each dashboard's plan decides** its own loudness, which names count (for example whether
   Context and Bench names are included) and how groups are combined, and the page states those
   choices in a quiet line.

5. **Show the work.** Every aggregate says how many names it rests on and how it was combined
   ("median of 4 priced names"). Names left out for thin data are counted or listed, never silently
   dropped (invariant #9).

6. **Event-outcome views** ("what happened after X") slice by the fixed market-cap bands in
   `CLAUDE.md` (nano < $50M · micro $50M–$300M · small $300M–$2B · mid $2B–$10B · large ≥ $10B),
   measured at the event, with the horizon, the comparison, n and the hit rate stated per tier. See
   `docs/BACKTEST.md` §"Sliced by market cap". The first dashboard that needs the bands defines them
   once in `domain/`.

7. **Cache.** Allowed, in-process, used only by dashboards. It must refresh after every new cron run
   and after any basket edit, and it keys on "a new run happened", never on a clock (the run schedule
   will grow: market open, market close, nightly, pre-market). It is built once, with the first
   dashboard that needs it, then shared, never reinvented per dashboard.
   It lives in `backend/dashboards/cache.py` (built with the Rotation Map): a dashboard's router calls
   `cached(conn, "<slug>", params, asof, compute)` and gets back the value with `status` ("hit" /
   "computed") and `compute_ms`; cache the expensive read per as-of and derive each view from it.
   The cross-card basket read lives beside it in `backend/dashboards/baskets.py` (built with the Rotation
   Map, shared since the Cross Radar): the counted cards and their rosters at the as-of (`cards_as_of`), the
   membership rows, the four groupings and who counts in each (`counted_rows`, `buckets`, `companies_in`),
   the chunked point-in-time read (`per_company`), and the companies whose data flags reach their price
   readings (`price_flags`, the `/data-flags` reader; built with the Narrative Index's fix, read by it and the
   Rotation Quadrant, not yet by the other price dashboards). A dashboard that groups cards' names reuses it
   rather than re-deriving the rule. The equal-weighted line math lives in `backend/dashboards/series.py` (the
   session calendar, the alignment and the chain; built with the Narrative Index, shared since the Rotation
   Quadrant): a dashboard that draws a basket as one line reuses it.

8. **Self-contained layout**, so dashboards build in parallel and delete cleanly. A slug is lowercase
   letters, digits and `-` (`tape-breadth`); the Python module name swaps `-` for `_`
   (`tape_breadth`). Everything a dashboard owns lives in these paths:
   - `frontend/src/dashboards/<slug>/`: the body component (default export, takes `DashboardProps`),
     its query hooks (TanStack Query over the shared `api/client.ts`, not in `api/hooks.ts`), its own
     CSS (imported by the component, not in `index.css`) and its tests.
   - `backend/dashboards/<slug>.py` (or a subpackage, `backend/dashboards/<slug>/`): the compute.
   - `backend/app/routers/dashboards/<slug>.py`: its router (`APIRouter(prefix="/<slug>")`, GET
     only) with its wire models in that same module, not `app/schemas_api.py`.
   - `backend/tests/dashboards/test_<slug>.py`: its backend tests.
   - `docs/dashboards/<slug>.md`: its plan.

   The only shared lines are one registry line on each side: `DASHBOARDS` in
   `frontend/src/dashboards/registry.ts` and `_DASHBOARD_ROUTERS` in
   `backend/app/routers/dashboards/__init__.py`. The shell (`DashboardShell.tsx`) draws the header,
   the title row and the status tag, so a dashboard component renders only its body.

9. **Contract regen in the same PR** (`CLAUDE.md`). A dashboard's routes change
   `backend/openapi.json` and `frontend/src/api/types.gen.ts`; regenerate both
   (`python -m app.openapi_export` from `backend/`, `npm run gen:api` from `frontend/`) in the same
   PR. Parallel dashboard PRs will conflict in those two files: after a rebase, regenerate both
   rather than hand-merging. A conflict on a registry line is two adjacent additions; keep both.

## Add a dashboard: the checklist

1. Write the plan, `docs/dashboards/<slug>.md`: what it shows, as-of or "now", its loudness, which
   names count (Context and Bench included or not), how groups are combined, what a thin-data name
   does, and whether it needs the shared cache. The operator signs off the plan before the build.
2. Backend compute: `backend/dashboards/<slug>.py`. Reads only; threads `serve_view(asof)` into every
   read (rule 2).
3. Backend router: `backend/app/routers/dashboards/<slug>.py` with `router = APIRouter(prefix="/<slug>")`
   and its wire models; add ONE line to `_DASHBOARD_ROUTERS` in
   `backend/app/routers/dashboards/__init__.py`.
4. Backend tests: `backend/tests/dashboards/test_<slug>.py`, including a past-as-of case where a fact
   published after the as-of stays invisible. The isolation tests already cover the new files.
5. Frontend: `frontend/src/dashboards/<slug>/` with the body component (default export) and its hooks,
   CSS and tests; add ONE line to `DASHBOARDS` in `frontend/src/dashboards/registry.ts`
   (`React.lazy(() => import("./<slug>/<Component>"))`, `status: "experimental"`, `asofAware` per the
   plan, `layout` only if the plan needs `"fill"`). Order in the array is menu order; the Board stays
   first, so add a new line at the end.
6. Regenerate the contract pair (rule 9) and commit it with the change.
7. Add the dashboard's row to the Index below.

## Remove a dashboard: the checklist

1. Delete `frontend/src/dashboards/<slug>/` and its line in `frontend/src/dashboards/registry.ts`. If
   `HOME_DASHBOARD` names it, point that line at another slug first.
2. Delete `backend/dashboards/<slug>.py` (or its subpackage), `backend/app/routers/dashboards/<slug>.py`
   and its line in `_DASHBOARD_ROUTERS`, and `backend/tests/dashboards/test_<slug>.py`.
3. Delete `docs/dashboards/<slug>.md` and its row in the Index below.
4. Regenerate the contract pair (rule 9) and commit it with the removal.
5. A search for the slug returns nothing. An old link to the dashboard lands on the index (an unknown
   slug redirects there).

## Index

| Dashboard | Slug | Status | Plan |
| --- | --- | --- | --- |
| Board | `board` | core | [`docs/BOARD.md`](BOARD.md) |
| Shared Names | `shared-names` | experimental | [`docs/dashboards/shared-names.md`](dashboards/shared-names.md) |
| Idea Timeline | `idea-timeline` | experimental | [`docs/dashboards/idea-timeline.md`](dashboards/idea-timeline.md) |
| Rotation Map | `rotation-map` | experimental | [`docs/dashboards/rotation-map.md`](dashboards/rotation-map.md) |
| What Changed | `what-changed` | experimental | [`docs/dashboards/what-changed.md`](dashboards/what-changed.md) |
| Cross Radar | `cross-radar` | experimental | [`docs/dashboards/cross-radar.md`](dashboards/cross-radar.md) |
| Catalyst Calendar | `catalyst-calendar` | experimental | [`docs/dashboards/catalyst-calendar.md`](dashboards/catalyst-calendar.md) |
| Leaders | `leaders` | experimental | [`docs/dashboards/leaders.md`](dashboards/leaders.md) |
| Price & Volume Landing | `landing` | experimental | [`docs/dashboards/landing.md`](dashboards/landing.md) |
| Narrative Index | `narrative-index` | experimental | [`docs/dashboards/narrative-index.md`](dashboards/narrative-index.md) |
| Rotation Quadrant | `rotation-quadrant` | experimental | [`docs/dashboards/rotation-quadrant.md`](dashboards/rotation-quadrant.md) |
