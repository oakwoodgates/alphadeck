-- M5 Part B: fact_theme_conviction — an operator-ratified THEME conviction that supplies Key 1 as a
-- FALLBACK for a basket member with no name-specific conviction of its own. Same shape/persistence as
-- fact_catalyst (bitemporal, append-only, provenanced; a correction is a NEW row, never an UPDATE), but
-- keyed by THESIS, not security: one theme conviction broadcasts to every eligible member. The grade is
-- ratified at 'flip' (capped at starter — belief can never mint a core); horizon_end is the operator-set
-- expiry (NULL -> the configured default). NEVER a model guess — source/source_ref carry the real basis.

CREATE TABLE IF NOT EXISTS fact_theme_conviction (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    thesis_id     uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,  -- the THEME (not co-located)
    grade         text NOT NULL,                 -- 'flip' (capped at starter); never 'core'
    label         text NOT NULL,                 -- human description (the operator's narrative basis)
    source        text NOT NULL,                 -- provenance kind: 'ratified' for now
    source_ref    text NOT NULL,                 -- the unique reference: URL / doc id (identity)
    ratified_by   text,                          -- the operator who ratified (NULL for future feeds)
    valid_from    date NOT NULL,                 -- the conviction's effective/event date (no lookahead)
    valid_to      date,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    horizon_end   date,                          -- the operator-set horizon (expiry); NULL -> default
    supersedes    uuid REFERENCES fact_theme_conviction (id),
    UNIQUE (tenant_id, source_ref, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_theme_conviction_asof
    ON fact_theme_conviction (tenant_id, thesis_id, valid_from, recorded_at);

-- Append-only guard (same mechanism as the other fact tables).
DROP TRIGGER IF EXISTS no_update ON fact_theme_conviction;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_theme_conviction
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
