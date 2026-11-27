-- Alpha Deck — the split list's ADD verb (framework completion F2-1, 2026-11-27): the steward may add a split that no
-- source lists, from an SEC filing the detector could not place (`pipeline/split_unplaced.py`) or from a split-shaped
-- jump the split-night hold released with no split listed (`pipeline/split_jumps.py`), citing the document that
-- states its ratio and first session (D3's cited values, 0215).
--
-- `action` (0061) gains `add`: the version a steward's add appends (author `operator`, value_source `steward`, a
-- cited_url always). It is the first version of a new logical split; the existing verbs act on it unchanged (revoke
-- or reject undoes it, restore returns it). No column is added and no row changes: a version is still never UPDATEd
-- (0052). Nothing in the call path reads `action`.
--
-- Idempotent: the constraint is dropped if present and re-added with the wider set (the 0061 pattern).

ALTER TABLE security_split DROP CONSTRAINT IF EXISTS security_split_action_check;
ALTER TABLE security_split ADD CONSTRAINT security_split_action_check
    CHECK (action IN ('backfill', 'detect', 'confirm', 'reject', 'revoke', 'restore', 'add'));
