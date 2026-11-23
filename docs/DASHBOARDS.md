# Dashboards

A dashboard is a display-only page that reads many of a user's cards, or many names, at once. This doc holds the
rules every dashboard keeps (1 to 9; code cites them by number), the registry, the looks, the thumbnails, the
checklists and the doc template. Read it before you add, change or remove a dashboard. Each dashboard's own doc is
`docs/dashboards/<slug>.md`; the Board's is [BOARD](BOARD.md).

## What a dashboard is

One registry entry and the files it owns (rule 8), so each builds alone and deletes cleanly. Its `status` in
`entry.ts` is `core` (a standing page; today only the Board) or `experimental` (it may be removed); promoting one to
`core` is the operator's call. The top nav's Dashboards menu lists every entry, `/dashboards` is the index (one tile
per entry), `/dashboards/<slug>` is one dashboard, and an unknown slug lands on the index. Links carry `?asof=`.

## The registry

`frontend/src/dashboards/registry.ts` builds `DASHBOARDS` from every `<slug>/entry.ts` and `<slug>/thumbnail.svg`;
the menu, index, routes and home page read it. `DashboardEntry` types an entry:

| Field | Meaning |
|---|---|
| `slug` | URL segment and folder name |
| `order` | Menu place, then label. The Board is 0, the rest step by 10 |
| `label`, `description` | Title; the line under it and in the menu |
| `status` | `core` or `experimental` |
| `asofAware` | `true` shows the as-of dial; `false` is a "now" view (rule 2) |
| `layout` | Optional: `"page"` (default, title row and padded body) or `"fill"` (no title row; the body fills the viewport) |
| `readsRecord` | Optional `true` for a page that reads the calls history; it renders the shared `RecordStartNote` (when the workspace's record starts) |
| `openCompanyInCockpit` | Optional `true` when company clicks (`onSelectThesis(id, nameKey)`) should jump straight to the Cockpit instead of opening the shell's company overlay (Board Decision Queue, Ticker Lookup) |
| `component` | `React.lazy(() => import("./<Component>"))` |

The registry adds each entry's `thumbnail` from its folder. `buildRegistry` fails loud, naming the folder, on a
duplicate slug, a slug that is not its folder, a missing `entry`, or an `entry.ts` and a `thumbnail.svg` without
each other (`__tests__/registry.test.ts`). `__tests__/RecordStartNote.test.tsx` lists the `readsRecord` slugs and
checks each page renders the note. `DashboardShell.tsx` draws the header and status tag; a component renders its
body only.

## The Board is the home dashboard

The Board is the first entry (`board/entry.ts`: order 0, `core`, as-of aware, `layout: "fill"`). Its body
(`BoardDashboard.tsx`) wraps `frontend/src/board/`, which keeps its own tests; it has no backend under
`dashboards/` and reads the app's own endpoints. Its doc is [BOARD](BOARD.md).

- `HOME_DASHBOARD` in `registry.ts` names the dashboard `/` renders, in place, never a redirect, so bookmarks and
  `?asof=` links keep working. Home is lit only at `/`; the same dashboard at `/dashboards/<slug>` lights the
  Dashboards menu. A name the registry lacks lands on the index.
- Every dashboard opens the Cockpit through the shell's `onSelectThesis` with `state.from` set, so the Cockpit's
  Back returns to the page the click came from. Clicking a company (`onSelectThesis(id, nameKey)`) opens the
  shell's slide-over `CompanyDrawer` (`GET /theses/{thesis_id}/member-panel`) in place unless `openCompanyInCockpit`
  is set; clicking a thesis (`nameKey` omitted) or using the drawer's **Open in Cockpit** action navigates to the
  Cockpit.

## Workspace scope

A dashboard reads the caller's workspace plus the shared platform data (invariant 12). Every dashboard router
takes `app.deps.get_workspace` and passes it to every card-selecting read (`baskets.cards_as_of`, `picker_cards`,
`price_flags`) and to the cache key (rule 7), so a page computed for one workspace is never served to another.
Pinned by `tests/app/test_workspace_isolation.py` (`test_a_cached_dashboard_page_is_never_served_to_b`) and
`tests/dashboards/test_cache.py`. See [TENANT_SCOPES](TENANT_SCOPES.md).

## The rules

### 1. Display only

A dashboard never feeds a call, changes a card's state, writes to the database, sends a notification or calls the
LLM. It may read anything, including the `calls` log and the live call, which it reads with
`call_for_thesis(..., record=False)` as the Board does (the default `record=True` appends to the `calls` log, the
nightly's write).
`backend/tests/dashboards/test_isolation.py` scans the real tree:

- `test_leaf_rule_nothing_outside_app_dashboards_and_tests_imports_a_dashboard` and
  `test_leaf_rule_inside_app_only_main_mounts_dashboards_and_only_their_routers_import_them`: a dashboard is a
  leaf; the call path, pipeline, signals, calls and editions never import one.
- `test_no_dashboard_imports_the_llm_or_notify`.
- `test_no_dashboard_writes_to_the_database`: no SQL write in code strings, no `call_for_thesis` without
  `record=False`.

The scans are a floor, not a proof: a write through an imported writer passes them, so review checks every import
a dashboard adds. One exception: a page may use an action the app already offers elsewhere, today archiving and
restoring a card (`POST /theses/{id}/archive`, `/unarchive`) on the Board. Dashboard backend code never writes.

### 2. As-of

`asofAware: true` shows the dial and hands the body the app's as-of; `false` is a "now" view (the Catalyst
Calendar, the Idea Timeline) whose compute takes `market_today()` as a parameter.

A dashboard calls `domain.market_time.serve_view(asof)` once per request and threads its `(known_at, clock)` pair
into every read; it never builds its own cutoff. A past as-of reads the public clock: a fact counts from when it
became public, up to EDGAR's next open after that night (invariant 1).

A card's call in effect at an as-of is its `calls` row with the greatest `(asof, seq)` on or before it; a
reconstructed row is marked. A card created after the as-of is not counted; a card with no roster snapshot by then
reads today's roster, and the page counts it (`baskets.cards_as_of`).

A countdown to a future date counts from `domain.market_time.countdown_origin(asof, clock)`: the action day on a
live view (the user acts the day after the night), the as-of on a past one. Read `market_clock()` once for both
`serve_view` and the origin, and put the origin in the cache key. An age (of a filing, of a state) counts from the
as-of. On a past view a relative count also shows its date.

### 3. Rankings and sorts

Allowed when code computes them from data, never an LLM, in neutral words: what the numbers show ("largest 1W
move"), never an instruction or verdict ("buy", "best idea"). A formula ranking prints its formula. A dashboard
never judges whether an idea is good (invariant 4).

### 4. Each dashboard states its choices

Its doc decides its loudness, which names count (Context and Bench in or out) and how groups combine; the page
states them in a quiet line.

### 5. Show the work

Every aggregate says how many names it rests on and how it was combined ("median of 4 priced names"). A name left
out for thin data is counted or listed, never silently dropped (invariants 6 and 9).

A company whose data flags reach its price readings at the as-of (`baskets.price_flags`, read inside the cached
read) is left out of every price aggregate and list, because a scale break chains fake returns and fake crosses.
It is never hidden: the shared `FlaggedNote.tsx` opens to each ticker and reason. A page that keeps the row instead
states its rule through `flaggedRule(...)`.

A stopped price feed on a company that is on no live card of the workspace reads as no longer priced
(`baskets.off_card_reason`): the nightly stopped fetching it, and it still trades. On a card it stays a stopped
feed, a data fault.

### 6. Event-outcome views

A view of what happened after an event slices by the market-cap bands, measured at the event, stating per tier the
horizon, the comparison, n and the hit rate. A pooled number never stands alone; the pooled row follows the tiers.
Say what happened, never that a signal "works"; label a liquidity cut. The bands live in
`backend/domain/market_cap.py` (`CapTier`, `cap_tier`), mirrored in `CLAUDE.md`; reasoning in `docs/BACKTEST.md`
§"Sliced by market cap". The Track Record implements it; per-row pages leave the statistic to it.

### 7. Cache, warm-up and shared readers

One in-process cache, `backend/dashboards/cache.py`, used only by dashboards. A router calls, after `serve_view`:

```python
got = cached(conn, "<slug>", params, asof, compute, workspace=workspace)
```

`workspace` is required; the key is `(workspace, slug, params, asof)`. `params` holds what else the value depends on
(the clock; the countdown origin). `got` carries `value`, `status` (`hit` or `computed`) and `compute_ms`. Cache the
expensive read per as-of; derive each grouping or toggle per request.

It clears on events, never a clock: a cron run or `data_repair` row clears everything; a roster snapshot or a card
created, archived, renamed or switched to a meta index clears page entries; a company's change stamp clears page
entries and that company's pieces. The module docstring holds the contract. For authors:

- A writer that changes a company's stored data outside the company write lock calls
  `db.change_stamp.mark_changed`, or dashboards serve that company stale.
- Catalysts, kill criteria, exclusions and the decisions log move no cache event, so a page reads them live on
  every request (`baskets.with_live_entries`).

The warm-up: the nightly pass empties the cache, so the cron sidecar's last step,
`python -m pipeline.warm_dashboards`, reads each heavy page's default view for every workspace at the night just
recorded, and the first morning visit is a hit. It is read-only and fail-open, with the system token and the workspace in
`X-Alphadeck-Workspace`. `warm_dashboards.PAGES` mirrors each page's default query, and
`tests/pipeline/test_warm_dashboards.py` proves the warmed read is the next visit's hit; a page whose default query
changes updates both. Kill switch: `ALPHADECK_WARM_DASHBOARDS` (compose passes it).

Shared readers; each module's docstring is its contract:

| Module | Owns |
|---|---|
| `dashboards/baskets.py` | Counted cards and rosters (`cards_as_of`), groupings, the point-in-time read (`per_company`), price flags, `with_live_entries`, `picker_cards` |
| `dashboards/series.py` | Session calendar, basket line, move since a night against IWM |
| `dashboards/nights.py` | Recorded-night shapes and trigger identity |
| `dashboards/filings.py` | What a company filed, as one `FilingRow` (`read_filing_rows`) |
| `dashboards/market_cap.py` | The approximate cap cell (`cap_cell`); the bands stay in `domain/market_cap.py` |
| `dashboards/breadth.py` | Per-card theme breadth |

A page imports shared code from these, never from another dashboard, so a dashboard leaves with its files. The
exception is the Daily Digest, built from four other pages' computes.

### 8. Self-contained and self-registering

A slug is lowercase letters, digits and `-`; the Python module swaps `-` for `_`. A dashboard owns:

- `frontend/src/dashboards/<slug>/`: the body (default export, takes `DashboardProps`), `entry.ts`,
  `thumbnail.svg`, its hooks (over `api/client.ts`, not `api/hooks.ts`), its CSS (not `index.css`) and tests.
- `backend/dashboards/<slug>.py` (or a subpackage): the compute.
- `backend/app/routers/dashboards/<slug>.py`: `router = APIRouter(prefix="/<slug>")`, GET only, its wire models
  beside it, not in `app/schemas_api.py`.
- `backend/tests/dashboards/test_<slug>.py` and `docs/dashboards/<slug>.md`.

No shared line to edit: the registry collects the folder, and `backend/app/routers/dashboards/__init__.py` mounts
every module defining a module-level `router`, in sorted order; a module without one is skipped, and an import
failure fails the app's start (`tests/dashboards/test_router_discovery.py`). `HOME_DASHBOARD` is the only dashboard
name outside a dashboard's files. A one-card dashboard serves `GET .../cards` from `baskets.picker_cards` and
`GET .../{thesis_id}`, which answers 404 for a card not counted at the as-of.

### 9. Contract regen in the same PR

A dashboard's routes change `backend/openapi.json` and `frontend/src/api/types.gen.ts`; regenerate both in the same
PR (the `contract-sync` skill). After a rebase, regenerate rather than hand-merge. They are the only shared files a
new dashboard touches.

## Looks

Two looks; each page's stylesheet names its look in its header comment.

- **Research set:** a document. One column or one wide table, one line per row, tabular numbers, quiet uppercase
  labels, no badges or color by kind.
- **Trading set:** a tape. Dense rows, mono tabular numerals, a sticky header, no cards or chrome, a hairline
  between rows, a quiet work line, filters as text chips. The sign tone (positive and negative mixed toward gray)
  only on signed returns; the Board's state colors only on a state chip; a score bar is gray. Nothing bold but the
  ticker.

Both: the app's color tokens only; one accent per page, for the one exception its doc names (invariant 7, WB#3); a
control renders only when it changes what the page shows; on a phone a wide table scrolls sideways with its first
column pinned.

## Thumbnails

Each dashboard's tile on the index carries `frontend/src/dashboards/<slug>/thumbnail.svg` (the menu has none). The
registry finds it like the entry, so it leaves with the folder; the build keeps it a separate file
(`build.assetsInlineLimit` in `frontend/vite.config.ts`). It is drawn by code, never captured, and shows the page's
shape, never its data: the repo is public and a drawing cannot carry a card name, it never goes stale, and the
index makes no requests.

`frontend/src/dashboards/__tests__/thumbnails.test.ts` enforces rules 1 to 7 (its constants hold the numbers):

1. Every dashboard folder has one (`buildRegistry` refuses a folder without one).
2. The root is the `ROOT_VIEWBOX` svg, background transparent.
3. No words: no text elements, titles, descriptions or comments.
4. Self-contained and flat: no images, scripts, styles, filters, gradients or patterns; links only to an `#id`.
5. Colors only `none` or a hex from the first `:root` block of `frontend/src/index.css`.
6. Quiet: structure in `NEUTRAL_TOKENS`, at most `MAX_ACCENTS` accents, used where the page uses that color.
7. At most `MAX_BYTES`.

Review checks the one rule the test cannot: draw what makes the page different, not a generic table.

## Who can do what

| Action | Who | Enforced by |
|---|---|---|
| Read a dashboard (`GET /dashboards/...`) | Anyone signed in, in their own workspace | `require_user` on the mount (`app/main.py`), `get_workspace`; `tests/app/test_auth_gate.py` |
| Archive or restore a card from the Board | The user, on their own card | `get_thesis_or_404` (another workspace's card is a 404) |
| Warm the cache | The machine (cron), with the system token | `app.auth.system_may` (GETs under `/dashboards/`, `/clock`, `/health` only); `test_the_system_token_reads_dashboards_and_the_clock_only` |
| Add, remove or promote a dashboard; change `HOME_DASHBOARD` | A code change; promotion to `core` is the operator's call | review |

## Add a dashboard

1. Write `docs/dashboards/<slug>.md` from the template below.
2. Compute `backend/dashboards/<slug>.py`: reads only, `serve_view(asof)` threaded into every read.
3. Router `backend/app/routers/dashboards/<slug>.py` with `Depends(get_workspace)`; the package mounts it.
4. Tests `backend/tests/dashboards/test_<slug>.py`, with a past-as-of case where a fact published after the as-of
   stays invisible.
5. Frontend folder with `entry.ts` (`status: "experimental"`, a free `order`; `readsRecord` and the note for a
   history page, plus the slug in the note test's list) and `thumbnail.svg`.
6. A heavy page adds its default query to `warm_dashboards.PAGES` and the test's table.
7. Regenerate the contract pair (rule 9).

## Remove a dashboard

1. Repoint `HOME_DASHBOARD` if it names the slug; change the Daily Digest first if it reads the compute.
2. Delete the five paths of rule 8.
3. Drop the slug from `warm_dashboards.PAGES` and the note test's list.
4. Regenerate the contract pair. A search for the slug returns nothing; an old link lands on the index.

## The dashboard doc template

Each `docs/dashboards/<slug>.md`, 400 words or fewer (500 at most). The docs checker fails a dashboard without a
doc, a doc without a dashboard, and a doc missing one of the six headings.

```markdown
# <Label>

`<slug>` · follows the as-of dial (or: a "now" view) · rules: [DASHBOARDS](../DASHBOARDS.md)

## What it shows
The question it answers and the unit (all cards, one card, one name).

## Who counts
Which cards and names are rows; Context and Bench; thin data and flagged prices.

## Data and code
| Piece | Where |
|---|---|
| Compute | `backend/dashboards/<slug>.py` |
| Route | `GET /dashboards/<slug>` |
| Shared readers | the rule 7 modules it uses |
| Cache key | the `params` it passes to `cached`, or none |
| Frontend | `frontend/src/dashboards/<slug>/` |
| Tests | `backend/tests/dashboards/test_<slug>.py`, frontend tests |

## As-of behavior
What a past as-of reads; inputs that are always "now"; countdowns.

## Rules not visible in code
A rule and its why, one line each. Name constants; never copy values.

## Known limits
```

Never copy what `entry.ts`, the page strings or the wire shapes own. No status banner, sign-off, timings or plans.

## Known limits

- Two reads are copied, not shared: the call-in-effect SELECT (`_IN_EFFECT_SQL`, three routers) and the arm-night
  walk (`hot_board.arm_nights`, `entry_windows.armed_run`). Changing either rule changes every copy.
- A page not in `PAGES`, or any page after a later run or edit, computes cold on its next visit.

## Related

[BOARD](BOARD.md) · [INVARIANTS](INVARIANTS.md) · [TENANT_SCOPES](TENANT_SCOPES.md) ·
[DISPLAY_SIGNALS](DISPLAY_SIGNALS.md) · [DOCS_STANDARD](DOCS_STANDARD.md)
