-- Alpha Deck — the operator's DISMISS of an "is this real?" card (operator ask, 2026-10-24): an APPEND-ONLY log of
-- the operator saying a released, unlisted split-shaped price jump (`pipeline/split_jumps.py`, the Admin Splits
-- panel) was a REAL MOVE, and of its Undo.
--
-- WHY. A split-shaped jump the split-night hold released with no split listed is shown to the operator as "is this
-- real?" for 30 days (AXG). Until now the card had no button: a real move the operator already recognized kept
-- standing on the panel (and in the nightly quiet line) for a month. A dismissal takes it off the default list —
-- never off the panel: a "dismissed" filter lists it (WB#2, hide never vanish), and its Undo puts it back (WB#1).
--
-- WHAT IT IS NOT. A dismissal is DISPLAY ONLY: never a call input. The split-night hold (`signals/split_hold.py`,
-- `domain/split_hold.py`) never reads this table — it holds and releases a jump exactly as before; a dismissal
-- neither lists a split nor rejects one (the split list, 0052, is untouched).
--
--   split_jump_dismissal  one row per action. `action` = 'dismiss' (the operator: "this was a real move") or
--                         'undo' (naming the dismissal it takes back — `undoes`). A jump is keyed by
--                         (security_id, step_date): the session the step landed on. `detail` keeps the evidence
--                         the card showed (the two bars, the step). `author` is the operator ('operator:*'):
--                         the system never dismisses. A dismissal STANDS while no undo names it.
--
-- `recorded_at` is the honest instant of the click (stamped now, never backdated). Every row carries `tenant_id`;
-- ON DELETE CASCADE on security_id (a master row that goes takes its log with it). Additive + idempotent
-- (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE). Depends only on 0001 (tenant, security_master,
-- raise_no_update()).

CREATE TABLE IF NOT EXISTS split_jump_dismissal (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    security_id uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    step_date   date        NOT NULL,
    action      text        NOT NULL CHECK (action IN ('dismiss', 'undo')),
    undoes      uuid        NULL REFERENCES split_jump_dismissal (id),
    reason      text        NOT NULL CHECK (btrim(reason) <> ''),
    author      text        NOT NULL CHECK (author LIKE 'operator:%'),
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT split_jump_dismissal_undo_pairing CHECK ((action = 'undo') = (undoes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_split_jump_dismissal_jump
    ON split_jump_dismissal (tenant_id, security_id, step_date, recorded_at);
-- at most ONE undo per dismissal: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_split_jump_dismissal_one_undo
    ON split_jump_dismissal (undoes) WHERE undoes IS NOT NULL;

-- Append-only, the same real DB guard as `calls` / `security_ticker_history` (raise_no_update() from 0001)
DROP TRIGGER IF EXISTS no_update ON split_jump_dismissal;
CREATE TRIGGER no_update BEFORE UPDATE ON split_jump_dismissal
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
