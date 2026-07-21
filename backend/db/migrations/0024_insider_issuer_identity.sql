-- Alpha Deck — capture the Form 4 issuer + reporting-owner IDENTITY (issuer CIK/name + owner CIK).
--
-- WHY: SEC code 'P' ("open market OR private purchase") is filed by parties that are NOT the officers /
-- directors the Lakonishok-Lee open-market-purchase literature is about — and some of those inflate Key-1
-- insider conviction with large, AT-MARKET blocks the price screen (below-day-low / the $2B ceiling) does
-- NOT catch. A systematic scan of `fact_insider_txn` surfaced the cleanest sub-case: the ISSUER filing a
-- Form 4 on ITSELF (reporting owner == issuer) — KYOCERA-on-KYOCERA ($690M @ $21.75), Roivant-on-Roivant
-- ($350M @ $21) — a buyback / treasury / ADR mechanic, never personal insider conviction (#3). The detector
-- only ever had `security_id` + txns, so it could not tell "the filer IS the company" from a real buy.
--
-- This captures the identity keys the exclusion (and a later, deferred affiliate-block pass) needs:
--   issuer_cik   — the `<issuer><issuerCik>` (canonical issuer id)
--   issuer_name  — the `<issuer><issuerName>` (self-describing; the deferred fund/affiliate pass wants it)
--   rpt_owner_cik — the `<reportingOwner><reportingOwnerId><rptOwnerCik>` (canonical FILER id)
-- Issuer-self ⇔ `rpt_owner_cik == issuer_cik` — a canonical, robust match. The insider detector uses it on
-- NEWLY-ingested (and replay) rows; already-ingested rows (these columns NULL) fall back to a name match
-- against `security_master.name`, so the exclusion is effective on today's data with no backfill required.
--
-- SCOPE — identity capture only. The excluded rows STAY in `fact_insider_txn` + the display tape (recall is
-- sacred, #9); only the CALL's open-market conviction total skips a self-filing. This is the same "keep the
-- fact, screen the call" shape as the price screen. The deferred Class-B pass (10%-owner fund/affiliate
-- blocks vs genuine activist buys) is left for a labeled-sample decision; these columns set it up recall-safe.
--
-- Additive + idempotent; append-compatible with the `no_update` row trigger (it guards UPDATEs, not schema).
-- Natural key is unchanged (`… accession, insider_name, valid_from, txn_seq, recorded_at`), so idempotency of
-- the incremental ingest is untouched. Existing rows stay NULL (the ingest is incremental — `existing_
-- accessions` skips stored filings — so these populate on NEWLY-ingested filings only; backfill is separate).

ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS issuer_cik    text;
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS issuer_name   text;
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS rpt_owner_cik text;
