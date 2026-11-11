# Connectors — signing an AI client in with OAuth

An AI client (claude.ai's custom connectors, Claude Code's `/mcp` sign-in; ChatGPT and Grok in a later phase) reads a
user's workspace through the hosted agent tools endpoint `<origin>/mcp` ([`AGENT_TOOLS.md`](AGENT_TOOLS.md) §Hosted
endpoint). It can sign in two ways: a personal access token pasted into the client (Settings › Agents, unchanged), or
the **OAuth sign-in** this page describes: the client sends the user to OUR consent page, the user signs in with their
Alpha Deck email and password, clicks Allow, and the client gets its own token. Read-only: a connected app can read,
never write.

Code: `backend/app/oauth/` (`store.py` the tables, `provider.py` the MCP SDK provider, `server.py` the routes),
`backend/app/routers/oauth_consent.py` (the consent page's API), the `oauth` principal in `backend/app/auth.py`,
`/account/connections` in `backend/app/routers/account.py`, migration `0080_oauth.sql`; the page
`frontend/src/oauth/ConsentPage.tsx` and the Connected apps section of `frontend/src/settings/agents/AgentsTab.tsx`.
Tests: `backend/tests/app/test_oauth.py`, the AS and consent-route checks in `backend/tests/app/test_auth_gate.py`.

## The flow

```
client → GET https://<origin>/authorize?client_id&redirect_uri&code_challenge(S256)&state&scope&resource
       ← 302 /oauth/consent/<consent id>              (the SDK handler checked the client; our provider stored a PENDING consent)
browser → the SPA: signed out? → /login?next=/oauth/consent/<id> → sign in → back
        → GET  /api/oauth/consent/<id>  (session cookie ONLY) → {client, scopes, redirect_host, expires_at}
        → the page: "<client> wants to read your Alpha Deck workspace … Allow / Deny"
        → POST /api/oauth/consent/<id> {decision}  (session cookie ONLY) → {redirect_to}
        → window.location = redirect_to = <redirect_uri>?code=…&state=…&iss=<issuer>   (or error=access_denied)
client → POST https://<origin>/token (form: grant_type=authorization_code, code, code_verifier, client_id, redirect_uri, resource)
       ← {access_token (1 h), refresh_token (rotating, 30 d), token_type: Bearer, expires_in, scope}
client → POST https://<origin>/mcp  Authorization: Bearer <access_token>   → the hosted endpoint, principal "oauth"
```

The authorization server is the MCP SDK's own (`mcp==2.3.0`, `create_auth_routes`): its handlers parse the requests,
check PKCE S256, the redirect URI, the scopes and the client's credentials, and write the RFC error bodies. Ours is the
provider underneath (the tables) and ONE replaced route, the metadata document. Every token and code is an opaque
`secrets.token_urlsafe(32)`, never a JWT; only its sha256 is stored.

## The routes

| Route | Who answers | Notes |
|---|---|---|
| `GET /.well-known/oauth-authorization-server` | ours (RFC 8414) | the SDK's document plus `none` among `token_endpoint_auth_methods_supported` and `revocation_endpoint_auth_methods_supported`, and `authorization_response_iss_parameter_supported: true`; `issuer` is the SAME string the protected-resource document names in `authorization_servers` (the origin with a trailing slash, pydantic's spelling) |
| `GET /authorize` | the SDK → `provider.authorize` | stores a pending consent (ten minutes) and redirects the browser to `/oauth/consent/<id>`; a `resource` other than `<origin>/mcp` is an `invalid_target` redirect; a missing one is bound to `<origin>/mcp` |
| `POST /token` | the SDK → the provider | form-encoded only; the code grant and the refresh grant |
| `POST /register` | the SDK → `provider.register_client` | dynamic client registration (RFC 7591), with the hygiene below |
| `POST /revoke` | the SDK → `provider.revoke_token` | RFC 7009; an access token revokes itself, a refresh token revokes the whole grant |
| `GET` / `POST /oauth/consent/{id}` | ours, behind `require_user` | the consent page's read and decision; the browser SESSION only (403 for any bearer, so no app can approve its own request) |
| `GET /account/connections`, `DELETE /account/connections/{grant_id}` | ours | the Connected apps section |

