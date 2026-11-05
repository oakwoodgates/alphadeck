# Agent access and MCP

Status: dashboard access and portable workflow recipes implemented, 2026-11-02; agent ergonomics (lean
inspect replies, pointer/outline/batch reads, Board evidence references, recipes 1.1.0) 2026-11-05.
Stage: MONITOR, with extension points for SCORE and the research stages.

## Approved scope

Build a reusable, read-only agent-access framework and deliver Board, Rotation Map, and Narrative Index
through a local MCP server. Agents discover views, choose settings, read exact displayed observations,
inspect items, and obtain images without writing database queries or scripts. The same captured
application responses feed the React view, structured observation, and image.

The operator approved this scope in the planning chat on 2026-11-01. Authentication for remote access,
mutation tools, an autonomous agent runtime, and sharing an existing browser tab remain later work.
All rules in `CLAUDE.md`, `DASHBOARDS.md`, and `INVARIANTS.md` continue to apply.

After the Board client pilot, the operator approved Cockpit and evidence drill-downs: follow a Board
item into the actual Cockpit, inspect its call/member/source details, and obtain images from captured
application responses. Cockpit is a detail surface, not another tile in the dashboard menu. The
existing tool names remain compatible; the capability catalog also describes detail surfaces.

The operator subsequently approved three portable read-only workflows: Board review, call explanation,
and a two-date Rotation Map comparison. The local server supplies versioned instructions through MCP
prompts and workflow discovery/lookup tools. The connected client's agent executes them. This adds no
model invocation, background runner, scheduled job, or workflow launcher to the alphadeck UI.

Privacy requirement reaffirmed by the operator during the build: thesis names and private platform data
must stay out of commits and PR content. Tests use synthetic data. Live validation captures remain in
ignored local storage; reports contain counts and outcomes, not private payloads. Review tracked changes
and proposed PR text for accidental disclosure before publication.

## Architecture decisions

1. MCP is an adapter over application capabilities. It does not own domain computations, credentials
   for PostgreSQL, signal logic, or a model connection. The local Python process uses the official MCP
   SDK and calls an allowlisted HTTP read endpoint. Future clients and agent runtimes use that boundary.
2. Each dashboard owns serializable capability metadata beside its registration. The frontend and a
   generated server catalog share it. Unsupported dashboards are discoverable but explicitly unavailable
   for reading/rendering. JSON schemas describe the supported controls; unknown settings fail closed.
3. `GET /dashboards/capture` lives in the dashboard router package to preserve its leaf boundary. It
   collects canonical application output in one read-only repeatable-read transaction, bypassing the
   shared computation cache. Calls use `record=False`. The request pins the market clock once.
4. A database snapshot is not a snapshot of external run/repair artifacts. Capture reports that limitation,
   observes generation before/after, and refuses a capture when that generation changes. It does not
   claim unchanged generation detects every possible file edit.
5. Temporary observation snapshots are bounded by age, count, and memory. They preserve captured output,
   not a new database history axis. An expired ID never silently resolves to current data.
6. The separate `mcp-render.html` entry renders the real React dashboard components using frozen GET
   responses. Existing TypeScript presentation logic determines formatting, sorting, and explanations.
   Observation text/tables/labels are extracted from the rendered view, with clipping/truncation stated.
   Target re-renders compare the UI asset fingerprint and semantic observation to the original capture;
   a changed frontend requires a new snapshot rather than returning a mismatched image.
7. Rendering uses a fresh browser context, blocks external requests and mutations, and serves only
   captured API responses. No archive/restore action is enabled in a capture. Browser readiness includes
   completed expected queries and settled layout, not just an arbitrary sleep.
8. Initial access is trusted local deployment visibility, not authenticated tenant authorization. Existing
   Board metadata and default-tenant aggregate limitations are reported. Remote access requires its own
   authoritative authentication and scoping work before enablement.
9. Following an advertised navigation link starts a new read-only capture at the parent's selected date.
   It is explicitly a fresh database transaction, with parent linkage in the result. A temporal-context
   guard rejects changes to market-night, clock, or countdown semantics; it does not freeze the database
   between captures. Existing snapshots remain frozen and are never silently refreshed.
