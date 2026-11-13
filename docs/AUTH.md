# Auth — the login door

Application routes sit behind the login gate, with the public and conditional protocol routes listed below. This page is the model, the roles, the four ways a caller is
recognized, the user CLI, and the first-deploy order. Code: `backend/app/auth.py` (the gate),
`backend/app/routers/auth.py` (the `/auth` routes), `backend/app/routers/account.py` (the `/account` routes the
Settings page uses), `backend/app/routers/oauth_consent.py` + `backend/app/oauth/` (the OAuth sign-in for AI clients,
[`CONNECTORS.md`](CONNECTORS.md)), `backend/pipeline/users.py` (the CLI), migrations `0077_auth.sql`,
`0079_user_meta.sql` and `0080_oauth.sql`; the guards are `backend/tests/app/test_auth_gate.py` and
`backend/tests/app/test_oauth.py`.

**One private workspace per user.** Each user has a workspace (`app_user.workspace_id`, a `kind='workspace'`
tenant): their theses, calls, decisions and ratified facts live there and no other user sees them; the companies,
prices and filings are shared data (`docs/TENANT_SCOPES.md`). The founding operator's cards moved into the
**founding workspace** (`…0002`) when migration 0078 ran. A super admin (the data steward) sees other workspaces'
cards on Admin as ids only.

## The model

| Table | What it holds |
|---|---|
| `app_user` | `email` (unique case-insensitively), `display_name`, `password_hash` (argon2id; the password itself is never stored), `role` (`user` / `super_admin`), `workspace_id`, `created_at`, `disabled_at` |
| `user_session` | one browser sign-in: the sha256 of the cookie's random token, `created_at`, `last_seen_at`, `expires_at` (the idle deadline), `revoked_at` |
| `api_token` | one named bearer token: its sha256, `created_at`, `last_used_at`, `revoked_at` |
| `oauth_client`, `oauth_consent`, `oauth_grant`, `oauth_code`, `oauth_access_token`, `oauth_refresh_token` | the OAuth sign-in (0080): the apps that may ask, each pending request, each user's connected apps, and their codes and tokens, sha256 only ([`CONNECTORS.md`](CONNECTORS.md)) |
| `user_meta` | the user's PREFERENCES, 1:1 with `app_user` (`user_id` PK; 0079): one typed column per setting, NULL = unset, a missing row = every setting unset; first `slack_webhook_url` (their own push destination), `created_at`, `updated_at`. Not the gate's table: nothing here rides into `/auth/me` |

Tokens (session and API) are `secrets.token_urlsafe(32)` (256 bits); the database keeps only their sha256, so a
read of these tables cannot be replayed as a credential. Nothing is deleted: sign-out, revocation and disabling
stamp a column, and re-enabling clears it.

## The four principals

`require_user` (mounted on application routers in `app/main.py`, on the ROUTER, so a request with no credential answers
401 before any parameter is validated) tries, in order:

1. **session** — the `alphadeck_session` cookie → a live, unexpired, unrevoked session of an enabled user. The
   idle deadline is `ALPHADECK_SESSION_IDLE_DAYS` (default 30) and slides forward on use; `last_seen_at` is written
   at most once per 5 minutes.
2. **token** — `Authorization: Bearer <token>` → an unrevoked API token of an enabled user (`last_used_at` likewise).
   For the MCP agent tools (`ALPHADECK_MCP_TOKEN`, [`AGENT_TOOLS.md`](AGENT_TOOLS.md)), the deploy script's probes
   (`ALPHADECK_OPS_TOKEN`, [`DEPLOY.md`](DEPLOY.md)) and curl.
3. **oauth** — `Authorization: Bearer <token>` → a live OAuth access token (one hour) of an app the user connected
   through the OAuth sign-in ([`CONNECTORS.md`](CONNECTORS.md)): unexpired, unrevoked, its grant live, its app not
   disabled, its user enabled (`last_used_at` on the token and the grant likewise). Two rules, both in the gate:
   - **read-only:** a token without the `write` scope (no token carries it yet) answers 403 on every request that
     is not `GET` / `HEAD` / `OPTIONS`, before the route runs, so it covers every router;
   - **the role is capped by the scopes:** `super_admin` only when the user is one AND the token carries `steward`,
     else `user`, so a steward's `read`-only connector gets 403 on `/admin`.

   It never reaches `/account/*` or `/auth/change-password` (403: a connected app never manages the account that
   connected it). `CurrentUser.scopes` carries the token's scopes (a session's and a personal token's are their
   role's: `read`, plus `steward` for a super admin; the system token's are `read`).
