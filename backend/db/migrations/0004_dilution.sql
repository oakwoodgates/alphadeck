-- M4a-i: fact_dilution — structured convertible-note (dilution) facts parsed from EDGAR 8-Ks.
-- Bitemporal + append-only like the other fact tables (a correction is a NEW row, never an UPDATE).

CREATE TABLE IF NOT EXISTS fact_dilution (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL REFERENCES tenant (id),
    security_id            uuid NOT NULL REFERENCES security_master (id),
    instrument_kind        text NOT NULL,                 -- 'convertible_notes' (more flavors later)
    accession              text NOT NULL,                 -- EDGAR provenance (the issuance 8-K)
    principal_total_usd    numeric,
    shares_outstanding     numeric,                       -- seeded basis for the % overhang
    shares_outstanding_ref text,                          -- provenance for shares_outstanding
    terms                  jsonb NOT NULL,                -- the full parsed ConvertTerms
    valid_from             date NOT NULL,                 -- issuance/effective date (no lookahead)
    valid_to               date,
    recorded_at            timestamptz NOT NULL DEFAULT now(),
    supersedes             uuid REFERENCES fact_dilution (id),
    UNIQUE (tenant_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_dilution_asof
    ON fact_dilution (tenant_id, security_id, valid_from, recorded_at);

-- Append-only guard (same mechanism as the other fact tables).
DROP TRIGGER IF EXISTS no_update ON fact_dilution;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_dilution
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
