-- 0209 — the cash/burn value's YEAR-TO-DATE PACE, stored as figures (V2; the extractor correctness plan's PR 3,
-- operator decision D3 and its sub-decision "a column", approved 2026-11-15).
--
-- WHY. The pace check (``domain.burn_pace``) reads the latest quarter's burn against the year-to-date pace. The
-- extractor carried the pace on ``ExtractedFact`` but stored it only inside the note, in one machine-written
-- segment, and every screen parsed it back out of that prose. A steward correction that kept the old note re-applied
-- the OLD pace to the corrected burn. Stored figures end both: a reader reads the columns; a correction that does not
-- restate the pace stores none.
--
-- WHAT. The same two nullable columns on the fact and on the candidate:
--   * ytd_quarterly_burn_usd numeric NULL  the year to date ending on the burn's end date, scaled to one quarter, in
--                                          the value's own currency, burn sign (positive = burning, negative = cash
--                                          generated). NULL = not recorded.
--   * ytd_burn_days          integer NULL  that year to date's span in days (above zero). NULL = not recorded.
-- Both or neither (a pace without its span cannot be read). The caveat sentence is NOT stored: it is computed on
-- read by the pure rule from the stored numbers, and it never changes a tier.
--
-- NO BACKFILL, NO DEFAULT: existing rows stay NULL; a reader falls back to the note's segment for them
-- (``domain.burn_pace.stored_pace``). Set only on the INSERT of a new version (append-only; the ``no_update``
-- triggers stand). Display only: nothing under calls/ or signals/ reads them.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; each check added only when absent).
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE fact_cash_burn DROP COLUMN ytd_quarterly_burn_usd, DROP COLUMN ytd_burn_days;
--   ALTER TABLE extraction_candidate DROP COLUMN ytd_quarterly_burn_usd, DROP COLUMN ytd_burn_days;
--   DELETE FROM schema_migrations WHERE filename = '0209_v2_cash_burn_pace.sql';

ALTER TABLE fact_cash_burn       ADD COLUMN IF NOT EXISTS ytd_quarterly_burn_usd numeric NULL;
ALTER TABLE fact_cash_burn       ADD COLUMN IF NOT EXISTS ytd_burn_days          integer NULL;
ALTER TABLE extraction_candidate ADD COLUMN IF NOT EXISTS ytd_quarterly_burn_usd numeric NULL;
ALTER TABLE extraction_candidate ADD COLUMN IF NOT EXISTS ytd_burn_days          integer NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fact_cash_burn_ytd_pace_whole'
          AND conrelid = 'fact_cash_burn'::regclass
    ) THEN
        ALTER TABLE fact_cash_burn ADD CONSTRAINT fact_cash_burn_ytd_pace_whole
            CHECK ((ytd_quarterly_burn_usd IS NULL) = (ytd_burn_days IS NULL)
                   AND (ytd_burn_days IS NULL OR ytd_burn_days > 0));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'extraction_candidate_ytd_pace_whole'
          AND conrelid = 'extraction_candidate'::regclass
    ) THEN
        ALTER TABLE extraction_candidate ADD CONSTRAINT extraction_candidate_ytd_pace_whole
            CHECK ((ytd_quarterly_burn_usd IS NULL) = (ytd_burn_days IS NULL)
                   AND (ytd_burn_days IS NULL OR ytd_burn_days > 0));
    END IF;
END
$$;
