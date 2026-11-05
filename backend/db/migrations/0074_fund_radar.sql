-- Alpha Deck — the fund launch radar's three fact tables (docs/FUND_RADAR.md; the SPAC radar's shape, 0030).
--
-- fact_fund_filing: one fund-registration FILING seen on the EDGAR daily index (485APOS / 485BPOS / N-1A /
-- N-1A/A, and S-1 / S-1/A from a filer named like a fund), read from its header (<accession>.hdr.sgml).
-- valid_from = the filing's PUBLIC date (#1): a private-to-public filing counts from the day SEC RELEASED it
-- (the header's first line), never from its old acceptance; otherwise the night of its ACCEPTANCE-DATETIME
-- (an 18:00 acceptance counts that evening), else the filing date.
--
-- fact_fund_series: one row per (accession, fund series) the header names — new, new-classes-on-an-existing
-- series, or existing. An S-1 header carries no series block: its filer's name is the fund name, keyed
-- 'cik:<filer cik>' (series_id NULL), so the natural key stays idempotent. FACTS ONLY — the stage of a fund
-- (filed / effective / ticker / listed) is derived READ-TIME (radar/fund_stage.py), never stored.
--
-- fact_fund_symbol: one (series, class, symbol) seen in a symbol file, by source. Neither file carries a date,
-- so valid_from = the first night WE saw it (honest, and the history only starts the day the radar does).
--
-- All append-only (no_update trigger); a changed re-observation is a NEW version (later recorded_at); reads
-- take DISTINCT ON (natural key) ... recorded_at DESC. Idempotency tests COUNT THE TABLE.

CREATE TABLE IF NOT EXISTS fact_fund_filing (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenant (id),
    accession          text NOT NULL,          -- the natural key
    form               text NOT NULL,
    filer_ciks         text[] NOT NULL,        -- one per co-registrant (zero-padded), first = the lead filer
    filer_name         text NOT NULL,          -- the lead filer's conformed name (the trust)
    accepted_at        timestamptz,            -- ACCEPTANCE-DATETIME (ET -> instant); NULL = absent from the header
    filing_date        date,
    effectiveness_date date,                   -- the header's EFFECTIVENESS-DATE (a 485APOS names its target)
    private_to_public  boolean NOT NULL DEFAULT false,
    released_on        date,                   -- the header's first-line date (the release day when private-to-public)
    public_date        date NOT NULL,          -- the PUBLIC date (#1) — same value as valid_from
    source_ref         text NOT NULL,          -- the EDGAR filing index URL (#6)
    valid_from         date NOT NULL,          -- = public_date
    recorded_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_fund_filing_public ON fact_fund_filing (tenant_id, public_date);

DROP TRIGGER IF EXISTS no_update ON fact_fund_filing;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_fund_filing
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

CREATE TABLE IF NOT EXISTS fact_fund_series (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    accession    text NOT NULL,
    series_key   text NOT NULL,       -- the series ID, or 'cik:<filer cik>' for an S-1's name-only row
    series_id    text,                -- S000...; NULL for a name-only row
    series_name  text NOT NULL,
    kind         text NOT NULL,       -- new | new_classes | existing
    classes      jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{class_id, name, ticker}] as the filing lists them
    valid_from   date NOT NULL,       -- = the filing's public date
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, accession, series_key, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_fund_series_sid ON fact_fund_series (tenant_id, series_id);
CREATE INDEX IF NOT EXISTS ix_fund_series_acc ON fact_fund_series (tenant_id, accession);

DROP TRIGGER IF EXISTS no_update ON fact_fund_series;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_fund_series
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

CREATE TABLE IF NOT EXISTS fact_fund_symbol (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    series_id    text NOT NULL,
    class_id     text NOT NULL DEFAULT '',
    symbol       text NOT NULL,
    source       text NOT NULL CHECK (source IN ('sec_ticker_file', 'nasdaq_directory')),
    valid_from   date NOT NULL,       -- the first night we saw it in this source
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, series_id, class_id, symbol, source, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_fund_symbol_sid ON fact_fund_symbol (tenant_id, series_id);

DROP TRIGGER IF EXISTS no_update ON fact_fund_symbol;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_fund_symbol
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
