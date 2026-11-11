# Agent tools (the data-first MCP server)

Status: Phase 1 built 2026-11-06 (the server and the MONITOR read tools); Phase 2 (dashboards as agent views)
built 2026-11-06; Phase 3 (prompts and the Claude Code skill) built 2026-11-06; Phase 4 retired the capture server
2026-11-06 (see "History"); the shared tool contract (PR #508) made tool registration declarative 2026-11-06; Phase 5
(the research-stage reads) built 2026-11-06; the hosted endpoint (per-user MCP Phase 1: the server in the API at
`/mcp`, each user's own token, the Settings "Agents" tab) built 2026-11-08. Stage: MONITOR, plus a read-only look at
SURFACE and TRIAGE (Phase 5). Code: `backend/agent_tools/`.

## What it is

A read-only MCP server that lets an agent (Claude Code, or any MCP client) ask the operator's MONITOR
questions: what is armed and why, explain this call, what is this company on; and, since Phase 5, the research
stages' questions: what did discovery find, what is left to ratify on this card. Its tools return the platform's
own objects, shaped from the API's JSON: no browser, no DOM, no snapshots. Every reply is bounded, states the
`asof` it was judged at and its `source` (`record` = the nightly calls log; `live` = the API recomputed or read
it now), and carries the platform's wording: `exit_by` is a signal-validity horizon, not a trade exit; setup
strength (wire `confidence`) is not a probability; grade is call strength, never a size; display signals never
feed the call. The server computes no state, grade, date or number, and it writes nothing (GET only).

Shape: `client -> resolve -> view -> fit`. `client.py` GETs the loopback API; `resolve.py` turns a thesis name
or a ticker into an id; `views/` are PURE functions over the API's JSON (no I/O, no clock); `budget.py` is the
one size rule. Each tool asks `client_for()` for its API client: the stdio server hands back its one loopback client,
and the hosted endpoint (below) builds one per call that carries the caller's own token.

A tool declares a `Contract` in `tool_args.py` (its sections, what each section needs, its narrowings and its
settings); the one `check` turns a bad call into `invalid_arguments` naming what the tool takes, and prompts are
entries in `PROMPTS` (`prompt_args.py`). `tests/agent_tools/test_contract.py` covers every registered contract and
the prompt table, so adding a tool is a view, a contract and a fixture.

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

The guided reads are eight prompts (the eighth is the steward's); see "Prompts and the Claude Code skill" below. Four more tools read the research
stages; see "Research-stage reads (Phase 5)".

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


## Research-stage reads (Phase 5)

Four more tools read the Workbench's stages as bounded views over its existing GETs, so "what did discovery find" and
"what is still to ratify on this card" cost a few calls instead of a 1.2 MB run file and an 86 KB scored view. They are
read-only: ratify, sign-off, include and exclude stay in the Workbench. Views are under `backend/agent_tools/views/research/`
(pure, like the others) and each tool's contract is declared in `views/research/__init__.py`. Every reply starts
`thesis`, `asof` (null on an undated tool), `source: live` and ends with `sections` (each section and what it needs),
`next_cursor`, `next_cursor_for`. The API's row order is the UI's: nothing here sorts, scores or derives.

| Tool | Reads | Default reply | Budget |
|---|---|---|---|
| `get_triage(thesis, asof?, section?, segment?, t?)` | `/workbench/theses/{id}/scored?asof=` (recomputed on the API, about 1.5 s for 80 names) | the funnel per segment (`members_total`, `confirmed`, `unconfirmed`; the three silent links marked `silent`), `counts`, then the member rows: the five meters (pips, market cap as the figure), `unconfirmed_estimates`, the distinct provenance `sources`, the two data flags only when set. The funnel and the counts ride the first page; 79 members walk in 4 pages. `section=member` (needs `t`) reads one name with every meter's provenance (`detail` strings cut at 200); `segment` (needs `segment`), `unconfirmed` and `silent` page the matching rows. A meter is never a call input | 8,000 |
| `get_discovery(thesis, run?, section?, status?, segment?, t?)` | `/workbench/theses/{id}/runs`, then `/runs/{run_id}` (the newest unless `run` names one); `section=terms` also reads the thesis | the run's report (pages, `failed_terms`, `capped_terms`, `empty_terms` whole: a capped term means the hits past the cap were not read), counts by status, source and segment, then the placements in the run's order (`status` and `segment` narrow). `section=placement` (needs `t`, a ticker or a name), `ambiguous` (the names waiting for an explicit pick, with their candidates), `report` (adds the placements behind each flagged term), `terms` (the term set joined with the run; a term in no placement stays, with 0). A card with no saved run answers with `runs: []` and a note; the run loader's 404 and an unknown `run` reach the caller as `api_rejected` with the API's detail. VERIFY and off-thesis are recommendations, never deletions | 8,000 |
| `get_extract(t, thesis?, section?, fact?)` | `/workbench/securities/{id}/extract` (`?thesis_id=` only when `thesis` is given) | the three scoring-fact candidates (AUTO / FLAG / HUMAN) with at most two passages each, every excerpt cut at 300 characters with `excerpt_total_chars`. `section=passages` with `fact` reads one fact's passages in full. The extract is DB-free: it does not know whether a fact is already on file (`get_triage` section=member does) | 8,000 |
| `get_onboarding(thesis, section?)` | `/workbench/theses/{id}/onboarding` | `counts`, `window`, `stalled`, `paused`, `history_legs`, `last_run`, the first 10 pending names and the latest 5 finished ones; `section=pending` or `recent_done` pages the full list. A name still onboarding is why a card is `ingest_fresh: false` | 8,000 |

**Cost is the operator's to spend.** `get_extract` makes up to 4 EDGAR requests when the filings are not cached and, with
`thesis`, ONE LLM call (the grounded purity proposal) when a key is set; the prompts call it only when the operator names the
company, and it passes `thesis` only when the operator asks for the on-thesis purity proposal. `get_triage` recomputes the
scored view on every read (about 1.5 s for 80 names), so it is read once per review. The three undated tools
(`get_discovery`, `get_extract`, `get_onboarding`) take no `asof` and answer `asof: null`.

## Steward reads (Phase 3)

Two more tools read the Admin panels a super admin (the steward) works from, so "processing stock splits with Claude"
reads the split list with both sources' evidence in a few calls. Read-only, like every tool here: a split is
confirmed, rejected or restored only on Admin > Splits. They are registered with `scope="steward"`, so a plain user
never lists them. Views are under `backend/agent_tools/views/admin/` (pure; the contracts are declared in
`views/admin/__init__.py`). Both are undated (`asof: null`, `source: live`; an `asof` argument is refused), read the
shared data layer live, and page inside the 8,000-character rule. The API's order is the panel's: nothing here sorts,
scores or decides whether the two sources agree.

| Tool | Reads | Default reply | Budget |
|---|---|---|---|
| `get_splits(section?, t?)` | `/admin/splits` | `counts` (needs_review, announced, confirmed, set_aside, unlisted and dismissed jumps, total), then every needs-review row: ticker, company name, the dates, the label, `decided_by`, the SEC filing's reading beside Massive.com's (`sec`: form, filed, `date_kind`, ratio, `abstain_reason`, the located passage cut at 300 with `passage_chars`; `polygon`: execution date, ratio), the `confirm_options` labels and the allowed `actions`; and the first five unlisted jumps. `section=needs_review`, `announced`, `confirmed` or `set_aside` pages that bucket (a set-aside row adds `restore_to` and its note); `section=jumps` pages the unlisted then the dismissed jumps; `section=one` with `t` reads one ticker's every entry (every field, `version_id` included, the passage cut at 1,500) and every jump (with the bars after it) in full, `found: 0` when the list does not hold it | 8,000 |
| `get_admin_status(section?)` | `/admin/status` | the cron verdict and its alarms, the record's edge and missed nights (counts of the partial, reconstructed and late-only nights), the newest finished pass (its problems cut at 3 with the total, the self-audit as counts and the failed checks' names) and any unfinished one, the newest backup, `backend_sha` and `cron_sha`, the feed-freshness, split-basis and price-repair counts, the four one-line jobs and the panel counts. `section=tape`, `price_repairs`, `split_defects`, `missing_closes`, `record` or `run` pages one panel's rows whole, each row under its list's name | 8,000 |

**Three layers.** (1) The registry: a tool registered `steward` is neither listed nor callable for a principal without
the scope (`forbidden`, "This tool needs the steward scope.", before any API request). (2) The gate behind it: an OAuth
token without `steward` is a plain user at the API even when its user is a super admin. (3) The API: `/admin/*` is
`require_super_admin`, and its 403 ("stewards only") answers the tool as `forbidden` too. Each layer is tested with the
others bypassed (`test_principal.py`). A stdio server learns its scopes once, from `GET /auth/me` with its token: the
stdio server lists the two tools when its token is a super admin's. A thesis from another workspace is already spelled
`workspace <8 hex> · thesis <8 hex>` in the status payload; the view copies it and resolves nothing.

The `splits-review` prompt (below) is the guided read over `get_splits`; it is listed only to a caller who holds the
steward scope.

## Prompts and the Claude Code skill

Eight guided reads (the last one, `splits-review`, is listed to stewards only), each a prompt file under `backend/agent_tools/prompts/` (package data) that a handler fills with its
arguments. The agent's own reading goes under a separate heading and it says what it did not read.

| Prompt | Arguments | Call plan | Budget on the fixtures (MEASURED) |
|---|---|---|---|
| `board-review` | `asof?`, `max_items=5` | `list_theses(limit=max_items)`, then `get_call detail=brief` per queue card, flags only where `missing_count` is above zero | at most 10 calls / 36,000 (7 / 18,609 at 5 cards) |
| `explain-call` | `thesis`, `asof?` | `get_call` (its `catalyst_surface` carries the catalysts), `section=triggers`, `get_thesis`; `section=risk_signals` only when the operator asks | at most 4 calls / 32,000 (3 / 19,785) |
| `morning-brief` | `asof?` | `what-changed`, `daily-digest`, then up to three more calls in order of need: the digest's `due`, `insider` and `tape` sections where the default cut them, or in place of one a what-changed section or `forming-quietly` | at most 5 calls / 36,000 (5 / 32,236) |
| `ticker-check` | `t`, `asof?` | `lookup_ticker`, `get_call detail=brief` for at most 2 cards (armed or warming first), `get_display_signals` for the first | at most 4 calls / 24,000 (4 / 15,218) |
| `compare-rotation` | `start`, `end`, `group_by=business_type`, `group?` | `read_dashboard("rotation-map")` at both dates, matched by group `key` | default at most 4 calls / 32,000 (4 / 22,042); with `group` at most 2 / 4,000 (2 / 2,920) |
| `ratify-queue` | `thesis`, `asof?` | `get_triage` once (the funnel, the unconfirmed names and the meters without a value), `get_thesis` once (not signed off, model drafts, no conviction or context role), `get_discovery(section="ambiguous")` only when the card has a saved run; `get_extract` only when the operator names the company | at most 3 calls / 24,000 (3 / 23,486) |
| `splits-review` | `t?` | steward only: `get_splits` once (counts and the needs-review rows with both sources side by side), `section=one` for at most 3 names (the named one, else a disagreement or an abstain), `section=jumps` only when the default counted an unlisted jump; the report ends "confirm or reject each on Admin > Splits" | at most 4 calls / 24,000 (4 / 9,147) |
| `discovery-review` | `thesis` | `get_discovery` once (report, capped and empty terms, counts), `section="terms"`, `section="ambiguous"` when any, at most one slice (`status`, `segment` or one `placement`) | at most 4 calls / 32,000 (4 / 27,114) |

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
`--write` regenerates the blocks after a prompt edit. The server's `instructions` name the eight prompts and this path. A prompt carries a scope like a tool: `list_prompts` hides, and `get_prompt` refuses (invalid params, "This prompt needs the steward scope."), one the caller does not hold, so a plain caller's tools and prompts listing is the same fourteen tools and seven prompts as before.

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
| `api_rejected` | The API answered another 4xx; carries its `detail` (cut at 300). A **401** says instead to set `ALPHADECK_MCP_TOKEN` (see "The token" below) | no |
| `output_too_large` | The reply exceeds its budget at its smallest | no |
| `forbidden` | The tool needs a scope the caller does not hold ("This tool needs the steward scope."); such a tool is not listed for that caller either. Needed by `get_splits` and `get_admin_status`; also the API's own 403 behind them ("stewards only", for a token that is not a super admin's) | no |
| `internal_error` | Anything else (logged to stderr by class only) | no |

## Install and register

The SDK (`mcp==2.3.0`) is a base dependency since the hosted endpoint (the API image mounts the server); the `mcp`
extra is kept as an empty list so `-e ".[dev,replay,mcp]"` (`scripts/ensure-venv.ps1` / `.sh`, what CI installs)
keeps working. In the MAIN checkout, from `backend/`:

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

### The token (the login door)

Every API route sits behind a login ([`docs/AUTH.md`](AUTH.md)), so the server sends
`Authorization: Bearer <token>` with each request. The token comes from the environment variable
`ALPHADECK_MCP_TOKEN` in the shell (or user environment) the client launches the server from; `.mcp.json` does not
name it, because a stdio server inherits its parent's environment. Mint one per user on the stack it reads (prod
and dev keep separate users, so each needs its own token):

