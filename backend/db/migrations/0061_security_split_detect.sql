-- Alpha Deck — the split list's NIGHTLY DETECTION (split plan S4, 2026-10-22): two CHECK constraints of
-- ``security_split`` (0052) widened for the versions the nightly writes. No column is added and no row changes.
--
-- 1. ``action`` gains ``detect``: a version the nightly detector (``pipeline/detect_splits.py``, author
--    ``system:detect_splits``) appended — a new split found in a fresh SEC filing / Polygon row, or an open one
--    (needs review / announced) whose evidence changed. ``backfill`` stays the S1 backfill's verb.
-- 2. ``confirmed_via`` gains the two TAPE pairings: ``auto:sec+tape`` and ``auto:polygon+tape`` — a split ONE
--    documentary source states (ratio + first session) whose first session the stored price tape confirms: the
--    close steps by about the ratio exactly on that session with the volume moving the other way (the tolerance
--    is MEASURED, ``domain/splits.py`` TAPE_*). The tape never confirms a split on its own and never arbitrates
--    between two sources that disagree. ``auto:sec+polygon`` (the two documentary sources agreeing exactly) and
--    ``operator`` are unchanged.
--
-- Idempotent: each constraint is dropped if present and re-added with the wider set.

ALTER TABLE security_split DROP CONSTRAINT IF EXISTS security_split_action_check;
ALTER TABLE security_split ADD CONSTRAINT security_split_action_check
    CHECK (action IN ('backfill', 'detect', 'confirm', 'reject', 'revoke', 'restore'));

ALTER TABLE security_split DROP CONSTRAINT IF EXISTS security_split_confirmed_via_check;
ALTER TABLE security_split ADD CONSTRAINT security_split_confirmed_via_check
    CHECK (confirmed_via IN ('auto:sec+polygon', 'auto:sec+tape', 'auto:polygon+tape', 'operator'));
