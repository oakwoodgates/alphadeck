-- Alpha Deck — initial bitemporal schema (M1a).
--
-- Every fact carries two time axes:
--   valid_from  = event/effective time (when it happened / was effective in the filing)
--   recorded_at = transaction time     (when WE learned it; corrections get a later recorded_at)
-- Facts are APPEND-ONLY: a correction inserts a NEW row, never an UPDATE-in-place. This is what
-- makes the point-in-time replay honest (a later correction cannot leak into an earlier as-of read).

CREATE TABLE IF NOT EXISTS tenant (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL
);

-- one seeded tenant; multi-tenant serving deferred, the seam is here from day one
INSERT INTO tenant (id, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'default')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS security_master (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    cik         text,
    ticker      text,
    cusip       text,
    figi        text,
    name        text,
    valid_from  date NOT NULL,                          -- identity row: the broadener UPDATEs it in place
    valid_to    date,                                   -- (NOT append-only; nothing reads the master as-of)
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fact_insider_txn (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    security_id  uuid NOT NULL REFERENCES security_master (id),
    insider_name text,
    insider_role text,
    txn_code     text,                                  -- 'P' = open-market purchase
    shares       numeric,
    price        numeric,
    usd          numeric,
    accession    text NOT NULL,                         -- EDGAR provenance anchor
    valid_from   date NOT NULL,                         -- transaction/effective date in the filing
    valid_to     date,
    recorded_at  timestamptz NOT NULL DEFAULT now(),    -- when WE learned it (transaction-time)
    supersedes   uuid REFERENCES fact_insider_txn (id),
    UNIQUE (tenant_id, accession, insider_name, valid_from, recorded_at)
);

CREATE TABLE IF NOT EXISTS fact_price_eod (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    security_id uuid NOT NULL REFERENCES security_master (id),
    d           date NOT NULL,
    open        numeric,
    high        numeric,
    low         numeric,
    close       numeric,
    volume      numeric,
    valid_from  date NOT NULL,                          -- = d
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, d, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_insider_asof
    ON fact_insider_txn (tenant_id, security_id, valid_from, recorded_at);
CREATE INDEX IF NOT EXISTS ix_price_asof
    ON fact_price_eod (tenant_id, security_id, valid_from, recorded_at);

-- Append-only enforcement: a real DB guard (not a convention). UPDATE on a fact table raises.
CREATE OR REPLACE FUNCTION raise_no_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'fact tables are append-only; UPDATE on % is not allowed (insert a correction instead)',
        TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS no_update ON fact_insider_txn;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_insider_txn
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

DROP TRIGGER IF EXISTS no_update ON fact_price_eod;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_price_eod
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
