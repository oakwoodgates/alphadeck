-- Alpha Deck — the split list's STEWARD-CITED VALUES (platform fixes W2-1, decision D3, 2026-11-25): the steward may
-- confirm a needs-review split with values neither source states (POM: an ADR termination, 10 ADSs -> 3 ordinary
-- shares; the 6-K's 10 -> 1 is the ordinary shares' consolidation), citing the document that states them.
--
-- 1. ``value_source`` (0052) gains ``steward``: the row carries the steward's own ratio and first session. The other
--    three (sec | polygon | both) are unchanged; ``confirmed_via`` stays ``operator`` (who confirmed, 0061).
-- 2. ``cited_url text NULL``: the https URL of the document the steward cites (any https URL; checked by the route,
--    ``app/routers/admin_splits.py``). A steward row always carries one (the CHECK below); a system row carries NULL.
-- 3. ``tape_override boolean NULL``: on a steward row, TRUE when the steward confirmed although the stored tape's
--    step at the first session sits outside ``domain.splits.TAPE_TOLERANCE`` of the entered factor (or no bar could
--    be read), FALSE when the tape agreed; NULL on a system row.
--
-- Both columns ride every later version of the split as content (``repositories/split_repo.py`` CONTENT_COLUMNS),
-- so a revoke and its restore keep the citation. No row changes: a version is still never UPDATEd (0052).
--
-- Additive + idempotent: the value_source check is replaced by its catalog name (the inline 0052 check), the
-- columns are added only when absent, and the steward check is dropped if present and re-added.

ALTER TABLE security_split DROP CONSTRAINT IF EXISTS security_split_value_source_check;
ALTER TABLE security_split ADD CONSTRAINT security_split_value_source_check
    CHECK (value_source IN ('sec', 'polygon', 'both', 'steward'));

ALTER TABLE security_split ADD COLUMN IF NOT EXISTS cited_url text NULL;
ALTER TABLE security_split ADD COLUMN IF NOT EXISTS tape_override boolean NULL;

ALTER TABLE security_split DROP CONSTRAINT IF EXISTS security_split_steward_cites;
ALTER TABLE security_split ADD CONSTRAINT security_split_steward_cites
    CHECK (value_source <> 'steward' OR cited_url IS NOT NULL);
