# Auth — the login door

Every route of the API sits behind a login. This page is the model, the roles, the three ways a caller is
recognized, the user CLI, and the first-deploy order. Code: `backend/app/auth.py` (the gate),
`backend/app/routers/auth.py` (the `/auth` routes), `backend/pipeline/users.py` (the CLI), migration
`0077_auth.sql`; the guards are `backend/tests/app/test_auth_gate.py`.

**One workspace in this release.** Every user sits on tenant `…0001` (`db.session.DEFAULT_TENANT_ID`), the one
tenant the app has always used, so what a signed-in user SEES is exactly what the app showed before: every call,
score and dashboard is unchanged. Private workspaces per user are the next change (they move the operator's cards
off the shared data tenant); until then a second account would see the same cards.

## The model

| Table | What it holds |
|---|---|
| `app_user` | `email` (unique case-insensitively), `display_name`, `password_hash` (argon2id; the password itself is never stored), `role` (`user` / `super_admin`), `workspace_id`, `created_at`, `disabled_at` |
| `user_session` | one browser sign-in: the sha256 of the cookie's random token, `created_at`, `last_seen_at`, `expires_at` (the idle deadline), `revoked_at` |
| `api_token` | one named bearer token: its sha256, `created_at`, `last_used_at`, `revoked_at` |

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
| User and token management | own password only (`/account`) | the CLI |

The backend enforces every row; the frontend only hides what a role cannot use.

## The CLI (`python -m pipeline.users`)

No sign-up page and no email delivery: the steward creates every account.

```bash
python -m pipeline.users create --email ana@example.com --display-name Ana --super-admin   # prints a 20-char password ONCE
python -m pipeline.users create --email bo@example.com --display-name Bo --password-stdin < pw.txt
python -m pipeline.users disable --email bo@example.com      # sign-in refused; live sessions + tokens stop at once
python -m pipeline.users enable --email bo@example.com       # the inverse
python -m pipeline.users reset-password --email bo@example.com [--password-stdin]   # also signs every session out
python -m pipeline.users list                                # email, role, workspace, created, disabled, sessions, tokens; never a hash
python -m pipeline.users token create --email ana@example.com --name mcp   # prints the token ONCE
python -m pipeline.users token revoke --id <token uuid>
```

`--workspace <uuid>` defaults to `…0001`. A second `create` of an email that exists refuses; it never overwrites.
In a container: `docker exec -it alphadeck-backend-1 python -m pipeline.users …`.

## The first deploy (the operator's go at each step)

1. **Back up** (`scripts/deploy.sh` does it, label it `pre-auth`).
2. **Migrate** 0077 from a host venv against the target DB (`python -m db.migrate` with `DATABASE_URL` pointed at
   it), so the user can be created before the new image starts refusing every request.
3. **Create the steward:** `python -m pipeline.users create --email <operator> --display-name <name> --super-admin`
   (note the printed password; change it at `/account` after the first sign-in).
4. **Mint the ops token:** `python -m pipeline.users token create --email <operator> --name ops`.
5. **`.env`:** `ALPHADECK_OPS_TOKEN=<that token>` (the deploy script's `/admin/status` probes) and
   `ALPHADECK_SYSTEM_TOKEN=<a long random value>` (the warm-up; e.g.
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Leave `ALPHADECK_COOKIE_SECURE` unset (true).
   Current Chrome and Firefox treat `http://localhost` as a secure context and keep a Secure cookie there, so the
   default should work on the laptop (not yet checked in a browser on prod: if sign-in succeeds but the next page
   asks again, the browser dropped the cookie, and `ALPHADECK_COOKIE_SECURE=false` is the switch). Any other
   plain-http host needs `false`; after the server move, TLS makes the default right.
6. **Deploy** backend + frontend + cron (`scripts/deploy.sh`). Sign in; walk the UI; recompute the current night's
   calls and diff them against the record (identical: nothing in this change reads or writes a call).
7. **The MCP token:** `token create --name mcp`, set `ALPHADECK_MCP_TOKEN` in the user environment, restart the
   Claude Code session ([`AGENT_TOOLS.md`](AGENT_TOOLS.md)).

## Tests

`backend/tests/app/test_auth_gate.py` makes an open route a failing test: the route-gate walk over `app.routes`
(every route but the two open ones carries `require_user` / `require_super_admin`), the 401 sweep (every operation
in the schema, no credential → 401; a 422 there would mean the gate ran after validation), the 403 sweep (a plain
user on every steward route), the system principal, and the login / logout / password / disable / throttle /
expiry / revocation / cookie-flag cases. Every other API test runs signed in as a super admin through an autouse
override (`tests/auth_fixtures.py`, wired in `tests/app/conftest.py` and `tests/dashboards/conftest.py`).
