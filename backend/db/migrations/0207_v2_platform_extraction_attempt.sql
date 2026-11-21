-- 0207 — platform_extraction_attempt: one row per attempt to extract a company's PLATFORM values (a share count,
-- cash/burn) in the background (V2 W3a; the W3 build plan, Q3 and Q4, approved decision B3 (a)).
--
-- WHY. Nothing else records that an extraction happened: a skipped AUTO value writes no row, a candidate that
-- repeats the newest one is not written again, and a failure writes nothing at all. The gate
-- (`platform_values.extraction.due`) needs "since its last attempt", so every attempt appends one row here,
-- whatever its outcome:
--   * status 'done'    — the machine writer ran; `shares_reason` / `cash_reason` are its `MachineOutcome.reason`
--                        per value type (applied, already_on_file, older_than_on_file, after_today, pinned,
--                        flagged, nothing_found);
--   * status 'error'   — the fetch or the write failed; `error` is a bounded, ONE-LINE, operator-facing reason
--                        (never the raw exception: no URL, no secret). Nothing else was written;
--   * status 'busy'    — another writer held the company for the whole wait; nothing was written (not a failure);
--   * status 'skipped' — nothing could be read (no CIK); `detail` says why.
-- `inputs_key` names the filings the extractor read: the CIK plus the newest 10-Q / 10-K / 20-F / 40-F accession
-- ('' parts when there is none). `detail` is a short note for busy / skipped (a deviation from the plan's column
-- list: the plan had nowhere to say why a row was skipped). `live_fetches` counts the SEC requests the attempt
-- made (NULL when the client cannot say).
--
-- NOT A FACT. No as-of read, public clock, point-in-time view or mirror reads it; it is bookkeeping for the gate
-- and the steward (never a call input). APPEND-ONLY (`no_update`): a new attempt is a new row. `recorded_at` is
-- when we wrote it (bookkeeping, never a history gate, #1).
--
-- DATA scope (domain/scopes.py): the row carries the data tenant's id (`require_write_tenant` refuses any other).
-- Depends only on 0001 (tenant, security_master, raise_no_update()).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE platform_extraction_attempt;
--   DELETE FROM schema_migrations WHERE filename = '0207_v2_platform_extraction_attempt.sql';

CREATE TABLE IF NOT EXISTS platform_extraction_attempt (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),            -- the data tenant
    security_id   uuid        NOT NULL REFERENCES security_master (id),
    run_kind      text        NOT NULL CHECK (run_kind IN ('nightly', 'onboarding', 'backfill', 'steward', 'get_data')),
    inputs_key    text        NOT NULL,      -- CIK + the latest 10-Q / 10-K / 20-F / 40-F accessions ('' parts if none)
    status        text        NOT NULL CHECK (status IN ('done', 'error', 'busy', 'skipped')),
    shares_reason text        NULL,          -- MachineOutcome.reason per value type (status 'done')
    cash_reason   text        NULL,
    error         text        NULL,          -- operator-facing, one line, clipped (status 'error')
    detail        text        NULL,          -- why a row is busy or skipped, in a few words
    live_fetches  integer     NULL CHECK (live_fetches IS NULL OR live_fetches >= 0),
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_extraction_attempt_error_text CHECK (status <> 'error' OR error IS NOT NULL),
    CONSTRAINT platform_extraction_attempt_error_one_line
        CHECK (error IS NULL OR (length(error) <= 200 AND position(E'\n' IN error) = 0))
);

CREATE INDEX IF NOT EXISTS ix_platform_extraction_attempt_company
    ON platform_extraction_attempt (tenant_id, security_id, recorded_at);

DROP TRIGGER IF EXISTS no_update ON platform_extraction_attempt;
CREATE TRIGGER no_update BEFORE UPDATE ON platform_extraction_attempt
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