10. Backend adapters own navigation destinations and captured-JSON reference pointers. The frontend
    associates their opaque IDs with actual rendered targets. MCP follows only IDs advertised by both
    the stored target and its capture envelope. Evidence inspection never fetches an external source URL.
11. Workflow recipes have one packaged source: Markdown instructions and JSON metadata under
    `backend/agent_access/recipes/`. MCP prompts and tool lookup use the same registry and rendered
    instructions. Retrieving a recipe performs no capture, browser work, database read, or model call.
    The client owns execution and interpretation; a recipe is not an executable workflow guarantee.

## Contracts

- Capability: stable slug and version, metadata, availability flags, defaults, settings schema, and the
  explicit subset of settings forwarded as backend query parameters.
- Capture context: effective as-of, market clock, public/record clock, public cutoff, decision day,
  countdown origin, effective scope, and limitations. The temporal guard hashes every context field
  except the per-surface `limitations`, so a followed capture's limitations can differ (the Cockpit
  adds its own).
- Frozen response: `method`, `path`, canonical query values, HTTP status, and JSON body.
- Observation: snapshot ID, requested settings, source context/consistency, visible text, structured
  tables/notes, identifiable targets, and explicit completeness/visibility information.
- Navigation: an opaque link ID on a target and its backend-owned capture destination; a follow result
  identifies its parent and its independent capture. Captured source references identify bounded JSON
  within an existing snapshot and distinguish call provenance from display basis and authored metadata.
- Errors: stable machine-readable category, actionable explanation, and retryability. No successful
  response conceals an unsupported operation, missing snapshot, or partial render failure.

## Tools

| Tool | Purpose |
|---|---|
| `list_dashboards` | Discover dashboards and their implemented capabilities. |
| `describe_dashboard` | Learn controls, definitions, defaults, date behavior, and limitations. |
| `read_dashboard` | Capture a requested view, or follow an advertised link into a fresh related capture. |
| `inspect_dashboard_item` | Inspect a stable target or its captured evidence within a frozen view: one field by pointer, a value's outline, or a batch of one target's references. |
| `render_dashboard` | Obtain an overview or target image from the same captured output. |
| `list_workflows` | Discover versioned recipes and their inputs, defaults, and required surfaces. |
| `get_workflow` | Read a recipe template, or validate its inputs and obtain instructions for client execution. |

Tools are read-only. Transport annotations are descriptive; HTTP allowlists, a read-only database
transaction, and browser request restrictions enforce the boundary.

## Build increments and acceptance

1. Contracts and catalog: registration is validated; adding an adapter has a documented path.
2. MCP infrastructure: a real SDK client discovers/invokes tools; settings, errors, limits, expiry, and
   cancellation are tested; diagnostics never contaminate stdio protocol output.
3. Board and Rotation Map: application/API parity, no appended calls or domain mutations, correct
   historical/public-clock behavior, exclusions and data flags preserved.
4. Visual observation: actual React views use identical frozen payloads for text and image; state,
   ordering, SPY comparison, and expanded content agree; failures and clipped content are explicit.
5. Narrative Index and release: chart-heavy integration proves extensibility; current-only semantics
   are tested; generated contracts and repository checks pass; real client/browser workflows are verified.
6. Cockpit and evidence: Board navigation reaches the actual Cockpit; the call, member details, all
   source references, and computation detail are inspectable. Fresh navigation and frozen evidence
   have explicit boundaries. Temporal drift, invalid links/references, capture-safe controls, and
   historical metadata caveats are verified through synthetic and live development workflows.
7. Portable workflows: the three recipes are discoverable through both prompts and tools; supplied
   arguments are validated; prompt and tool retrieval agree. Synthetic scripted clients exercise their
   data/evidence paths and recovery boundaries. These tests establish protocol and data availability,
   not model reasoning quality or completion in every third-party client.

## Growth path

Additional research/SCORE capabilities reuse context, provenance, policy, and output contracts. Remote
transport comes with authentication and scope enforcement. Proposed edits are distinct from executed
commands; later commands require actor provenance, version checks, idempotency, and an audit trail.
Agent-authored proposals never become operator-ratified facts merely because an agent submitted them.
An agent runtime is a consumer of these capabilities, responsible for reasoning and workflow checkpoints.

