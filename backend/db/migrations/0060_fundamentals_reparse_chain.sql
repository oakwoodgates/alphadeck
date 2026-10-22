-- Alpha Deck — fact_fundamentals: a filing may carry a RE-PARSE of its number (the revenue-label fix), as an
-- explicit link — never a silent duplicate.
--
-- WHY: the revenue-label fix (2026-10-20, `ingest.fundamentals.select_revenue`) reads each company's CURRENT
-- revenue tag. Where the old first-present rule stored another tag's DIFFERENT number for the same filing
-- (VICR's 2025-08-01 10-Q holds Q2-2025 = $141.0M under `Revenues` where the current tag reports $96.0M), the
-- fix appends the current tag's number as a NEW VERSION OF THAT SAME FILING, so the read never mixes two
-- definitions on one filing and the number dates at the filing that first made it public (not at a year-later
-- comparative — the D3 phantom). 0057's UNIQUE (…, period_end, valid_from) — one row per filing — refuses
-- that version outright.
--
-- THE KEY (one column added): (tenant, security, metric_key, period_end, valid_from, supersedes), NULLS NOT
-- DISTINCT. `supersedes` (0036's provenance column, unused on this table until now — the `backfill_accepted`
-- precedent on the insider table) names the version a re-parse replaces:
--   * an ORIGINAL (supersedes NULL) stays ONE per filing — exactly 0057's guarantee: any second plain row for a
--     filing, whatever its number or stamp, is refused loudly (NULLS NOT DISTINCT, Postgres 15+);
--   * a RE-PARSE names the version it replaces, and a version can be replaced only ONCE, so a filing's versions
--     form one linear chain (original -> re-parse -> …) and a repeated re-parse is refused;
--   * the guard below refuses a re-parse that does not change the number or that names another filing's
--     version, so the chain can never grow a no-op link behind a correct read (count-the-table).
-- A label that switches BACK on a filing (old -> new -> old number) is admitted as one more link; a key on the
-- value would refuse it and leave the read on the stale number.
--
-- The READ is unchanged: a filing's versions order by `recorded_at` (a re-parse carries the honest stamp of the
-- pass that wrote it, later than what it replaces), the latest-recorded wins, and D3a's run counts each filing
-- once (`db.bitemporal.first_knowable_versions`). The new key contains 0057's, so no existing row can violate
-- it; existing rows are untouched. DROP-then-ADD by name keeps re-application idempotent (the 0057 precedent).

ALTER TABLE fact_fundamentals
    DROP CONSTRAINT IF EXISTS fact_fundamentals_filing_version_key;
ALTER TABLE fact_fundamentals
    DROP CONSTRAINT IF EXISTS fact_fundamentals_filing_reparse_key;
ALTER TABLE fact_fundamentals
    ADD CONSTRAINT fact_fundamentals_filing_reparse_key
    UNIQUE NULLS NOT DISTINCT (tenant_id, security_id, metric_key, period_end, valid_from, supersedes);

CREATE OR REPLACE FUNCTION fact_fundamentals_reparse_guard() RETURNS trigger AS $$
DECLARE
    prev fact_fundamentals%ROWTYPE;
BEGIN
    IF NEW.supersedes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO prev FROM fact_fundamentals WHERE id = NEW.supersedes;
    IF prev.tenant_id IS DISTINCT FROM NEW.tenant_id
        OR prev.security_id IS DISTINCT FROM NEW.security_id
        OR prev.metric_key IS DISTINCT FROM NEW.metric_key
        OR prev.period_end IS DISTINCT FROM NEW.period_end
        OR prev.valid_from IS DISTINCT FROM NEW.valid_from THEN
        RAISE EXCEPTION 'fact_fundamentals re-parse must replace a version of the SAME filing (supersedes %)',
            NEW.supersedes
            USING ERRCODE = 'check_violation';
    END IF;
    IF prev.value = NEW.value THEN
        RAISE EXCEPTION 'fact_fundamentals re-parse must change the number (% on %)', NEW.value, NEW.valid_from
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS reparse_guard ON fact_fundamentals;
CREATE TRIGGER reparse_guard BEFORE INSERT ON fact_fundamentals
    FOR EACH ROW EXECUTE FUNCTION fact_fundamentals_reparse_guard();
