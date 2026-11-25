# Agent tools

A read-only MCP server that lets an agent (any MCP client) ask Alpha Deck's questions: what is armed and why,
explain this call, what is this company on, what did discovery find, what is left to set on a card. Its tools
return the platform's own objects, shaped from the API's JSON. It runs two ways. Hosted, it is mounted in the API
at `/mcp` when `ALPHADECK_PUBLIC_ORIGIN` is set, and each user signs in with their own token or OAuth: this is how
a client connects. As a local stdio process over the loopback API (`alphadeck-prod`, `alphadeck-dev`) it is a
developer tool. A client that cannot run MCP prompts reads the same guided reads with the `get_recipe` tool. Read
it before you add or change a tool, a prompt or the hosted endpoint, or wire up a client.

## Code

| Piece | Module |
|---|---|
| Server, tool and prompt registration, `VERSION`, `INSTRUCTIONS` | `backend/agent_tools/server.py` |
| The loopback API client (GET only), the 401 message | `backend/agent_tools/client.py` |
| Thesis and ticker resolution | `backend/agent_tools/resolve.py` |
| Pure views over the API's JSON, with their budgets | `backend/agent_tools/views/` (`dashboards/`, `research/`, `admin/`) |
| The size rule | `backend/agent_tools/budget.py` (`fit`) |
| Tool contracts; prompt table | `backend/agent_tools/tool_args.py`; `backend/agent_tools/prompt_args.py` (`PROMPTS`, `bind_values`) |
| Prompt texts | `backend/agent_tools/prompts/*.md` |
| Recipes: the one render, the one scope rule, the tool | `backend/agent_tools/server.py` (`render_prompt`, `prompt_held`, `get_recipe`); `backend/agent_tools/views/recipe.py` |
| Error codes | `backend/agent_tools/errors.py` |
| The caller's scopes | `backend/agent_tools/principal.py` |
| The hosted endpoint; the nightly window | `backend/agent_tools/hosted.py`; `backend/agent_tools/window.py` |
| Skill sync | `backend/agent_tools/skill_sync.py` |

The shape is `client -> resolve -> view -> fit`. Each tool asks `client_for()` for its API client: the stdio
server hands back its one loopback client; the hosted endpoint builds one per call carrying the caller's token. A
tool declares a `Contract` (its sections, what each needs, its narrowings and settings); one `check` turns a bad
call into `invalid_arguments` naming what the tool takes, before any request.

## Rules every reply follows

- **Read-only.** `ApiClient` sends GET only, and every tool is annotated `readOnlyHint`. One exception spends
  something: `get_extract` calls `GET /workbench/securities/{id}/extract`, which charges one unit of a plain user's
  daily extract allowance (the steward is not counted; [AUTH](AUTH.md) "User-triggered fetches"), and may make
  EDGAR requests and, with `thesis`, one model call.
- **No tool computes** a state, grade, date or number: views are pure functions over the API's JSON, and the API's
  row order is the UI's (nothing re-sorts or ranks).
- **Every reply states its `asof` and `source`**: `record` is the nightly call of record, `live` a recompute or a
  live read. An omitted `asof` is the API's `current_night`; the server never derives a date.
- **The platform's wording:** `exit_by` is a signal-validity horizon, not a trade exit; setup strength (wire
  `confidence`) is not a probability; grade is call strength, never a size; display signals never feed the call.
- **Bounded.** Every list goes through the size rule below.

## Tools

`thesis` takes a UUID or an exact name (case-insensitive); `asof` is `YYYY-MM-DD`; `cursor` continues a paged list.
Each tool's description (served at `tools/list`) is its contract; this is the index.

