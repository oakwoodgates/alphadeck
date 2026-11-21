-- 0205 — platform_value_decision: the steward's decisions on a PLATFORM value (a share count, cash/burn) (V2 W2a;
-- the W2 build plan, Q2 and Q3).
--
-- WHY. V2 makes the share count and cash/burn one value per company for everyone, set by the platform (an AUTO
-- filing value) or by the steward. The steward approves a candidate (`extraction_candidate`, 0200), corrects a
-- value, dismisses a candidate, or undoes one of those. This table is where each such decision lives. Nothing in
-- this change writes a row here (W2b / W2c do); the reads only READ them.
--
-- Modeled on insider_txn_hold_decision (0083):
--   * `action` — 'approve' (apply a candidate's own value: `candidate_id` + the `fact_id` it wrote), 'correct'
--     (the steward's own value: the `fact_id` it wrote, optionally closing `candidate_id`), 'dismiss' (close a
--     candidate, no fact), 'undo' (the Undo of one approve / correct / dismiss, naming it in `revokes`).
--   * `fact_id` — the fact version an approve or correct wrote, in `fact_shares_outstanding` or `fact_cash_burn`
--     by `fact_type` (no foreign key: two tables). One decision per fact version.
--   * `reason` — why (a dismiss's reason, an undo's, a note); `decided_by` — the steward's email.
--   * `recorded_at` — when the steward decided (bookkeeping, never a history gate, #1).
--
-- THE UNDO IS A SET-ASIDE (db/set_aside.py). The fact version written by an approve or correct that a standing
-- undo names is skipped by every read of its table (both clocks, the basket prefetch, the replay mirror), so the
-- read falls back to the version it replaced. Nothing is deleted. An undone dismiss reopens its candidate.
-- An undo is final: a new decision redoes it (an undo of an undo is refused).
--
-- APPEND-ONLY (`no_update`). At most ONE undo per decision; an undo never undoes an undo and names a decision of
-- the SAME tenant, company and value type; a candidate named here belongs to the same tenant, company and type
-- (the `check_shape` trigger, so a hand-typed INSERT cannot build a shape the read misreads).
--
-- DATA scope (domain/scopes.py): the row carries the data tenant's id.
-- Depends on 0001 (tenant, security_master, raise_no_update()) and 0200 (extraction_candidate).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE platform_value_decision;
--   DROP FUNCTION platform_value_decision_check_shape();
--   DELETE FROM schema_migrations WHERE filename = '0205_v2_platform_value_decision.sql';

CREATE TABLE IF NOT EXISTS platform_value_decision (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),                -- the data tenant
    security_id   uuid        NOT NULL REFERENCES security_master (id),
    fact_type     text        NOT NULL CHECK (fact_type IN ('shares_outstanding', 'cash_burn')),
    action        text        NOT NULL CHECK (action IN ('approve', 'correct', 'dismiss', 'undo')),
    candidate_id  uuid        NULL REFERENCES extraction_candidate (id),
    fact_id       uuid        NULL,   -- the fact version this decision wrote (approve / correct); no FK: two tables
    revokes       uuid        NULL REFERENCES platform_value_decision (id),
    reason        text        NULL,
    decided_by    text        NOT NULL CHECK (btrim(decided_by) <> ''),       -- the steward's email
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT platform_value_decision_undo_pairing CHECK ((action = 'undo') = (revokes IS NOT NULL)),
    CONSTRAINT platform_value_decision_writes_a_fact CHECK ((action IN ('approve', 'correct')) = (fact_id IS NOT NULL)),
    CONSTRAINT platform_value_decision_names_a_candidate CHECK (action NOT IN ('approve', 'dismiss') OR candidate_id IS NOT NULL),
    CONSTRAINT platform_value_decision_undo_is_bare CHECK (action <> 'undo' OR (candidate_id IS NULL AND fact_id IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_platform_value_decision_company
    ON platform_value_decision (tenant_id, security_id, fact_type, recorded_at);
CREATE INDEX IF NOT EXISTS ix_platform_value_decision_candidate
    ON platform_value_decision (candidate_id) WHERE candidate_id IS NOT NULL;
-- at most ONE undo per decision: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_value_decision_one_undo
    ON platform_value_decision (revokes) WHERE revokes IS NOT NULL;
-- one decision per written fact version: the set-aside list names each version once
CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_value_decision_one_per_fact
    ON platform_value_decision (fact_id) WHERE fact_id IS NOT NULL;

CREATE OR REPLACE FUNCTION platform_value_decision_check_shape() RETURNS trigger AS $$
DECLARE
    t_tenant   uuid;
    t_security uuid;
    t_type     text;
    t_action   text;
BEGIN
    IF NEW.revokes IS NOT NULL THEN
        SELECT tenant_id, security_id, fact_type, action
          INTO t_tenant, t_security, t_type, t_action
          FROM platform_value_decision WHERE id = NEW.revokes;
        IF FOUND THEN
            IF t_action = 'undo' THEN
                RAISE EXCEPTION 'platform_value_decision: an undo cannot undo another undo (%)', NEW.revokes;
            END IF;
            IF t_tenant IS DISTINCT FROM NEW.tenant_id
               OR t_security IS DISTINCT FROM NEW.security_id
               OR t_type IS DISTINCT FROM NEW.fact_type THEN
                RAISE EXCEPTION 'platform_value_decision: an undo must name a decision of the same company and value type (%)',
                    NEW.revokes;
            END IF;
        END IF;  -- an unknown id: the foreign key refuses it
    END IF;
    IF NEW.candidate_id IS NOT NULL THEN
        SELECT tenant_id, security_id, fact_type
          INTO t_tenant, t_security, t_type
          FROM extraction_candidate WHERE id = NEW.candidate_id;
        IF FOUND AND (t_tenant IS DISTINCT FROM NEW.tenant_id
                      OR t_security IS DISTINCT FROM NEW.security_id
                      OR t_type IS DISTINCT FROM NEW.fact_type) THEN
            RAISE EXCEPTION 'platform_value_decision: the candidate % is not of the same company and value type',
                NEW.candidate_id;
        END IF;  -- an unknown id: the foreign key refuses it
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_shape ON platform_value_decision;
CREATE TRIGGER check_shape BEFORE INSERT ON platform_value_decision
    FOR EACH ROW EXECUTE FUNCTION platform_value_decision_check_shape();

DROP TRIGGER IF EXISTS no_update ON platform_value_decision;
CREATE TRIGGER no_update BEFORE UPDATE ON platform_value_decision
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
