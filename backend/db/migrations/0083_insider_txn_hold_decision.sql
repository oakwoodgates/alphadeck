-- 0083 — insider_txn_hold_decision: a steward's decision about ONE held insider row (insider_txn_hold, 0082).
--
-- WHY. Since rule 2026-11-insider-rows-dated-after-filing the insider read (db.insider_resolution ->
-- domain.insider_holds.resolve_holds, both engines) counts a held Form 4 row from the night its filing became
-- public when the filing was ON TIME (its period of report within CallConfig.insider_hold_on_time_days of the
-- acceptance). A held row of a LATE filing waits for a steward. This table is where that decision lives; the
-- read honors the newest standing one. The writer (the steward API) is the next step: nothing in this change
-- writes a row here, the read only READS them.
--
-- Modeled on insider_amendment_decision (0050):
--   * `action` — 'approve' (count the row, dated at `ratified_date`), 'dismiss' (do not count it), 'revoke'
--     (the Undo of one approve / dismiss, naming it in `revokes`; the state before it comes back).
--   * `ratified_date` — the trade date the steward ratified, NOT NULL exactly when action = 'approve'.
--   * `reason` / `basis` — why, and who (the author: 'steward:approve' / 'steward:dismiss' / 'steward:revoke').
--   * `detail` — the evidence the steward read (free JSON, display only).
--   * `form` — the held row's form ('4' or '4/A'); the read applies decisions of form '4' holds only.
--   * `recorded_at` — when the steward decided. On the PUBLIC clock an operator decision counts from when it
--     was made (db.public_clock.aux_public_sql), the existing rule for operator decisions.
-- A decision names its hold by (security_id, accession, txn_seq, form) — the hold's natural key without the
-- tenant (the same tenant column rides beside it).
--
-- APPEND-ONLY (`no_update`). At most ONE revoke per decision; a revoke never revokes a revoke, and names a
-- decision of the SAME hold (a trigger, so a hand-typed INSERT cannot build a shape the read misreads).
--
-- DATA scope (domain/scopes.py), like insider_txn_hold.
-- Depends on 0001 (tenant, security_master, raise_no_update()).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE insider_txn_hold_decision;
--   DROP FUNCTION insider_txn_hold_decision_check_revoke();
--   DELETE FROM schema_migrations WHERE filename = '0083_insider_txn_hold_decision.sql';

CREATE TABLE IF NOT EXISTS insider_txn_hold_decision (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL REFERENCES tenant (id),
    security_id    uuid        NOT NULL REFERENCES security_master (id),
    accession      text        NOT NULL,
    txn_seq        integer     NOT NULL,
    form           text        NOT NULL CHECK (form IN ('4', '4/A')),
    action         text        NOT NULL CHECK (action IN ('approve', 'dismiss', 'revoke')),
    ratified_date  date        NULL,
    reason         text        NOT NULL,
    basis          text        NOT NULL,
    detail         jsonb       NULL,
    revokes        uuid        NULL REFERENCES insider_txn_hold_decision (id),
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT insider_txn_hold_decision_approve_date
        CHECK ((action = 'approve') = (ratified_date IS NOT NULL)),
    CONSTRAINT insider_txn_hold_decision_revoke_pairing
        CHECK ((action = 'revoke') = (revokes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_insider_txn_hold_decision_security
    ON insider_txn_hold_decision (tenant_id, security_id, recorded_at);
-- at most ONE revoke per decision: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_insider_txn_hold_decision_one_revoke
    ON insider_txn_hold_decision (revokes) WHERE revokes IS NOT NULL;

CREATE OR REPLACE FUNCTION insider_txn_hold_decision_check_revoke() RETURNS trigger AS $$
DECLARE
    target_tenant    uuid;
    target_security  uuid;
    target_accession text;
    target_seq       integer;
    target_form      text;
    target_action    text;
BEGIN
    IF NEW.revokes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT tenant_id, security_id, accession, txn_seq, form, action
      INTO target_tenant, target_security, target_accession, target_seq, target_form, target_action
      FROM insider_txn_hold_decision WHERE id = NEW.revokes;
    IF target_action = 'revoke' THEN
        RAISE EXCEPTION 'insider_txn_hold_decision: a revoke cannot revoke another revoke (%)', NEW.revokes;
    END IF;
    IF target_tenant IS DISTINCT FROM NEW.tenant_id
       OR target_security IS DISTINCT FROM NEW.security_id
       OR target_accession IS DISTINCT FROM NEW.accession
       OR target_seq IS DISTINCT FROM NEW.txn_seq
       OR target_form IS DISTINCT FROM NEW.form THEN
        RAISE EXCEPTION 'insider_txn_hold_decision: a revoke must name a decision of the same held row (%)',
            NEW.revokes;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_revoke ON insider_txn_hold_decision;
CREATE TRIGGER check_revoke BEFORE INSERT ON insider_txn_hold_decision
    FOR EACH ROW EXECUTE FUNCTION insider_txn_hold_decision_check_revoke();

DROP TRIGGER IF EXISTS no_update ON insider_txn_hold_decision;
CREATE TRIGGER no_update BEFORE UPDATE ON insider_txn_hold_decision
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
