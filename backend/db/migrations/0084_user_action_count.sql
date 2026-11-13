-- 0084 — user_action_count: the per-user DAILY ALLOWANCE on user-triggered fetches (security hardening PR 1b,
-- app/allowance.py, docs/AUTH.md §User-triggered fetches).
--
-- What: one row per (user, action, market day) holding how many of that action the user triggered that day. The
-- actions are the fetch paths a plain user's click starts: 'extract', 'price_pull', 'auto_confirm', 'holdings',
-- 'chain_draft', 'term_run' (Settings.allowance_<action>_per_day). `market_day` is the calendar date in market time
-- (domain.market_time.market_today), so the count starts fresh at midnight there.
--
-- Why a table, not an in-process counter: an in-process counter resets on every restart and is wrong the moment
-- the API runs more than one worker. The count is taken with ONE upsert statement (INSERT ... ON CONFLICT DO UPDATE
-- ... WHERE count + n <= limit), so two racing requests can never both take the last unit.
--
-- Operational state, not a fact: no tenant_id (keyed on the user, like app_user / user_meta, so domain/scopes.py
-- names nothing here), no bitemporal columns, plain UPDATEs. A steward is never counted. Rows of past days are
-- never read again; they are tiny (at most six a user a day) and may be deleted freely.
--
-- Depends on 0077 (app_user). Additive + idempotent (CREATE ... IF NOT EXISTS).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE user_action_count;
--   DELETE FROM schema_migrations WHERE filename = '0084_user_action_count.sql';

CREATE TABLE IF NOT EXISTS user_action_count (
    user_id     uuid        NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    action      text        NOT NULL,
    market_day  date        NOT NULL,
    count       integer     NOT NULL CHECK (count >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, action, market_day)
);
