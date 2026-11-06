# Agent tools (the data-first MCP server)

Status: Phase 1 built 2026-11-06 (the server and the MONITOR read tools); Phase 2 (dashboards as agent views)
built 2026-11-06; Phase 3 (prompts and the Claude Code skill) built 2026-11-06; Phase 4 retired the capture server
2026-11-06 (see "History"). Stage: MONITOR.
Code: `backend/agent_tools/`.

## What it is

A read-only MCP server that lets an agent (Claude Code, or any MCP client) ask the operator's MONITOR
questions: what is armed and why, explain this call, what is this company on. Its tools return the platform's
own objects, shaped from the API's JSON: no browser, no DOM, no snapshots. Every reply is bounded, states the
`asof` it was judged at and its `source` (`record` = the nightly calls log; `live` = the API recomputed or read
it now), and carries the platform's wording: `exit_by` is a signal-validity horizon, not a trade exit; setup
strength (wire `confidence`) is not a probability; grade is call strength, never a size; display signals never
feed the call. The server computes no state, grade, date or number, and it writes nothing (GET only).

Shape: `client -> resolve -> view -> fit`. `client.py` GETs the loopback API; `resolve.py` turns a thesis name
or a ticker into an id; `views/` are PURE functions over the API's JSON (no I/O, no clock); `budget.py` is the
one size rule. The same views can later be mounted in the API (Phase 7) without a rewrite.

## Tools

