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

The Board is a registry entry like any other, the FIRST one (`frontend/src/dashboards/board/entry.ts`,
`order: 0`): slug `board`, status `core`, as-of aware, `layout: "fill"`. Its body is a thin wrapper (`frontend/src/dashboards/board/BoardDashboard.tsx`) around
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

   **A flagged price history is left out (2026-10-29).** Every price dashboard (the Rotation Map, the Cross
   Radar, Leaders, the Price & Volume Landing and the Narrative Index) leaves out a company whose data flags
   reach its price readings at the as-of (`dashboards.baskets.price_flags`, the reader behind the Cockpit's
   grayed price cells): a scale break chains fake returns and fake crosses. It is read inside each page's
   cached read with the request's one `serve_view(asof)` pair, and the company is out of every aggregate and
   list on the page. It is never vanished: the page shows a quiet "N names left out for a flagged price
   history" that opens to each ticker and the flags' labels (`frontend/src/dashboards/FlaggedNote.tsx`), and
   an expanded group lists the name grayed as "left out: <reason>". Measured on dev with a prod data copy
   before the change: 6 of the 31 flagged names showed on the Leaders lists.

6. **Event-outcome views** ("what happened after X") slice by the fixed market-cap bands in
   `CLAUDE.md` (nano < $50M · micro $50M–$300M · small $300M–$2B · mid $2B–$10B · large ≥ $10B),
   measured at the event, with the horizon, the comparison, n and the hit rate stated per tier. See
   `docs/BACKTEST.md` §"Sliced by market cap". The bands are defined once, in
   `backend/domain/market_cap.py` (`CapTier`, `cap_tier`; built with the Peer Table, which slices its names by them).

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
   readings (`price_flags`, the `/data-flags` reader, read by every price dashboard; `flagged_in_view` names
   them on a page, rule 5). A dashboard that groups cards' names reuses it rather than re-deriving the rule.
   A one-card dashboard starts from its picker, `picker_cards` (the counted cards A→Z with their link counts;
   built with the Chain Wave, shared since the Filing Stream).
   The cron warms the heavy pages after each pass (`pipeline.warm_dashboards`, last in the nightly wake, GETs
   only, each page's default view for the next visit's as-of; `FEED_LOOP.md` §the dashboards' cache warm-up), so a
   dashboard whose default query changes updates its entry in that module's `PAGES`.
   The equal-weighted line math lives in `backend/dashboards/series.py` (the session calendar, the alignment
   and the chain; built with the Narrative Index, shared since the Rotation Quadrant): a dashboard that draws
   a basket as one line reuses it.
   What each company filed lives in `backend/dashboards/filings.py` (built with the Filing Stream, shared with
   the Read-Throughs table): every filing-derived fact the system stores, plus every other form in the company's
   EDGAR index (`fact_filing_index`, migration 0070: display only, each filing shown once as its richest row),
   normalized to one `FilingRow` shape (`read_filing_rows`: the point-in-time view plus the Form 4/A SELECT, both on
   the request's one `serve_view(asof)` pair). A page that lists what a company filed reuses it rather than
   re-reading the fact tables; a page that wants one family names it (`kinds=`; Insider Buying reads `form4` and `form4a`
   alone, and every other kind's read is skipped, not filtered). A ratified fact, and a catalyst a person ratified, carry who ratified them and the
   day (`ratifier`, `ratified_on`; the Evidence Ledger's authority classes read them).

8. **Self-contained layout, self-registering**, so dashboards build in parallel and delete cleanly. A slug
   is lowercase letters, digits and `-` (`tape-breadth`); the Python module name swaps `-` for `_`
   (`tape_breadth`). Everything a dashboard owns lives in these paths:
   - `frontend/src/dashboards/<slug>/`: the body component (default export, takes `DashboardProps`),
     its `entry.ts` (below), its query hooks (TanStack Query over the shared `api/client.ts`, not in
     `api/hooks.ts`), its own CSS (imported by the component, not in `index.css`) and its tests.
   - `frontend/src/dashboards/<slug>/thumbnail.svg`: its thumbnail, the drawing on its tile in the
     `/dashboards` index (see "Thumbnails" below). The registry collects it from the folder the way it
     collects `entry.ts`: nothing imports it.
   - `backend/dashboards/<slug>.py` (or a subpackage, `backend/dashboards/<slug>/`): the compute.
   - `backend/app/routers/dashboards/<slug>.py`: its router (a module-level
     `router = APIRouter(prefix="/<slug>")`, GET only) with its wire models in that same module, not
     `app/schemas_api.py`.
   - `backend/tests/dashboards/test_<slug>.py`: its backend tests.
   - `docs/dashboards/<slug>.md`: its plan.

   **There is no shared line to edit.** The frontend registry (`frontend/src/dashboards/registry.ts`) builds
   `DASHBOARDS` from every `<slug>/entry.ts` (`import.meta.glob`): each exports
   `entry: DashboardEntry` (its slug = its folder name, its label, description, status, `asofAware`,
   `layout`, a numeric menu `order` and `component: React.lazy(() => import("./<Component>"))`), sorted by
   `order` then label; the Board is 0 and the rest step by 10, so a new dashboard picks a free number. It
   also globs every `<slug>/thumbnail.svg` (as URLs), pairs each with the entry of the same folder and sets
   the entry's `thumbnail`; an `entry.ts` never writes it. A duplicate slug, a slug that is not its folder,
   an `entry.ts` without `entry`, an `entry.ts` without a `thumbnail.svg` beside it, or a `thumbnail.svg`
   without an `entry.ts` beside it fails loud, naming the folder. The backend
   package (`backend/app/routers/dashboards/__init__.py`) mounts every module in itself that defines a
   module-level `router` (an `APIRouter`), in sorted module-name order; a module without one (a shared
   helper) is skipped, and one that fails to import fails the app's start. `HOME_DASHBOARD` (one line in
   `registry.ts`) is the only dashboard name outside a dashboard's own paths. The shell
   (`DashboardShell.tsx`) draws the header, the title row and the status tag, so a dashboard component
   renders only its body.

9. **Contract regen in the same PR** (`CLAUDE.md`). A dashboard's routes change
   `backend/openapi.json` and `frontend/src/api/types.gen.ts`; regenerate both
   (`python -m app.openapi_export` from `backend/`, `npm run gen:api` from `frontend/`) in the same
   PR. Parallel dashboard PRs will conflict in those two files: after a rebase, regenerate both
   rather than hand-merging. They are the only shared files a new dashboard touches (rule 8).

## Thumbnails

Every dashboard has a thumbnail: a small drawing at the top of its tile on the `/dashboards` index. The
Dashboards ▾ menu stays a plain list, with no thumbnails.

A thumbnail is one file in the dashboard's own folder, `frontend/src/dashboards/<slug>/thumbnail.svg`,
beside its `entry.ts`. The registry finds it the way it finds the entry (rule 8): nothing imports it and
there is no shared list. It goes when the folder goes, so the remove checklist does not change. The build
keeps each thumbnail as its own small file, not inlined into the main chunk (`build.assetsInlineLimit` in
`frontend/vite.config.ts` names `thumbnail.svg`).

**It is drawn by code, not captured.** It shows the shape of the page, never its data. That is on purpose:

- A screenshot would show card names, and this repo is public. A drawing cannot carry a name.
- A drawing never goes stale and is never empty at a past as-of.
- It costs no requests. The index does not call any dashboard's endpoint.
- A builder draws it in the same PR, and GitHub renders the SVG in the diff, so a reviewer sees it without a
  running stack.

**The rules.** `frontend/src/dashboards/__tests__/thumbnails.test.ts` reads every folder's `thumbnail.svg`
and enforces rules 1 to 7:

1. **It exists for every dashboard folder.** `buildRegistry` refuses a folder without one too, so the app
   never starts with a dashboard missing its thumbnail.
2. **The root is** `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">` (16:10, the shape of the
   tile's frame). The background is transparent: the frame supplies it.
3. **No words.** No `<text>`, `<tspan>`, `<textPath>`, `<title>`, `<desc>` or `<foreignObject>`, and no
   text or comment anywhere in the file. A thumbnail can never carry a ticker, a card name or a number, so
   it is always safe to commit to a public repo.
4. **Self-contained and flat.**
   - None of `<image>`, `<script>`, `<style>`, `<filter>`, `<linearGradient>`, `<radialGradient>` or
     `<pattern>`.
   - No `style=` attribute and no `on*` attribute.
   - No `href` or `xlink:href` except `#id`, and no `url(` except `url(#id)`.
5. **The app's colors only.** Every `fill`, `stroke` and `stop-color` is `none` or a hex from the first
   `:root` block of `frontend/src/index.css`. The test reads the hexes from that file, so recoloring a
   token fails the test and points at the drawings. `opacity`, `fill-opacity` and `stroke-opacity` are free.
6. **Quiet** (inverse loudness, Workbench principle 3). Draw structure in the neutral tokens: `--bg`,
   `--bg-2`, `--panel`, `--panel-2`, `--line`, `--line-2`, `--txt`, `--txt-2`, `--txt-3`, `--txt-4` and
   `--incub`. Every other color token in `:root` is an accent, and a drawing uses at most three of them.
   Use an accent where the page itself uses that color: the lifecycle colors for states, green and red for
   a signed number, the one accent a page keeps for its one exception.
7. **At most 4 KB.** Draw with paths and short strokes, and set shared styles on a `<g>`. A line of
   placeholder "text" is one stroke with round caps.

**One rule the test cannot check, so review does: draw what makes the page different, not a generic
table.** About half the dashboards are tables, and fifteen identical row stripes tell you nothing. Draw the
feature that sets the page apart: the heat bar that shrinks down a ranking, the diagonal wave across a
grid, the as-of line through the windows.

## Add a dashboard: the checklist

1. Write the plan, `docs/dashboards/<slug>.md`: what it shows, as-of or "now", its loudness, which
   names count (Context and Bench included or not), how groups are combined, what a thin-data name
   does, and whether it needs the shared cache. The plan names, in one line, what the thumbnail draws
   (see "Thumbnails"); the operator signs off the plan, thumbnail line included, before the build.
2. Backend compute: `backend/dashboards/<slug>.py`. Reads only; threads `serve_view(asof)` into every
   read (rule 2).
3. Backend router: `backend/app/routers/dashboards/<slug>.py` with a module-level
   `router = APIRouter(prefix="/<slug>")` and its wire models. That is all: the package mounts it.
4. Backend tests: `backend/tests/dashboards/test_<slug>.py`, including a past-as-of case where a fact
   published after the as-of stays invisible. The isolation and discovery tests already cover the new
   files.
5. Frontend: `frontend/src/dashboards/<slug>/` with the body component (default export), its hooks, CSS
   and tests, and its `entry.ts` exporting `entry: DashboardEntry` (`slug` = the folder name,
   `order` = a free menu place after the Board's 0, `component: React.lazy(() => import("./<Component>"))`,
   `status: "experimental"`, `asofAware` per the plan, `layout` only if the plan needs `"fill"`). The
   folder also carries `thumbnail.svg` beside `entry.ts` (see "Thumbnails"). Nothing imports it; the
   registry finds it. Without it, the registry and the thumbnail test fail. That is all: the registry
   collects both.
6. Regenerate the contract pair (rule 9) and commit it with the change.

## Remove a dashboard: the checklist

1. Delete `frontend/src/dashboards/<slug>/` (its `entry.ts` and `thumbnail.svg` go with it). If
   `HOME_DASHBOARD` names it, point that line at another slug first.
2. Delete `backend/dashboards/<slug>.py` (or its subpackage), `backend/app/routers/dashboards/<slug>.py`
   and `backend/tests/dashboards/test_<slug>.py`.
3. Delete `docs/dashboards/<slug>.md`.
4. Regenerate the contract pair (rule 9) and commit it with the removal.
5. A search for the slug returns nothing. An old link to the dashboard lands on the index (an unknown
   slug redirects there).

## Index

The `/dashboards` page is the index, one tile per dashboard under its thumbnail; each plan lives in
`docs/dashboards/<slug>.md` (the Board's is `docs/BOARD.md`).
