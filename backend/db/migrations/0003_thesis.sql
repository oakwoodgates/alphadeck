-- Alpha Deck — thesis spine + accountability log (M3a).
--
-- These are OPERATIONAL tables (the thesis is mutable current state), NOT bitemporal facts: no
-- valid_from/recorded_at axes here. The bitemporal fact tables (0001) remain the point-in-time source
-- detectors read; signal firings are RE-DERIVED from those facts on every read, never persisted.
-- tenant_id is on every table from day one (auth deferred). Children FK the thesis with ON DELETE
-- CASCADE so a thesis truncate cleans the whole spine.

CREATE TABLE IF NOT EXISTS thesis (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    parent_id   uuid REFERENCES thesis (id),            -- nullable; umbrella/segment hierarchy (M5)
    name        text NOT NULL,
    narrative   text NOT NULL,                          -- the operator's words, preserved
    ticker      text,
    -- position (0..1 per thesis); its presence (opened_on <= asof) drives the Managing state
    position_entry_price   numeric,
    position_current_price numeric,
    position_opened_on     date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS basket_member (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    thesis_id   uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    ordinal     int  NOT NULL,                          -- preserves basket order on read
    ticker      text NOT NULL,
    role        text NOT NULL,
    archetype   text NOT NULL,                          -- leader | high_beta | lotto | shovel
    security_id uuid REFERENCES security_master (id),   -- nullable until resolved
    detail      text
);

CREATE TABLE IF NOT EXISTS evidence (
    id          uuid PRIMARY KEY,                       -- domain-supplied id; APPEND-ONLY
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    thesis_id   uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    kind        text NOT NULL,                          -- display label e.g. "FORM 4", "8-K"
    label       text NOT NULL,
    ref         text NOT NULL,                          -- URL / EDGAR accession
    date_label  text,
    ordinal     int  NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    thesis_id   uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    label       text NOT NULL,
    kind        text,
    when_date   date,                                   -- drives the catalyst_surface filter
    when_label  text,
    ordinal     int  NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_criterion (
    id          uuid PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    thesis_id   uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    text        text NOT NULL,
    ordinal     int  NOT NULL
);

-- Accountability log: every assembled CallCard, write-only and immutable. NOT the read path — the API
-- recomputes the card live from facts at the requested asof. The full card is stored as jsonb; state
-- and verdict are denormalized as columns for a future scoreboard.
CREATE TABLE IF NOT EXISTS calls (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq         bigint GENERATED ALWAYS AS IDENTITY,    -- total insertion order (now() ties within a txn)
    tenant_id   uuid NOT NULL REFERENCES tenant (id),
    thesis_id   uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    asof        date  NOT NULL,
    state       text  NOT NULL,
    verdict     text  NOT NULL,
    card        jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_basket_member_thesis  ON basket_member (thesis_id);
CREATE INDEX IF NOT EXISTS ix_evidence_thesis       ON evidence (thesis_id);
CREATE INDEX IF NOT EXISTS ix_catalyst_thesis       ON catalyst (thesis_id);
CREATE INDEX IF NOT EXISTS ix_kill_criterion_thesis ON kill_criterion (thesis_id);
CREATE INDEX IF NOT EXISTS ix_calls_thesis_asof     ON calls (thesis_id, asof);

-- The accountability log is immutable: enforce "never rewrite a recorded call" as a real DB guard
-- (reuses raise_no_update() from 0001), not a convention.
DROP TRIGGER IF EXISTS no_update ON calls;
CREATE TRIGGER no_update BEFORE UPDATE ON calls
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
