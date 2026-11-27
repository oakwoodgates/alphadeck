-- Alpha Deck — the steward's DISMISS of a split filing the nightly could not place (framework completion F1-2,
-- 2026-11-27): an APPEND-ONLY log of the steward saying an unplaced split filing (`pipeline/split_unplaced.py`: the
-- nightly's `split_detect.unplaced` rows, or a recorded long-window check's) is not a split to list, and of its Undo.
--
-- WHY. The nightly re-reads every split filing filed in the last 60 days, so an unplaced filing is listed again
-- every night for up to 60 nights; a dismissal kept only in a run artifact would be gone the next night. A dismissal
-- takes the filing off the default list, never off the panel: a "dismissed" list keeps it (WB#2, hide never vanish),
-- and its Undo puts it back (WB#1). The shape is 0068's (`split_jump_dismissal`) with `accession` for `step_date`.
--
-- WHAT IT IS NOT. A dismissal is DISPLAY ONLY: never a call input. The split detector, the split list and the
-- split-night hold never read this table; a dismissal neither lists a split nor rejects one (0052 is untouched).
--
--   split_filing_dismissal  one row per action. `action` = 'dismiss' (the steward: "not a split to list") or 'undo'
--                           (naming the dismissal it takes back: `undoes`). A filing is keyed by (security_id,
--                           accession): one SEC filing about one company. `detail` keeps the evidence the row
--                           showed (form, filed, the reader's reason). `author` is the operator ('operator:*'): the
--                           system never dismisses. A dismissal STANDS while no undo names it.
--
-- `recorded_at` is the honest instant of the click (stamped now, never backdated). Every row carries `tenant_id`
-- (DATA scope, `domain/scopes.py`); ON DELETE CASCADE on security_id (a master row that goes takes its log with it).
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE). Depends only on 0001 (tenant,
-- security_master, raise_no_update()).

CREATE TABLE IF NOT EXISTS split_filing_dismissal (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    security_id uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    accession   text        NOT NULL CHECK (accession ~ '^\d{10}-\d{2}-\d{6}$'),
    action      text        NOT NULL CHECK (action IN ('dismiss', 'undo')),
    undoes      uuid        NULL REFERENCES split_filing_dismissal (id),
    reason      text        NOT NULL CHECK (btrim(reason) <> ''),
    author      text        NOT NULL CHECK (author LIKE 'operator:%'),
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT split_filing_dismissal_undo_pairing CHECK ((action = 'undo') = (undoes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_split_filing_dismissal_filing
    ON split_filing_dismissal (tenant_id, security_id, accession, recorded_at);
-- at most ONE undo per dismissal: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_split_filing_dismissal_one_undo
    ON split_filing_dismissal (undoes) WHERE undoes IS NOT NULL;

-- Append-only, the same real DB guard as `calls` / `split_jump_dismissal` (raise_no_update() from 0001)
DROP TRIGGER IF EXISTS no_update ON split_filing_dismissal;
CREATE TRIGGER no_update BEFORE UPDATE ON split_filing_dismissal
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
