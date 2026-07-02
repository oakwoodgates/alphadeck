-- Alpha Deck — the per-member conviction/size weight (TRIAGE, the last crafting slice).
--
-- `conviction` holds the operator's per-name weight — a 1–5 scale (1 = starter … 5 = full). NULLABLE:
-- NULL means "unset / no conviction expressed," NEVER 0 — an unweighted name is "the operator hasn't said,"
-- not "zero size" (so future size-weighted attribution can't silently treat unset as zero-weight). It is
-- stored operator METADATA: it never feeds the meters / verdict / grade (#4 — the system sizes from the
-- signals, it doesn't judge the idea); the Board / SCORE display or consume it later. Operator-authored by
-- definition (no LLM recommendation — the usual #10 recommend-then-confirm doesn't apply). OPERATIONAL like
-- the rest of the chain (no bitemporal axes; overwritten on the full-replace promote). Additive + idempotent.

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS conviction smallint;  -- operator weight 1–5; NULL = unset
