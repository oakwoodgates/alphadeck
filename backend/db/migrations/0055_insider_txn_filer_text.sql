-- Alpha Deck — capture each Form 4 transaction's OWN WORDS about whose shares it reports: the ownership nature,
-- the footnotes the transaction cites, and every indirect holding the filing states. With them, an INTERNAL
-- TRANSFER (a buy and a sale between linked filers) is screened at read, and a private purchase gets its note.
--
-- WHY (found 2026-10-21, AMKR). An internal transfer looks like a buy plus a sale: on 2025-02-24 James J. Kim
-- SOLD 869,565 AMKR shares at $21.85 and Sujoda Investments, LP — his children's partnership — BOUGHT the same
-- 869,565 at $21.85 the same day ("a transaction between members of the Kim family group"). The re-run of 2025
-- armed AMKR on that phantom buy (114 of 243 days). The joint-report evidence of 0054 (direct/indirect + the
-- post-trade holding) does NOT link the two sides — MEASURED on prod: it links 0 of the 3 AMKR pairs (the
-- holdings differ). What links them is in each filing's own words: Susan Y. Kim's purchase filing reports shares
-- held "By James J. Kim 2024 GRAT" (she is a trustee of her father's trusts) and James Kim's sale filing reports
-- shares held "By John T. Kim Family Trust". The stored row carried none of that text.
--
--   ownership_nature — the transaction's `<ownershipNature><natureOfOwnership>` ("By Spouse",
--                      "By Sujoda Investments, LP"). NULL = none stated / not captured.
--   txn_footnotes    — the text of every footnote the transaction cites, in first-reference order. The located
--                      passage behind the Event Ledger's private-purchase note.
--   filing_natures   — every INDIRECT ownership nature the FILING states, on its transactions and its holdings
--                      (Table I): the filer's own statement of whose shares it reports. The internal-transfer
--                      screen reads it (`domain/insider_internal_transfers.py`).
--   For both arrays NULL = not captured; '{}' = captured, the filing states none. text[] (not jsonb), the
--   `fact_corporate_event.items` precedent: psycopg round-trips a Python list as text[], so every repair tool that
--   copies a row verbatim into its next version keeps working unchanged.
--
-- The read-time rule never screens on missing evidence: a row whose filing_natures is NULL can link nothing, so a
-- row not yet repaired can only stay counted, never be screened wrongly.
--
-- ALL NULLABLE, populated progressively (the aff_10b5_1 / 0054 rollout shape): the ingest captures all three on
-- newly-ingested filings; the stored history is repaired by `pipeline.repair_insider_filer_text` (re-parse the
-- cached forms/<accession> XML, NULL-only, append-only re-version stamped now — a replay pinned before the repair
-- still reads what the system held then, #1).
--
-- NATURAL KEY UNTOUCHED: descriptive, per transaction — not identity. Additive + idempotent. All three flow into
-- the replay mirror via the export's `SELECT *` (an array -> a JSON string the mirror decodes back to a list).

ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS ownership_nature text;  -- NULL = none stated / not captured
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS txn_footnotes text[];   -- NULL = not captured; {} = none
ALTER TABLE fact_insider_txn ADD COLUMN IF NOT EXISTS filing_natures text[];  -- NULL = not captured; {} = none
