-- Alpha Deck — add security_id to fact_insider_txn's natural-key constraint (the 2026-08-17 prod abort).
--
-- WHY: one logical insider fact is identified per (tenant, SECURITY, accession, insider, valid_from,
-- txn_seq). That is the grain the shared as-of read keys on — db/bitemporal.py dedups on
-- _FACT_IDENTITY['fact_insider_txn'] INSIDE a `WHERE security_id = …` scope — and the grain the
-- incremental ingest deliberately writes: `existing_accessions` skips per (tenant, security), so one
-- issuer held as TWO master rows (share classes / dual listings — the SEC maps one CIK to many tickers)
-- legitimately stores the SAME Form 4 once per security scope. The 0002 constraint omitted security_id,
-- under-keying identity: it forbade same-INSTANT versions of two DIFFERENT logical facts.
--
-- THE ABORT: the aff_10b5_1 backfill corrects every latest-version-NULL row inside per-batch
-- transactions, `recorded_at` defaulting to now() — transaction_timestamp, CONSTANT within a batch. Two
-- corrections of one filing key under two securities therefore carried identical
-- (tenant, accession, insider, valid_from, txn_seq, recorded_at) tuples -> UniqueViolation mid-run
-- (accession 0000072903-23-000037; the batch rolled back, the committed prefix stayed clean). The ingest
-- itself never tripped this only because pipeline.ingest_thesis commits PER SECURITY — an accident of
-- commit boundaries, not a guarantee.
--
-- WIDENING IS SAFE: the new column set is a superset, so any rows unique under the old key are unique
-- under the new one — the ADD cannot fail on existing data. Nothing does ON CONFLICT against this
-- constraint (real ingest idempotency is existing_accessions, already per-security). Same name kept;
-- DROP-then-ADD mirrors 0002 so re-application stays idempotent. The as-of read is unchanged — the
-- constraint now simply matches what it always keyed on.

ALTER TABLE fact_insider_txn
    DROP CONSTRAINT IF EXISTS fact_insider_txn_natural_key;
ALTER TABLE fact_insider_txn
    ADD CONSTRAINT fact_insider_txn_natural_key
    UNIQUE (tenant_id, security_id, accession, insider_name, valid_from, txn_seq, recorded_at);