## Coordination

The primary chat owns scope, decisions, integration, and acceptance. Astra owns backend capture and
correctness review. Sol assignments own the frontend/metadata and the MCP process/browser integration,
with separate file ownership. Agents report changed files, validation evidence, and unresolved issues.
Local build checkpoints stay under ignored `docs/temp/`; this document retains the architectural decisions.

## Install and run

The API, the frontend, and the local MCP process are separate processes. The MCP process reads the API's
read-only capture endpoint and renders the frontend's `mcp-render.html` in a local headless Chromium. It
needs no database URL and no vendor or model API key; the API uses its existing configuration. The API
must include the capture endpoint: an older server answers 404 for `/dashboards/capture`. Do not point
experimental server processes at a writable production database.

### Install

From `backend/` of the checkout that runs the server (normally the main checkout), install the optional
dependencies, then the browser, with that checkout's Python:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
.venv\Scripts\python.exe -m playwright install --only-shell chromium
```

The browser is about 120 MB, installed once per machine per Playwright version (the `mcp` extra pins
it). The server only renders headless, so the headless shell is all it needs. Without it, a read fails
at once with `browser_not_installed`; installing it fixes the next read without restarting the server.

### Choose the stack it reads

| Target | `--backend-base` | `--ui-base` | Who serves the renderer |
|---|---|---|---|
| Built-in defaults (dev loop) | `http://127.0.0.1:8001` | `http://127.0.0.1:5173` | A Vite server you run from `frontend/` (below). |
| Prod stack | `http://127.0.0.1:8000` | `http://127.0.0.1:8080` | Prod's frontend image already serves it; no frontend process is needed. |
| Dev stack | `http://127.0.0.1:8001` | `http://127.0.0.1:8081` | The dev frontend image, only once it is rebuilt from a commit that includes the renderer. |

The defaults target DEV: the dev stack's API on 8001 and a Vite server on 5173. For that Vite server, run
`npm ci` from `frontend/`, then either `npm run dev -- --host 127.0.0.1` (port 5173), or `npm run build`
followed by `npm run preview -- --host 127.0.0.1 --port 5173` for the built renderer. `mcp-render.html`
is a separate build entry; it shows no dashboard without an injected capture. A dev frontend image built
before the renderer answers `/mcp-render.html` with the ordinary app page, so reads through 8081 fail
with `renderer_not_served` until that image is rebuilt.

### Register with Claude Code

The repository's `.mcp.json` declares two project servers with the arguments above: `alphadeck-prod`
and `alphadeck-dev`. Both launch `${ALPHADECK_MCP_COMMAND}`. Set that variable once, as a user
environment variable, to the absolute path of the MAIN checkout's console script:

- Windows: `<main checkout>\backend\.venv\Scripts\alphadeck-mcp.exe`, for example
  `[Environment]::SetEnvironmentVariable('ALPHADECK_MCP_COMMAND', '<path>', 'User')` in PowerShell,
  then restart Claude Code so it sees the variable.
- Linux: `<main checkout>/backend/.venv/bin/alphadeck-mcp`, exported from your shell profile.

The registration uses the console script, not `python -m agent_access`, so a session's working directory
can never shadow the installed package (Claude Code does not document a stdio server's working
directory). Approve the two servers when Claude Code first asks, then check them with `claude mcp list`.
If the variable is unset, `claude mcp list` shows the literal `${ALPHADECK_MCP_COMMAND}` and
`✗ Failed to connect` for both servers.

Builder agents cannot use `alphadeck-prod`: `.claude/agents/builder.md` denies `mcp__alphadeck-prod__*`.
Builds have no reason to read prod; `alphadeck-dev` stays available to them.

### Other MCP clients

Configure the client to launch the console script with absolute paths. The generic form, with the
built-in defaults spelled out:

```powershell
alphadeck-mcp --backend-base http://127.0.0.1:8001 --ui-base http://127.0.0.1:5173
```