4. **system** — `Authorization: Bearer $ALPHADECK_SYSTEM_TOKEN` (an env secret on backend + cron; unset = no
   system principal). It can only `GET` under `/dashboards/`, `/clock` and `/health`; anything else answers 403.
   The cron's dashboard warm-up (`pipeline.warm_dashboards`) uses it; unset, the warm-up prints one loud line and
   exits 1 instead of looping on 401s.

Else 401. A disabled user's sessions and tokens stop working at once (the gate checks `disabled_at` on every
request). `require_super_admin` = `require_user` + the role, else 403. The secret compare is constant-time.

The default API's open routes are `GET /health` and `POST /auth/login`. When `ALPHADECK_PUBLIC_ORIGIN` is set,
the hosted connector also mounts public discovery documents (`/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource/mcp`) and the OAuth protocol routes `/authorize`, `/token`, `/register`
and `/revoke` outside `require_user`. These use their own protocol checks: client authentication (including
public clients), PKCE for the code exchange, registration validation/throttling, and the session-only consent
decision; they are not anonymous access to workspace data. `/oauth/consent/{id}` still requires a browser
session, and `/mcp` requires a valid user bearer token. See [`CONNECTORS.md`](CONNECTORS.md).
`/docs`, `/redoc` and `/openapi.json` remain behind `require_user` (the generated contract
`backend/openapi.json` is exported in-process and is unaffected).

## The routes

