# Auth — the login door

Every route of the API sits behind a login. This page is the model, the roles, the three ways a caller is
recognized, the user CLI, and the first-deploy order. Code: `backend/app/auth.py` (the gate),
`backend/app/routers/auth.py` (the `/auth` routes), `backend/app/routers/account.py` (the `/account` routes the
Settings page uses), `backend/pipeline/users.py` (the CLI), migrations `0077_auth.sql` and `0079_user_meta.sql`; the
guards are `backend/tests/app/test_auth_gate.py`.

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
| `user_meta` | the user's PREFERENCES, 1:1 with `app_user` (`user_id` PK; 0079): one typed column per setting, NULL = unset, a missing row = every setting unset; first `slack_webhook_url` (their own push destination), `created_at`, `updated_at`. Not the gate's table: nothing here rides into `/auth/me` |

Tokens (session and API) are `secrets.token_urlsafe(32)` (256 bits); the database keeps only their sha256, so a
read of these tables cannot be replayed as a credential. Nothing is deleted: sign-out, revocation and disabling
stamp a column, and re-enabling clears it.

## The three principals

`require_user` (mounted on every router in `app/main.py`, on the ROUTER, so a request with no credential answers
401 before any parameter is validated) tries, in order:

1. **session** — the `alphadeck_session` cookie → a live, unexpired, unrevoked session of an enabled user. The
   idle deadline is `ALPHADECK_SESSION_IDLE_DAYS` (default 30) and slides forward on use; `last_seen_at` is written
   at most once per 5 minutes.
2. **token** — `Authorization: Bearer <token>` → an unrevoked API token of an enabled user (`last_used_at` likewise).
   For the MCP agent tools (`ALPHADECK_MCP_TOKEN`, [`AGENT_TOOLS.md`](AGENT_TOOLS.md)), the deploy script's probes
   (`ALPHADECK_OPS_TOKEN`, [`DEPLOY.md`](DEPLOY.md)) and curl.
3. **system** — `Authorization: Bearer $ALPHADECK_SYSTEM_TOKEN` (an env secret on backend + cron; unset = no
   system principal). It can only `GET` under `/dashboards/`, `/clock` and `/health`; anything else answers 403.
   The cron's dashboard warm-up (`pipeline.warm_dashboards`) uses it; unset, the warm-up prints one loud line and
   exits 1 instead of looping on 401s.

Else 401. A disabled user's sessions and tokens stop working at once (the gate checks `disabled_at` on every
request). `require_super_admin` = `require_user` + the role, else 403. The secret compare is constant-time.

Open routes: `GET /health` and `POST /auth/login`, nothing else. `/docs`, `/redoc` and `/openapi.json` are served
behind `require_user` (the generated contract `backend/openapi.json` is exported in-process and is unaffected).

## The routes

