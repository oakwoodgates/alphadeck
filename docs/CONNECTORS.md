# Connectors

How an AI client (claude.ai's custom connectors, Claude Code's `/mcp` sign-in) signs a user in with OAuth and reads
that user's workspace through the hosted agent tools endpoint `<origin>/mcp`. The client sends the user to Alpha
Deck's own consent page; the user signs in with their Alpha Deck email and password and clicks Allow; the client
gets its own token, which can read and never write. A client can also use a personal access token pasted from
Settings > Agents, which reads only too. All of it is available when `ALPHADECK_PUBLIC_ORIGIN` is set
([AGENT_TOOLS](AGENT_TOOLS.md) "Hosted endpoint"). Read it before you change `app/oauth/`, the consent page or the
`oauth` principal.

## Code

| Piece | Module |
|---|---|
| Tables, TTLs, scopes, static client ids | `backend/app/oauth/store.py` |
| The MCP SDK provider (`AlphaDeckAuthProvider`, `LoopbackTolerantClient`) | `backend/app/oauth/provider.py` |
| The routes (SDK routes composed with ours) | `backend/app/oauth/server.py` (`mount_oauth`) |
| The consent page's API | `backend/app/routers/oauth_consent.py` |
| The `oauth` principal | `backend/app/auth.py` ([AUTH](AUTH.md) "The four principals") |
| Connected apps | `/account/connections` in `backend/app/routers/account.py` |
| Tables and the two static clients | migration `0080_oauth.sql` |
| The pages | `frontend/src/oauth/ConsentPage.tsx`; Connected apps in `frontend/src/settings/agents/AgentsTab.tsx` |

## How it works

### The flow

```
client  -> GET  <origin>/authorize?client_id&redirect_uri&code_challenge(S256)&state&scope&resource
        <- 302  /oauth/consent/<consent id>        (the SDK checked the client; the provider stored a pending consent)
browser -> the SPA; signed out: /login?next=/oauth/consent/<id>, sign in, come back
        -> GET  /api/oauth/consent/<id>   (session cookie only)  -> client, scopes, redirect host, expiry
        -> POST /api/oauth/consent/<id> {decision}  (session cookie only)  -> {redirect_to}
        -> <redirect_uri>?code=...&state=...&iss=<issuer>   (or error=access_denied)
client  -> POST <origin>/token  (form: authorization_code, code, code_verifier, client_id, redirect_uri, resource)
        <- {access_token, refresh_token (rotating), token_type: Bearer, expires_in, scope}
client  -> POST <origin>/mcp  Authorization: Bearer <access token>   -> the hosted endpoint, principal "oauth"
```

The authorization server is the MCP SDK's (`create_auth_routes`): its handlers parse the requests, check PKCE S256,
the redirect URI, the scopes and the client's credentials, and write the RFC error bodies. Ours is the provider
underneath (the tables) and one replaced route, the metadata document. Every token and code is an opaque
`secrets.token_urlsafe(32)`, never a JWT; only its sha256 is stored.

### The routes

| Route | Who answers | Notes |
|---|---|---|
| `GET /.well-known/oauth-authorization-server` | ours (RFC 8414) | the SDK's document plus `none` among the auth methods and `authorization_response_iss_parameter_supported`; its `issuer` is the same string the protected-resource document names in `authorization_servers` |
| `GET /authorize` | the SDK, then `provider.authorize` | stores a pending consent and redirects to the consent page; a `resource` other than `<origin>/mcp` is `invalid_target`, a missing one is bound to it; bounded (below) |
| `POST /token` | the SDK, then the provider | form-encoded; the code grant and the refresh grant |
| `POST /register` | the SDK, then `provider.register_client` | mounted only when `ALPHADECK_OAUTH_DYNAMIC_REGISTRATION` is on (below) |
| `POST /revoke` | the SDK, then `provider.revoke_token` | RFC 7009: an access token revokes itself, a refresh token revokes the whole grant |
| `GET`, `POST /oauth/consent/{id}` | ours, behind `require_user` | the browser session only (403 for any bearer, so no app approves its own request) |
| `GET /account/connections`, `DELETE /account/connections/{grant_id}` | ours | Connected apps |