`.venv\Scripts\python.exe -m agent_access` with the same arguments is equivalent when the client's
working directory is `backend/`. Base origins can also be configured through
`ALPHADECK_MCP_BACKEND_BASE` and `ALPHADECK_MCP_UI_BASE`. Only explicit loopback origins are accepted;
credentials, URL paths, redirects, and remote hosts are rejected. The local process speaks stdio, so
start it through the client rather than expecting an HTTP MCP endpoint. `--trace` emits request IDs,
operation names, durations, outcomes, and each reply's size in characters (`tool=… ok=… chars=…`) to
stderr, never captured content, so a workflow's cost is measured, not estimated. The SDK is pinned to 2.3.0;
an official SDK stdio client is part of verification. Other clients must support a compatible protocol.

The default complete request deadline is 180 seconds (configurable from 1 to 300). Backend reads time
out after 130 seconds and browser readiness has its own bound. The API checks a cooperative 120-second
work budget between expensive reads and enforces a 120-second per-SQL-statement timeout. Disconnecting
the client is not a promise to interrupt a synchronous database calculation immediately.

### Errors that mean the setup, not the data

Before each `read_dashboard` capture, the server checks the UI and the browser, so a broken setup fails
in seconds instead of after a full capture. The UI check (a GET of `/mcp-render.html`, 5-second
timeout) runs on every read. The browser check launches and closes headless Chromium; a success is
remembered for the server's lifetime, and a failure is checked again on the next read. A target
`render_dashboard` reports the same launch codes.

| Code | When it happens | Retryable | Fix |
|---|---|---|---|
| `browser_not_installed` | Chromium for Playwright is not installed on this machine. | No | Run `python -m playwright install --only-shell chromium` with the MCP server's Python. No restart. |
| `ui_unavailable` | The `--ui-base` origin refuses the connection, times out, or answers `/mcp-render.html` with a 5xx. | Yes | Start that frontend, or point `--ui-base` at a running one. |
| `renderer_not_served` | The `--ui-base` origin answers `/mcp-render.html` with a redirect, a 4xx, or a 200 page that isn't the renderer: a frontend built before it, or a different app. | No | Rebuild that frontend from a commit that includes the renderer, or point `--ui-base` at one that serves it. |
| `browser_unavailable` | Chromium is installed but failed to start, or failed while rendering a page. | Yes | Retry. If it repeats, read stderr: a launch failure logs the first line of Playwright's message. |
| `render_timeout` | The dashboard did not become ready within the rendering bound (40 seconds per step). | Yes | Retry. If it repeats, check that the frontend answers quickly and serves current assets. |

The capture itself, which runs after those checks pass, reports two more codes about the API:

| Code | When it happens | Retryable | Fix |
|---|---|---|---|
| `backend_unavailable` | The `--backend-base` API refuses the connection or doesn't accept it in time ("The Alpha Deck API at <origin> isn't answering"), or drops the connection mid-capture. A capture that is merely slow is `capture_timeout`. | Yes | Start the API, or point `--backend-base` at a running one. If it drops connections, check the API's logs (a database restart does this). |
| `capture_timeout` | The API accepted the request but the capture didn't finish within the client's 130 seconds; the API's own work budget is 120 seconds. | Yes | Retry later. If it repeats, check the API's load and its database; a large deployment's Board capture takes about 80 seconds. |

## Start a workflow from your agent client

After connecting the local MCP process, use ordinary chat. For example:

> Run alphadeck's Board review workflow for the current market night. Show the first five Decision Queue items.

> Run alphadeck's call-explanation workflow for the card I select.

> Run alphadeck's Rotation Map comparison between 2026-10-23 and 2026-10-30, grouped by card.

The agent discovers the available recipes with `list_workflows`, retrieves one with `get_workflow`,
and follows its instructions using the dashboard tools. The report appears in that client's chat.
The client needs tool execution enabled and a running API/renderer for the actual dashboard reads.
Recipe discovery and retrieval themselves work while those two services are stopped.

