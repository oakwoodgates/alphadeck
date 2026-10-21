-- Alpha Deck — capture each Form 4 transaction's OWNERSHIP evidence: direct vs indirect, and the holding
-- left after the trade. With it, one trade reported in SEPARATE filings by several reporting persons (a JOINT
-- REPORT) is counted ONCE at read.
--
-- WHY (found 2026-10-21, INFQ, "$95.2M across 2 insiders"). `parse_form4` stores each Table I transaction once,
-- under the filing's FIRST reporting owner (MEASURED on prod: 0 of 146,111 stored (security, accession) pairs
-- carry a second owner). The double count happens ACROSS filings: a second reporting person files their OWN
-- Form 4 for the same trade (David Singer reporting the Maverick funds' sales that Maverick's joint filing
-- already reports; a spouse reporting the other's sale "By Spouse"; five Kim-family filers reporting one
-- Sujoda Investments purchase on AMKR). The filings prove it is one trade (same date, code, shares and price,
-- the same post-trade holding, held indirectly by at least one of them), but the stored row carried none of
-- that evidence, so the detectors summed the trade once per filer and counted each filer as an insider.
--
--   direct_indirect    — the per-transaction `<ownershipNature><directOrIndirectOwnership>`: 'D' (the
--                        reporting person's own shares) or 'I' (held through a fund, trust, spouse, ...).
--   shares_owned_after — the per-transaction `<postTransactionAmounts><sharesOwnedFollowingTransaction>`:
--                        the holding the trade left. A filing that reports a VALUE instead leaves it NULL.
--
-- The read-time rule (`domain/insider_joint_reports.py`, after `resolve_insider_rows`, the same function for the
-- Postgres read and the replay mirror) collapses a joint report only on this evidence, and a row with either
-- column NULL NEVER merges — so a row not yet repaired can only stay double-counted, never collapse wrongly.
--
-- BOTH NULLABLE, populated progressively (the aff_10b5_1 / security_title rollout shape): the ingest captures
-- both on newly-ingested filings; the stored history is repaired by `pipeline.repair_insider_ownership`
-- (re-parse the cached forms/<accession> XML, NULL-only, append-only re-version stamped now — so a replay pinned
-- before the repair still reads the double count the system held then, #1).
--
-- NATURAL KEY UNTOUCHED: both are descriptive, per transaction (txn_seq is already in the key) — not identity.
-- Additive + idempotent. Both flow into the replay mirror via the export's `SELECT *`.

-- The CHECK rides on the column definition, so it is added exactly when the column is (idempotent).
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS direct_indirect text
    CHECK (direct_indirect IN ('D', 'I'));                                           -- NULL = pre-capture / unstated
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS shares_owned_after numeric;  -- NULL = pre-capture / value-reported
