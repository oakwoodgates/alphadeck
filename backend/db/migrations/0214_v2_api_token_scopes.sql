-- 0214 — api_token.scopes: read-only personal access tokens (the steward agent plan, Track T; decisions D-B (b),
-- D-C (b), D-D (a), D-I (a)).
--
-- WHAT. One nullable column on the personal-token table (0077):
--   * scopes text[] NULL   NULL = a FULL token: its user's role, and it can change things (today's meaning; from
--                          now on minted only with `python -m pipeline.users token create --write`). A non-NULL
--                          array = a RESTRICTED token: it can only read, it never reaches /account or
--                          /auth/change-password, and it holds exactly these scopes (app/auth.py, `restricted_may`,
--                          the OAuth access token's rule).
-- The column is the token's CEILING, never a grant above its user's role: `steward` counts only while the user is a
-- super admin (`capped_role`), so a demoted steward's `read steward` token reads as a plain user.
-- A CHECK keeps a restricted token to the scopes an agent may hold: `read` is required, and nothing outside
-- {read, steward} (no `write`: a token that can change things is a NULL row).
--
-- THEN every existing token becomes read-only with its user's role scopes (D-B (b)): `read`, plus `steward` for a
-- super admin. Revoked tokens and disabled users' tokens too, so no pre-0214 row stays full. Scripts that write
-- with a personal token need a new one minted with `--write`; the deploy probe and the agent tools only GET.
-- No new table: domain/scopes.py already names api_token (user-keyed).
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the check added only when absent; the UPDATE touches NULL rows,
-- and the runner applies the file once, so a later `--write` token is never narrowed).
--
-- The inverse, by hand, after a backup (it makes every token full again):
--   ALTER TABLE api_token DROP CONSTRAINT api_token_scopes_read_only;
--   ALTER TABLE api_token DROP COLUMN scopes;
--   DELETE FROM schema_migrations WHERE filename = '0214_v2_api_token_scopes.sql';

ALTER TABLE api_token ADD COLUMN IF NOT EXISTS scopes text[] NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'api_token_scopes_read_only' AND conrelid = 'api_token'::regclass
    ) THEN
        ALTER TABLE api_token ADD CONSTRAINT api_token_scopes_read_only
            CHECK (scopes IS NULL OR ('read' = ANY (scopes) AND scopes <@ ARRAY['read', 'steward']::text[]));
    END IF;
END
$$;

UPDATE api_token t
SET scopes = CASE WHEN u.role = 'super_admin' THEN ARRAY['read', 'steward'] ELSE ARRAY['read'] END
FROM app_user u
WHERE u.id = t.user_id AND t.scopes IS NULL;