| Workflow ID | Version | Inputs | Report |
|---|---|---|---|
| `board-review` | 1.1.0 | Optional `asof`; `max_items` defaults to 5 (1–20). | Board/queue coverage and a shortlist in displayed Decision Queue order, with reasons and data caveats. |
| `explain-call` | 1.1.0 | Required `thesis` (name or UUID); optional `asof`. | Platform call and conditions, exact captured evidence, unresolved gaps, and separately labeled agent interpretation. |
| `compare-rotation` | 1.1.0 | Required `start_date` and `end_date`, earlier first; `group_by` defaults to `card`. | Two dated readings with percentage-point differences, breadth/coverage, membership and flag changes. |

Version 1.1.0 (2026-11-05) reads evidence through references instead of whole responses. `board-review`
lists the whole Decision Queue, checks its order from each queue target's `theses[<i>]` label, and makes
ONE batch read per shortlisted item; it falls back to the captured responses, and calls the review
partial, only when an item lacks a reference. `explain-call` confirms the thesis from its card's
`/theses` row and reads the Cockpit's references in batches, with `outline` and `pointer` for the whole
computed call. `compare-rotation` only states where the response listing comes from under the lean
inspect defaults.

Omitting `asof` uses the API's current market night when the agent captures the dashboard, not the
operator's wall-clock date. The call workflow resolves the thesis from the captured Board and asks
for clarification on ambiguous names. Runtime selections remain request data, never recipe content
committed to the repository.

The lookup distinction is intentional:

```json
{"name":"explain-call"}
```

This returns a template with `ready: false`, its input schema, and instructions. It lets an agent
discover the required inputs without guessing a selection. To instantiate a recipe, supply an
`arguments` object, including an empty object when defaults suffice:

```json
{"name":"board-review","arguments":{"max_items":5}}
```

`ready: true` means that the inputs passed validation and the recipe is ready for the client to
execute. It does not mean a dashboard was read, a report was produced, or the running services passed
a health check. Invalid dates, unknown fields, missing required inputs, and reversed comparison dates
fail explicitly. Results include the recipe version, normalized arguments, and the common evidence
and recovery instructions.

Clients that support MCP prompts can instead discover the same three IDs with `prompts/list` and
request instructions with `prompts/get`. Prompt arguments are strings on the wire (for example,
`max_items: "5"`); the registry validates and normalizes them to the same values used by tool lookup.
The client may expose a prompt picker, an argument form, or a slash command. There is no universal
slash-command syntax, and alphadeck does not install a client-specific command or `SKILL.md`.

The instructions require evidence references, capture context, pagination/coverage disclosure,
explicit recovery from expired snapshots, and separation of platform calculations from agent
interpretation. Current historical-metadata and roster-fallback limitations still apply. No recipe
records an operator decision, edits a thesis, fetches external source documents, or runs unattended.

## Maintain a workflow recipe

Each recipe has matching `<id>.json` metadata and `<id>.md` instructions in the packaged recipes
directory. Its instructions include evidence and recovery rules. Metadata declares the ID, version, title,
description, stage, input schema, defaults, and required dashboard surfaces. Review and version
changes to the instructions alongside their synthetic scenarios. Keep names, examples, and fixtures
synthetic; private reports and captures belong only in ignored local storage.

The registry validates the bundled assets and arguments before exposing instructions. Markdown is
read as text; runtime arguments are supplied as a separate JSON data block, never evaluated as a
template or used to select a file. Prompt and tool adapters must continue using this single source.
Hard access/consistency controls remain in the tools and capture service. A later runtime can consume
these recipes, but scheduling, persisted runs, retries, and report validation need explicit code.

## Using a captured view

1. Call `list_dashboards`, then `describe_dashboard` to obtain current settings and support flags.
2. Call `read_dashboard` with a slug and settings. Omit `asof` for the current server night.
3. Keep its `snapshot_id`. Use `inspect_dashboard_item` to page advertised targets or inspect captured
   source JSON. Target IDs are opaque; copy them from the response rather than constructing them.
4. Call `render_dashboard` with the same ID for the stored overview, or an advertised target ID for a
   bounded target image. Check clipping metadata. To change view settings, obtain a new snapshot.