| Tool | Arguments | Reads | Scope |
|---|---|---|---|
| `get_clock` | none | `/clock` | `read` |
| `list_theses` | `asof`, `include_archived`, `state`, `detail` (`queue`, `full`), `limit`, `cursor` | `/theses?with_call=true` | `read` |
| `get_thesis` | `thesis`, `detail` (`summary`, `full`), `cursor` | `/theses/{id}` | `read` |
| `get_call` | `thesis`, `asof`, `section`, `detail` (`brief`, `standard`), `cursor` | `/theses/{id}/call` | `read` |
| `get_data_flags` | `thesis` (omit for every card), `asof`, `cursor` | `/theses/{id}/data-flags` or `/data-flags` | `read` |
| `get_display_signals` | `thesis`, `tickers` (1 to 8), `asof`, `cursor` | `/theses/{id}/display-signals` | `read` |
| `get_decisions` | `thesis`, `cursor` | `/theses/{id}/decisions` | `read` |
| `lookup_ticker` | `t`, `asof`, `section`, `cursor` | `/dashboards/ticker-lookup/...` | `read` |
| `list_dashboards` | none | the registry in code (no I/O) | `read` |
| `read_dashboard` | `dashboard`, `asof`, `section`, `cursor`, `thesis`, `group`, `change`, `since`, `group_by`, `include_silent`, `rows`, `days`, `role` | `/dashboards/<slug>` | `read` |
| `get_triage` | `thesis`, `asof`, `section`, `segment`, `t`, `cursor` | `/workbench/theses/{id}/scored`, `/platform-values/theses/{id}` | `read` |
| `get_discovery` | `thesis`, `run`, `section`, `status`, `segment`, `t`, `cursor` | `/workbench/theses/{id}/runs[/{run}]` | `read` |
| `get_extract` | `t`, `thesis`, `section`, `fact`, `cursor` | `/workbench/securities/{id}/extract` | `read` |
| `get_onboarding` | `thesis`, `section`, `cursor` | `/workbench/theses/{id}/onboarding` | `read` |
| `get_recipe` | `name`, `arguments`, `cursor` | the package's prompt files (no API request) | `read` |
| `get_splits` | `section`, `t`, `reason`, `cursor` | `/admin/splits` | `steward` |
| `get_admin_status` | `section`, `cursor` | `/admin/status` | `steward` |
| `get_shared_facts` | `section`, `t`, `fact`, `pen`, `cursor` | `/admin/platform-values/queue`, `/admin/platform-values/{id}` | `steward` |
| `get_insider_holds` | `section`, `t`, `cursor` | `/admin/insider-holds` | `steward` |
| `get_admin_onboarding` | `section`, `cursor` | `/admin/onboarding` | `steward` |

**Dashboards.** `read_dashboard` reads the eight dashboards that have agent views (`views/dashboards/`), not every
registered dashboard. `list_dashboards` returns, per dashboard, its settings with their allowed values, its
sections as `{section: needs}` (`thesis`, `group` or nothing) and the narrowings it takes, so a first read never
costs a rejection. A setting the dashboard does not take, an unknown section or a section missing its `thesis` or
`group` is `invalid_arguments` listing the accepted values. A cold dashboard is computed on the read and says so
(`cache: computed`); a cache hit carries the cold time as `cold_compute_ms`. Dashboards are display only, never a
call input.

**Research reads.** `get_triage`, `get_discovery`, `get_extract` and `get_onboarding` are bounded views over the
Workbench's GETs; purity, sign-off, include and exclude stay in the Workbench. A share count or cash value is a
shared value set by the platform and the steward: no tool or prompt tells anyone to ratify one, and a user who
thinks one is wrong flags it in the Workbench. For a plain user's token `get_extract` returns the purity candidate
only (a 20-F or 40-F filer answers `purity-not-covered`); a steward's token also gets the share-count and cash
candidates, a fresh parse that is not the shared value. `get_extract` costs EDGAR requests when the filings are not
cached (its description gives the count) and spends the allowance unit above; `get_triage` recomputes the scored
view on every read, so a review reads it once. `get_discovery`, `get_extract` and `get_onboarding` take no `asof`
and answer `asof: null`.