| Tool | Reads | Default reply | Budget |
|---|---|---|---|
| `get_clock()` | `/clock` | `current_night`, `action_day`, `market_today`, `market_tz` | 500 |
| `list_theses(asof?, include_archived?, state?, detail?, limit?)` | `/theses?with_call=true&asof=` | `queue` = live armed cards whose record is dated `asof`, in `/theses` order (never sorted). `cards` is ordered QUEUE-FIRST: the queue cards in full, in queue order, each with the nightly RECORD in effect at `asof` (state, verdict, grades, setup strength, `exit_by`, `arm_until`, armed ticker, counts, `record_asof`) and `triggers` holding its record's first fired trigger (`ticker`, `kind`, `label`, `event_date`) plus `trigger_count`; then the other live cards briefly (`id`, `name`, `state`, `record_asof`) in `/theses` order; then the archived cards. Lean: `ticker` omitted when null, `archived` / `stale` / `reconstructed` when false, `basket_size` except with `detail=full` (every card in full; `state` filters). `limit` (1 to 100) caps how many queue cards come in full (queue order); the rest of the queue is listed briefly, `queue` and `counts` stay whole, and the reply is held to 8,000 (a 5-card review fits one page; the cards past it continue by `cursor`). `cards_total` = every card the list holds after the `state` filter (`cards` is one page of them) | 28,000, the one exception to the 8K rule: the page holds the whole queue in one read (`full`: 24,000) |
| `get_thesis(thesis, detail?)` | `/theses/{id}` | narrative (600 chars), links in link order (silent links last: they never count), each with its `member_count` and its members grouped under it (`cursor` counts members across the links in that order), catalysts, kill criteria, SIGNAL terms; `full` adds `thesis_fit`, `detail`, every term | 8,000 (`full`: 24,000) |
| `get_call(thesis, asof?, section?)` | `/theses/{id}/call` | the call recomputed live: state, verdict, grades, keys, horizons, armed name, armed members (10), watch tickers, every fired trigger with its sources (no `detail`), missing, heads of `counter_case` and `expression`, risk signals counted by kind | 8,000 |
| `get_call(..., detail="brief")` | same | the decision alone, for a pass over many cards: state, verdict, grades, setup strength, keys (turned only), horizons, armed name, each trigger as ONE line `TICKER kind grade event_date src=N label` (label cut at 40 characters, `...` marks a cut; no sources; a long list continues with `cursor`: a 42-trigger card shows 21), `missing` cut at 100 characters each (`{text, total_chars, truncated}`), risk signals by kind, `counter_case` length, catalyst count | 3,500 |
| `get_call(..., section=...)` | same | `triggers` (with each source's computation), `risk_signals` (10 per page), `counter_case`, `members`, `expression` in full | 24,000 |
| `get_data_flags(thesis?, asof?)` | `/theses/{id}/data-flags` or `/data-flags` | per flagged name: each flag's kind, label, level, reason, `clears` (what clears it), `since`, affects, `call_affected`; notes and splits counted; cross-card adds the cards holding it and `compute_s` (it is slow). The API lists only names that carry a flag, a note or a split: `names_total` is its own count of the card's names, `listed` the rows it returned (a different count from `list_theses`'s `cards_total`). Call it per card only when `get_call` reports missing conditions or `list_theses` shows `missing_count` above zero | 8,000 |
| `get_display_signals(thesis, tickers?, asof?)` | `/theses/{id}/display-signals` | breadth and sector headlines; per member `{kind: {key, label}}`; with `tickers` (1 to 8) each signal's metrics | 8,000 |
| `get_decisions(thesis)` | `/theses/{id}/decisions` | the operator's log, newest first, as the API gives it | 8,000 |
| `lookup_ticker(t, asof?, section?)` | `/dashboards/ticker-lookup/resolve` then `/{security_id}` | identity, the cards it sits on (link, `context_role`, the record there), record triggers, tape figures, next 5 card catalysts, decisions, filings summary; `cache` passes through, `compute_ms` only when this read computed the page (else `cold_compute_ms`); `section=filings`, `triggers` (every record trigger event on the name) or `catalysts` (every upcoming card catalyst) = 25 rows a page | 8,000 |

Common to every tool: `asof` is optional `YYYY-MM-DD`; omitted, it is the API's `current_night` (the server never
derives a date). `thesis` takes a UUID or an exact name (case-insensitive). Every tool is annotated
`readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false` and advertises an
`outputSchema`. Replies are compact JSON text plus `structuredContent`.

The guided reads are five prompts; see "Prompts and the Claude Code skill" below.

## Dashboards (Phase 2)

Two more tools make the eight MONITOR dashboards bounded, shaped reads over `GET /dashboards/<slug>` (a raw read is
139K to 5.6 MB). They are display only, never a call input, and the API's row order is the UI's order: nothing here
re-sorts, ranks or computes (views under `backend/agent_tools/views/dashboards/`, pure like the others).

- `list_dashboards()`: the registry from code (no I/O, 3,193 characters MEASURED on the registry; held to 3,500): per
  dashboard its slug, purpose, whether it is dated, its settings with their allowed values and defaults, its sections
  as `{section: needs}` (`needs` is `thesis`, `group` or null: the sections that read one card or one group say so, so
  a first read never costs a rejection) and the narrowing parameters it takes.
- `read_dashboard(dashboard, asof?, section?, cursor?, thesis?, group?, change?, since?, group_by?, include_silent?,
  rows?, days?, role?)`: every reply is at most 8,000 characters and starts with the same header: `dashboard`, `asof`
  (null for the two undated ones), `source: live`, the API's `clock`, `cache` and the read time where it gives them (a
  cold read computes the dashboard in 5 to 45 s and says `cache: computed` with `compute_ms`; on a cache hit the API's
  number is the cold time, so it rides as `cold_compute_ms` and `compute_ms` is absent), the effective `settings`, `coverage` (the
  dashboard's own counts and caveats: flagged names, roster fallback, unrecorded cards, silent rows left out), the
  `sections`, and `next_cursor` / `next_cursor_for` for the one list a reply pages.
  A setting or narrowing the dashboard does not take, an `asof` on an undated dashboard, an unknown section, or a
  section that needs a `thesis` or `group` without one is `invalid_arguments` naming the accepted values (`hot-board
  does not take days; it takes asof, rows, thesis, section, cursor`; a value outside an enum or pattern lists the
  allowed ones), checked before any request. `change` narrows `what-changed` to ONE change (a key of `by_change`, so
  its `items_total` equals that count); an empty section or narrowing carries a `note`. A card whose last pass did not
  ingest every name cleanly keeps `ingest_fresh: false` in the digest, and the digest's `coverage.cards_not_fresh`
  names up to 10 of them.

| Dashboard | Dated | Settings | Default reply (the paged list) | Sections | Narrows |
|---|---|---|---|---|---|
| `daily-digest` | yes | | counts, nights, forming, positions, then `last_night.cards` (paged) and the tape's first 3 AND last 3 rows (`rows`, `tail`: the API lists rises first, so the falls sit at the tail; `rows_shown` / `tail_shown` beside `rows_total`), the leading insider (2) and due (4) rows with their totals | `last_night` (each card's lines cut at 12 with `lines_total`; with `thesis`: that one card, every line, paged by line), `tape`, `insider`, `due` | `thesis` (last night) |
| `what-changed` | no | `since` (`last_night`, `7d`, `YYYY-MM-DD`) | `anchor` (latest, baseline), counts by section and change, `items` (paged) | `column_moves`, `keys`, `names`, `triggers`, `clocks`, `new_cards` | `thesis`, `change` |
| `hot-board` | yes | `rows` (`armed`, `all`) | candidate counts, `rows` (paged; two triggers each, tape, cap) | `row` (a card's rows in full) | `thesis` |
| `entry-windows` | yes | | `rows` (paged; clocks, member and catalyst counts) | `members`, `catalysts` (paged by card) | `thesis` |
| `forming-quietly` | yes | | `rows` (paged; a digest of each card, not its 38K) | `names`, `risks`, `missing`, `watch`, `quiet`, `adoption` (each needs `thesis`) | `thesis` |
| `catalyst-calendar` | no | | counts, a bucket index, `items` (one flat list, each with its `bucket`; the cursor crosses buckets) | `bucket` (with `group`), `passed`, `undated` | `thesis`, `group` (a bucket key) |
| `rotation-map` | yes | `group_by` (`link`, `business_type`, `sector`, `card`), `include_silent` | SPY reference, `groups` (paged; window returns, SMA, RVOL, crosses, RS counts) | `members` (with `group`), `flagged` | `group` |
| `trigger-log` | yes | `days` (30, 90, 180), `role` (`entry`, `risk`, `both`) | window, counts by kind and week, `rows` (paged) | `row` (a card's rows in full) | `thesis` |

A row's strings are cut where a view says so; a cut string carries `<field>_total_chars` beside it. `thesis` takes
an id or exact name; `group` a key from the default reply. The 5.6 MB trigger log is read whole and shaped without
copying the row list.


## Prompts and the Claude Code skill

Five guided reads, each a prompt file under `backend/agent_tools/prompts/` (package data) that a handler fills with its
arguments. The agent's own reading goes under a separate heading and it says what it did not read.

| Prompt | Arguments | Call plan | Budget on the fixtures (MEASURED) |
|---|---|---|---|
| `board-review` | `asof?`, `max_items=5` | `list_theses(limit=max_items)`, then `get_call detail=brief` per queue card, flags only where `missing_count` is above zero | at most 10 calls / 36,000 (6 / 17,046 at 5 cards) |
| `explain-call` | `thesis`, `asof?` | `get_call`, `section=triggers` (and `risk_signals`), `get_thesis` | unchanged |
| `morning-brief` | `asof?` | `what-changed`, `daily-digest`, then the digest sections the default cut (due, then insider), at most one more call | at most 5 calls / 36,000 (5 / 31,958) |
| `ticker-check` | `t`, `asof?` | `lookup_ticker`, `get_call detail=brief` for at most 2 cards (armed or warming first), `get_display_signals` for the first | at most 4 calls / 24,000 (4 / 15,195) |
| `compare-rotation` | `start`, `end`, `group_by=business_type`, `group?` | `read_dashboard("rotation-map")` at both dates, matched by group `key` | default at most 4 calls / 32,000 (4 / 22,042); with `group` at most 2 / 4,000 (2 / 2,920) |

`compare-rotation` refuses a broken rule before any read: `start` not before `end`, a date not `YYYY-MM-DD`, a
`group_by` outside `business_type`, `sector`, `card`, `link`, or `link` without `group` (a full link comparison is
dozens of pages). The rule text reaches the client as an invalid-params error (the SDK hides any other exception raised
while rendering a prompt). Each prompt file is at most 2,000 characters.

A user runs one as `/mcp__alphadeck-prod__morning-brief 2026-11-05`. A Claude Code session's model cannot run an MCP
prompt (the docs expose prompts only as user-typed commands), so this repository carries the same recipes as the
skill `.claude/skills/alphadeck-agent/SKILL.md`: the model invokes it when the operator asks for one of the reads, or
the operator types `/alphadeck-agent morning-brief 2026-11-05`. Each recipe block in the skill is the prompt file,
verbatim, between `<!-- recipe:<name> -->` markers. `python -m agent_tools.skill_sync --check` exits 1 with a diff when
a block differs from its file, a prompt has no block or a block names no prompt (a test runs the same check in CI);
`--write` regenerates the blocks after a prompt edit. The server's `instructions` name the five prompts and this path.

## The size rule

Every list in a reply is trimmed by ONE function, `budget.fit`: it measures the compact encoding
(`separators=(",", ":")`, ASCII) and keeps the longest prefix of the named list that fits the tool's budget.
When it cuts, the reply carries `next_cursor` (the first index not shown) and `next_cursor_for` (which list);
pass `cursor=<next_cursor>` to continue. A string a view cuts carries `total_chars` beside it. Budgets are
constants in each view module and are asserted in `backend/tests/agent_tools/test_views.py` against synthetic
fixtures at the measured production sizes, so a size regression fails CI. If a reply cannot fit even at its
smallest, the tool fails `output_too_large` naming the parameter that would shrink it.

## Errors

A failure is an `isError` result whose text is compact JSON `{"code", "message", "retryable"}` (an unknown or
ambiguous thesis adds `candidates`). It never carries exception text, paths or payloads.

| Code | Meaning | Retry? |
|---|---|---|
| `invalid_arguments` | A malformed argument (a bad date, an unknown `section`, a negative `cursor`, too many tickers) | no |
| `unknown_thesis` | No thesis with that id or exact name; up to 5 candidates whose names contain the text | no |
| `ambiguous_thesis` | Several theses have that exact name; pass an id | no |
| `unknown_ticker` | No company has that ticker (a former ticker also resolves) | no |
| `backend_unavailable` | The API is not reachable at `--backend-base` (stack down, wrong port) | yes |
| `api_timeout` | The API did not answer within `--timeout` | yes |
| `api_error` | The API answered 5xx or a non-JSON body | yes |
| `api_rejected` | The API answered another 4xx; carries its `detail` (cut at 300) | no |
| `output_too_large` | The reply exceeds its budget at its smallest | no |
| `internal_error` | Anything else (logged to stderr by class only) | no |

## Install and register

The server needs the `mcp` extra, which `scripts/ensure-venv.ps1` / `.sh` now install
(`-e ".[dev,replay,mcp]"`, what CI installs). In the MAIN checkout, from `backend/`:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,replay,mcp]"
```

The server needs no database URL, no browser and no model key: only the API. Phase 4 dropped Playwright and
jsonschema from the `mcp` extra (they were the capture server's); a venv installed before keeps them as unused
packages until `pip uninstall playwright jsonschema`.

### Register with Claude Code

`.mcp.json` declares two project servers: `alphadeck-prod` (`--backend-base http://127.0.0.1:8000`) and
`alphadeck-dev` (`--backend-base http://127.0.0.1:8001`). Both launch `${ALPHADECK_MCP_COMMAND}`, the MAIN
checkout's `alphadeck-mcp` console script. Set that variable once, as a user environment variable, to its
absolute path:

- Windows: `<main checkout>\backend\.venv\Scripts\alphadeck-mcp.exe`, for example
  `[Environment]::SetEnvironmentVariable('ALPHADECK_MCP_COMMAND', '<path>', 'User')` in PowerShell, then
  restart Claude Code so it sees the variable.
- Linux: `<main checkout>/backend/.venv/bin/alphadeck-mcp`, exported from your shell profile.

The console script (not `python -m agent_tools`) means a session's working directory can never shadow the
installed package. Approve the two servers when Claude Code first asks, then check them with `claude mcp list`;
while the variable is unset it shows the literal `${ALPHADECK_MCP_COMMAND}` and "Failed to connect" for both.
Builder agents cannot use `alphadeck-prod` (`.claude/agents/builder.md` denies `mcp__alphadeck-prod__*`);
`alphadeck-dev` stays available to them.

### Other MCP clients

Launch the console script over stdio (there is no HTTP endpoint), with absolute paths:

```powershell
alphadeck-mcp --backend-base http://127.0.0.1:8001
```

Options: `--backend-base` (a loopback origin with an explicit port; default `http://127.0.0.1:8001` or
`ALPHADECK_MCP_BACKEND_BASE`; credentials, URL paths and remote hosts are refused), `--timeout` seconds per API
request (1 to 600, default 60: the cross-card `/data-flags` read and a cold dashboard take up to about 35 s) and
`--trace` (one stderr line per call: `tool=<name> ok=<bool> chars=<n> ms=<n>`, never content). Phase 4 removed
`--ui-base`: a client config that still passes it must drop it (the server refuses an unknown argument).

Run by hand: `python -m agent_tools --backend-base http://127.0.0.1:8001 --trace` (it speaks MCP on stdio).

## Reconnect after a contract change

An MCP client keeps the tool schemas it loaded when it connected. After this server's tools change (a new
argument, a renamed field, a new tool), reconnect the server in the client (in Claude Code: `/mcp`, then
reconnect, or restart the session); otherwise the model keeps calling the old contract. The server's advertised
version (`alphadeck` `2.1.0`) changes with its contract: a tool or prompt added or removed, or a description
whose meaning changes, bumps the MINOR. A client sees the version at `initialize`; a running Claude Code session keeps
the schemas it loaded, and a fresh session reads the new ones.

