-- Alpha Deck — the per-member thesis-fit prose (Phase 2, Slice 5c).
--
-- `thesis_fit` holds WHY a name sits in its value-chain segment — the thesis-fit reasoning the
-- narrative->chain drafter (S5) drafts (system_drafted), then the operator edits (operator_edited) or
-- hand-authors (operator_set). Named for WHAT it holds, not its origin: it outlives the draft (authored_by
-- records WHO wrote it). Kept DISTINCT from `detail` (the live board/cockpit "met" cell, e.g. "mkt $1.2B")
-- and from a segment's own `descriptor` — the two DD layers (stored reference vs drafted reasoning) stay
-- separate. OPERATIONAL like the rest of the chain (no bitemporal axes). Additive + idempotent.

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS thesis_fit text;  -- the thesis-fit reasoning ("why here")
