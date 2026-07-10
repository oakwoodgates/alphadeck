-- 0019 — operator_decision: the decision-capture log (gate-1 ratified 2026-07-10).
--
-- The operator's ACTUAL decisions — take / pass / close (+ void, the reversibility inverse) — as an
-- APPEND-ONLY event log. One table, three rooms: the Scoreboard's missing column (platform calls vs
-- OPERATOR DECISIONS vs follow-blindly — capture must start long before the Scoreboard exists, it can
-- never be backfilled), the position feed that makes the Managing state reachable (assembler._state
-- already emits it; nothing could set a position), and the gate's override record (a take against a
-- not-yet verdict, logged — never blocked; invariant #5: advisory only, this LOGS fills made elsewhere).
--
-- Deliberately NOT fact_* — an operator event log (the `calls` family), never a scoring fact: it must
-- never enter the PointInTimeData scoring reads. Two time axes ride every row (the #1 discipline):
-- decision_date = VALID time (when the fill/decision happened), recorded_at = TRANSACTION time (when it
-- was logged) — the derived-position read (repositories/decisions_repo) is as-of BOTH, so a replayed
-- past call never sees a later-logged fill.
--
-- The thesis.position_* columns become a SEED-ERA FALLBACK: any decision rows make this log
-- authoritative (see effective_position). Rationale: the promote upsert overwrites those columns from a
-- request that never carries them (a narrative edit would silently close a position stored there), and
-- a position open/close is temporal — never UPDATE-in-place; append + derive, the calls-log pattern.
--
-- A row is never UPDATEd (trigger below) or DELETEd; a mistake is corrected by appending
-- action='void' with voids=<the mistaken row's id> — the workbench reversibility principle applied
-- to the log (the inverse is visible, nothing is destroyed).
CREATE TABLE IF NOT EXISTS operator_decision (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq           bigint GENERATED ALWAYS AS IDENTITY,  -- total insertion order (now() ties within a txn)
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    thesis_id     uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    security_id   uuid REFERENCES security_master (id),  -- the name acted on; NULL = thesis-level (a pass)
    action        text NOT NULL CHECK (action IN ('take', 'pass', 'close', 'void')),
    decision_date date NOT NULL,          -- VALID time: the fill/decision date the operator states
    shares        numeric,                -- take/close detail (optional; dollars derivable from bars)
    price         numeric,                -- the fill price (optional; EOD can approximate later)
    reason        text,                   -- pass rationale / any note (optional, encouraged)
    voids         uuid REFERENCES operator_decision (id),  -- action='void' points at the mistaken row
    call_state    text,                   -- the platform's stance when logged (display denormalization;
    call_verdict  text,                   --   attribution re-derives from the calls-log join)
    recorded_at   timestamptz NOT NULL DEFAULT now()      -- TRANSACTION time
);

CREATE INDEX IF NOT EXISTS ix_operator_decision_thesis
    ON operator_decision (tenant_id, thesis_id, recorded_at);

-- append-only, enforced (reuses raise_no_update() from 0001) — same guarantee as the calls log
DROP TRIGGER IF EXISTS no_update ON operator_decision;
CREATE TRIGGER no_update BEFORE UPDATE ON operator_decision
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
