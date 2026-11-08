-- 0080 — our own OAuth 2.1 sign-in for the hosted /mcp endpoint (docs/CONNECTORS.md, docs/AUTH.md).
--
-- Six tables, nothing else; no existing table changes and no row moves.
--
-- * oauth_client — an app that may ask a user to connect: the two STATIC clients seeded below (Claude Code's
--   client id and `alphadeck-claude`, both public: no secret) and the clients that register themselves through
--   `POST /register` (`registration='dcr'`, the raw registration document in `metadata`, any secret stored as its
--   sha256 only). `disabled_at` set = the client is refused everywhere (`pipeline.users oauth-client disable`).
-- * oauth_consent — the pending request between `/authorize` and the user's Allow / Deny on the consent page
--   (ten minutes). Not bound to a user until decided: whoever signs in decides for THEIR account, and the row
--   records who (`user_id`) and what (`decision`, `scopes_granted`). Never deleted; an expired row is inert.
-- * oauth_grant — one LIVE row per (user, client): the "Connected apps" row on Settings > Agents. A second
--   consent for the same client updates its scopes. Revoking it (`revoked_at`) kills every token below it.
-- * oauth_code — the single-use authorization code (ten minutes), bound to the grant, the PKCE challenge, the
--   redirect URI and the resource. A code presented twice revokes its grant.
-- * oauth_access_token — the bearer an app sends to /mcp (one hour). `last_used_at` is touched at most every
--   five minutes, like a personal access token.
-- * oauth_refresh_token — rotating (30 days, slid on each use): a use issues a successor and stamps
--   `rotated_to`; presenting a rotated token again revokes the grant (replay detection).
--
-- Every token and code is an opaque random string of which ONLY the sha256 is stored (0077's rule), so a read of
-- these tables cannot be replayed. Access-control state, not facts with temporal meaning: not bitemporal, and no
-- tenant_id (keyed on the user, so domain/scopes.py names nothing here). No row is ever deleted by the app.
-- Idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING), like every migration.
--
-- The inverse, by hand, after a backup:
--   DROP TABLE oauth_refresh_token, oauth_access_token, oauth_code, oauth_grant, oauth_consent, oauth_client;
--   DELETE FROM schema_migrations WHERE filename = '0080_oauth.sql';
CREATE TABLE IF NOT EXISTS oauth_client (
    client_id                   text PRIMARY KEY,
    client_name                 text NULL,
    redirect_uris               text[] NOT NULL,
    scope                       text NOT NULL,          -- space-separated scopes the client may ask for
    token_endpoint_auth_method  text NOT NULL
        CHECK (token_endpoint_auth_method IN ('none', 'client_secret_post', 'client_secret_basic')),
    client_secret_hash          text NULL,              -- sha256 of the secret the register handler issued
    registration                text NOT NULL CHECK (registration IN ('static', 'dcr')),
    grant_types                 text[] NOT NULL,
    metadata                    jsonb NULL,             -- the raw registration document (never the secret)
    created_at                  timestamptz NOT NULL DEFAULT now(),
    disabled_at                 timestamptz NULL
);

CREATE TABLE IF NOT EXISTS oauth_consent (
    id                      uuid PRIMARY KEY,
    client_id               text NOT NULL REFERENCES oauth_client (client_id),
    redirect_uri            text NOT NULL,
    redirect_uri_explicit   boolean NOT NULL,
    scopes_requested        text[] NOT NULL,
    code_challenge          text NOT NULL,
    state                   text NULL,
    resource                text NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    expires_at              timestamptz NOT NULL,
    user_id                 uuid NULL REFERENCES app_user (id),
    decided_at              timestamptz NULL,
    decision                text NULL CHECK (decision IN ('allow', 'deny')),
    scopes_granted          text[] NULL
);

CREATE TABLE IF NOT EXISTS oauth_grant (
    id              uuid PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES app_user (id),
    client_id       text NOT NULL REFERENCES oauth_client (client_id),
    scopes          text[] NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz NULL,
    revoked_at      timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS oauth_grant_live_uq ON oauth_grant (user_id, client_id)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS oauth_grant_user_idx ON oauth_grant (user_id);

CREATE TABLE IF NOT EXISTS oauth_code (
    code_hash               text PRIMARY KEY,
    grant_id                uuid NOT NULL REFERENCES oauth_grant (id),
    client_id               text NOT NULL,
    scopes                  text[] NOT NULL,
    redirect_uri            text NOT NULL,
    redirect_uri_explicit   boolean NOT NULL,
    code_challenge          text NOT NULL,
    resource                text NULL,
    expires_at              timestamptz NOT NULL,
    used_at                 timestamptz NULL
);

CREATE TABLE IF NOT EXISTS oauth_access_token (
    token_hash      text PRIMARY KEY,
    grant_id        uuid NOT NULL REFERENCES oauth_grant (id),
    scopes          text[] NOT NULL,
    resource        text NOT NULL,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz NULL,
    revoked_at      timestamptz NULL
);
CREATE INDEX IF NOT EXISTS oauth_access_token_grant_idx ON oauth_access_token (grant_id);

CREATE TABLE IF NOT EXISTS oauth_refresh_token (
    token_hash      text PRIMARY KEY,
    grant_id        uuid NOT NULL REFERENCES oauth_grant (id),
    scopes          text[] NOT NULL,
    resource        text NOT NULL,
    expires_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    rotated_to      text NULL,                          -- the sha256 of its successor
    revoked_at      timestamptz NULL
);
CREATE INDEX IF NOT EXISTS oauth_refresh_token_grant_idx ON oauth_refresh_token (grant_id);

-- The two static clients (public: token_endpoint_auth_method 'none', no secret).
--  * Claude Code's client id, served as a known client without fetching its metadata document; its loopback
--    redirect URIs match on ANY port (app/oauth/provider.py, LoopbackTolerantClient).
--  * `alphadeck-claude`: the client id a user types in Claude's "use your own OAuth client" fallback.
INSERT INTO oauth_client
    (client_id, client_name, redirect_uris, scope, token_endpoint_auth_method, registration, grant_types)
VALUES
    ('https://claude.ai/oauth/claude-code-client-metadata', 'Claude Code',
     ARRAY['http://localhost/callback', 'http://127.0.0.1/callback'], 'read steward', 'none', 'static',
     ARRAY['authorization_code', 'refresh_token']),
    ('alphadeck-claude', 'Claude',
     ARRAY['https://claude.ai/api/mcp/auth_callback'], 'read steward', 'none', 'static',
     ARRAY['authorization_code', 'refresh_token'])
ON CONFLICT (client_id) DO NOTHING;