```powershell
python -m pipeline.users token create --email <you> --name mcp    # prints the token ONCE
[Environment]::SetEnvironmentVariable('ALPHADECK_MCP_TOKEN', '<token>', 'User')   # then restart Claude Code
```

The token is never logged and never passed as an argument (an argv is visible to every process). Without it, or
with a revoked one, every tool fails `api_rejected` with a message naming `ALPHADECK_MCP_TOKEN` and the command
above. One variable serves one stack at a time: the two `.mcp.json` servers share it, so with prod and dev users
that differ, set it to the token of the stack you are reading (a per-server variable can come later).

### Other MCP clients

Launch the console script over stdio, with absolute paths (an install with a public address also serves the
server over HTTP: "Hosted endpoint" below):

```powershell
alphadeck-mcp --backend-base http://127.0.0.1:8001
```

Options: `--backend-base` (a loopback origin with an explicit port; default `http://127.0.0.1:8001` or
`ALPHADECK_MCP_BACKEND_BASE`; credentials, URL paths and remote hosts are refused), `--timeout` seconds per API
request (1 to 600, default 60: the cross-card `/data-flags` read and a cold dashboard take up to about 35 s) and
`--trace` (one stderr line per call: `tool=<name> ok=<bool> chars=<n> ms=<n>`, never content). Phase 4 removed
`--ui-base`: a client config that still passes it must drop it (the server refuses an unknown argument).

