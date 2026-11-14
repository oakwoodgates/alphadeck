-- 0206 — the `ratified_by` vocabulary on the two PLATFORM value tables (V2 W2a; the W2 build plan, finding 8).
--
-- WHY. Who set a share count or a cash/burn value is read by the value's state (`domain/platform_values.py`):
--   * NULL or 'auto' — the machine applied a clean filing value ("from the filing");
--   * 'operator'     — a V1 row carried over ("verified", carried);
--   * an email       — the steward who set it ("verified").
-- Until now `ratified_by` was free text (the ratify CLIs took any `--by`), so any other word could be stored and
-- read as nobody knows what. This check refuses it on every NEW row of `fact_shares_outstanding` and
-- `fact_cash_burn`.
--
-- NOT VALID: the rows already stored are NOT judged (their count and values are not verified here), so a database
-- holding V1 rows migrates cleanly whatever they say. W10 runs `ALTER TABLE ... VALIDATE CONSTRAINT` after the
-- cutover dry run shows no odd values.
--
-- Idempotent: each constraint is added only when `pg_constraint` does not have it yet (the 0076 pattern).
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE fact_shares_outstanding DROP CONSTRAINT fact_shares_outstanding_ratified_by_vocabulary;
--   ALTER TABLE fact_cash_burn DROP CONSTRAINT fact_cash_burn_ratified_by_vocabulary;
--   DELETE FROM schema_migrations WHERE filename = '0206_v2_ratified_by_vocabulary.sql';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fact_shares_outstanding_ratified_by_vocabulary'
          AND conrelid = 'fact_shares_outstanding'::regclass
    ) THEN
        ALTER TABLE fact_shares_outstanding ADD CONSTRAINT fact_shares_outstanding_ratified_by_vocabulary
            CHECK (ratified_by IS NULL OR ratified_by IN ('auto', 'operator')
                   OR ratified_by ~ '^[^@[:space:]]+@[^@[:space:]]+$')
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fact_cash_burn_ratified_by_vocabulary'
          AND conrelid = 'fact_cash_burn'::regclass
    ) THEN
        ALTER TABLE fact_cash_burn ADD CONSTRAINT fact_cash_burn_ratified_by_vocabulary
            CHECK (ratified_by IS NULL OR ratified_by IN ('auto', 'operator')
                   OR ratified_by ~ '^[^@[:space:]]+@[^@[:space:]]+$')
            NOT VALID;
    END IF;
END $$;
