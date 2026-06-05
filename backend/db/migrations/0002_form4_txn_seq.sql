-- A single Form 4 can carry multiple transactions for the same insider on the same date
-- (e.g. an option exercise 'M' + a sale 'S'). Distinguish them by their position within the filing
-- so the bitemporal natural key doesn't collide distinct transactions into one.

ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS txn_seq int NOT NULL DEFAULT 0;

ALTER TABLE fact_insider_txn
    DROP CONSTRAINT IF EXISTS fact_insider_txn_tenant_id_accession_insider_name_valid_fro_key;
ALTER TABLE fact_insider_txn
    ADD CONSTRAINT fact_insider_txn_natural_key
    UNIQUE (tenant_id, accession, insider_name, valid_from, txn_seq, recorded_at);
