# Auth

Who may call the API and what each caller may do: the four ways the gate recognizes a caller, the sign-in rules,
the routes a user manages their own account with, the steward-only surface, the per-user daily allowances, the
per-workspace caps, the user CLI and the nightly prune of sign-in rows. Read it before you add a route, a role
check, a setting a user can spend, or a sign-in table. Errors and request limits are in [SECURITY](SECURITY.md);
the OAuth sign-in for AI clients is in [CONNECTORS](CONNECTORS.md); what a workspace is, in
[TENANT_SCOPES](TENANT_SCOPES.md).

## Code

| Piece | Module |
|---|---|
| The gate (`require_user`, `require_super_admin`), sessions, tokens, password checks | `backend/app/auth.py` |
| `/auth` routes | `backend/app/routers/auth.py` |
| `/account` routes (the Settings page) | `backend/app/routers/account.py` |
| OAuth consent routes and the authorization server | `backend/app/routers/oauth_consent.py`, `backend/app/oauth/` |
| Daily allowances | `backend/app/allowance.py` |
| Workspace caps | `backend/app/workspace_caps.py` |
| The user CLI | `backend/pipeline/users.py` |
| The prune | `backend/db/housekeeping.py` |
| Tables | migrations `0077_auth.sql`, `0079_user_meta.sql`, `0080_oauth.sql`, `0084_user_action_count.sql`; the caps columns in 0203 |

`app_user` holds the email (unique case-insensitively), the argon2id password hash, the role (`user` or
`super_admin`, the steward), the workspace and `disabled_at`. `user_session` and `api_token` hold the sha256 of a
`secrets.token_urlsafe(32)` token, never the token, so a read of these tables cannot be replayed as a credential.
`user_meta` holds a user's preferences (today the Slack webhook); nothing in it rides into `/auth/me`. The
`oauth_*` tables are described in [CONNECTORS](CONNECTORS.md).

Request paths never delete a sign-in row: sign-out, revocation and disabling stamp a column, and re-enabling clears
it. The nightly prune deletes expired rows ("Housekeeping" below).

## How it works

### The four principals

`require_user` is mounted on every router in `app/main.py`, on the router itself, so a request with no credential
answers 401 before any parameter is validated. It tries, in order:

| Principal | Credential | What it may do |
|---|---|---|
| `session` | the `alphadeck_session` cookie of a live session | everything the user's role allows; the idle expiry slides forward on use |
| `token` | `Authorization: Bearer <token>` matching an unrevoked `api_token` | the same; used by the MCP agent tools, the deploy script's probes and curl |
| `oauth` | a bearer matching a live OAuth access token | read only: any method but `GET`, `HEAD` or `OPTIONS` is 403 (no token carries `write`); the role is `super_admin` only when the user is one and the token carries `steward`; never `/account/*` or `/auth/change-password` (`auth.oauth_may`) |
| `system` | the `ALPHADECK_SYSTEM_TOKEN` secret (constant-time compare) | `GET` under `/dashboards/`, `/clock` and `/health` only, else 403 (`auth.system_may`); the cron's dashboard warm-up uses it, and with it unset the warm-up prints one line and exits 1 |

Else 401. The gate reads `disabled_at` and the role on every request, so disabling a user, revoking a token and a
role change all take effect on the next request. `last_seen_at` and `last_used_at` are written at most every five
minutes. `require_super_admin` is `require_user` plus the role, else 403.

Open routes: `GET /health` and `POST /auth/login`. `/docs`, `/redoc` and `/openapi.json` sit behind
`require_user`. When `ALPHADECK_PUBLIC_ORIGIN` is set, `app/main.py` also mounts, outside the gate, the hosted
`/mcp` endpoint with its RFC 9728 document and the OAuth authorization server: its RFC 8414 document, `/authorize`,
`/token` and `/revoke`, plus `/register` only when `ALPHADECK_OAUTH_DYNAMIC_REGISTRATION` is on. Each
authenticates its own way; none reads workspace data without a user's token ([CONNECTORS](CONNECTORS.md)).

### Sign-in protections

- An unknown email, a wrong password and a disabled account get the same 401, and an unknown email still spends one
  argon2 verify, so timing does not reveal who has an account.
- Failed sign-ins are counted per client address, whatever email was typed (`auth.throttle_key`; the address
  comes from `app/client_address.py`, [SECURITY](SECURITY.md) "The caller's address"). Past the limit the address
  gets 429 for a short pause. A success does not reset the count: otherwise an attacker holding one account could
  keep guessing between sign-ins.
- Only a few argon2 checks run at once (`auth.password_check_slot`, sign-in and change-password alike); a check
  that finds no slot in time answers 503 with `Retry-After`, and a 503 never counts as a failure. The throttle and
  the slots are in-process (one API worker).
