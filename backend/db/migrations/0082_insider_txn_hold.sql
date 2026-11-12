-- 0082 — insider_txn_hold: the Form 4 / Form 4/A rows the ingest HOLDS instead of storing as a fact.
--
-- WHY. The ingest refuses a transaction whose date cannot be true (`ingest.edgar.form4.implausible_txn_date`:
-- a year before 1934, or a year after the accession's own filing year). Until now a refused row was printed and
-- stored NOWHERE, which had two costs:
--   * a filing whose EVERY row is refused left nothing behind, so `existing_accessions` never listed it and the
--     nightly re-read and re-printed it on every pass (MEASURED on prod 2026-11-11: 7 filings, 20 identical lines
--     a night);
--   * a refused row inside a filing that otherwise stored printed once, on the night it was first read, and was
--     visible nowhere afterwards (MEASURED: 22 rows in 20 stored filings).
-- A refused row is now written HERE, once, in the same transaction as the filing's good rows. It is still not a
-- fact: no corrected date is invented (#3), and nothing on the call path, no signal and no dashboard reads this
-- table. It only makes the row durable, the filing "already handled", and the hold visible to the operator once.
--
-- ONE ROW PER HELD ROW, NEVER UPDATED (the `no_update` trigger the fact tables use; the unique key makes a
-- re-read an `ON CONFLICT DO NOTHING` no-op, so the table never grows on a re-run).
--   * `form` — '4' (a Form 4) or '4/A' (a row of an amendment; the amendment record itself still keeps only its
--     good rows, exactly as before).
--   * `reason` — the rule's own code: 'year_before_1934' / 'year_after_filing_year'. 'after_filing' (a date
--     after the filing's own filing date) is RESERVED for the next step and written by nothing today, so that
--     step needs no second migration.
--   * `row` — the parsed row as `parse_form4` returns it (dates as ISO text), plus `txn_date_as_filed`, the text
--     of the filing's own `<transactionDate>` element.
--   * `period_of_report_as_filed` / `signature_date_as_filed` — the filing's `<periodOfReport>` and its LAST
--     `<signatureDate>`, as text, exactly as filed (either can itself be a typo: evidence, never a correction).
--   * `filed` / `accepted` — the filing date and the acceptance instant the ingest resolved (NULL when it had
--     none).
--   * `recorded_at` — when we wrote the hold. Bookkeeping, never a history gate (#1).
--
-- DATA scope (domain/scopes.py), like `fact_insider_txn`: the row copies the ingest's tenant_id.
-- Depends only on 0001 (tenant, security_master, raise_no_update()).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE insider_txn_hold;
--   DELETE FROM schema_migrations WHERE filename = '0082_insider_txn_hold.sql';

CREATE TABLE IF NOT EXISTS insider_txn_hold (
    id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                  uuid        NOT NULL REFERENCES tenant (id),
    security_id                uuid        NOT NULL REFERENCES security_master (id),
    accession                  text        NOT NULL,
    txn_seq                    integer     NOT NULL,   -- the row's position in the filing (the ingest's txn_seq)
    form                       text        NOT NULL CHECK (form IN ('4', '4/A')),
    reason                     text        NOT NULL
        CHECK (reason IN ('year_before_1934', 'year_after_filing_year', 'after_filing')),
    "row"                      jsonb       NOT NULL,   -- the parse_form4 row + txn_date_as_filed
    period_of_report_as_filed  text        NULL,
    signature_date_as_filed    text        NULL,       -- the LAST signature date when there are several
    filed                      date        NULL,
    accepted                   timestamptz NULL,
    recorded_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT insider_txn_hold_natural_key UNIQUE (tenant_id, security_id, accession, txn_seq, form)
);

DROP TRIGGER IF EXISTS no_update ON insider_txn_hold;
CREATE TRIGGER no_update BEFORE UPDATE ON insider_txn_hold
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
