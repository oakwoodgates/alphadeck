-- Workbench scoring facts (Phase 2, Slice 2): the real data behind the data-derived score meters.
--
-- Each is a provenance-carrying, bitemporal, append-only fact mirroring fact_catalyst (0005) EXACTLY:
-- operator-ratified from a real filing (source_ref = the 10-K/10-Q segment line it came from), a
-- correction is a NEW row (never an UPDATE), and the as-of/replay reads stay honest. NEVER a model
-- number — the LLM may propose a source, but the operator ratifies the real figure from the real filing.
-- The SCORES re-derive from these facts on read (Option B); only these underlying facts are stored.
--
-- (The dilution meter does NOT get a fact here — it reuses the existing dilution clock / fact_dilution.)

-- exposure PURITY: the % of revenue from a named business line (the 10-K segment table).
CREATE TABLE IF NOT EXISTS fact_revenue_mix (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    security_id   uuid NOT NULL REFERENCES security_master (id),
    segment_label text NOT NULL,                 -- the revenue line, e.g. "nuclear" / "enrichment"
    mix_pct       numeric NOT NULL,              -- % of revenue from that line (the purity number, 0..100)
    source        text NOT NULL,                 -- provenance kind: 'ratified' | '10-k'
    source_ref    text NOT NULL,                 -- the 10-K segment (URL/accession) — provenance + identity
    ratified_by   text,                          -- the operator who ratified (NULL for future feeds)
    valid_from    date NOT NULL,                 -- the filing's effective date (no lookahead)
    valid_to      date,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    supersedes    uuid REFERENCES fact_revenue_mix (id),
    UNIQUE (tenant_id, source_ref, recorded_at)
);
CREATE INDEX IF NOT EXISTS ix_revenue_mix_asof
    ON fact_revenue_mix (tenant_id, security_id, valid_from, recorded_at);
DROP TRIGGER IF EXISTS no_update ON fact_revenue_mix;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_revenue_mix
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

-- MARKET-CAP basis: shares outstanding (the 10-Q cover / XBRL). market cap = close × shares (derived on read).
CREATE TABLE IF NOT EXISTS fact_shares_outstanding (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    security_id   uuid NOT NULL REFERENCES security_master (id),
    shares        numeric NOT NULL,              -- shares outstanding
    source        text NOT NULL,
    source_ref    text NOT NULL,                 -- the 10-Q cover / XBRL fact — provenance + identity
    ratified_by   text,
    valid_from    date NOT NULL,
    valid_to      date,
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    supersedes    uuid REFERENCES fact_shares_outstanding (id),
    UNIQUE (tenant_id, source_ref, recorded_at)
);
CREATE INDEX IF NOT EXISTS ix_shares_outstanding_asof
    ON fact_shares_outstanding (tenant_id, security_id, valid_from, recorded_at);
DROP TRIGGER IF EXISTS no_update ON fact_shares_outstanding;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_shares_outstanding
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

-- cash-RUNWAY basis: cash on hand + quarterly burn (the 10-Q). runway months = cash / (burn/3) (derived on read).
CREATE TABLE IF NOT EXISTS fact_cash_burn (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenant (id),
    security_id        uuid NOT NULL REFERENCES security_master (id),
    cash_usd           numeric NOT NULL,         -- cash + equivalents on hand
    quarterly_burn_usd numeric NOT NULL,         -- net cash used in ops per quarter (<= 0 means cash-positive)
    source             text NOT NULL,
    source_ref         text NOT NULL,            -- the 10-Q (URL/accession) — provenance + identity
    ratified_by        text,
    valid_from         date NOT NULL,
    valid_to           date,
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    supersedes         uuid REFERENCES fact_cash_burn (id),
    UNIQUE (tenant_id, source_ref, recorded_at)
);
CREATE INDEX IF NOT EXISTS ix_cash_burn_asof
    ON fact_cash_burn (tenant_id, security_id, valid_from, recorded_at);
DROP TRIGGER IF EXISTS no_update ON fact_cash_burn;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_cash_burn
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