- A new password has at least `auth.MIN_PASSWORD_LEN` characters. Changing it signs out every other session.

### The cookie and CSRF

`alphadeck_session` is `HttpOnly; SameSite=Lax; Path=/`, plus `Secure` when `ALPHADECK_COOKIE_SECURE` is true (the
default; the dev overlay sets it false). Current Chrome and Firefox keep a `Secure` cookie on `http://localhost`;
if sign-in succeeds and the next page asks again, the browser dropped the cookie, and on a plain-http host other
than localhost the setting must be false. The frontend keeps no credential in browser storage.

There is no CSRF token. SameSite=Lax keeps the cookie off cross-site POSTs, and the API reads JSON bodies only: a
cross-site form cannot send `application/json` without a preflight the API never grants. Two GET routes do write:
`GET /workbench/securities/{id}/extract` and `GET /workbench/securities/{id}/etf-holdings` charge the caller's daily
allowance (one `user_action_count` upsert) before they fetch. A cross-site top-level navigation carries a Lax
cookie, so another site can spend a signed-in user's allowance units for those two actions; it reads and changes
nothing else.

## The routes

Every `/account` route acts on the caller's own rows (no user id in a path) and refuses the system token and an
`oauth` principal (403).

| Route | Who | Notes |
|---|---|---|
| `POST /auth/login` | anyone | sets the cookie; the protections above |
| `POST /auth/logout`, `GET /auth/me` | anyone signed in | `/auth/me` adds a `token` object (principal, client id, scopes) for a bearer; the hosted `/mcp` endpoint reads it |
| `POST /auth/change-password` | a session or a personal token | 400 on a wrong current password |
| `PUT /account/profile` | the user | display name and email; 409 when another user has the email |
| `GET`, `PUT`, `DELETE /account/notifications/slack-webhook`; `POST .../test` | the user | the URL is write-only (a webhook URL is a credential) and must be a Slack hooks URL; the test posts one line through the real path |
| `GET /account/agents` | the user | whether the install is hosted, the connector URL, whether OAuth is mounted |
| `GET`, `POST /account/tokens`; `DELETE /account/tokens/{id}` | the user | a new token is in the create reply only; at most `account.MAX_LIVE_TOKENS` live; another user's id is 404, never 403 |
| `GET /account/connections`; `DELETE /account/connections/{id}` | the user | the apps connected through OAuth; a disconnect stops every token under the grant |
| `GET`, `POST /oauth/consent/{id}` | a browser session only | 403 for any bearer, so no app approves its own request ([CONNECTORS](CONNECTORS.md)) |

`backend/tests/app/test_account.py` and `test_account_tokens.py` pin the `/account` rules.

## The Settings page