The five authorization-server routes are Starlette routes outside the API's route-gate walk, open by nature (each
authenticates its own way); the gate test lists them. They exist only when `ALPHADECK_PUBLIC_ORIGIN` is set, mounted
beside `/mcp` from `app/main.py`.

## The tables (migration 0080)

Access-control state, not facts: not bitemporal, no `tenant_id` (a grant belongs to a USER; the login door picks the
workspace), and the app deletes no row.

| Table | What it holds |
|---|---|
| `oauth_client` | `client_id`, `client_name`, `redirect_uris`, `scope` (what it may ask for), `token_endpoint_auth_method` (`none` / `client_secret_post` / `client_secret_basic`), `client_secret_hash` (sha256), `registration` (`static` / `dcr`), `grant_types`, `metadata` (the raw registration document, never the secret), `created_at`, `disabled_at` |
| `oauth_consent` | the pending request between `/authorize` and the click: client, redirect URI, requested scopes (`read` always included), PKCE challenge, `state`, `resource`, `expires_at` (ten minutes); once decided, `user_id`, `decision`, `scopes_granted`, `decided_at` |
| `oauth_grant` | one LIVE row per (user, client): the Connected apps row. A second consent for the same client updates its scopes. Revoking it kills every token below it |
| `oauth_code` | single use, ten minutes, bound to the grant, the scopes, the redirect URI, the PKCE challenge and the resource. A used code presented again revokes its grant |
| `oauth_access_token` | one hour; `last_used_at` touched at most every five minutes |
| `oauth_refresh_token` | rotating: a use issues a successor (thirty days from then) and stamps `rotated_to`; presenting a rotated token again revokes the grant (replay detection) |

## Scopes and what each lets a client do

| Scope | The consent page's words | Who can grant it |
|---|---|---|
| `read` | "Read your cards, calls, decisions and ratified facts" | every user (always on the page, added when the app asked for nothing) |
| `steward` | "Read the Admin readouts (the split list, the status)" | a super admin only; a client granted it lists `get_splits` and `get_admin_status` and the `splits-review` prompt; a plain user sees it struck, "not available to your account", and Allow leaves it out |

At the API (the `oauth` principal, [`AUTH.md`](AUTH.md)): a token without `write` (none carries it) answers 403 on
every non-GET request, inside the gate; the role is `super_admin` only when the user is one AND the token carries
`steward`; `/account/*` and `/auth/change-password` refuse it. At `/mcp` the token's scopes become the tool principal's.
Fourteen tools need `read`; `get_splits` and `get_admin_status` need `steward`, so a connector granted `read steward` by a super admin lists 16 tools and 8 prompts, and any other lists 14 and 7.

## The consent page

`/oauth/consent/<id>`, inside the signed-in shell (a signed-out visitor goes through `/login?next=…` and comes back;
the login page honors `next` for that one path shape only). Quiet: the app's name, "A known client." for the two
static clients or "This app registered itself under that name. Alpha Deck has not verified it." with an `unverified`
tag for a self-registered one (its name is text only, never a link, cut at 60 characters), the scope lines, "It will
not be able to change anything, and you can disconnect it any time from Settings › Agents.", "You will be sent back to
<host>.", Allow / Deny, and "Signed in as … Not you? Sign out". Allow and Deny both send the browser back to the app
(Deny with `error=access_denied`). An expired, answered or disabled-client request reads "This sign-in request has
expired or was already answered." The page never shows a token.

## Per-client steps

- **claude.ai:** Customize → Connectors → Add custom connector; paste `<origin>/mcp`; under Authentication choose
  **Sign in now** and leave the client identity on its default (Claude registers itself through `/register`); sign in
  on the page that opens, then Allow. If Claude asks for a client ID instead, enter `alphadeck-claude` and leave the
  secret blank (the static fallback below). Free plan: one custom connector; Team: the Owner adds it under
  Organization settings → Connectors and each member signs in individually.
- **Claude Code:** `claude mcp add --transport http alphadeck <origin>/mcp` (no header), then in a new session run
  `/mcp` and authenticate: the browser opens the consent page. Claude Code's client id
  (`https://claude.ai/oauth/claude-code-client-metadata`) is a known static client here, so it works whether or not
  Claude Code falls back to registration. A token pasted with `--header "Authorization: Bearer <token>"` still works.
- **ChatGPT, Grok:** a later phase (their redirect URIs and a live test each).

