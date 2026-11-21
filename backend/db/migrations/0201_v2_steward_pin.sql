-- 0201 — steward_pin: "always ask me" for one company's platform value (V2 W1b; the V2 W0/W1 plan, Q3).
--
-- WHAT. A company whose extractor result is reliably wrong (a dual class, an ADR ratio) gets pinned: W3's AUTO
-- writer, seeing the LATEST event for (company, value type) is 'pin', writes a FLAG candidate (flag 'pinned')
-- instead of a fact. A pin never blocks a steward's own write.
--
-- APPEND-ONLY EVENTS (the `no_update` trigger): one row per pin or unpin, latest `recorded_at` per
-- (tenant_id, security_id, value_type) wins. The log keeps who pinned (`decided_by`, the steward's email) and when;
-- unpinning writes an 'unpin' row, never a delete.
--
-- DATA scope (domain/scopes.py): the row carries the data tenant's id. Nothing reads or writes it yet (W2 / W3).
-- Depends only on 0001 (tenant, security_master, raise_no_update()).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE steward_pin;
--   DELETE FROM schema_migrations WHERE filename = '0201_v2_steward_pin.sql';

CREATE TABLE IF NOT EXISTS steward_pin (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    security_id uuid        NOT NULL REFERENCES security_master (id),
    value_type  text        NOT NULL CHECK (value_type IN ('shares_outstanding', 'cash_burn')),
    action      text        NOT NULL CHECK (action IN ('pin', 'unpin')),
    note        text        NULL,
    decided_by  text        NOT NULL,     -- the steward's email
    recorded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_steward_pin_company
    ON steward_pin (tenant_id, security_id, value_type, recorded_at);

DROP TRIGGER IF EXISTS no_update ON steward_pin;
CREATE TRIGGER no_update BEFORE UPDATE ON steward_pin
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
