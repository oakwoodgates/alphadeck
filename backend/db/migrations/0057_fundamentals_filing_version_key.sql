-- Alpha Deck — fact_fundamentals' version key moves from recorded_at to valid_from (the honest stamp).
--
-- WHY: until this migration the companyfacts ingest stamped `recorded_at = filed 00:00 UTC` — a backdated
-- transaction time (MEASURED on prod: all 120,876 rows). 0036 put that stamp in the UNIQUE
-- (tenant, security, metric, period_end, recorded_at), and because the stamp was a function of `filed`, the
-- constraint really keyed one version per FILING: "a re-ingest of the SAME filing can never grow the table".
--
-- The ingest now stamps the HONEST transaction time (`ingest.fundamentals.store_quarters`: one database
-- instant per batch). One pass writes several filings of the same period together — a name's onboarding
-- pull carries the original quarter AND each later 10-Q/10-K comparative (MEASURED: 20,752 prod identities
-- hold >= 2 versions written in one transaction) — so under the old key those versions would share one
-- recorded_at and collide. And the guarantee the old key gave would be gone: two honest stamps are always
-- distinct, so a duplicate append of the same filing would no longer be refused.
--
-- THE NEW KEY says what the old one meant: one version per (tenant, security, metric, period_end, FILING)
-- — `valid_from` is the filing date, and it is exactly the ingest's own idempotency key
-- (`_existing_versions`). MEASURED on prod 2026-10-21: 0 groups violate it, so the ADD cannot fail on
-- existing data (the old key was a bijection of this one on every stored row). Nothing does ON CONFLICT
-- against either constraint. Existing rows are untouched (no rewrite of history). DROP-then-ADD by name keeps
-- re-application idempotent (the 0002 / 0037 precedent); the old constraint carries Postgres's generated
-- name, truncated at 63 bytes exactly as 0036 created it on every database.

ALTER TABLE fact_fundamentals
    DROP CONSTRAINT IF EXISTS fact_fundamentals_tenant_id_security_id_metric_key_period_e_key;
ALTER TABLE fact_fundamentals
    DROP CONSTRAINT IF EXISTS fact_fundamentals_filing_version_key;
ALTER TABLE fact_fundamentals
    ADD CONSTRAINT fact_fundamentals_filing_version_key
    UNIQUE (tenant_id, security_id, metric_key, period_end, valid_from);
