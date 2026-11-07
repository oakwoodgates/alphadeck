-- 0077 — the login door: users, server-side sessions, API tokens (auth PR 1b, docs/AUTH.md).
--
-- Three tables, nothing else. No existing table changes and no row moves: every user created in this PR sits on
-- the one existing tenant (`db.session.DEFAULT_TENANT_ID`, ...0001), so what a signed-in user sees is exactly what
-- the app showed before; workspaces arrive with PR 2.
--
-- * app_user — one account. `email` is unique case-insensitively (the lower(email) index); the stored text keeps
--   the case the steward typed. `password_hash` is an argon2id PHC string (app/auth.py); the plaintext is never
--   stored. `role` is 'user' or 'super_admin' (the data steward: Admin, Backtest, the business-type re-tag).
--   `disabled_at` set = the account cannot sign in and its sessions and tokens stop working at once (the gate
--   checks it on every request); clearing it re-enables the account. No row is ever deleted by the app.
-- * user_session — one browser sign-in. The cookie carries a random token; only its sha256 is stored, so a read
--   of this table cannot be replayed as a cookie. `expires_at` is the idle deadline (slid forward on use);
--   `revoked_at` set = signed out (or revoked by a password change).
-- * api_token — a named bearer token for a non-browser caller (the MCP agent tools, the deploy script, curl).
--   Stored as sha256 only; minted and revoked by `python -m pipeline.users token ...`.
--
-- Not bitemporal: these are access-control state, not facts with temporal meaning (invariant #1 does not apply).
-- Idempotent (IF NOT EXISTS), like every migration.
CREATE TABLE IF NOT EXISTS app_user (
    id              uuid PRIMARY KEY,
    email           text NOT NULL,
    display_name    text NOT NULL,
    password_hash   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('user', 'super_admin')),
    workspace_id    uuid NOT NULL REFERENCES tenant (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    disabled_at     timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS app_user_email_lower_uq ON app_user (lower(email));

CREATE TABLE IF NOT EXISTS user_session (
    id              uuid PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES app_user (id),
    token_hash      text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz NULL
);
CREATE INDEX IF NOT EXISTS user_session_user_idx ON user_session (user_id);

CREATE TABLE IF NOT EXISTS api_token (
    id              uuid PRIMARY KEY,
    user_id         uuid NOT NULL REFERENCES app_user (id),
    name            text NOT NULL,
    token_hash      text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz NULL,
    revoked_at      timestamptz NULL
);
CREATE INDEX IF NOT EXISTS api_token_user_idx ON api_token (user_id);