Run by hand: `python -m agent_tools --backend-base http://127.0.0.1:8001 --trace` (it speaks MCP on stdio).

## Hosted endpoint

The same server, mounted IN the API at `/mcp` (Streamable HTTP, stateless; `backend/agent_tools/hosted.py`), so
any MCP client that can send a bearer token reaches it without a local process. Every user reads only their own
workspace: the API's login door signs each request in.

- **The switch:** `ALPHADECK_PUBLIC_ORIGIN`, the app's public origin (for example `https://deck.example.com`; no
  path; `http` only on localhost, e.g. `http://localhost:8081` on dev). Unset (the default) = no `/mcp`, no
  well-known document, nothing changes. Set = the connector URL is `<origin>/mcp`. Prod keeps it unset until the
  server move ([`DEPLOY.md`](DEPLOY.md)): the host has no TLS yet, and a token must never cross a network in clear.
- **The token:** each user's own personal access token, created on **Settings > Agents** (or with
  `python -m pipeline.users token create`) and sent as `Authorization: Bearer <token>`. The endpoint verifies it
  by asking the API's own `GET /auth/me` in-process with that bearer and nothing else, so a revoked token, a
  disabled user, a session cookie alone and the system token are all 401; there is no cache, so a revocation bites
  on the next request. Every API read a tool makes carries the same bearer, so the workspace is the API's choice,
  never the server's (nothing under `agent_tools/` names a tenant).
