-- Alpha Deck — R8-B: an applied amendment replaces only the original rows it ACCOUNTS for.
--
-- R8-A applied a partial / consolidation / addition / restatement / field change by suppressing its whole
-- original accession and emitting the amendment's rows ("rows replace"). That silences a sale the filer never
-- retracted when the 4/A only ADDS an omitted line (MRCY 0001049521-26-000071 adds a grant and re-reports none
-- of the original's two sales). R8-B matches each original row to the amendment's rows (same owner; same date or
-- the same share count — `domain.insider_amendments.match_rows`) and an `apply` decision now STORES the original
-- rows it replaces, keyed "<accession>#<txn_seq>": an unmatched original row is KEPT. The match is computed once,
-- against the full stored rows, when the decision is written (the R8-A link-decision precedent), so no windowed
-- read ever recomputes it.
--
-- NULL = the R8-A semantics (the whole original is replaced): every decision recorded before this migration,
-- and an operator's apply that ratifies an amendment the matcher could not resolve. Additive + idempotent.

ALTER TABLE insider_amendment_decision ADD COLUMN IF NOT EXISTS replaced_rows text[] NULL;
