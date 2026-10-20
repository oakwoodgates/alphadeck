-- Alpha Deck — R8-A refinement (operator-approved 2026-10-18): the `field_change` disposition.
--
-- A 4/A whose Table I is identical to the original's but that changes a field a CALL signal reads on a signal
-- row (the reporting owner's role or name, the 10b5-1 flag — seniority reads the role) is no longer
-- "clerical / no change": it routes to the operator as `field_change` (FRHC 0000929638-26-003399, CFO ->
-- "Special Advisor to CEO"; CRCW 0001641172-25-016743). The link table's disposition CHECK (0050) gains the
-- value. A separate file rather than an edit of 0050 so a database that already applied 0050 (a test DB, a
-- preview) is upgraded in place; the runner records each file by NAME, so the number cannot collide.
--
-- Idempotent: the constraint is dropped if present and re-added with the full value set.

ALTER TABLE insider_amendment_link DROP CONSTRAINT IF EXISTS insider_amendment_link_disposition_check;
ALTER TABLE insider_amendment_link ADD CONSTRAINT insider_amendment_link_disposition_check
    CHECK (disposition IN ('no_rows', 'clerical', 'field_change', 'unlinked', 'correction', 'void',
                           'duplicates', 'partial', 'consolidation', 'addition', 'restatement', 'ambiguous'));
