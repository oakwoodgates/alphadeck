-- 0079 — user_meta: per-USER preferences, 1:1 with app_user (docs/AUTH.md §Settings page).
--
-- What: one row per user (`user_id` PK -> app_user), one TYPED column per setting, NULL = unset (never a sentinel
-- default), a missing row = every setting unset (readers LEFT JOIN, writers upsert). The first setting is
-- `slack_webhook_url`, the user's own push destination for their cards' armed calls and the nightly digest
-- (docs/FEED_LOOP.md step 3), written by `PUT /account/notifications/slack-webhook`.
--
-- Why: the Slack destination moves from the WORKSPACE (0076's `tenant.notify_webhook`, set only by the CLI) to the
-- USER, so the Settings page can set it and every user of a workspace is told. Name and email stay on `app_user`
-- (the login identity the gate reads on every request); `user_meta` holds preferences, so a webhook URL (a
-- credential) never rides into `/auth/me` or the gate's row.
--
-- Settings state, not a fact: no tenant_id (keyed on the user, so `domain/scopes.py` names nothing here), no
-- bitemporal columns, plain UPDATEs (0077's rule for app_user). A disabled user keeps their row; the nightly's
-- read skips them (`repositories.user_meta_repo.webhooks_by_workspace`).
--
-- Data: each workspace's `tenant.notify_webhook` is copied to every ENABLED user of that workspace (a workspace
-- with no webhook yields no row), then the column is dropped. MEASURED 2026-11-07: prod and dev hold zero, so
-- this copies nothing there; it keeps the pushes of any install that did set one.
--
-- Idempotent: IF NOT EXISTS / ON CONFLICT DO NOTHING / IF EXISTS; a second run changes nothing (the copy is
-- guarded on the column still existing, since a re-run finds it dropped).
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE tenant ADD COLUMN notify_webhook text NULL;
--   -- copy back only where a workspace has exactly one user with a URL (two users on different URLs have no
--   -- single inverse, which is why the destination moved to the user):
--   UPDATE tenant t SET notify_webhook = m.slack_webhook_url FROM app_user u JOIN user_meta m ON m.user_id = u.id
--   WHERE u.workspace_id = t.id AND m.slack_webhook_url IS NOT NULL
--     AND (SELECT count(*) FROM app_user u2 JOIN user_meta m2 ON m2.user_id = u2.id
--          WHERE u2.workspace_id = t.id AND m2.slack_webhook_url IS NOT NULL) = 1;
--   DROP TABLE user_meta;
--   DELETE FROM schema_migrations WHERE filename = '0079_user_meta.sql';
CREATE TABLE IF NOT EXISTS user_meta (
    user_id            uuid PRIMARY KEY REFERENCES app_user (id),
    slack_webhook_url  text NULL,          -- the user's own push destination; NULL = none (the log only)
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'tenant' AND column_name = 'notify_webhook'
    ) THEN
        INSERT INTO user_meta (user_id, slack_webhook_url)
        SELECT u.id, t.notify_webhook
        FROM app_user u JOIN tenant t ON t.id = u.workspace_id
        WHERE t.notify_webhook IS NOT NULL AND u.disabled_at IS NULL
        ON CONFLICT (user_id) DO NOTHING;
    END IF;
END $$;

-- The workspace-level destination is retired: the nightly reads user_meta from this migration on.
ALTER TABLE tenant DROP COLUMN IF EXISTS notify_webhook;