- **Claude Code:**
  `claude mcp add --transport http alphadeck https://deck.example.com/mcp --header "Authorization: Bearer <token>"`,
  then start a new session. The `.mcp.json` form, if your client expands variables in headers (keep the token in
  the environment, never in the file):

  ```json
  { "mcpServers": { "alphadeck-http": { "type": "http", "url": "https://deck.example.com/mcp",
      "headers": { "Authorization": "Bearer ${ALPHADECK_MCP_TOKEN}" } } } }
  ```
- **The OAuth sign-in:** a client that connects through OAuth (claude.ai's custom connectors, Claude Code's
  `/mcp` sign-in; ChatGPT and Grok in a later phase) signs the user in on OUR consent page and gets a one-hour
  access token the endpoint verifies the same way (`GET /auth/me`; the `oauth` principal, read-only, its scopes the
  ones the user allowed). A 401 carries
  `WWW-Authenticate: Bearer ... resource_metadata="<origin>/.well-known/oauth-protected-resource/mcp"`; that RFC 9728
  document (`resource` = the connector URL, `authorization_servers` = the origin, as the SDK spells it with a
  trailing slash, `scopes_supported` = `read`, `steward`) points at our authorization server, whose RFC 8414 document
  names the same issuer. The flow, the clients, the tables and revocation: [`CONNECTORS.md`](CONNECTORS.md).
