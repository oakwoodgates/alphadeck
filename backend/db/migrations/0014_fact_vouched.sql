-- Alpha Deck — the scoring-fact VOUCHED provenance (SURFACE redesign, Slice 1a).
--
-- The three-state fact model's confirm/override PROVENANCE: when the operator ratifies a scoring fact, record
-- whether they CONFIRMED the system estimate as-is ('confirmed') or OVERRODE it ('overridden'). NULL = a manual
-- ratify with no estimate shown (legacy + hand-entered before estimates existed).
--
-- CRITICAL — `vouched` is PROVENANCE, NOT a trust tier. NULL / 'confirmed' / 'overridden' are ALL equally-trusted
-- operator-ratified facts and MUST score identically: the bitemporal as-of read NEVER filters or branches on
-- `vouched` (it is read only by the future MONITOR drift-cron + the agree/disagree display signal). A legacy
-- NULL-vouched fact scores EXACTLY as a fresh confirm — nothing about scoring changes here.
--
-- Estimates themselves are NEVER rows (computed-on-read; the unfiltered as-of read would otherwise leak them
-- into the score AND the Armed call). Only the operator's ratify persists — this column tags THAT persisted row.
--
-- Append-compatible with the `no_update` row trigger (it guards row UPDATEs, not schema). Additive + idempotent.

ALTER TABLE fact_revenue_mix        ADD COLUMN IF NOT EXISTS vouched text;  -- 'confirmed' | 'overridden' | NULL
ALTER TABLE fact_shares_outstanding ADD COLUMN IF NOT EXISTS vouched text;  -- (same)
ALTER TABLE fact_cash_burn          ADD COLUMN IF NOT EXISTS vouched text;  -- (same)