| Route | What it does |
|---|---|
| `POST /auth/login` | `{email, password}` → sets the session cookie, returns the user. 401 on a wrong pair (the same answer for an unknown email, a wrong password and a disabled account, and an unknown email still spends one argon2 verify, so timing does not reveal who has an account). 429 after `ALPHADECK_LOGIN_MAX_FAILURES` (5) failures for one (email, client address) inside `ALPHADECK_LOGIN_WINDOW_S` (60 s), for `ALPHADECK_LOGIN_LOCKOUT_S` (60 s). In-process, like the draft-job registry (one worker). Behind the nginx proxy the client address is the proxy's, so in practice the throttle is per email. |
| `POST /auth/logout` | revokes this browser's session, clears the cookie |
| `GET /auth/me` | `{id, email, display_name, role, workspace_id}` |
| `POST /auth/change-password` | `{current, new}`; `new` needs at least 12 characters; every OTHER session of the user is signed out |
| `PUT /account/profile` | `{display_name, email}` → the user (`/auth/me`'s shape). 400 on an empty name or an email with no `@`; 409 `that email is already in use` when another user has it (case-insensitively); changing only the case of your own email is allowed. Sessions and tokens stay signed in |
| `GET /account/notifications/slack-webhook` | `{set, updated_at}`; the URL is NEVER returned, by this or any route (a webhook URL is a credential) |
| `PUT /account/notifications/slack-webhook` | `{url}` → 204; the URL must start with `https://hooks.slack.com/` (400 otherwise); replaces any earlier one, stamps `updated_at` |
| `DELETE /account/notifications/slack-webhook` | → 204; clears the URL (the row stays); a no-op when none is set |
| `POST /account/notifications/slack-webhook/test` | → `{delivered, detail}`: one plain line ("Alpha Deck test message for <name>") through the real Slack POST path, 10 s timeout; a failure is `delivered: false`, never an error. 404 when none is saved |
| `GET /account/agents` | `{hosted, connector_url, steward, stdio_server}`: `hosted` when `ALPHADECK_PUBLIC_ORIGIN` is set, and then `connector_url` = `<origin>/mcp` (else null); `steward` = the caller is a super admin; `stdio_server` = true (the local agent tools server exists on every install). See `AGENT_TOOLS.md` §Hosted endpoint |
| `GET /account/tokens` | `{tokens: [{id, name, created_at, last_used_at, revoked_at}]}`: my own API tokens, live first, then revoked, newest first; never the token or its hash |
| `POST /account/tokens` | `{name}` → 201 `{id, name, created_at, token}`: the token is in THIS reply only, never again (only its sha256 is stored). The name is trimmed, 1 to 60 characters (400 otherwise); at most 20 live tokens per user (409 `too many tokens; revoke one first`) |
| `DELETE /account/tokens/{token_id}` | → 204; my own token only: another user's token or an unknown id is 404 `no such token` (alike, never 403); an already revoked token is 204; a token may revoke itself. It stops working on its next request |

Every `/account` route acts on the caller's own rows (no user id in any path); the system token is refused (403).

## The Settings page

`/settings/profile` (display name + email, and the change-password form below them) and
`/settings/notifications` (the user's own Slack webhook: a status line, a write-only URL field, Save, and when one is
set Remove and Send a test message) and `/settings/agents` (how an AI client reaches the user's workspace: the
connector URL and the four client how-tos when the install is hosted, a quiet paragraph when not; and the user's own
access tokens: create one, shown once until Done; revoke one, which stays on screen grayed for the visit; "Show
revoked tokens"; `AGENT_TOOLS.md` §Hosted endpoint). `/settings` and an unknown tab go to `/settings/profile`; the old `/account`
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
| User and token management | own name, email, password and Slack webhook, and own API tokens on the Agents tab (`/settings`) | the CLI, plus their own on `/settings` |
| The Agents tab (`/settings/agents`) | yes: the connector URL and their own tokens | yes, plus one sentence on the steward scope |

The backend enforces every row; the frontend only hides what a role cannot use.

## The CLI (`python -m pipeline.users`)

No sign-up page and no email delivery: the steward creates every account.

```bash
python -m pipeline.users create --email op@example.com --display-name Op --super-admin --founding   # the operator: the founding workspace
python -m pipeline.users create --email bo@example.com --display-name Bo --password-stdin < pw.txt     # a NEW workspace "Bo's workspace"
python -m pipeline.users create --email cy@example.com --display-name Cy --workspace <uuid>         # attach to an existing workspace
python -m pipeline.users disable --email bo@example.com      # sign-in refused; live sessions + tokens stop at once
python -m pipeline.users enable --email bo@example.com       # the inverse
python -m pipeline.users reset-password --email bo@example.com [--password-stdin]   # also signs every session out
python -m pipeline.users list                                # email, role, workspace, webhook flag, created, disabled, sessions, tokens; never a hash
python -m pipeline.users token create --email op@example.com --name mcp   # prints the token ONCE
python -m pipeline.users token revoke --id <token uuid>
```

`create` makes a NEW workspace named "<display name>'s workspace" for each user unless `--workspace <uuid>` names an
existing workspace or `--founding` attaches to the founding workspace `…0002` (the two flags exclude each other). A
second `create` of an email that exists refuses and makes no workspace; it never overwrites. `list` shows each user's
workspace id and name (and `webhook` when that USER set one on the Settings page; the URL is never printed). The CLI
does not set webhooks: each user sets their own. A workspace's pushes go to every enabled user of it who set a
webhook; with none set, the env `SLACK_WEBHOOK_URL` serves only a super admin's workspace, and every other workspace
gets the log (`FEED_LOOP.md`).
In a container: `docker exec -it alphadeck-backend-1 python -m pipeline.users …`.

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
user on every steward route), the system principal, and the login / logout / password / disable / throttle /
expiry / revocation / cookie-flag cases. Every other API test runs signed in as a super admin through an autouse
override (`tests/auth_fixtures.py`, wired in `tests/app/conftest.py` and `tests/dashboards/conftest.py`).
