-- 0213 — the theme conviction folds into the card's catalyst list (V2 W6c; the W6 plan, Q5; operator decisions
-- N1 (a), N3 (a), N4 (a)).
--
-- WHAT. Since W6c the call reads a card's theme conviction from its catalyst list: a counting entry that names
-- no member IS the theme conviction (signals/thesis_catalyst.py, docs/CALL_LOGIC.md §3c), and nothing reads
-- fact_theme_conviction any more. This migration converts every fact_theme_conviction version (one row on the
-- fork and on prod: the "Small-scale nuclear (system)" card) into the new store, so the call it feeds does not
-- move by one byte:
--
--   * fact_thesis_catalyst: one row per theme version, in the CONVERTED shape (N4 (a)): entry_id =
--     md5(thesis_id::text || source_ref)::uuid (deterministic: pipeline/seed.py names the same id), counts = true,
--     security_ids = '{}' (the whole card), the same grade, label and valid_from, citation = source_ref,
--     relevant_until = horizon_end, source kept ('ratified'), entered_at / entered_by NULL, valid_to kept,
--     recorded_at COPIED from the source row (a move, the 0078 precedent: a record-clock read at any instant sees
--     what it saw), supersedes mapped onto the converted rows. With no entry time its public instant is 06:00 ET
--     of its stated date (db/public_clock.py, the `dated` kind), exactly the old `date` kind on valid_from: the
--     ONE named exception to R5 (an operator entry counts from the night it was saved).
--   * catalyst: one calendar row per converted entry if absent (id = entry_id, the newest version's label,
--     kind 'theme', no date, ordinal after the card's last row). Undated, so no card's catalyst surface moves;
--     present, so the operator can see the entry on the list and switch it off (the list writer refuses to drop
--     the row of a counting entry).
--
-- fact_theme_conviction itself is KEPT, unread, until a drop migration after go-live (the W6 plan, "After
-- go-live"). A converted row whose source is not 'ratified', or whose grade is not 'flip', fails the new table's
-- CHECKs and stops this migration loudly (none exists: the fork's one row is ratified + flip).
--
-- Idempotent: the converted rows carry deterministic ids (md5('0213:' || the source row's id)) and conflict-skip;
-- the calendar row is inserted only when its id is absent. A re-run writes nothing.
--
-- The inverse, by hand, after a backup:
--   DELETE FROM catalyst c WHERE EXISTS (SELECT 1 FROM fact_thesis_catalyst f
--       WHERE f.entry_id = c.id AND f.source = 'ratified' AND f.entered_at IS NULL);
--   DELETE FROM fact_thesis_catalyst WHERE source = 'ratified' AND entered_at IS NULL;  -- no_update guards UPDATE only
--   DELETE FROM schema_migrations WHERE filename = '0213_v2_theme_conviction_fold.sql';
-- (and deploy code that reads fact_theme_conviction again)

INSERT INTO fact_thesis_catalyst
       (id, tenant_id, thesis_id, entry_id, counts, security_ids, grade, label, citation, relevant_until,
        source, entered_at, entered_by, valid_from, valid_to, recorded_at, supersedes)
SELECT md5('0213:' || t.id::text)::uuid,
       t.tenant_id,
       t.thesis_id,
       md5(t.thesis_id::text || t.source_ref)::uuid,
       true,
       '{}'::uuid[],
       t.grade,
       t.label,
       t.source_ref,
       t.horizon_end,
       t.source,
       NULL,
       NULL,
       t.valid_from,
       t.valid_to,
       t.recorded_at,
       CASE WHEN t.supersedes IS NULL THEN NULL ELSE md5('0213:' || t.supersedes::text)::uuid END
  FROM fact_theme_conviction t
 ORDER BY t.recorded_at, t.id
    ON CONFLICT DO NOTHING;

INSERT INTO catalyst (id, tenant_id, thesis_id, label, kind, when_date, when_label, ordinal)
SELECT e.entry_id,
       e.tenant_id,
       e.thesis_id,
       e.label,
       'theme',
       NULL,
       NULL,
       COALESCE((SELECT max(c.ordinal) FROM catalyst c WHERE c.thesis_id = e.thesis_id), -1) + e.n
  FROM (SELECT DISTINCT ON (f.entry_id) f.entry_id, f.tenant_id, f.thesis_id, f.label,
               dense_rank() OVER (PARTITION BY f.thesis_id ORDER BY f.entry_id) AS n
          FROM fact_thesis_catalyst f
         WHERE f.source = 'ratified' AND f.entered_at IS NULL
         ORDER BY f.entry_id, f.recorded_at DESC, f.id DESC) e
 WHERE NOT EXISTS (SELECT 1 FROM catalyst c WHERE c.id = e.entry_id);
