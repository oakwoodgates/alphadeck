-- 0200 — extraction_candidate: an extractor result for a PLATFORM value (a share count, cash/burn) that has not
-- been applied as a fact (V2 W1b; the V2 W0/W1 plan, Q2).
--
-- WHAT. One row per extractor result that is NOT a fact:
--   * outcome 'flag'    — a FLAG-tier value awaiting the steward: the raw value (which may be empty when the
--                         extractor found none), its detected `flags` (dual-class, stale-cover, ytd-raw, ...), the
--                         filing it came from and the located passages. It names its filing (`source_ref`) and
--                         the cover / period date (`event_date`) that would become `valid_from` if applied.
--   * outcome 'nothing' — the extractor found no value; `reason` says why (no-annual-filing, cover-not-located,
--                         cash-generative, financials-in-exhibit, exhibit-ambiguous, ...).
-- An AUTO value is applied as a fact row and never lands here.
--
-- NOT A FACT, BY CONSTRUCTION. This table is not in `db.bitemporal._FACT_IDENTITY`, so every as-of read (both
-- clocks), `fact_exists` and `append_fact` refuse it; it has no public clock (`db.public_clock.PUBLIC_TIME`); no
-- point-in-time view has an accessor for it; and the replay mirror never exports it. A candidate can never move a
-- meter, a signal or a call (`tests/db/test_v2_schema.py` pins all of this).
--
-- APPEND-ONLY (the `no_update` trigger): a newer extraction writes a newer row; the steward's decision on a
-- candidate is W2's table, never an edit here. `recorded_at` is bookkeeping (when we wrote it), never a history
-- gate (#1).
--
-- DATA scope (domain/scopes.py): the row carries the data tenant's id. Nothing reads or writes it yet (W2 derives
-- the per-value state from it; W3 writes it).
-- Depends only on 0001 (tenant, security_master, raise_no_update()).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE extraction_candidate;
--   DELETE FROM schema_migrations WHERE filename = '0200_v2_extraction_candidate.sql';

CREATE TABLE IF NOT EXISTS extraction_candidate (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid        NOT NULL REFERENCES tenant (id),          -- the data tenant
    security_id        uuid        NOT NULL REFERENCES security_master (id),
    fact_type          text        NOT NULL CHECK (fact_type IN ('shares_outstanding', 'cash_burn')),
    outcome            text        NOT NULL CHECK (outcome IN ('flag', 'nothing')),
    reason             text        NULL,      -- 'nothing': no-annual-filing | cover-not-located | cash-generative | ...
    flags              text[]      NOT NULL DEFAULT '{}',
    source             text        NULL,
    source_ref         text        NULL,      -- the filing URL
    event_date         date        NULL,      -- cover / period date (would become valid_from)
    shares             numeric     NULL,
    cash_usd           numeric     NULL,
    quarterly_burn_usd numeric     NULL,
    statement_currency text        NULL,
    ads_ratio          integer     NULL,
    ads_ratio_status   text        NULL,
    filing_accession   text        NULL,
    filing_accepted    timestamptz NULL,
    note               text        NULL,
    located_passages   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT extraction_candidate_nothing_has_reason CHECK (outcome <> 'nothing' OR reason IS NOT NULL),
    CONSTRAINT extraction_candidate_flag_has_filing CHECK (outcome <> 'flag' OR (source_ref IS NOT NULL AND event_date IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_extraction_candidate_company
    ON extraction_candidate (tenant_id, security_id, fact_type, recorded_at);

DROP TRIGGER IF EXISTS no_update ON extraction_candidate;
CREATE TRIGGER no_update BEFORE UPDATE ON extraction_candidate
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