**Admin reads.** The steward's five tools read one Admin panel each, live and undated, over the panel's own GET
(`views/admin/`): `get_splits`, `get_admin_status`, `get_shared_facts` (Admin > Shared facts: the queue, and
`section=company` with `t` for the company page, `fact` narrowing it to one value), `get_insider_holds` (Admin >
Held insider rows, bucketed as the panel is; `section=one` with `t` reads a ticker's rows in full) and
`get_admin_onboarding` (Admin > Onboarding: the shared queue, failed names first; the same view as
`get_onboarding`, for the whole queue). Each view copies the API's values and its order, cuts a long string
with its full length beside it and pages by `cursor`. The queue is paged by the API, so its `cursor` is the API's
`offset` and `pen` (`after`, `only`, `exclude`) is passed through; a company page steps down to shorter lists and
excerpts until it fits, and answers `output_too_large` naming `fact` when even the smallest does not. A user's
email is never shown (the company page carries it for the steward; the tool drops it). A tool reads only: an
approval, a correction, a dismissal, a held row's date and an onboarding Retry stay on the panel. The Insider
amendments panel has no tool: its GET returns every Form 4/A with its evidence (measured on dev: 18.8 to 38 s and
29 MB), so the owed amendments are read on the panel.

## Scopes

A tool or a prompt carries a scope. Every tool but five needs `read`, which every user holds; the Admin reads
(`get_splits`, `get_admin_status`, `get_shared_facts`, `get_insider_holds` and `get_admin_onboarding`) and the
five steward prompts (`splits-review`, `admin-triage`, `facts-review`, `insider-review` and `price-feed-review`)
need `steward`. Three layers hold the line:

1. The registry: a tool or prompt whose scope the caller lacks is neither listed nor callable (`forbidden` before
   any request; a prompt is refused as invalid params). `get_recipe` applies the prompt rule through the same helper
   (`prompt_held`): it lists only the recipes the caller holds and answers `forbidden` for a steward recipe named
   without the scope.
2. The gate: an OAuth token or a read-only personal token without `steward` is a plain user at the API even when
   its user is a super admin, and both are refused every write ([CONNECTORS](CONNECTORS.md)).
3. The API: `/admin/*` is `require_super_admin`, and its 403 answers the tool as `forbidden` too.

So a steward's session or a connector granted `read steward` lists 20 tools and 12 prompts; a plain user, or a
steward's app granted `read` only, lists 15 and 7. The stdio server learns its scopes once, from `GET /auth/me`
with its token. Nothing under `agent_tools/` names a tenant: the API scopes every read to the token's workspace.

## Prompts, recipes and the Claude Code skill

Twelve guided reads, one prompt file each under `backend/agent_tools/prompts/`, registered from `PROMPTS`. Each
file holds the call plan and its call and character budget; the agent's own reading goes under a separate heading
and it says what it did not read.

| Prompt | Arguments | Scope |
|---|---|---|
| `morning-brief` | `asof?` | `read` |
| `board-review` | `asof?`, `max_items?` | `read` |
| `explain-call` | `thesis`, `asof?` | `read` |
| `ticker-check` | `t`, `asof?` | `read` |
| `compare-rotation` | `start`, `end`, `group_by?`, `group?` | `read` |
| `card-data-check` | `thesis`, `asof?` | `read` |
| `discovery-review` | `thesis` | `read` |
| `splits-review` | `t?` | `steward` |
| `admin-triage` | none | `steward` |
| `facts-review` | `t?` | `steward` |
| `insider-review` | `t?` | `steward` |
| `price-feed-review` | none | `steward` |

`compare-rotation` refuses a broken argument rule (`prompt_args.check_compare`) before any read, as an
invalid-params error. `test_server.py` caps each prompt file's size.

**The steward's reviews.** The five steward prompts read the Admin panels through the steward tools. Each item they
report says what waits, the evidence read, an "Agent suggestion:" naming one of the panel's own buttons and a
"Where:" naming the Admin location. A suggestion is the model's and nothing applies it (invariant 10); a figure,
date, symbol or CIK is never the model's. No tool reads the Insider amendments panel, so `admin-triage` and
`insider-review` name it as read by hand. `test_card_data_check.py` bars the word "ratify" from every prompt file;
each prompt has a call-plan test.

**Recipes.** A recipe is a prompt read as a tool result, for a client that cannot run an MCP prompt (a Claude Code
session's model cannot; a hosted client may not offer prompts). `get_recipe()` lists the recipes the caller holds,
in `PROMPTS` order, with each one's arguments (`name`, `required`, `default`). `get_recipe(name, arguments)` returns
the recipe's text with its arguments filled in: the same text `prompts/get` returns, because both call one function
(`render_prompt`), and `test_recipe.py` renders every prompt both ways and compares. `arguments` is a map of at
most 8 string values; a missing required argument, one the recipe does not take or a broken rule (`check_compare`)
is `invalid_arguments` with the rule's sentence. A new prompt joins the index with no edit to the tool. A recipe is
read from the package's files: the tool makes no API request and is undated (`asof` is a recipe argument).

**The Claude Code skill.** The skill `.claude/skills/alphadeck-agent/SKILL.md` is the generated Claude Code copy of
the READ recipes: each block between `<!-- recipe:<name> -->` markers is the prompt file verbatim. A steward's
recipes are in no copy, because a file cannot hold a scope: they come from `get_recipe`, which the server gates (the
skill's setup names them).
Edit the prompt file, never the block, then run `python -m agent_tools.skill_sync --write` (from `backend/`);
`--check` exits 1 with a diff when a block differs, a read prompt has no block, a block names a steward prompt or
a block names no prompt, and `tests/agent_tools/test_skill.py` runs the same check plus the skill's size and
frontmatter bars. The server's `INSTRUCTIONS` name the prompts and `get_recipe`, never a harness.

## The size rule

Every list in a reply is trimmed by one function, `budget.fit`: it measures the compact JSON encoding and keeps the
longest prefix of the named list that fits the tool's budget (a constant in each view module). When it cuts, the
reply carries `next_cursor` (the first index not shown) and `next_cursor_for` (which list); pass
`cursor=<next_cursor>` to continue. A string a view cuts carries its total length beside it. A reply that cannot fit
at its smallest fails `output_too_large` naming the parameter that would shrink it.
`backend/tests/agent_tools/test_views.py` asserts each budget against synthetic fixtures at production sizes.

## Errors

A failure is an `isError` result whose text is compact JSON `{"code", "message", "retryable"}` (an unknown or
ambiguous thesis adds `candidates`), never exception text, paths or payloads. The codes are `errors.CODES`;
`errors.RETRYABLE` marks the retryable ones.

| Code | Meaning |
|---|---|
| `invalid_arguments` | a malformed argument, an unknown section or run id; the message names what is accepted |
| `unknown_thesis`, `ambiguous_thesis` | no thesis with that id or exact name (with candidates), or several with that name |
| `unknown_ticker` | no company has that ticker (a former ticker resolves) |
| `backend_unavailable`, `api_timeout`, `api_error` | the API is down, slow past `--timeout`, or answered 5xx or non-JSON (retryable) |
| `api_rejected` | another 4xx, with its `detail` cut short; a 401 instead names `ALPHADECK_MCP_TOKEN` and how to mint one |
| `forbidden` | a scope the caller lacks, or the API's own 403 |
| `output_too_large` | the reply exceeds its budget at its smallest |
| `internal_error` | anything else (logged to stderr by class only) |

## Install and register

The SDK (`mcp`) is a base dependency (the API image mounts the hosted server); the empty `mcp` extra stays so
`-e ".[dev,replay,mcp]"` keeps working.

- **A client connects to the hosted endpoint.** Give it the connector URL `<origin>/mcp` and, as a header,
  `Authorization: Bearer <token>`, or let it sign in with OAuth ([CONNECTORS](CONNECTORS.md)). Mint one token per
  client in Settings > Agents (or with `python -m pipeline.users token create --email <you> --name <client>`); a
  steward's token also lists the steward's tools and recipes, so a trader and a steward each keep their own entry
  and token. A client's config holds only the URL and the token; the server enforces the rest. The endpoint accepts
  only the origin's own host in `Host`, so a client on one machine uses the origin's host exactly (`localhost`, not
  `127.0.0.1`, when the origin is `http://localhost:<port>`).
- **Claude Code.** `claude mcp add --transport http <name> <url> --header "Authorization: Bearer <token>"` adds the
  URL. `.claude/agents/builder.md` denies builder agents the name `alphadeck-prod`, so a hosted entry for prod takes
  that name; an entry with another name is not covered by the deny. Other MCP clients take the same URL and header,
  or the OAuth sign-in.
- **The stdio server is a developer tool.** It reads a stack on this machine over the loopback API and needs no
  database URL and no model key, only the API. `.mcp.json` declares `alphadeck-prod` (API on 8000) and
  `alphadeck-dev` (8001). Both launch `${ALPHADECK_MCP_COMMAND}`: set it once, as a user environment variable, to
  the absolute path of the main checkout's `alphadeck-mcp` console script (`backend/.venv/Scripts/alphadeck-mcp.exe`
  on Windows, `backend/.venv/bin/alphadeck-mcp` on Linux), then restart Claude Code. The console script, not
  `python -m agent_tools`, so a session's working directory can never shadow the package.
- **The stdio token.** Every API route needs a credential ([AUTH](AUTH.md)), so the stdio server sends
  `Authorization: Bearer <token>` from `ALPHADECK_MCP_TOKEN` in the environment the client launches it from; it is
  never an argument (an argv is visible to every process) and never logged. Mint one per user on the stack it reads
  with `python -m pipeline.users token create --email <you> --name mcp` (or Settings > Agents): a read-only token
  holding the user's role scopes, which is all the tools need. Prod and dev keep separate users, and the two servers
  share the one variable, so set it to the token of the stack you read.
- **Another stdio client.** Run `alphadeck-mcp --backend-base http://127.0.0.1:<port>` over stdio. The base must be a
  loopback origin with an explicit port; `--timeout` and `--trace` are in `--help`.

## Hosted endpoint

The same server, mounted in the API at `/mcp` (Streamable HTTP, stateless; `agent_tools/hosted.py`), available
when `ALPHADECK_PUBLIC_ORIGIN` is set. Each user reads only their own workspace.

- **The switch.** `ALPHADECK_PUBLIC_ORIGIN` is the app's public origin: no path, `https`, or `http` on a loopback
  host (`localhost` or a loopback address). Unset, nothing is mounted. Set, the connector URL is `<origin>/mcp` and
  the OAuth sign-in is mounted beside it. A client on another machine needs an `https` origin: a bearer token must
  never cross a network in clear ([SECURITY](SECURITY.md) "TLS"), and a loopback origin is out of its reach.
- **Verification.** The endpoint asks the API's own `GET /auth/me` in-process with the request's bearer and nothing
  else, so a revoked token, a disabled user, a session cookie alone and the system token are all 401. There is no
  cache: a revocation bites on the next request. Every API read a tool makes carries the same bearer.
- **Two kinds of token.** A personal access token (Settings > Agents or the CLI; read-only unless made with the
  CLI's `--write`), sent as a header; or an OAuth access token from the sign-in in [CONNECTORS](CONNECTORS.md).
  A 401 carries `WWW-Authenticate` with
  `resource_metadata` pointing at the RFC 9728 document (`/.well-known/oauth-protected-resource/mcp`), which names
  the connector URL, the authorization server and the two scopes.
- **The nightly window.** A hosted reply during the nightly pass carries `"nightly_window": true` at its top level,
  a note and never a refusal (`window.py`). The stdio server leaves the rule to its caller (the skill).
- **nginx.** `location = /mcp` is exact, never a prefix: a redirect to `/mcp/` would strip the Authorization
  header. Buffering is off for event streams. `GET /mcp` answers 405 (there is no server-initiated stream).
- **Transport checks.** The `Host` header must be the origin's host and a browser `Origin`, when sent, must be the
  origin (the SDK's transport security settings).

## Reconnect after a contract change

An MCP client keeps the tool schemas it loaded when it connected. After a tool changes (a new argument, a renamed
field, a new tool), reconnect the server in the client (in Claude Code, `/mcp` then reconnect, or a new session).
The advertised version (`server.VERSION`, pinned in `test_server.py`) changes with the contract: a tool or prompt
added or removed, or a description whose meaning changes, bumps the MINOR.

## Who can do what

| Action | Who | Enforced by |
|---|---|---|
| The 15 `read` tools (`get_recipe` included) and 7 prompts | anyone signed in (session, token or OAuth app), on their own workspace | the API gate; `server.SCOPES` |
| The 5 steward tools, the 5 steward prompts and their recipes | the steward, or an app the steward granted `steward` | the registry (`SCOPES`, `prompt_held`), the OAuth role cap, `require_super_admin` |
| Mint an MCP token (read-only) | the user (Settings > Agents) or the operator (CLI) | `/account/tokens`; `pipeline.users` |
| Read prod from a builder agent | nobody | `.claude/agents/builder.md` |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_PUBLIC_ORIGIN` | `domain/settings.py` `public_origin` | yes (backend) |
| `ALPHADECK_MCP_COMMAND` | `.mcp.json` (the user's environment) | no |
| `ALPHADECK_MCP_TOKEN` | `agent_tools/client.py` (the user's environment) | no |
| `ALPHADECK_MCP_BACKEND_BASE` | `agent_tools/server.py` (the stdio default base) | no |

## Tests

`backend/tests/agent_tools/`: `test_views.py` (shape and budget per view, every cursor walked), `test_server.py`
(the SDK client in-process against a fake API, one real stdio subprocess, the counts per scope, the version pin),
`test_dashboards.py`, `test_research.py`, `test_admin_views.py`, `test_recipe.py` (every recipe equals its prompt,
the index per scope, the largest reply), `test_contract.py` (every registered contract),
`test_resolve.py`, `test_client.py`, one test per prompt's call plan through the real stdio client
(`test_card_data_check.py` also guards that no prompt says to ratify a share count or cash), `test_skill.py`,
`test_principal.py` (the scope gate, the stdio principal, the window, and grep guards: no tenant, no `db` or
`repositories` import) and `test_hosted.py` (the endpoint against the real API and database: two users and a
steward, the 401 shapes, the RFC 9728 document, interleaved calls, no redirect). Fixtures are synthetic
(`Thesis 01`, `TK01`).

## Known limits

- `get_extract` spends a plain user's daily extract allowance through a GET route ([AUTH](AUTH.md) "The cookie and
  CSRF").
- One `ALPHADECK_MCP_TOKEN` serves both stdio servers, so it reads one stack at a time.
- A running client keeps stale schemas until it reconnects.

## Related

[CONNECTORS](CONNECTORS.md) (OAuth), [AUTH](AUTH.md) (tokens, the gate), [SECURITY](SECURITY.md) (TLS),
[PLATFORM_VALUES](PLATFORM_VALUES.md) (shared values), `.claude/skills/alphadeck-agent/SKILL.md`.
