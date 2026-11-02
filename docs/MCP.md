# Agent access and MCP

Status: implemented and locally validated, 2026-11-01. Stage: MONITOR, with extension points for SCORE and the research stages.

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

## Contracts

- Capability: stable slug and version, metadata, availability flags, defaults, settings schema, and the
  explicit subset of settings forwarded as backend query parameters.
- Capture context: effective as-of, market clock, public/record clock, public cutoff, decision day,
  countdown origin, effective scope, and limitations.
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
| `inspect_dashboard_item` | Inspect a stable target or its captured evidence within a frozen view. |
| `render_dashboard` | Obtain an overview or target image from the same captured output. |

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

The API, frontend, and local MCP process are three separate processes. The API must include this change;
an older server answers 404 for `/dashboards/capture`. No database URL or vendor/model API key is needed
in the MCP process. The API uses its existing configuration. Do not point experimental server processes
at a writable production database.

From `backend/`, install the optional local dependencies and browser:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
.venv\Scripts\python.exe -m playwright install chromium
```

From `frontend/`, run `npm ci`, then either `npm run dev -- --host 127.0.0.1` (port 5173), or
`npm run build` followed by `npm run preview -- --host 127.0.0.1 --port 5173` for the built renderer.
`mcp-render.html` is a separate build entry; it shows no dashboard without an injected capture.

Configure a compatible MCP client to launch the following command with its working directory set to
`backend/` (use absolute executable/working-directory paths in the client's configuration):

```powershell
.venv\Scripts\python.exe -m agent_access --backend-base http://127.0.0.1:8001 --ui-base http://127.0.0.1:5173
```

The equivalent installed executable is `alphadeck-mcp`. Base origins can also be configured through
`ALPHADECK_MCP_BACKEND_BASE` and `ALPHADECK_MCP_UI_BASE`. Only explicit loopback origins are accepted;
credentials, URL paths, redirects, and remote hosts are rejected. The local process speaks stdio, so
start it through the client rather than expecting an HTTP MCP endpoint. `--trace` emits request IDs,
operation names, durations, and outcomes to stderr, never captured content. The SDK is pinned to 2.3.0;
an official SDK stdio client is part of verification. Other clients must support a compatible protocol.

The default complete request deadline is 180 seconds (configurable from 1 to 300). Backend reads time
out after 130 seconds and browser readiness has its own bound. The API checks a cooperative 120-second
work budget between expensive reads and enforces a 120-second per-SQL-statement timeout. Disconnecting
the client is not a promise to interrupt a synchronous database calculation immediately.

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
   `source_ref_id` with its `target_id` to `inspect_dashboard_item`. Source JSON uses the existing bounded
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
JSON references. Associate those IDs with rendered targets through the capture binding helpers.
Validate every pointer, reject duplicate IDs or excessive relationships, and keep new domain joins
inside the application adapter. Remote URLs never become capture destinations.

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