Snapshots live only in the MCP process: ten-minute expiry, eight retained captures, and a 64 MiB
serialized-content/image storage budget. Python/browser working memory can exceed that serialization
budget. Restarting the process or evicting a capture invalidates its ID; IDs confer no authorization.
Tool results cap structured output at 128 KiB and expose truncation/pagination. Exact source JSON is
available in bounded text chunks; concatenate them before parsing a large response. Overview images
cover a 1440 by 1080 viewport, and target images report visible intersections with scrolling/clipping
ancestors. A screenshot does not imply the whole dashboard fits in that image.

### Lean replies (tool-result `schema_version` "2")

Every tool result carries `schema_version: "2"` (2026-11-05). Version 2 changed only the inspect reply:

- **Compact header.** `inspect_dashboard_item` returns `snapshot_id`, `dashboard`, `asof`, `captured_at`,
  `expires_at`, `settings` and `context_hash` (the hash the temporal guard compares) instead of the full
  context. `read_dashboard` and `render_dashboard` keep the full `context` and `consistency`; take them
  from the read.
- **No listings on a data read.** A call that reads captured JSON (`source_response_index`,
  `source_ref_id` or `source_ref_ids`) returns no target page and no response listing unless it passes
  `limit` / `source_limit`. `total` and `source_total` still count them, and `0` is accepted everywhere.
  A call that reads no data keeps the old defaults (20 targets, 20 listed responses).
- **Lean target listings.** A listed target's `source_refs` entries carry `id`, `label` and `category`;
  the constant `kind` (`captured_json`) is left out of listings. The stored target keeps it, and
  resolving a reference reads the stored target, so validation is unchanged.
- **Read page budget.** `read_dashboard`'s target page takes the room the rest of its reply leaves under
  100,000 bytes, between 16,000 and 48,000 bytes of targets; an inspect page of targets has 56,000. A
  target's identifiers and references are never split across pages; only its DOM details truncate.

### Reading evidence: pointer, outline, batch

`inspect_dashboard_item` reads captured JSON five ways, all from the frozen capture only.

| Read | Arguments | Returns |
|---|---|---|
| A whole response | `source_response_index` | `source_data`: the response's JSON text, paged by `data_offset` / `data_limit` (at most 24,000 characters a page). |
| One field | `source_response_index` + `pointer` (RFC 6901, e.g. `/verdict`; `""` = the whole response) | `source_data`: only that value, paged the same way. An unresolvable pointer is `unknown_pointer`. |
| A value's shape | `outline: true` with `source_response_index` (optionally `pointer`) or `source_ref_id` | `source_data` with `encoding: "json_outline"`: each child's key or index, type and size in characters, no values, 200 per page (`outline_offset` / `next_outline_offset`). |
| One reference | `target_id` + `source_ref_id` | `source_data` with the reference's `reference_id`, `category`, `label` and `pointer`. |
| Several references | `target_id` + `source_ref_ids` (1–32 unique IDs that target advertises) | `source_data_items`, in request order. |

A batch item is `{reference_id, category, label, pointer, response_index, path, text, total_characters,
truncated, next_offset}`; `text` is the value's JSON text, at most 4,000 characters, and every item
together stays within what the rest of the reply leaves under 112,000 bytes. Each item's frame is
reserved first, so a batch never drops an item: one cut by either bound says so (`truncated: true`,
`next_offset`), and continues with a single `source_ref_id` + `data_offset: next_offset` read, which
serializes the value identically. One ID the target does not advertise fails the whole call with
`unknown_reference`. `source_ref_ids` needs `target_id` and excludes `source_ref_id`,
`source_response_index`, `pointer`, `outline`, `outline_offset`, `data_offset` and `data_limit`.

The pattern for an agent: list a target's references (they ride on every target listing), batch-read the
ones it needs, outline a large value before reading it, then read single fields by pointer.

The Cockpit hides the call rail when the member panel fully covers it, keeping observation visibility
consistent with the painted UI. Target rendering rejects hidden targets and detected overlay coverage
after scrolling. The additional occlusion check is bounded sampling, explicitly reported in image
metadata; it cannot certify every pixel. `cropped` describes geometric clipping, not an exhaustive
occlusion guarantee. Choose an exposed target or another captured view when rendering is rejected.

