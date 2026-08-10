-- Alpha Deck — basket_member.signed_off + the honest-authorship reset (Discovery cleanup S1).
--
-- THE CONFIDENCE LADDER: include + accept collapse into ONE ladder — Excluded → Included
-- (system-recommended) → Signed off. `signed_off` is the top rung: the operator's per-NAME endorsement
-- of the company. A MARKER only — it never sets authorship (the description stays a model draft until
-- the operator EDITS it), never gates promote (include gates; sign-off marks), and never feeds the
-- call/score (#4).
--
-- THE RESET (NON-DESTRUCTIVE — content columns untouched; only the authorship LABEL + the new flag
-- re-base): the old `accept` flipped authored_by system_drafted → operator_set WITHOUT changing the
-- text — a false "the operator wrote this" claim the UI rendered as "your words". Re-base to the
-- honest ground state (a field is the operator's only if the operator changed it):
--   operator_set    → signed_off = true,  authored_by = 'system_drafted'  (old accept/hand-add = ENDORSED)
--   operator_edited → signed_off = false, authored_by = 'system_drafted'  (accepted relabel — the legacy
--                                                                          edits were test-only; the next
--                                                                          real edit re-earns "your words")
--   system_drafted  → signed_off = false (the column default); authorship unchanged
-- Go-forward, `operator_edited` returns the moment the operator actually edits a description; the
-- retired `operator_set` never returns for basket members (promote translates a legacy payload). The
-- enum value itself stays — it is load-bearing for the TERM SET's "seed" authorship, untouched here.
--
-- Runner-tracked once (schema_migrations), so this backfill cannot re-fire over post-migration rows.

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS signed_off boolean NOT NULL DEFAULT false;

UPDATE basket_member SET signed_off = true, authored_by = 'system_drafted'
 WHERE authored_by = 'operator_set';
UPDATE basket_member SET authored_by = 'system_drafted'
 WHERE authored_by = 'operator_edited';