## Tests

`backend/tests/agent_tools/`: `test_views.py` (shape and budget per view, every cursor walked to the end),
`test_server.py` (the official SDK client in-process against an httpx `MockTransport` fake API, plus one REAL
stdio subprocess against a loopback fake API: 10 tools, 5 prompts, read-only, output schemas), `test_dashboards.py`,
`test_resolve.py`, `test_client.py`, and the prompt call plans through the real stdio client: `test_morning_brief.py`,
`test_board_review.py`, `test_explain_call.py`, `test_ticker_check.py`, `test_compare_rotation.py` (with the
argument-rule rejections), plus
`test_skill.py` (the skill equals the prompt files; its size and frontmatter bars). The route the list reads is tested against the database in
`backend/tests/app/test_theses_with_call.py`. Fixtures are synthetic (`Thesis 01`, `TK01`): no card or company
name from a live card.

## History

From 2026-11-02 to 2026-11-06 a DOM-capture MCP server served agents: a capture endpoint on the API froze a
dashboard's responses, a headless Chromium rendered them through a separate frontend render page and the agent
read the rendered DOM. Phase 4 (PR #506) retired it whole (the server package, the endpoint, the renderer, the
components' instrumentation and the Chromium CI job) because an agent needs the platform's objects, not its
pixels; the data-first server here replaced it in Phases 1 to 3 (PRs #503, #504, #505).