`/settings/<tab>` holds Profile (name, email, password), Notifications (the user's own Slack webhook) and Agents
(the connector URL when hosted, the user's access tokens, the connected apps). The tabs come from one registry,
`frontend/src/settings/registry.ts`: a new tab is one folder and one entry. It is reached from the user menu. The
OAuth consent page sits inside the signed-in shell; a signed-out visitor goes through `/login?next=...` and comes
back (`frontend/src/auth/next.ts` honors that one path shape only). The webhook receives the pushes for the user's
workspace and the nightly digest, never the ops pages ([FEED_LOOP](FEED_LOOP.md) step 3).

## Who can do what

The backend enforces every row; the frontend only hides what a role cannot use.

| Surface | Plain user | Steward | Enforced by |
|---|---|---|---|
| Board, Workbench, Scoreboard, dashboards, radar, decisions | own workspace | own workspace | `require_user`, `app.deps.get_workspace` |
| Admin and every `/admin/*` route; `/backtest/*` | 403 (nav hidden) | yes | `require_super_admin` on the router (`app/main.py`) |
| Business-type re-tag, Surface an ETF (`resolve-etf`), the price pull (`ingest-prices`), the flag explainer (`facts/explain`) | 403 | yes | `require_super_admin` on the route |
| Share count or cash/burn on `POST /workbench/facts` | 403 | yes, to the data tenant | the route's steward check |
| Purity on `POST /workbench/facts` | own workspace | own workspace | `get_workspace` |
| Extract and holdings | own cards only, within the allowance; extract is the purity candidate only | any company, uncounted, all three candidates | `require_own_security`, `allowance.charge` |
| Chain draft, term and tier runs | within the allowance | uncounted | `allowance.charge` |
| A flag on a shared value (`/value-flags`) | own cards only, one open flag per value, within the allowance | 403 on raise and withdraw; closes and reopens flags on Admin | `routers/value_flags.py` ([VALUE_FLAGS](VALUE_FLAGS.md)) |
| Add a fund or a SPAC to a card (`/radar/funds/attach`, `/radar/spac/attach`) | a fund must already be in the company list (else 404, nothing written); within the caps | may also add the fund to the company list | the route; `workspace_caps.check` |
| Accounts, roles, caps, OAuth clients | own profile, password, webhook, tokens and apps | the same | the operator's CLI below |

**No user-authored writes to the shared company master.** Only a steward adds or changes a `security_master` row.
A ticker typed into a fund route passes the `securities/symbols.py` pattern before it reaches the master or an
OpenFIGI request. A plain user's chain draft lays parsed identity over the master for that response only; a
steward's draft stores it. `backend/tests/app/test_shared_master_writes.py` pins it with a grep guard that allows a
master writer under `app/routers/` (and one hop into `backend/workbench/`) only at a listed steward-gated site.

`backend/tests/app/test_auth_gate.py` makes an open route a failing test: it walks `app.routes`, sends every
operation without a credential (401, never 422), sends a plain user to every steward route (403), and covers the
system principal, the cookie flags, throttling, expiry and revocation. `test_oauth.py` holds the `oauth` rules.
Every other API test runs as a super admin through an autouse override (`tests/auth_fixtures.py`).

## User-triggered fetches

A plain user's click may still start a live fetch of server-sourced data (SEC, the price vendor, a model), because
the Workbench is unusable without it. Each fetch is bounded:

- **Own cards only.** Extract and holdings answer 404 for a company that is not on one of the caller's own cards
  (`workbench.require_own_security`), checked before anything is counted. Every screen that fetches works on a
  saved card's members.
- **A daily allowance per user and action** (`app/allowance.py`): each fetch counts one unit in
  `user_action_count` for the current market day (`domain.market_time.market_today`). Past the day's allowance the
  route answers 429 naming the reset. The count is one upsert, so racing requests never pass the limit; a request
  is counted when admitted, so a fetch that then fails still spent its unit. `0` means no limit. The steward and
  the system principal are never counted. The actions are extract, holdings, chain draft, term runs (terms and
  tier recommendations share one count), flags (they bound the steward's Slack messages; a refused flag spends
  nothing, a withdrawal never refunds) and new names loaded at once. That last one keeps the key
  `ALPHADECK_ALLOWANCE_PRICE_PULL_PER_DAY` so the variable name does not change, though no user route pulls prices.
- **One load path for a new name.** A promote, a fund attach or a SPAC attach starts its companies' data load
  through `app.routers.workbench.start_member_load`: the fast lane while today's units last, else the onboarding
  queue (the answer says which).
- **No database transaction held across the network.** Request routes end their read before calling SEC, a vendor
  or a model (`backend/tests/app/test_read_then_call.py`).

`backend/tests/app/test_user_fetches.py` pins all of it with a real plain user and a real steward.

## Workspace caps

What a workspace holds is a platform cost, so three caps on its `tenant` row bound it: live cards, distinct
companies over the live cards' basket rows (silent links included, a company on two cards counted once), and
search terms per card. NULL means no cap; zero is a real cap. `pipeline.users create` writes the configured
defaults into a new workspace; attaching a user to an existing workspace leaves its caps alone, and a workspace
older than the caps keeps NULL until the operator sets them.

**The rule (`app/workspace_caps.py`):** a request is refused only when a count would end above its cap and above
the count before the request. Why: a workspace already over a cap must still read, edit, remove, archive and
re-promote; only a request that adds past a cap is refused. The refusal is a 409 naming the count and the limit,
raised before any write, so nothing is written and no name is dropped. `workspace_caps.check` takes a
per-workspace advisory transaction lock and the route commits in that transaction, so two racing adds cannot both
take the last slot. Every route that adds calls it: the promote, unarchive, the fund and SPAC attaches, and the two
term routes. Caps belong to the workspace, so the steward is not exempt.

`backend/tests/app/test_workspace_caps.py` pins it, including two racing promotes for the last slot.

## The CLI (`python -m pipeline.users`)

There is no sign-up page and no email delivery: the operator creates every account in the backend container
(`docker exec -it alphadeck-backend-1 python -m pipeline.users ...`). Flags are in `--help`.

| Subcommand | What it does |
|---|---|
| `create` | a user and, by default, a new workspace with the default caps; `--workspace <uuid>` attaches to an existing one; `--founding` attaches to the founding workspace and is only for the operator who holds the founding cards |
| `disable`, `enable` | refuse or restore sign-in; live sessions and tokens stop at once |
| `set-role` | `user` or `super_admin`, effective on the next request |
| `reset-password` | a new password; signs every session out |
| `list` | users, roles, workspaces, whether a webhook is set, sessions and tokens; never a hash or a URL |
| `token create`, `token revoke` | a personal access token, printed once |
| `oauth-client list`, `add`, `disable` | the OAuth clients ([CONNECTORS](CONNECTORS.md)) |
| `caps` | print or set one workspace's caps |

- `create` of an email that exists refuses; nothing is ever overwritten. Generated passwords and tokens are
  printed once and stored only as a hash.
- `set-role` and `disable` refuse to leave no enabled super admin. To swap the steward, promote the new one first,
  then demote the old one.
- The CLI never sets a webhook; each user sets their own on the Settings page. A workspace's pushes go to every
  enabled member who set one; with none, the env `SLACK_WEBHOOK_URL` serves only a super admin's workspace, and
  every other workspace gets the log.

`backend/tests/pipeline/test_users.py` covers the commands.

## Housekeeping (the nightly prune)

`python -m db.housekeeping --prune [--dry-run]` deletes, in one transaction, the sign-in rows nobody needs, each a
number of days past its expiry or revocation (`ALPHADECK_PRUNE_*_DAYS`):

- expired OAuth codes, access tokens and refresh tokens (a reused one after that is simply invalid);
- undecided OAuth sign-in requests past expiry; a decided one is kept, since it records who allowed what;
- self-registered OAuth clients past an age with no grant and no request; a static client is never deleted;
- expired or signed-out browser sessions;
- daily-allowance counts for old market days.

It never deletes `oauth_grant`, `api_token` or a user (Settings shows revoked ones grayed). `--dry-run` runs the
same deletes and rolls them back, so its counts are exact. The cron sidecar runs it in each nightly wake, after the
editions page and before the change-log prune (`prune_expired` in `backend/scripts/daily_cron.sh`, bounded by
`timeout`, fail-open), so it runs only where the cron runs. `backend/tests/db/test_housekeeping.py` pins it.

## Settings

Defaults live in `domain/settings.py`. A Setting reaches a container only through `docker-compose.yml`'s
`environment:` list; `backend/tests/domain/test_settings.py` checks that compose's fallbacks match the defaults.

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_COOKIE_SECURE` | `cookie_secure` | yes (backend; the dev overlay sets false) |
| `ALPHADECK_SESSION_IDLE_DAYS` | `session_idle_days` | no |
| `ALPHADECK_SYSTEM_TOKEN` | `system_token` | yes (backend and cron) |
| `ALPHADECK_LOGIN_MAX_FAILURES`, `_WINDOW_S`, `_LOCKOUT_S` | `login_*` | yes (backend) |
| `ALPHADECK_PASSWORD_CHECK_CONCURRENCY`, `_WAIT_S` | `password_check_*` | yes (backend) |
| `ALPHADECK_PUBLIC_ORIGIN` | `public_origin` ([AGENT_TOOLS](AGENT_TOOLS.md) "Hosted endpoint") | yes (backend) |
| `ALPHADECK_OAUTH_DYNAMIC_REGISTRATION` | `oauth_dynamic_registration` | yes (backend) |
| `ALPHADECK_OAUTH_AUTHORIZE_PER_ADDRESS_HOUR`, `_PER_HOUR` | `oauth_authorize_*` | yes (backend) |
| `ALPHADECK_ALLOWANCE_<ACTION>_PER_DAY` (six) | `allowance_*_per_day` | yes (backend) |
| `ALPHADECK_WORKSPACE_CAP_<CAP>_DEFAULT` (three) | `workspace_cap_*_default` | yes (backend) |
| `ALPHADECK_PRUNE_SESSION_DAYS`, `_OAUTH_DAYS`, `_DCR_CLIENT_DAYS`, `_ALLOWANCE_DAYS` | `prune_*_days` | yes (cron) |
| `ALPHADECK_OPS_TOKEN` | `scripts/deploy.sh` (read from `.env` on the host) | not a Setting |

## Known limits

- `GET .../extract` and `GET .../etf-holdings` write an allowance count, so a cross-site navigation can spend a
  signed-in user's units for those two actions ("The cookie and CSRF").
- The login throttle and the password-check slots are in-process: they hold for the one API worker the image runs.
- Prod runs as the local compose stack; anything beyond localhost needs TLS first ([SECURITY](SECURITY.md) "TLS").

## Related

[SECURITY](SECURITY.md) (errors, request limits, the caller's address), [CONNECTORS](CONNECTORS.md) (OAuth),
[TENANT_SCOPES](TENANT_SCOPES.md) (workspaces), [AGENT_TOOLS](AGENT_TOOLS.md) (the MCP token),
[VALUE_FLAGS](VALUE_FLAGS.md) (flags on shared values).
