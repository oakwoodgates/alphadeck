-- Alpha Deck — the security CIK change log (T7): an APPEND-ONLY side table recording the operator's
-- ratified re-point of a basket name's `security_master.cik` to its SUCCESSOR issuer, so every re-point has
-- an EXACT, auditable inverse (WB#1).
--
-- WHY. A company can re-register under a NEW CIK and keep its ticker (a redomicile / holding-company
-- reorganization — the successor issuer of Exchange Act Rule 12g-3 files an 8-K12B, the predecessor a
-- 15-12G). Every filing leg reads the STORED CIK, so the name's Form 4s, 8-Ks and 13D/Gs silently stop
-- (MEASURED on prod 2026-10-17: UROY 0001711570 -> 0002143673, REAX 0001862461 -> 0002136387). The nightly
-- detector (pipeline/successor_cik.py) RECOMMENDS the successor; nothing is auto-applied (#3). The
-- operator's Adopt re-points the CIK IN PLACE (the row id stays, so the tape, every fact, the basket links
-- and the sign-off are kept) and refreshes the identity fields that depend on the CIK from the successor's
-- submissions (the enrich_identity derivation).
--
-- `security_master` is UPDATE-in-place identity with NO history of its own (0032 — nothing reads the master
-- as-of), so this log carries the master's PRIOR and NEW values of EVERY column the change touched, as one
-- JSON object each (`prior_values` / `new_values`: cik, name, the submissions-sourced identity columns, enriched_source /
-- enriched_at). An undo writes the prior object back verbatim — the master returns BYTE-FOR-BYTE — and
-- leaves the history behind. Reversing is APPENDING an `undo` row. `prior_cik` / `new_cik` repeat the
-- headline values as columns so the log reads at a glance.
--
-- `basis` names the author ('operator:adopt-cik' / 'operator:undo'): auth is deferred project-wide, so "the
-- operator" is the only author today. `detail` carries the evidence the Adopt rested on (the pass, the
-- succession / deregistration filings, the identity applied).
--
-- `tenant_id` is COPIED from the master row by the one writer (repositories/cik_change_repo.py), so it
-- cannot disagree with it. ON DELETE CASCADE on security_id: a master row that goes takes its history with
-- it; nothing else deletes.
--
-- Numbered 0049 with 0047 / 0048 reserved by other in-flight changes: the runner (db/migrate.py) applies
-- every unapplied file in filename order and records each by NAME, so a gap is harmless and a lower
-- number landing later still applies. This table depends only on 0001 (tenant, security_master,
-- raise_no_update()).
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).

CREATE TABLE IF NOT EXISTS security_cik_change (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    security_id  uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    action       text        NOT NULL CHECK (action IN ('adopt', 'undo')),
    prior_cik    text        NULL,
    new_cik      text        NULL,
    -- the master's values BEFORE and AFTER this row's write, every column it touched — exactly what an undo
    -- restores (`prior_values`) and checks still stands (`new_values`)
    prior_values jsonb       NOT NULL,
    new_values   jsonb       NOT NULL,
    basis        text        NOT NULL,                  -- the author: operator:adopt-cik / operator:undo
    detail       jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- the evidence: the pass, the filings, the identity
    undoes       uuid        NULL REFERENCES security_cik_change (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT security_cik_change_undo_pairing CHECK ((action = 'undo') = (undoes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_security_cik_change_security ON security_cik_change (security_id, created_at);
-- at most ONE undo per change: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_security_cik_change_one_undo
    ON security_cik_change (undoes) WHERE undoes IS NOT NULL;

-- An undo reverses an adopt of the SAME security — never another undo (no redo chains: re-adopting is a new
-- adopt), never a different security's change. Structural, so a hand-typed INSERT cannot build a shape the
-- "standing change" read misreads.
CREATE OR REPLACE FUNCTION security_cik_change_check_undo() RETURNS trigger AS $$
DECLARE
    target_security uuid;
    target_action   text;
BEGIN
    IF NEW.undoes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT security_id, action INTO target_security, target_action
      FROM security_cik_change WHERE id = NEW.undoes;
    IF target_action = 'undo' THEN
        RAISE EXCEPTION 'security_cik_change: an undo cannot undo another undo (%)', NEW.undoes;
    END IF;
    IF target_security IS DISTINCT FROM NEW.security_id THEN
        RAISE EXCEPTION 'security_cik_change: an undo must name a change of the same security (% vs %)',
            NEW.security_id, target_security;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_undo ON security_cik_change;
CREATE TRIGGER check_undo BEFORE INSERT ON security_cik_change
    FOR EACH ROW EXECUTE FUNCTION security_cik_change_check_undo();

-- Append-only, the same real DB guard as `calls` / `price_symbol_change` (raise_no_update() from 0001)
DROP TRIGGER IF EXISTS no_update ON security_cik_change;
CREATE TRIGGER no_update BEFORE UPDATE ON security_cik_change
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