- **Scopes:** 14 tools need `read`, which every user holds; `get_splits` and `get_admin_status` need `steward` (a
  `super_admin`). A tool whose scope the caller lacks is not listed and answers `forbidden`: a steward's connector
  (an app granted `read steward`) lists 16 tools and 8 prompts, a plain user or a steward's app granted `read` only
  lists 14 and 7.
- **The nightly window:** a hosted reply between 22:15 and 23:30 market time carries `"nightly_window": true` at
  its top level (a note, never a refusal): the nightly pass is writing the record. The stdio server leaves the rule
  to its caller (the skill).
- **nginx** (`frontend/nginx.conf`): `location = /mcp` (exact, never a prefix: a redirect to `/mcp/` would strip
  the Authorization header; buffering off for event-stream replies; 300 s timeouts),
  `location ^~ /.well-known/oauth-protected-resource`, and the authorization server's five exact locations
  ([`CONNECTORS.md`](CONNECTORS.md)), all straight to the backend without the `/api` prefix.
  `GET /mcp` answers 405 (stateless: there is no server-initiated stream to open); `POST /mcp` is never redirected.
- **Transport checks:** the `Host` header must be the origin's host (bare, or with any port; the SDK matches a
  value exactly or `host:*` as `host:` plus any port) and a browser `Origin`, when sent, must be the origin.

## Reconnect after a contract change

An MCP client keeps the tool schemas it loaded when it connected. After this server's tools change (a new
argument, a renamed field, a new tool), reconnect the server in the client (in Claude Code: `/mcp`, then
reconnect, or restart the session); otherwise the model keeps calling the old contract. The server's advertised
version (`alphadeck` `2.5.0`) changes with its contract: a tool or prompt added or removed, or a description
whose meaning changes, bumps the MINOR. A client sees the version at `initialize`; a running Claude Code session keeps
the schemas it loaded, and a fresh session reads the new ones.

## Tests

`backend/tests/agent_tools/`: `test_views.py` (shape and budget per view, every cursor walked to the end),
`test_server.py` (the official SDK client in-process against an httpx `MockTransport` fake API, plus one REAL
stdio subprocess against a loopback fake API: 14 tools, 7 prompts for a plain caller, read-only, output schemas), `test_dashboards.py`, `test_research.py` (the four research views:
sizes, cursor walks, narrowing, rejections, the wire-key guard, purity), `test_contract.py` (every registered contract),
`dump_contract.py` (prints the sha256 of the tools and prompts listing: the proof a refactor changed no contract),
`test_resolve.py`, `test_client.py`, and the prompt call plans through the real stdio client: `test_morning_brief.py`,
`test_board_review.py`, `test_explain_call.py`, `test_ticker_check.py`, `test_ratify_queue.py`, `test_discovery_review.py`,
`test_compare_rotation.py` (with the
argument-rule rejections), `test_splits_review.py` (the steward's call plan, and a plain stdio caller not offered the prompt), `test_admin_views.py` (the two steward tools: default replies, every section, the rejections, the budget), plus
`test_skill.py` (the skill equals the prompt files; its size and frontmatter bars), `test_principal.py` (every tool read-only with a scope, the scope gate, the stdio principal, the window, the grep guards: no tenant, no `db` or `repositories` import) and `test_hosted.py` (the hosted endpoint against the real API and database: two users and a steward, the 401 shapes, the RFC 9728 document, 50 interleaved calls, the steward reads against a seeded split, no redirect). The route the list reads is tested against the database in
`backend/tests/app/test_theses_with_call.py`. Fixtures are synthetic (`Thesis 01`, `TK01`): no card or company
name from a live card.

## History

From 2026-11-02 to 2026-11-06 a DOM-capture MCP server served agents: a capture endpoint on the API froze a
dashboard's responses, a headless Chromium rendered them through a separate frontend render page and the agent
read the rendered DOM. Phase 4 (PR #506) retired it whole (the server package, the endpoint, the renderer, the
components' instrumentation and the Chromium CI job) because an agent needs the platform's objects, not its
pixels; the data-first server here replaced it in Phases 1 to 3 (PRs #503, #504, #505). Version 2.5.0 (Phase 3 of the
per-user plan) added the two steward reads, the scope-gated `splits-review` prompt and the client's 403 to `forbidden`
mapping.