The authorization-server routes are Starlette routes outside the API's route-gate walk, each authenticating its
own way; `backend/tests/app/test_auth_gate.py` lists them. They exist only when `ALPHADECK_PUBLIC_ORIGIN` is set.

### The tables

Access-control state, not facts: not bitemporal and no `tenant_id` (they are `user-keyed` in
[TENANT_SCOPES](TENANT_SCOPES.md); a grant belongs to a user, and the gate picks the workspace). Request paths stamp
`revoked_at` or `disabled_at` and never delete; the nightly prune deletes expired codes and tokens, undecided
requests and unused self-registered clients ([AUTH](AUTH.md) "Housekeeping").

| Table | What it holds |
|---|---|
| `oauth_client` | an app that may ask: id, name, redirect URIs, allowed scope, auth method, the secret's sha256, `registration` (`static` or `dcr`), `disabled_at` |
| `oauth_consent` | a pending request between `/authorize` and the click, then the decision and the granted scopes |
| `oauth_grant` | one live row per user and client: the Connected apps row; revoking it kills every token under it |
| `oauth_code` | single use, bound to the grant, scopes, redirect URI, PKCE challenge and resource; a used code presented again revokes its grant |
| `oauth_access_token` | short-lived; `last_used_at` touched at most every five minutes |
| `oauth_refresh_token` | rotating: each use issues a successor; presenting a rotated token again revokes the grant (replay detection) |

The lifetimes are constants in `store.py` (`CONSENT_TTL`, `CODE_TTL`, `ACCESS_TTL`, `REFRESH_TTL`).

### Scopes and the role cap

Two scopes (`store.SCOPES`): `read` (cards, calls, decisions, ratified facts) and `steward` (the Admin readouts the
five steward tools read). Every user can grant `read`, which is always on the consent page and added when an app
asked for nothing. Only a super admin can grant `steward` (`store.grantable`); a plain user sees it struck through
and Allow leaves it out. At the API, a token without `write` (none carries it) answers 403 on every non-GET
request, and the role is `super_admin` only when the user is one and the token carries `steward`, so a steward's
read-only connector gets 403 on `/admin`. At `/mcp` the token's scopes become the tool principal's, which decides
the tools and prompts the client lists ([AGENT_TOOLS](AGENT_TOOLS.md) "Scopes"). A read-only personal access token
follows the same two rules with the scopes stored on it, and the same refusal of `/account` (`auth.restricted_may`,
[AUTH](AUTH.md)).

### The consent page

`/oauth/consent/<id>` sits inside the signed-in shell. It shows the app's name (text only, never a link), whether
it is a known static client or a self-registered one marked unverified, the scope lines, the host the browser will
be sent back to, Allow and Deny, and who is signed in. Allow and Deny both send the browser back to the app (Deny
with `error=access_denied`). An expired or disabled-client request answers 410, a second decision 409. The page
never shows a token.

### Clients

Migration 0080 seeds two static clients (`store.STATIC_CLIENTS`), public (PKCE, no secret), scope `read steward`:

| Client id | For | Redirect URIs |
|---|---|---|
| `https://claude.ai/oauth/claude-code-client-metadata` | Claude Code | `http://localhost/callback`, `http://127.0.0.1/callback`; a registered loopback URI matches the same scheme, host and path on any port (`LoopbackTolerantClient`); everything else is the SDK's exact match |
| `alphadeck-claude` | claude.ai custom connectors | `https://claude.ai/api/mcp/auth_callback` |

The metadata does not advertise client ID metadata documents (the SDK has no server side for them), and with
registration off it names no `registration_endpoint`, so claude.ai asks for a client ID: the user enters
`alphadeck-claude` with no secret. Claude Code's own client id is static here, so it works with or without
registration. Settings > Agents shows each client's steps (`AgentsTab.tsx`). The steward adds another vetted app
with `pipeline.users oauth-client add` ([AUTH](AUTH.md) "The CLI"): stored as `static`, public unless
`--confidential` (a generated secret printed once, stored as its sha256), each redirect URI https or http on
localhost.

### Dynamic registration

