-- Alpha Deck — Massive.com's split LABEL on the split list (platform fixes W6-1, 2026-11-25): the split reader moved to
-- ``/stocks/v1/splits``, whose rows carry ``adjustment_type`` (forward_split | reverse_split | stock_dividend).
--
-- ``polygon_adjustment_type text NULL``: the label of the Massive.com row a version carries, as EVIDENCE beside
-- ``polygon_split_from`` / ``polygon_split_to``. It is NOT content (``repositories/split_repo.py`` EVIDENCE_COLUMNS,
-- outside CONTENT_COLUMNS): a re-run whose only difference is the label appends nothing, so the stored splits are
-- never re-versioned for it. Old rows keep NULL; a new row, and any row re-versioned for another reason, carries the
-- label; a steward's confirm, reject, revoke and restore carry it on.
--
-- No CHECK: a word Massive.com starts serving must never fail an insert (the reader maps an unknown word to NULL).
-- No row changes: a version is still never UPDATEd (0052). Additive + idempotent.

ALTER TABLE security_split ADD COLUMN IF NOT EXISTS polygon_adjustment_type text NULL;
