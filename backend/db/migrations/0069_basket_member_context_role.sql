-- Alpha Deck — the per-membership CONTEXT ROLE (Context roles, Phase 1: the role on the membership only).
--
-- `context_role` holds what part a company plays on THIS card — the operator's own words for it, one of
-- six: payer · counter_case · direct_read · participant · benchmark · financier. NULLABLE: NULL means
-- "unset," never a default — an untagged company is "the operator hasn't said," not any role. It is stored
-- operator METADATA and DISPLAY only: it never feeds the call, a recorded CallCard field or a score (#4),
-- and no LLM path writes it (#3) — the chain drafter, discovery and the reconciler leave it NULL.
--
-- PER COMPANY PER CARD, stored on `basket_member` (never on the security): a company in N links is N rows,
-- and the role rides EVERY one of them, identical — the promote route refuses two different roles on one
-- company (422) and fills a NULL row beside a set role. The CHECK pins the six wire values; a seventh is a
-- migration, never a free string. OPERATIONAL like the rest of the chain (no bitemporal axes; overwritten on
-- the full-replace promote). No backfill, no snapshot rewrite: every existing row is NULL, and a snapshot
-- object carries the key only when a role is set (so a role-less roster's content hash is unchanged).
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the CHECK is dropped-then-added so a re-execution is a
-- clean no-op). The CHECK validates the existing rows on creation — all NULL, so it passes trivially.

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS context_role text;  -- NULL = unset

ALTER TABLE basket_member DROP CONSTRAINT IF EXISTS basket_member_context_role_check;
ALTER TABLE basket_member ADD CONSTRAINT basket_member_context_role_check
    CHECK (context_role IS NULL OR context_role IN
           ('payer', 'counter_case', 'direct_read', 'participant', 'benchmark', 'financier'));
