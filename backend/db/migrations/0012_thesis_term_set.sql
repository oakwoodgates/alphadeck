-- Alpha Deck — the thesis's persisted, tiered discovery term set (Phase 2, discovery precision).
--
-- `term_set` holds the SIGNAL/BROAD keyword tiers the EDGAR precision filter reads — the input that decides
-- which EFTS hits PLACE a company. It moves the "is this term discriminating?" decision OFF the LLM and onto a
-- durable, thesis-OWNED object: a deterministic guard sets the default tiers (the `/terms` producer), and a
-- future operator-edit UI overrides them on the SAME object (each entry carries authored_by/source). Discovery
-- READS this set; it is NOT regenerated inline per draft and discarded.
--
-- Shape mirrors `segments`: a JSONB list of small structured objects, OPERATIONAL config (no bitemporal axes),
-- thesis-owned. CRUCIALLY it is written ONLY by the narrow `thesis_repo.set_term_set` (UPDATE … term_set …) —
-- the full `upsert` deliberately never names this column, so a `promote` that omits it CANNOT blank it (a
-- structural wipe-guard). Additive + idempotent.

ALTER TABLE thesis ADD COLUMN IF NOT EXISTS term_set jsonb NOT NULL DEFAULT '[]'::jsonb;  -- [{term, tier, authored_by, source}]
