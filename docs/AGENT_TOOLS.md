# Agent tools (the data-first MCP server)

Status: Phase 1 built 2026-11-06 (the server and the MONITOR read tools). Stage: MONITOR.
Code: `backend/agent_tools/`. Legacy: the capture server in [`docs/MCP.md`](MCP.md) (`python -m agent_access`)
stays beside it until Phase 4 retires it.

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
| `list_theses(asof?, include_archived?, state?, detail?)` | `/theses?with_call=true&asof=` | `queue` = live armed cards whose record is dated `asof`, in `/theses` order (never sorted). `cards` is ordered QUEUE-FIRST: the queue cards in full, in queue order, each with the nightly RECORD in effect at `asof` (state, verdict, grades, setup strength, `exit_by`, `arm_until`, armed ticker, counts, `record_asof`) and its record's first 3 `triggers` (`ticker`, `kind`, `label`, `event_date`) plus `trigger_count`; then the other live cards briefly (`id`, `name`, `state`, `record_asof`) in `/theses` order; then the archived cards. Lean: `ticker` omitted when null, `archived` / `stale` / `reconstructed` when false, `basket_size` except with `detail=full` (every card in full; `state` filters) | 20,000 (`full`: 24,000) |
| `get_thesis(thesis, detail?)` | `/theses/{id}` | narrative (600 chars), links in link order (silent links last: they never count), each with its `member_count` and its members grouped under it (`cursor` counts members across the links in that order), catalysts, kill criteria, SIGNAL terms; `full` adds `thesis_fit`, `detail`, every term | 8,000 (`full`: 24,000) |
| `get_call(thesis, asof?, section?)` | `/theses/{id}/call` | the call recomputed live: state, verdict, grades, keys, horizons, armed name, armed members (10), watch tickers, every fired trigger with its sources (no `detail`), missing, heads of `counter_case` and `expression`, risk signals counted by kind | 8,000 |
| `get_call(..., detail="brief")` | same | the decision alone, for a pass over many cards: state, verdict, grades, setup strength, keys (turned only), horizons, armed name, each trigger as `{ticker, kind, label, grade, event_date, source_count}` (no sources), missing, risk signals by kind, `counter_case` length, catalyst count | 2,500 |
| `get_call(..., section=...)` | same | `triggers` (with each source's computation), `risk_signals` (10 per page), `counter_case`, `members`, `expression` in full | 24,000 |
| `get_data_flags(thesis?, asof?)` | `/theses/{id}/data-flags` or `/data-flags` | per flagged name: each flag's kind, label, level, reason, `clears` (what clears it), `since`, affects, `call_affected`; notes and splits counted; cross-card adds the cards holding it and `compute_s` (it is slow) | 8,000 |
| `get_display_signals(thesis, tickers?, asof?)` | `/theses/{id}/display-signals` | breadth and sector headlines; per member `{kind: {key, label}}`; with `tickers` (1 to 8) each signal's metrics | 8,000 |
| `get_decisions(thesis)` | `/theses/{id}/decisions` | the operator's log, newest first, as the API gives it | 8,000 |
| `lookup_ticker(t, asof?, section?)` | `/dashboards/ticker-lookup/resolve` then `/{security_id}` | identity, the cards it sits on (link, `context_role`, the record there), record triggers, tape figures, next 5 card catalysts, decisions, filings summary; `cache` / `compute_ms` pass through; `section=filings` = 25 rows a page | 8,000 |

Common to every tool: `asof` is optional `YYYY-MM-DD`; omitted, it is the API's `current_night` (the server never
derives a date). `thesis` takes a UUID or an exact name (case-insensitive). Every tool is annotated
`readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false` and advertises an
`outputSchema`. Replies are compact JSON text plus `structuredContent`.

Prompts: `board-review(asof?, max_items=5)` (list once, then `get_call detail=brief` for the first queue cards, flags only where due; the standard `get_call` is for depth on one card)
and `explain-call(thesis, asof?)` (the call, its triggers and risk signals, the spine). Both keep the agent's
own reading under a separate heading and say what was not read.

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

Registration is unchanged: `.mcp.json` declares `alphadeck-prod` (`--backend-base http://127.0.0.1:8000`) and
`alphadeck-dev` (`--backend-base http://127.0.0.1:8001`), both launching `${ALPHADECK_MCP_COMMAND}`, the main
checkout's `alphadeck-mcp` console script (set it once as a user environment variable; see `docs/MCP.md`
"Register with Claude Code" for the per-OS path). After `pip install -e`, that console script serves THIS
server. It needs no database URL, no browser and no model key: only the API.

Options: `--backend-base` (a loopback origin with an explicit port; default `http://127.0.0.1:8001` or
`ALPHADECK_MCP_BACKEND_BASE`), `--timeout` seconds per API request (1 to 600, default 60: the cross-card
`/data-flags` read and a cold dashboard take up to about 35 s), `--trace` (one stderr line per call:
`tool=<name> ok=<bool> chars=<n> ms=<n>`, never content) and `--ui-base` (IGNORED; kept so the existing
registration still starts the server, until Phase 4).

Run by hand: `python -m agent_tools --backend-base http://127.0.0.1:8001 --trace` (it speaks MCP on stdio).

## Reconnect after a contract change

An MCP client keeps the tool schemas it loaded when it connected. After this server's tools change (a new
argument, a renamed field, a new tool), reconnect the server in the client (in Claude Code: `/mcp`, then
reconnect, or restart the session); otherwise the model keeps calling the old contract. The server's advertised
version (`alphadeck` `2.0.0`) changes with its contract.

## Tests

`backend/tests/agent_tools/`: `test_views.py` (shape and budget per view, every cursor walked to the end),
`test_server.py` (the official SDK client in-process against an httpx `MockTransport` fake API, plus one REAL
stdio subprocess against a loopback fake API: 8 tools, 2 prompts, read-only, output schemas), `test_resolve.py`,
`test_client.py`. The route the list reads is tested against the database in
`backend/tests/app/test_theses_with_call.py`. Fixtures are synthetic (`Thesis 01`, `TK01`): no card or company
name from a live card.
