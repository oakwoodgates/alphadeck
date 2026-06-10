-- Alpha Deck — Workbench value-chain structure (Phase 2, Slice 1).
--
-- The value-chain decomposition persists as STRUCTURED data on the thesis spine: a `segment` label on
-- each basket_member edge (which link the name sits in) + `authored_by` (who placed it — the authorship
-- seam), plus the ordered `segments` list on the thesis (the links of the chain).
--
-- This is OPERATIONAL, editable config (like the rest of 0003) — NOT a bitemporal fact: no
-- valid_from/recorded_at axes, no append-only no_update trigger. The SCORES are never stored; they
-- re-derive on read from the bitemporal facts (Option B), so a chain reopened months later shows current
-- numbers. Chain-evolution HISTORY is a taxonomy-era addition (versioning is added when that consumer
-- lands); the MVP persists current structure only. Additive + idempotent (ALTER ... IF NOT EXISTS).

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS segment text;            -- the value-chain link this name sits in
ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS authored_by text NOT NULL DEFAULT 'operator_set';  -- authorship seam
ALTER TABLE thesis        ADD COLUMN IF NOT EXISTS segments jsonb NOT NULL DEFAULT '[]'::jsonb;        -- the ordered chain (segment list)
