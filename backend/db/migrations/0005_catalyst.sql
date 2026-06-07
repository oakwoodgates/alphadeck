-- #10: fact_catalyst — the conviction key for theme/catalyst theses (the theme analog of an insider
-- buy). A deterministic/operator-ratified, verifiable commitment with provenance. Bitemporal +
-- append-only like every other fact table (a correction is a NEW row, never an UPDATE), so the
-- as-of/replay reads stay honest. NEVER a model guess — `source`/`source_ref` carry the real source.

CREATE TABLE IF NOT EXISTS fact_catalyst (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    security_id   uuid NOT NULL REFERENCES security_master (id),  -- the SUBJECT (filer/awardee) -> co-location
    catalyst_type text NOT NULL,                 -- CatalystType: regulatory | contract | gov_funding | ...
    grade         text NOT NULL,                 -- 'core' (binding) | 'flip' (provisional); set at ratification
    label         text NOT NULL,                 -- human description ("20-yr PPA with <hyperscaler>")
    source        text NOT NULL,                 -- provenance kind: 'ratified' | '8-k' | 'doe_award' | 'nrc'
    source_ref    text NOT NULL,                 -- the unique reference: URL / accession / award id (identity)
    ratified_by   text,                          -- the operator who ratified (NULL for automated feeds)
    valid_from    date NOT NULL,                 -- the catalyst's effective/event date (no lookahead)
    valid_to      date,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    supersedes    uuid REFERENCES fact_catalyst (id),
    UNIQUE (tenant_id, source_ref, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_catalyst_asof
    ON fact_catalyst (tenant_id, security_id, valid_from, recorded_at);

-- Append-only guard (same mechanism as the other fact tables).
DROP TRIGGER IF EXISTS no_update ON fact_catalyst;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_catalyst
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
