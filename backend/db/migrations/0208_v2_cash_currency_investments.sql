-- 0208 — the cash/burn value's STATEMENT CURRENCY and its INVESTMENT PART, stored (V2; the extractor correctness
-- plan's PR 6 and PR 1b, operator decisions D4 and D1 (d), approved 2026-11-15).
--
-- WHY. The extractor already knows both and they were lost before they were stored:
--   * the statement currency: an annual foreign filer reports in its own currency (CNY, JPY, CAD, ...) and the
--     `cash_usd` / `quarterly_burn_usd` columns then hold THOSE figures (the runway is a ratio, so it stays right).
--     The candidate carried `statement_currency` (0200), but the fact had nowhere to keep it: an approved CNY value
--     was stored bare and every screen printed "$";
--   * the investment part: cash is one "cash and short-term investments" number (D1 (b)); the part of it that is
--     short-term investments (plus the long-term marketable securities counted) lived only in the note.
--
-- WHAT. Two nullable columns on the fact and one on the candidate (the candidate's currency is its existing
-- `statement_currency`). Names unchanged elsewhere: `cash_usd` keeps its name (D4 (a), not (b)).
--   * fact_cash_burn.currency         text    NULL, an ISO 4217 code (three capital letters). NULL = NOT RECORDED
--                                     (a V1 row, a value whose currency the extractor could not establish, a
--                                     correction that did not state one) — never read as, or written as, 'USD'.
--   * fact_cash_burn.investments_usd  numeric NULL, the investment part ALREADY INSIDE `cash_usd`, in the same
--                                     currency. NULL = no split recorded (never a fake 0).
--   * extraction_candidate.investments_usd numeric NULL, the same, as the extractor read it.
--
-- NO BACKFILL, NO DEFAULT: existing rows stay NULL ("not recorded"). Values are set only on the INSERT of a new
-- version (append-only; the `no_update` triggers stand). Neither column is a call input: nothing under calls/ or
-- signals/ reads them; the readers that show a value (`SELECT *` through the as-of read) carry them for display.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the check added only when absent).
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE fact_cash_burn DROP COLUMN currency, DROP COLUMN investments_usd;
--   ALTER TABLE extraction_candidate DROP COLUMN investments_usd;
--   DELETE FROM schema_migrations WHERE filename = '0208_v2_cash_currency_investments.sql';

ALTER TABLE fact_cash_burn       ADD COLUMN IF NOT EXISTS currency        text    NULL;
ALTER TABLE fact_cash_burn       ADD COLUMN IF NOT EXISTS investments_usd numeric NULL;
ALTER TABLE extraction_candidate ADD COLUMN IF NOT EXISTS investments_usd numeric NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fact_cash_burn_currency_iso'
          AND conrelid = 'fact_cash_burn'::regclass
    ) THEN
        ALTER TABLE fact_cash_burn ADD CONSTRAINT fact_cash_burn_currency_iso
            CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$');
    END IF;
END
$$;