## What each client requires (from the clients' docs, 2026-11-08)

| Requirement | claude.ai custom connector | Claude Code | ChatGPT | Grok |
|---|---|---|---|---|
| Reachability | public HTTPS (IPv4; no 3xx to another host; never localhost) | any | public | public |
| PKCE | S256, required; the AS must advertise it | same | S256 | not confirmed |
| Registration | CIMD when advertised, else DCR, else a static client id | its CIMD client id; loopback redirects, port ignored | CIMD recommended, DCR supported | not confirmed |
| Discovery | 401 + `resource_metadata`; PRM `resource` exactly the URL entered; `authorization_servers[0]` | same | PRM + AS metadata | not confirmed |
| Redirect URI | `https://claude.ai/api/mcp/auth_callback` | `http://localhost/callback`, `http://127.0.0.1/callback` | `https://chatgpt.com/connector_platform_oauth_redirect` | not confirmed |
| `resource` (RFC 8707) | sent on authorize and token, no trailing slash | not confirmed | sent | not confirmed |
| Token endpoint | form-encoded; a refresh failure is `invalid_grant`; rotating refresh tokens | same | RFC 9207 `iss`, exact issuer match | — |

We do not advertise CIMD (`client_id_metadata_document_supported` is unset: the SDK has no server-side CIMD), so
claude.ai takes DCR.

## The static clients

Seeded by migration 0080, public (`token_endpoint_auth_method` `none`, no secret), scope `read steward`:

| Client id | Name | Redirect URIs |
|---|---|---|
| `https://claude.ai/oauth/claude-code-client-metadata` | Claude Code | `http://localhost/callback`, `http://127.0.0.1/callback`: a registered LOOPBACK URI matches the same scheme, host and path on ANY port (`LoopbackTolerantClient`); everything else is the SDK's exact match |
| `alphadeck-claude` | Claude | `https://claude.ai/api/mcp/auth_callback` (the "use your own OAuth client" fallback) |

## Dynamic registration hygiene

`POST /register` is open to the internet once hosted (a client is only an identifier; consent is per user). Its
limits: a `redirect_uris` entry that is not https and not http on a loopback host is refused
(`invalid_redirect_uri`); at most 20 registrations per client address per hour and 200 a day overall (429 beyond;
in-process, like the login throttle; behind the nginx proxy the client address is the proxy's, so in practice the
hourly cap is shared); a missing `scope` registers `read`; a secret the SDK issues is returned once and stored as its
sha256 (`/token` and `/revoke` compare the presented secret's hash). `/admin/status` counts the clients
(`oauth_clients: {static, dcr}`).

## Revocation

- **The user:** Settings › Agents › Connected apps › Disconnect (`DELETE /account/connections/{grant_id}`): the grant
  and every access and refresh token under it stop working on their next request; the row stays, grayed.
- **The app:** `POST /revoke` (an access token, or a refresh token = the whole grant).
- **The steward:** `python -m pipeline.users oauth-client disable --client-id <id>` refuses a client everywhere
  (`/authorize` answers the SDK's direct `invalid_request` "not found", `/token` `invalid_client`, its tokens 401);
  `oauth-client list` shows every client with its live-connection count. Disabling a user (`pipeline.users disable`)
  kills their apps' tokens at once too.

## Operational notes

- **TLS is required** off localhost: the origin must be https (the SDK refuses a non-loopback http issuer), and prod
  keeps `ALPHADECK_PUBLIC_ORIGIN` unset until the server move.
- **The issuer and the PRM's `authorization_servers` are spelled identically** (both `AnyHttpUrl(origin)`, with its
  trailing slash), and the consent redirect's `iss` is that same string; a test asserts it.
- **nginx** (`frontend/nginx.conf`): exact locations `= /authorize`, `= /token`, `= /register`, `= /revoke`,
  `= /.well-known/oauth-authorization-server`, each to the backend at the same path; `location =` beats the SPA's
  `location /`, so `/authorize` is never served `index.html`. The consent page itself is the SPA's.
- **The SDK quirk on `/revoke`:** its request model requires a `client_secret` key even for a public client, so the
  wrapper in `server.py` adds an empty one when a form has none (an RFC 7009 request from a public client is
  `token` + `client_id`).
- **Logs** carry client ids and user-id prefixes, never a token, a code or a secret.