| Route | What it does |
|---|---|
| `POST /auth/login` | `{email, password}` → sets the session cookie, returns the user. 401 on a wrong pair (the same answer for an unknown email, a wrong password and a disabled account, and an unknown email still spends one argon2 verify, so timing does not reveal who has an account). 429 "Too many failed sign-ins from your network. Try again in a minute." after `ALPHADECK_LOGIN_MAX_FAILURES` (5) failed sign-ins from one client ADDRESS inside `ALPHADECK_LOGIN_WINDOW_S` (60 s), whatever email was typed, for `ALPHADECK_LOGIN_LOCKOUT_S` (60 s): a short pause, not a lockout, and failures from another address never pause a real user. A success does not reset the count (it ages out), so an attacker holding one account cannot keep guessing between sign-ins. The address is `app/client_address.py`'s: nginx's `X-Real-IP`, believed only from the nginx container; an IPv6 caller counts by its /64. 503 "Sign-in is busy. Try again in a moment." (with `Retry-After`) when every password-check slot stays taken for `ALPHADECK_PASSWORD_CHECK_WAIT_S` (10 s): at most `ALPHADECK_PASSWORD_CHECK_CONCURRENCY` (4) argon2 checks run at once (each costs about 64 MB), for sign-in and change-password alike; a 503 never counts as a failed sign-in. In-process, like the draft-job registry (one worker). |
| `POST /auth/logout` | revokes this browser's session, clears the cookie |
| `GET /auth/me` | `{id, email, display_name, role, workspace_id}`, plus, for a bearer only, `token: {principal, client_id, scopes}` (`principal` `token` or `oauth`; `client_id` is the connected app's). The hosted `/mcp` endpoint reads it to know what the caller may do |
| `POST /auth/change-password` | `{current, new}`; `new` needs at least 12 characters; every OTHER session of the user is signed out |
| `PUT /account/profile` | `{display_name, email}` → the user (`/auth/me`'s shape). 400 on an empty name or an email with no `@`; 409 `that email is already in use` when another user has it (case-insensitively); changing only the case of your own email is allowed. Sessions and tokens stay signed in |
| `GET /account/notifications/slack-webhook` | `{set, updated_at}`; the URL is NEVER returned, by this or any route (a webhook URL is a credential) |
| `PUT /account/notifications/slack-webhook` | `{url}` → 204; the URL must start with `https://hooks.slack.com/` (400 otherwise); replaces any earlier one, stamps `updated_at` |
| `DELETE /account/notifications/slack-webhook` | → 204; clears the URL (the row stays); a no-op when none is set |
| `POST /account/notifications/slack-webhook/test` | → `{delivered, detail}`: one plain line ("Alpha Deck test message for <name>") through the real Slack POST path, 10 s timeout; a failure is `delivered: false`, never an error. 404 when none is saved |
| `GET /account/agents` | `{hosted, connector_url, steward, stdio_server, oauth}`: `hosted` when `ALPHADECK_PUBLIC_ORIGIN` is set, and then `connector_url` = `<origin>/mcp` (else null); `steward` = the caller is a super admin; `stdio_server` = true (the local agent tools server exists on every install); `oauth` = the OAuth sign-in is mounted. See `AGENT_TOOLS.md` §Hosted endpoint |
| `GET /account/tokens` | `{tokens: [{id, name, created_at, last_used_at, revoked_at}]}`: my own API tokens, live first, then revoked, newest first; never the token or its hash |
| `POST /account/tokens` | `{name}` → 201 `{id, name, created_at, token}`: the token is in THIS reply only, never again (only its sha256 is stored). The name is trimmed, 1 to 60 characters (400 otherwise); at most 20 live tokens per user (409 `too many tokens; revoke one first`) |
| `DELETE /account/tokens/{token_id}` | → 204; my own token only: another user's token or an unknown id is 404 `no such token` (alike, never 403); an already revoked token is 204; a token may revoke itself. It stops working on its next request |

| `GET /account/connections` | `{connections: [{id, client_name, client_known, scopes, created_at, last_used_at, revoked_at}]}`: the apps I connected through the OAuth sign-in, live first, then disconnected, newest first; `client_known` is false for an app that registered itself |
| `DELETE /account/connections/{grant_id}` | → 204; disconnects my own app: every access and refresh token it holds stops working. Another user's or an unknown id is 404 alike; a repeat is 204 |
| `GET /oauth/consent/{consent_id}` | an app's pending sign-in request for the consent page: `{client_name, client_known, scopes: [{name, label, grantable}], redirect_host, expires_at}`. Browser SESSION only (403 for any bearer); 404 unknown; 410 expired, answered, or its app disabled |
| `POST /oauth/consent/{consent_id}` | `{decision: "allow" \| "deny"}` → `{redirect_to}`. Session only; Allow grants only the scopes the user's role may grant (never `steward` to a plain user); 409 on a second decision, 410 expired. See [`CONNECTORS.md`](CONNECTORS.md) |

Every `/account` route acts on the caller's own rows (no user id in any path); the system token and a connected app
(the `oauth` principal) are refused (403).

## The Settings page

`/settings/profile` (display name + email, and the change-password form below them) and
`/settings/notifications` (the user's own Slack webhook: a status line, a write-only URL field, Save, and when one is
set Remove and Send a test message) and `/settings/agents` (how an AI client reaches the user's workspace: the
connector URL and the four client how-tos when the install is hosted, a quiet paragraph when not; and the user's own
access tokens: create one, shown once until Done; revoke one, which stays on screen grayed for the visit; "Show
revoked tokens"; and the apps connected through the OAuth sign-in, each with Disconnect, a disconnected one kept
grayed for the visit, "Show disconnected apps"; `AGENT_TOOLS.md` §Hosted endpoint, `CONNECTORS.md`). The OAuth consent
page `/oauth/consent/<id>` sits inside the signed-in shell; a signed-out visitor goes through
`/login?next=/oauth/consent/<id>` and comes back (the login page honors `next` for that one path shape only). `/settings` and an unknown tab go to `/settings/profile`; the old `/account`
link redirects there too. Reached from the user menu ("Settings"), never a top tab. The tabs come from one registry
(`frontend/src/settings/registry.ts`): a new settings page is one folder + one entry. What arrives on the webhook:
a card of the user's arming or changing while armed, and the nightly digest; never the ops pages
(`FEED_LOOP.md` step 3).

## The cookie and CSRF

`alphadeck_session`: `HttpOnly; SameSite=Lax; Path=/`, plus `Secure` when `ALPHADECK_COOKIE_SECURE` is true (the
default; the dev overlay sets it false, since dev is plain http). Prod needs TLS before any non-local user; until
the server move the laptop is the only client.

No CSRF token, on purpose. SameSite=Lax keeps the cookie off cross-site POSTs; the API reads JSON bodies only, and
a cross-site form cannot send `application/json` without a preflight the API never grants; and no GET route
mutates anything. The frontend keeps no credential in browser storage: the HttpOnly cookie is its only client
state.

## Roles: what each sees

| | `user` | `super_admin` (the data steward) |
|---|---|---|
| Board, Cockpit, Workbench, Scoreboard, dashboards, radar, decisions | yes | yes |
| Admin page and every `/admin/*` route (cron status, backups, run-daily, splits, price-feed repairs, insider amendments, onboarding) | 403; the nav entry is hidden; typing `/admin` shows "stewards only" | yes |
| Backtest page and `/backtest/*` | 403; hidden likewise | yes |
| The business-type re-tag (`POST /workbench/securities/{id}/business-type`), a write to the SHARED company master | 403; the control is hidden | yes |
| Surface an ETF (`POST /workbench/securities/resolve-etf`): adds a fund to the SHARED company master or marks an existing row a fund | 403; the Workbench control is hidden | yes |
| Live fetches on a click: extract, auto-confirm, holdings, the price pull, the chain draft, terms and tier runs (see "User-triggered fetches" below) | yes, for a company on one of their own cards (else 404) and within a daily allowance per action (else 429) | yes, for any company, unlimited |
| Add a fund to a card's ETFs link (`POST /radar/funds/attach`) | yes, for a fund already in the company list; any other symbol is a 404 ("This fund is not in the company list yet. A steward can add it.") and nothing is written | yes, and may add the fund to the company list (the same lookup-or-create as Surface an ETF) |
| User and token management | own name, email, password and Slack webhook, and own API tokens on the Agents tab (`/settings`) | the CLI, plus their own on `/settings` |
| The Agents tab (`/settings/agents`) | yes: the connector URL, their own tokens and their connected apps | yes, plus one sentence on the steward scope |
| The OAuth consent page (`/oauth/consent/<id>`) | yes: grants `read` only (`steward` shown struck, "not available to your account") | yes: may grant `read` and `steward` |

The backend enforces every row; the frontend only hides what a role cannot use.

**No user-authored writes to the shared company master.** Only the steward adds a row to `security_master` or
changes one. A ticker typed into either fund route above is stripped, uppercased and held to
`^[A-Z0-9][A-Z0-9.\-]{0,11}$` (422 otherwise); `securities.figi.map_ticker` applies the same check before a ticker
becomes an OpenFIGI request or a cache filename. `backend/tests/app/test_shared_master_writes.py` pins it: a
plain user's attempts change no master row, and a grep guard allows a call to a `securities.master` writer under
`app/routers/` only at a listed site that is steward-gated or inside a steward branch (the automatic OTC
price-symbol resolve in `POST /workbench/securities/{id}/ingest-prices`), and follows one hop into
`backend/workbench/`: a master writer there (the draft's identity enrichment, `workbench.enrichment.enrich_for_ciks`)
is reached from a router only inside a steward branch (`execute_draft(steward=...)`).

## User-triggered fetches

A plain user's click may still start a live fetch of server-sourced data (SEC, the price vendor, the LLM seams),
because the Workbench is unusable without it. Each fetch is bounded (security hardening PR 1b):

- **Own cards only.** Extract (`GET /workbench/securities/{id}/extract`), auto-confirm
  (`POST /workbench/facts/auto-confirm`), holdings (`GET /workbench/securities/{id}/etf-holdings`) and the price
  pull (`POST /workbench/securities/{id}/ingest-prices`) answer **404** "This company is not on any of your cards.
  Add it to a card first." for a company that is not a member of one of the caller's own cards. Every screen that
  fetches works on a saved card's members (the scored rows, the facts panel, the section runner, the sleeve rail,
  the fund-launch attach); the triage session's saved draft is not consulted, because the user writes it. The
  three path-routed checks run before anything is counted; auto-confirm names its company in the body, so it is
  counted first.
- **A daily allowance per user.** Each fetch path counts one unit for the caller in `user_action_count` (migration
  0084; keyed on the user, no tenant scope), per market day (midnight to midnight in `ALPHADECK_MARKET_TZ`). Over
  the day's allowance the route answers **429** "Daily allowance reached: N <actions> a day. It resets at midnight
  <zone> time." The count is one upsert statement, so racing requests never pass the limit. The settings, set high
  on purpose (a ceiling against a runaway loop, not a rationing tool; 0 = no limit on that action):

  | Setting | Default | Counts |
  |---|---|---|
  | `ALPHADECK_ALLOWANCE_EXTRACT_PER_DAY` | 5000 | extract |
  | `ALPHADECK_ALLOWANCE_PRICE_PULL_PER_DAY` | 5000 | the price pull, plus one per new name a promote's fast lane ingests (when today's units cannot cover them, the names go to the onboarding queue instead and the promote says so) |
  | `ALPHADECK_ALLOWANCE_AUTO_CONFIRM_PER_DAY` | 2000 | auto-confirm |
  | `ALPHADECK_ALLOWANCE_HOLDINGS_PER_DAY` | 1000 | holdings |
  | `ALPHADECK_ALLOWANCE_CHAIN_DRAFT_PER_DAY` | 100 | `POST /workbench/theses/{id}/draft-chain` |
  | `ALPHADECK_ALLOWANCE_TERM_RUN_PER_DAY` | 200 | `POST .../terms` and `POST .../recommend-tiers`, one shared count |

- **No shared-master write.** The automatic OTC price-symbol resolve runs on a steward's pull only (a plain user's
  OTC pull is priced under the canonical ticker; the steward's price-feed panel or the backfill sweep resolves it).
  A plain user's draft reads each discovered name's submissions and lays the parsed identity (sector, exchange,
  listing status) over the master rows for that response only; a steward's draft stores it.
- **No shared lock across the network.** The price pull fetches the bars first and takes the company write lock
  only for the compare-and-append, so a slow vendor never holds up the nightly (which waits for a company at most
  `ALPHADECK_INGEST_LOCK_WAIT_S`, then skips it).

The steward is neither checked nor counted. The screens show the 404 and the 429 as worded, on their own line.
`backend/tests/app/test_user_fetches.py` pins all of it with a real plain user and a real steward.

## Errors

What a failed request answers (security hardening PR 2, `backend/app/errors.py`). An error the code did not word
itself never reaches a plain user's screen:

- **A fixed sentence and a reference.** Every failure answers `{"detail": "<a fixed sentence for that failure>
  Reference 1a2b3c4d."}`. The reference is 8 random hex characters; the exception, with its traceback, is logged
  under it (logger `alphadeck.errors`: ERROR for a 5xx, WARNING otherwise), so a user who quotes it can be matched to
  the log line. No SQL, column value, server path or upstream (SEC, vendor, OpenFIGI, LLM) text is in the body.
- **A steward still sees the underlying error** (operator decision 2026-11-12): for a `super_admin` caller the body
  continues `Detail: <ExceptionClass>: <message>`. Every gated router carries `errors.note_caller`, which records
  the caller on the request; an unknown caller (the open `POST /auth/login`, or a failure before the gate ran) is
  treated as a plain user.
- **Database errors a route did not handle** (global handlers):

  | Error | Status |
  |---|---|
  | `DataError` (a NUL character, a number or date out of range) | 422 |
  | `UniqueViolation` (any other integrity conflict: 409 too) | 409 |
  | `CheckViolation`, `ForeignKeyViolation`, `NotNullViolation` | 422 |
  | `ProgramLimitExceeded` (a value too large for an index row) | 413 |
  | `QueryCanceled`, `LockNotAvailable` (the request timeouts, docs/DEPLOY.md §Connections) | 503 + `Retry-After: 5` |
  | any other `OperationalError` (cannot connect, too many clients) | 503 + `Retry-After: 5` |
  | any other exception | 500 |

- **Failures a route catches** (an upstream fetch, a triage-session file, a saved run that no longer validates, a
  malformed `ALPHADECK_CRON_AT`, the company write lock held by another writer) answer the same way, through the one
  helper `errors.fail(status, sentence, exc, user=...)`, with the route's own status (502, 500, 422, 409, 503).
- **Background job failures** follow the same contract on their poll (`GET /workbench/theses/{id}/draft-chain/jobs/{job_id}`
  and `GET /workbench/theses/{id}/ingest/jobs/{job_id}`): an unexpected fault in a chain draft answers "The draft
  failed on the server. Try again, and quote the reference if it keeps happening." and one in an on-promote data fetch
  "The data fetch failed on the server. Try again, and quote the reference if it keeps happening.", each with its
  reference. The job logs the exception under that reference when it fails and keeps the description; the poll adds
  `Detail: …` for a steward. A partial data fetch names the counts and each failed ticker with the parts that failed in
  plain words ("HIMS: insider filings, prices") plus a reference; the raw per-part messages are logged under it and
  shown to a steward only. A draft's own curated messages (discovery not ready, the counts it reports) and the
  timeout are shown as worded.
- **An explicit mapping keeps winning**: a route's own `HTTPException` (the duplicate-email 409 "that email is
  already in use", the own-cards 404, the daily-allowance 429, every domain 422) is answered as worded.
- **The request-validation 422** keeps FastAPI's list shape (`loc`, `msg`, `type`, and `ctx` where present) but
  never echoes the caller's `input` (it can be megabytes) or a documentation `url`.
- Domain messages the code writes for the operator are kept as worded: the promote's thesis validation and the
  term-set edit rules (422), the password and email rules (400), the What-Changed `since` rules (422).

`backend/tests/app/test_errors.py` pins the handlers, `test_error_text.py` the route sites (both callers), and
`test_hostile_input.py` sends hostile values to every route the database-layer sweep ranked (a status below 500 and
no row written; since security PR 3 every case passes, the ones only request-model bounds could fix included).

## Request limits

What a request may carry (security hardening PR 3). This section stands in for `docs/SECURITY.md`, which security
PR 4a creates; when both have merged, the table moves there and this section points to it.

**The one rule: limits apply to what comes IN on a request, never to stored data being read.** The domain models
(`Thesis`, `BasketMember`, `Segment`, `ExtractedFact`, ...) are built from database rows and server-side extraction on
every load (the nightly, the replay harness, the backtest, every route), so none of them carries a limit: a stored
value over a limit can never make a card unloadable. The limits live in one place, `backend/domain/text_types.py`
(each a named constant, so raising one is a one-line change), and are used only by the request models in
`app/schemas_api.py`: request-side copies of the nested models (`BasketMemberIn`, `SegmentIn`, `ExtractedFactIn`,
`LocatedPassageIn`, each a subclass of its domain model with bounded fields, turned back into the domain model by the
route) and query parameters.

| Field class | Limit |
|---|---|
| One-line label: thesis name, segment label, catalyst label, `kind`, `when_label`, `role`, `detail`, a basket row's or exclusion's displayed `ticker`, the card `ticker`, token name, display name, ratify `source`, `segment_label` | 200 characters |
| Short prose: segment descriptor, `thesis_fit`, kill criterion, exclusion or decision `reason`, ratify `note` | 2,000 characters |
| Narrative | 20,000 characters |
| Search term, seed, `surfaced_terms` item | 200 characters |
| Ticker that can reach the shared master (Surface an ETF, fund attach) | 12 characters, the `securities/symbols.py` pattern (PR 1a) |
| CIK (SPAC attach) | digits only, at most 10 |
| URL or `source_ref` (ratify, Slack webhook) | 2,000 characters |
| Email | 254 characters |
| Password (sign-in, change) | 12 to 1,024 characters, never trimmed |
| Security search `q` | 100 characters, matched literally (`%`, `_` and `\` escaped in the `LIKE`) |
| Basket rows per card · segments · identity overrides | 3,000 · 100 · 3,000 |
| Terms per card · seeds per term run · `surfaced_terms` per row | 500 · 100 · 200 |
| Catalysts · kill criteria · exclusions per card | 500 · 200 · 5,000 |
| The flag explainer (`POST /workbench/facts/explain`) | at most 10 passages, each excerpt at most 4,000 characters (prose rules), at most 20 flags; the rest per the classes above |
| Numbers | finite only (no NaN, no Infinity); `mix_pct` 0 to 100; ratify `shares` above 0 and at most 1e13; `ads_ratio` 1 to 1,000,000; `cash_usd`, `quarterly_burn_usd` and `estimate` within ±1e15; a decision's `shares` and `price` 0 to 1e13 |
| Dates on a request body | 1990-01-01 to 366 days after today's market date (`decision_date` also keeps "not after today") |
| `asof` (and the price window's `start` / `end`) on every read | 2000-01-01 to 7 days after today's market date |
| The triage-session autosave | `state` stays opaque (its strings are never inspected); the request body at most 25 MB (**413**), nesting at most 32 levels |

Text rules, every text class: leading and trailing whitespace is trimmed and nothing else is altered; a NUL or any
other control character (U+0000 to U+001F, U+007F to U+009F) is refused, except tab, newline and carriage return in
the prose classes (short prose, narrative, an explainer excerpt). A value over a limit is a **422** in FastAPI's list
shape, naming the field in `loc` and the limit in `msg` (for example `["body", "name"]`, "String should have at most
200 characters"); the screens show its first message, with the field, on their usual error line, and the forms carry
the same limits as `maxLength` (`frontend/src/api/limits.ts`). Nothing is ever cut short silently.

- **Sign-in** checks the email and password limits in the request model, so an over-long password is refused before
  any hash is computed, on the unknown-email branch too.
- **The flag explainer** also spends one unit of the extract allowance (it is an LLM call a click can repeat).
- **Resending old data.** A promote is a full replace: the editor resends the whole card. If a stored card ever holds
  a value over a limit, its next save is refused with a 422 naming the field, and the stored card is left exactly as
  it was; there is no bypass. On the data measured on 2026-11-12 no stored value is over any limit.
- **Request bodies in nginx** (`frontend/nginx.conf`): 1 MB by default; 25 MB only on the exact promote location
  (`= /api/workbench/theses`) and the triage-session location. Neither sets its own `add_header`, so both inherit the
  server's headers.
- **Unknown fields.** The request models that are plain `BaseModel`s still ignore a field they do not know (an older
  autosaved payload the promote accepts may carry one); the nested domain copies refuse one, as before.
- **Steward-only bodies** keep their own limits (`reason` at most 500 characters, symbol and CIK lengths) and are not
  held to the text rules above.

`backend/tests/app/test_request_bounds_guard.py` walks every request body model and query parameter reachable from a
route and fails on a `str`, `float`, `date`, list or mapping with no limit (a short, commented exemption list holds
the steward-only exceptions); it also fails if any domain model carries a request limit, and if the frontend's
`limits.ts` drifts from the backend. `test_request_limits.py` checks every class and collection at and past its limit
through the routes, `test_stored_over_limit.py` loads a card stored over every limit through the repository, the
nightly's read path and the API (and refuses its unchanged resend without touching it),
`backend/tests/domain/test_text_types.py` pins the types, and `backend/tests/ops/test_nginx_body_limits.py` reads the
nginx configuration.

## The CLI (`python -m pipeline.users`)

No sign-up page and no email delivery: the steward creates every account.

```bash
python -m pipeline.users create --email op@example.com --display-name Op --super-admin --founding   # the operator: the founding workspace
python -m pipeline.users create --email bo@example.com --display-name Bo --password-stdin < pw.txt     # a NEW workspace "Bo's workspace"
python -m pipeline.users create --email cy@example.com --display-name Cy --workspace <uuid>         # attach to an existing workspace
python -m pipeline.users disable --email bo@example.com      # sign-in refused; live sessions + tokens stop at once
python -m pipeline.users enable --email bo@example.com       # the inverse
python -m pipeline.users set-role --email bo@example.com --role super_admin   # or --role user; effective on the next request
python -m pipeline.users reset-password --email bo@example.com [--password-stdin]   # also signs every session out
python -m pipeline.users list                                # email, role, workspace, webhook flag, created, disabled, sessions, tokens; never a hash
python -m pipeline.users token create --email op@example.com --name mcp   # prints the token ONCE
python -m pipeline.users token revoke --id <token uuid>
python -m pipeline.users oauth-client list                       # the OAuth clients, static + self-registered; never a secret
python -m pipeline.users oauth-client disable --client-id <id>   # refused everywhere; its connections stop at once
python -m pipeline.users oauth-client add --client-id desk --name "Research Desk" --redirect-uri https://desk.example.com/cb [--redirect-uri ...] [--confidential]
```

`oauth-client add` registers an app the steward vetted (self-registration is off by default, `docs/CONNECTORS.md`):
stored as a known client (`registration='static'`, so the consent page does not warn), scope `read steward` (the
consent page still caps a grant by the user's role), public with PKCE unless `--confidential`, which prints a
generated client secret ONCE and stores only its sha256. Each redirect URI must be https, or http on localhost (at
most 10). An id that exists is refused, never overwritten.

`create` makes a NEW workspace named "<display name>'s workspace" for each user unless `--workspace <uuid>` names an
existing workspace or `--founding` attaches to the founding workspace `…0002` (the two flags exclude each other). A
second `create` of an email that exists refuses and makes no workspace; it never overwrites. `list` shows each user's
workspace id and name (and `webhook` when that USER set one on the Settings page; the URL is never printed). The CLI
does not set webhooks: each user sets their own. A workspace's pushes go to every enabled user of it who set a
webhook; with none set, the env `SLACK_WEBHOOK_URL` serves only a super admin's workspace, and every other workspace
gets the log (`FEED_LOOP.md`).
`set-role` and `disable` refuse to leave no enabled super admin ("refused: this would leave no enabled super admin;
promote another user first"): both lock every super admin row, apply the change, and roll back if none enabled would
remain. To swap the super admin, promote the new one first, then demote the old one:
`set-role --email new@example.com --role super_admin`, then `set-role --email old@example.com --role user`. A role
change takes effect on the user's next request (the gate reads the role every time), for sessions and tokens alike.
In a container: `docker exec -it alphadeck-backend-1 python -m pipeline.users …`.

## Housekeeping (the nightly prune)

`python -m db.housekeeping --prune [--dry-run]` deletes the sign-in rows nobody needs any more, in one transaction,
and prints one line of counts per table (security PR 5). Each kind waits a number of days past its expiry or
revocation (`ALPHADECK_PRUNE_*_DAYS`, passed to the cron sidecar):

| Table | Deleted | Default |
|---|---|---|
| `oauth_code`, `oauth_access_token`, `oauth_refresh_token` | expired more than N days ago (a reused code or refresh token after that is simply invalid) | `PRUNE_OAUTH_DAYS` 1 |
| `oauth_consent` | UNDECIDED requests expired more than N days ago; a decided one is kept at any age (it records who allowed what) | `PRUNE_OAUTH_DAYS` 1 |
| `oauth_client` | self-registered (`dcr`) clients older than N days with no grant (live or revoked) and no remaining request; a built-in or steward-added client is never deleted | `PRUNE_DCR_CLIENT_DAYS` 7 |
| `user_session` | expired, or signed out, more than N days ago | `PRUNE_SESSION_DAYS` 7 |
| `user_action_count` | daily-allowance counts for market days more than N days ago | `PRUNE_ALLOWANCE_DAYS` 7 |

Never deleted: `oauth_grant` and `api_token` (Settings shows revoked ones grayed), and users. `--dry-run` runs the same
deletes and rolls them back, so its counts are exact. The cron sidecar runs it in each nightly wake, right before the
change-log prune (`prune_expired` in `backend/scripts/daily_cron.sh`: `timeout` 300 s, fail-open); it runs only where
the cron runs (prod) and only on nights the nightly runs.

## The first deploy (the operator's go at each step)

1. **Back up** (`scripts/deploy.sh` does it, label it `pre-workspaces`).
2. **Deploy** backend + cron + frontend (`scripts/deploy.sh`). Migration 0078 runs on boot and moves the operator's
   cards, calls, decisions and ratified rows into the founding workspace `…0002`; the app refuses every request
   until the first user exists (no credential, no entry), and the nightly keeps running.
3. **Create the operator:** `python -m pipeline.users create --email <operator> --display-name <name> --super-admin
   --founding` (in the backend container; note the printed password, change it at `/settings/profile` after the first
   sign-in). `--founding` attaches the operator to the workspace holding their cards.
4. **Mint the ops token:** `python -m pipeline.users token create --email <operator> --name ops`.
5. **`.env`:** `ALPHADECK_OPS_TOKEN=<that token>` (the deploy script's `/admin/status` probes) and
   `ALPHADECK_SYSTEM_TOKEN=<a long random value>` (the warm-up; e.g.
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Leave `ALPHADECK_COOKIE_SECURE` unset (true).
   Current Chrome and Firefox treat `http://localhost` as a secure context and keep a Secure cookie there, so the
   default should work on the laptop (if sign-in succeeds but the next page asks again, the browser dropped the
   cookie, and `ALPHADECK_COOKIE_SECURE=false` is the switch). Any other plain-http host needs `false`; after the
   server move, TLS makes the default right. Redeploy the backend and cron so they read the new values.
6. **Verify:** sign in; walk the UI; `GET /admin/status` shows `data_tenant_theses: 0`; recompute the current night's
   calls and diff them against the record (identical: moving a card to a workspace changes no call).
7. **The MCP token:** `token create --name mcp`, set `ALPHADECK_MCP_TOKEN` in the user environment, restart the
   Claude Code session ([`AGENT_TOOLS.md`](AGENT_TOOLS.md)).

## Tests

`backend/tests/app/test_auth_gate.py` makes an open route a failing test: the route-gate walk over `app.routes`
(every route but the two open ones carries `require_user` / `require_super_admin`), the 401 sweep (every operation
in the schema, no credential → 401; a 422 there would mean the gate ran after validation), the 403 sweep (a plain
user on every steward route, including the two outside `/admin` and `/backtest`: the business-type re-tag and
Surface an ETF), the system principal, and the login / logout / password / disable / throttle /
expiry / revocation / cookie-flag cases, and lists the OAuth authorization server's five routes (open by nature)
and the two consent routes (`require_user`); `backend/tests/app/test_oauth.py` holds the sign-in to its rules
(PKCE, single-use codes, refresh rotation and replay, session-only consent, the role cap, a read-only token refused
on every non-GET operation, connected apps). Every other API test runs signed in as a super admin through an autouse
override (`tests/auth_fixtures.py`, wired in `tests/app/conftest.py` and `tests/dashboards/conftest.py`).