Current support: Board, Rotation Map, and Narrative Index implement observation, drill-down, and
images. Cockpit adds a detail surface with the same capabilities. The other registered dashboards are
discoverable with explicit unsupported capabilities.
Current-only views reject historical date selection when enabled. Invalid selected groups, cards, and
chart sessions fail rather than silently displaying a different requested view.

## Follow a card and inspect its evidence

1. Read Board and inspect a target's advertised `links`. Follow one by calling `read_dashboard` with
   `from_snapshot_id`, `target_id`, `link_id`, and `freshness: "fresh"`. This form is exclusive with a
   direct `dashboard`/`settings`/`asof` request; the destination and selected date come from the capture.
2. Keep the new Cockpit `snapshot_id`. Its origin identifies the parent capture; the two captures do
   not share a database snapshot. If the temporal context changed, read the parent again before following.
3. Inspect the Cockpit's call, member, trigger, and source targets. For exact evidence, pass an advertised
   `source_ref_id` with its `target_id` to `inspect_dashboard_item`, or several of that target's IDs as
   `source_ref_ids`. The Cockpit's `call_result` reference is the whole computed call: outline it first
   (`outline: true`), then read fields by `pointer` on its `response_index`. Source JSON uses the bounded
   `data_offset`/`data_limit` paging; concatenate chunks before parsing a large value.
4. Render the Cockpit overview or an advertised target using that same child snapshot. The actual UI
   discloses source references and their stored computation detail. External URLs are reference links,
   and their documents have not been retrieved or independently verified by this operation.

Cockpit preserves the application's current semantics. Authored narrative/evidence/catalysts/kill
criteria, roster/identity, feed metadata, position, and decision history are current metadata. Dated
call/scoring/display facts follow their canonical as-of readers; a historical call's roster can differ
from the displayed current roster. Capture reports this limitation visibly and in its context. A
display indicator or authored evidence reference is not automatically an input to the call.

Capture mode preserves decision history while disabling decision writes, undo, spine editors, and
exports. Normal interactive application controls retain their existing behavior. This increment adds
no signal rules, agent-authored conclusions, authentication, remote transport, or action tools.

## Board evidence references

The Board advertises each active card's evidence as captured-JSON references
(`capture_adapters/board.py`, `card_references`), so an agent reads a verdict or a flag without reading
the 50K call or the 160K flag list. Per active card, in `/theses` order:

| Category | Label | Pointer | What it is |
|---|---|---|---|
| `thesis_summary` | `theses[<i>]` | `/theses` → `/<i>` | The card's row in the captured `/theses?include_archived=true` list. `<i>` is its position, so ascending labels confirm the queue keeps `/theses` order. |
| `call_result` | the field name | `/theses/{id}/call` → `/<field>` | One per decision field present in the served call: `state`, `verdict`, `conviction_grade`, `confirmation_grade`, `entry_grade`, `armed_members`, `missing`, `counter_case`, `risk_signals`, `arm_until`, `exit_by`. A null field is referenced; an absent one is not. |
| `data_flag` | the member's ticker (else its security ID) | `/data-flags` → `/members/<j>` | Every flag entry whose `theses` holds the card, `call_affected: false` ones included. An entry two cards hold is ONE reference both cards advertise. |

An archived card has no call and gets no reference. `frontend/src/board/Board.tsx` binds each card's
references to its Decision Queue target and its lifecycle-card target; both list the same IDs. The
categories keep call output, card metadata and data quality distinct; nothing is recomputed and no call
logic is touched.

## Add a dashboard

1. Keep metadata/defaults/settings schema/response-path templates in its `capability.json` and reuse that
   metadata in `entry.ts`. Advertise only implemented capabilities. Response templates allow literal paths
   and UUID placeholders; they never authorize arbitrary endpoint requests.
2. Add a module under `app/routers/dashboards/capture_adapters/` exposing `SLUG`, `INCLUDE_ARCHIVED`, and
   `read(conn, request)`. Reuse canonical read/compute functions and wire models. Observe the supplied
   date context and work budget; return frozen responses. Never commit, ingest, notify, or call an LLM.
3. Add capture-state initialization and readiness/target markers to the actual React component. Reuse its
   presentation functions. Supply precise unsupported-state errors; preserve ordinary user interaction.
