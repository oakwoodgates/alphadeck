-- Alpha Deck — the fund SHARES-OUTSTANDING fact (ETF net flow, F1 — the sampler's store).
--
-- One daily-ish sample of an ETF sleeve's own shares outstanding, scraped from the issuer's fund page
-- (Global X) or the aggregator fallback (stockanalysis.com). Priced Δshares between samples IS the fund's
-- net creation/redemption flow — display context on the sleeve dossier, never a call input (#4/#6).
--
-- DISTINCT from fact_shares_outstanding (XBRL/companyfacts cover-page shares for OPERATING companies,
-- operator-ratified via the Workbench): this table is FEED data for funds — sampled, never ratified,
-- like fact_price_eod. Mirrors fact_price_eod's shape + discipline exactly:
--   d           = the SOURCE'S OWN stated as-of date (scraped from the page - the issuer's AS_OF_DATE /
--                 the aggregator's trading date), NEVER an assumed "today" (#1: valid_from = event time)
--   shares_out  = the sampled count (the aggregator's is ~10k-share rounded; `source` says which)
--   source      = the adapter that produced the sample ('globalx' | 'stockanalysis')
--   source_ref  = the exact URL sampled (#6: every reading traces to its page)
-- Append-only: a changed count for the SAME d is a NEW version (a later recorded_at); the as-of read's
-- DISTINCT ON (security_id, d) keeps the latest version — see db/bitemporal.py (_FACT_IDENTITY).

CREATE TABLE IF NOT EXISTS fact_fund_shares (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    security_id uuid NOT NULL REFERENCES security_master (id),
    d           date NOT NULL,
    shares_out  numeric NOT NULL,
    source      text NOT NULL,
    source_ref  text NOT NULL,
    valid_from  date NOT NULL,                          -- = d
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, d, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_fund_shares_asof
    ON fact_fund_shares (tenant_id, security_id, valid_from, recorded_at);

DROP TRIGGER IF EXISTS no_update ON fact_fund_shares;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_fund_shares
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
