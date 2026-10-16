-- Alpha Deck — call_annotation: an APPEND-ONLY side table of judgments ABOUT recorded call-of-record rows
-- (D3b), so the Scoreboard can set a row aside REVERSIBLY without touching the immutable `calls` log.
--
-- WHY. D3a (#390) dates a fundamentals fact by its FIRST knowability: a 10-Q that re-reports last year's
-- quarter as its prior-year comparative is no longer fresh news. Every honest row the cron wrote BEFORE D3a
-- was assembled under the old dating, and some of them are PHANTOM ARMS — an arm that rested only on a
-- year-old revenue inflection re-dated to the later filing (MEASURED 2026-10-16: tonight's AI Memory &
-- Storage and Nuclear Buildout arms, and more thesis-nights historically). The operator decided (Q2,
-- 2026-10-16): annotate them, exclude them from scoring, and keep the decision REVERSIBLE — they go back
-- and forth on whether those nights should count.
--
-- WHY A SIDE TABLE, NOT A CALLS COLUMN. `calls` carries the `no_update` trigger ("never rewrite a recorded
-- call"). 0042 disabled it for ONE statement because `reconstructed` is a FACT about how a row was made (a
-- backfill wrote it) that was always true. An annotation is a JUDGMENT made later, by a named rule, that
-- the operator may revoke — so it must live beside the row, append-only, with its own history: who said
-- what, when, under which code, on what evidence. Nothing in `calls` changes, ever.
--
--   action   — 'exclude' : the row is a phantom arm under the named rule; the Scoreboard's record read drops
--                          it (when the policy says so) BEFORE the per-as-of dedup, exactly like
--                          `calls.reconstructed` (`call_annotations_repo.excluded_clause`).
--              'note'    : the arm stands but the rule changed its composition (verdict / headline / armed
--                          set); stays SCORED, shown only as a quiet caption in the episode drawer.
--              'revoke'  : withdraws ONE earlier exclude/note (`revokes` names it). Revoking is appending.
--   reason   — the plain-English why (e.g. 'stale_comparative_revenue'), required on every row.
--   rule     — the code identity that made the judgment, e.g. 'd3a-first-knowability@<sha>'.
--   detail   — the evidence (jsonb): recorded vs re-assembled state / verdict / headline / armed set, and
--              the stale triggers (security, period_end, cited filing vs first filing).
--   revokes  — NULL for exclude/note; the annotation a 'revoke' withdraws. A CHECK pins the pairing.
--
-- ACTIVE = an exclude/note row that no revoke row references. A revoke can target only an exclude/note of
-- the SAME call (the trigger below), and at most one revoke per annotation (the partial unique index) — so
-- "active" is a single NOT EXISTS, never a recursive walk.
--
-- `tenant_id` / `thesis_id` / `asof` are COPIED from the `calls` row by the one writer
-- (`repositories/call_annotations_repo.py` INSERTs ... SELECT FROM calls), so they cannot disagree with it.
-- ON DELETE CASCADE mirrors `calls.thesis_id`: deleting a thesis (or a reconstructed row via the repair CLI)
-- takes its annotations with it; nothing else deletes.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; trigger + function DROP-then-CREATE / OR REPLACE).

CREATE TABLE IF NOT EXISTS call_annotation (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    call_id     uuid        NOT NULL REFERENCES calls (id) ON DELETE CASCADE,
    thesis_id   uuid        NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    asof        date        NOT NULL,
    action      text        NOT NULL CHECK (action IN ('exclude', 'note', 'revoke')),
    reason      text        NOT NULL,
    rule        text        NOT NULL,
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    revokes     uuid        NULL REFERENCES call_annotation (id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT call_annotation_revoke_pairing CHECK ((action = 'revoke') = (revokes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_call_annotation_call ON call_annotation (call_id);
CREATE INDEX IF NOT EXISTS ix_call_annotation_thesis_asof ON call_annotation (thesis_id, asof);
-- at most ONE revoke per annotation: "active" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_call_annotation_one_revoke
    ON call_annotation (revokes) WHERE revokes IS NOT NULL;

-- A revoke withdraws an exclude/note of the SAME call — never another revoke (no un-revoke chains), never
-- a different call's annotation. Structural, so a hand-typed INSERT cannot build a shape `active` misreads.
CREATE OR REPLACE FUNCTION call_annotation_check_revoke() RETURNS trigger AS $$
DECLARE
    target_call   uuid;
    target_action text;
BEGIN
    IF NEW.revokes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT call_id, action INTO target_call, target_action FROM call_annotation WHERE id = NEW.revokes;
    IF target_action = 'revoke' THEN
        RAISE EXCEPTION 'call_annotation: a revoke cannot revoke another revoke (%)', NEW.revokes;
    END IF;
    IF target_call IS DISTINCT FROM NEW.call_id THEN
        RAISE EXCEPTION 'call_annotation: a revoke must name an annotation of the same call (% vs %)',
            NEW.call_id, target_call;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_revoke ON call_annotation;
CREATE TRIGGER check_revoke BEFORE INSERT ON call_annotation
    FOR EACH ROW EXECUTE FUNCTION call_annotation_check_revoke();

-- Append-only, the same real DB guard as `calls` and every fact table (raise_no_update() from 0001):
-- reversing a judgment is a new 'revoke' row, never an edit.
DROP TRIGGER IF EXISTS no_update ON call_annotation;
CREATE TRIGGER no_update BEFORE UPDATE ON call_annotation
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