4. Run `npm run gen:mcp`; regenerate OpenAPI and TypeScript types if the API schema changed. The three
   pilot query controls are explicit in the HTTP endpoint; new kinds of backend controls require an
   intentional API-contract extension, while adapters using existing controls require no dispatcher edit.
5. Add synthetic tests for API parity/read-only behavior, date rules, missing data, view settings, and
   actual browser rendering. Verify adding/removing the adapter never changes MCP transport code.

For a detail surface, keep its capability beside the owning UI rather than adding a dashboard menu
entry; Cockpit is the first example. A thesis-scoped adapter declares `THESIS_SCOPED` and accepts the
explicit `thesis_id` query field. An optional `relationships(responses, request)` hook receives the
final frozen response array, including clock at index zero, and returns navigation recipes and exact
JSON references. Associate those IDs with rendered targets through the capture binding helpers in
`frontend/src/mcp/bindings.ts` (`useCaptureBindings`): `cockpitLinks(thesisId, selectedName)` for
advertised Cockpit links, `sourceRefs(path, pointer)` for one exact reference, and
`sourceRefsAt(selectors)` for several, where each selector is a response `path` plus an optional
`pointer` (omitted, it takes every reference the backend advertised into that response), in selector
order, each ID once. The Board uses `sourceRefsAt` for a card's `/theses` row, its whole call and its
flag entries. Validate every pointer, reject duplicate IDs or excessive relationships, and keep new
domain joins inside the application adapter. Remote URLs never become capture destinations.

## Known limits

Measured on synthetic captures at real scale (`tests/agent_access/board_scale.py`: 66 active cards, 42
armed, calls of about 49K characters, `/theses` about 90K, `/data-flags` about 163K with each flag entry
held by about 1.3 cards). Numbers are MEASURED with the tools' own reply encoding unless marked.

- **A 5-item `board-review` costs about 215K characters in 9 calls**, over the operator's 120K budget
  (target: under 100K in about 15 calls). `tests/agent_access/test_board_review_scale.py` runs it
  through the real server: get_workflow 6.7K, describe_dashboard 1.1K, `read_dashboard` 79K (an
  observation preview of about 28K, calibrated to prod, and 23 targets), one inspect page of the rest of
  the queue 39K, and five batch reads 15–20K each (88K). Listing the 42 queue targets with their 13–21
  references each costs about 119K; a batch is dominated by `armed_members` and `risk_signals` (both cut
  at 4,000), about 4.6 flag entries, and the per-item frame. The budget assertion is kept at 120K and
  marked as an expected failure until a design meets it; it is never loosened.
- **Board references and the relationship cap.** A capture holds at most 2,048 references. At 100 cards
  (the capture's card limit), 12 per card leaves room for about 848 distinct flag entries; beyond that
  the Board capture returns 413, and nothing is truncated.
- **One card with very many flag entries.** A target's references are never split across pages, so a
  target whose references alone exceed a page's budget makes that page start return `output_too_large`.
  With lean listings that is about 176 flag entries on one card at the read page's 16,000-byte floor
  (reached only when the rest of the read reply is large), about 562 at its usual 48,000, and about 658
  on an inspect page.

## Verification commands

From `frontend/`: `npm test`, `npm run build`, and `npm run check:mcp`. From `backend/`, use the checkout's
Python to run `-m pytest tests/agent_access tests/dashboards/test_capture.py tests/dashboards/test_capture_cockpit.py`, `-m ruff check .`, and
`-m black --check .`. Existing pytest configuration guards and selects a dedicated test database.

Real Chromium tests are opt-in after building `frontend/dist` and installing Chromium:

```powershell
$env:ALPHADECK_MCP_BROWSER_TESTS = '1'
.venv\Scripts\python.exe -m pytest tests/agent_access -q
```

These fixtures are synthetic and serve their own temporary local frontend. Live validation artifacts
belong under ignored `data/`, and must never be attached to a PR. The capability catalog drift check is
part of frontend CI; backend CI installs the optional MCP dependencies to exercise the SDK/runtime tests.
A separate CI job builds the frontend and runs the synthetic Chromium integration tests.
