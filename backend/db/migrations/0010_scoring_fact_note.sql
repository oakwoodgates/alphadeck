-- Workbench scoring facts — a free-text provenance `note` (Phase 2, Slice 2 follow-up for the seed).
--
-- The three scoring facts (0009) carry `source` (the basis kind) + `source_ref` (the filing URL, identity),
-- but no free-text field for the human-readable provenance the operator ratifies alongside the number:
-- the cash COMPOSITION (which marketable securities are included), one-time ADJUSTMENTS backed out of a
-- reported figure (e.g. NuScale's ENTRA1 settlement), or a DERIVATION (e.g. a single-quarter burn derived
-- from a six-month-only disclosure). This adds that note, mirroring fact_catalyst.label. Additive + nullable
-- (existing 0009 facts keep note = NULL); operational metadata on a bitemporal fact, append-only like the row.

ALTER TABLE fact_revenue_mix       ADD COLUMN IF NOT EXISTS note text;
ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS note text;
ALTER TABLE fact_cash_burn          ADD COLUMN IF NOT EXISTS note text;