`POST /register` exists only when `ALPHADECK_OAUTH_DYNAMIC_REGISTRATION` is true; it is off by default, and off the
SDK creates no route (nginx's `location = /register` stays and the backend answers 404). Why off: nobody can create
a client whose redirect sends a code to their own server, so a phishing link to a consent page can only hand a code
to a known client. A client that registered while it was on keeps working; nothing checks how a client registered.

When on, `/register` is open to the internet (a client is only an identifier; consent is per user) and bounded in
`server.py`: a body cap (413), at most `OAUTH_REDIRECT_URIS_MAX` redirect URIs of at most `URL_MAX` characters,
each https or loopback (`invalid_redirect_uri`), and per-address and daily registration limits (429, in-process; the
address is `app/client_address.py`'s). A missing `scope` registers `read`; an issued secret is returned once and
stored as its sha256. `GET /admin/status` counts clients by registration.

### Limits on `/authorize`

`/authorize` stores a pending request before anyone signs in, for any known client, so it is bounded: `state`,
`code_challenge` and the scope string have length limits (`domain/text_types.py`), a POST body is capped (413), and
new requests are limited per client address an hour (in-process) and in total an hour, counted from the table, so
a restart or a new address does not reset it (`ALPHADECK_OAUTH_AUTHORIZE_PER_ADDRESS_HOUR`, `_PER_HOUR`; 0 = no
limit). A refusal is the SDK's error redirect back to the app with its `state`. During a flood nobody can start a
new app connection for up to an hour; existing connections, web sign-in and personal tokens are unaffected.

### Revocation

- **The user:** Settings > Agents > Connected apps > Disconnect (`DELETE /account/connections/{grant_id}`): the
  grant and every token under it stop on their next request; the row stays, grayed.
- **The app:** `POST /revoke` (an access token, or a refresh token for the whole grant).
- **The operator:** `pipeline.users oauth-client disable --client-id <id>` refuses a client everywhere (its
  `/authorize` and `/token` requests fail and its tokens get 401); `oauth-client list` shows each client's live
  connections. Disabling a user stops their apps' tokens at once too.

## Contracts

- The issuer, the protected-resource document's `authorization_servers` entry and the consent redirect's `iss` are
  the same string (`store.issuer_for`, pydantic's spelling of the origin with a trailing slash); a test asserts it.
- nginx (`frontend/nginx.conf`) has exact locations for `/authorize`, `/token`, `/register`, `/revoke` and the
  metadata document, each to the backend at the same path: `location =` beats the SPA's `location /`, so
  `/authorize` is never served `index.html`. Each sets `X-Real-IP` ([SECURITY](SECURITY.md) "The caller's
  address").
- The SDK's revocation model requires a `client_secret` key even for a public client, so the wrapper in
  `server.py` adds an empty one when a form has none.
- Logs carry client ids and user-id prefixes, never a token, a code or a secret.

## Who can do what

| Action | Who | Enforced by |
|---|---|---|
| Start a sign-in (`/authorize`) | any known client | the SDK; the limits above |
| Allow or deny on the consent page | a signed-in user in a browser | `oauth_consent.py` (session only) |
| Grant `steward` | a super admin | `store.grantable` |
| Disconnect an app | the user who connected it | `/account/connections` (another user's id is 404) |
| Add, list or disable a client | the operator | `pipeline.users oauth-client` |
| Register a client | anyone, only when dynamic registration is on | `server.py` |

## Settings

The OAuth knobs (`ALPHADECK_PUBLIC_ORIGIN`, `ALPHADECK_OAUTH_DYNAMIC_REGISTRATION`, the two `/authorize` limits and
the prune ages) are in the settings table in [AUTH](AUTH.md) "Settings". A change takes `.env` plus a backend
redeploy.

## Known limits

- TLS is required off localhost: the origin must be https (the SDK refuses a non-loopback http issuer, and
  `ALPHADECK_PUBLIC_ORIGIN` accepts http only on loopback).
- Only the two static clients are known out of the box; any other client needs `oauth-client add` or dynamic
  registration.
- The registration and per-address `/authorize` counts are in-process (one API worker).

## Related

[AGENT_TOOLS](AGENT_TOOLS.md) "Hosted endpoint", [AUTH](AUTH.md), [SECURITY](SECURITY.md),
`backend/tests/app/test_oauth.py` (PKCE, single-use codes, refresh rotation and replay, session-only consent, the
role cap, read-only tokens, connected apps).
