-- Alpha Deck — BUSINESS-TYPE RE-TAG on the security master (Business-Type M1, S2).
--
-- The two-level business-type characterization (leaf + super-sector + royalty overlay) is DERIVED ON
-- READ from the stored ``sector`` via the editable maps in backend/securities/business_type/ — nothing
-- computed is stored (the 0028 origin discipline: re-tunable with zero re-enrich, the row keeps the raw
-- evidence). This column is the ONE stored exception: the operator's per-security RE-TAG, stored ONLY
-- when it DIFFERS from what the maps derive (the 0032 price_symbol store-on-diff idiom). NULL is the
-- common, healthy case ("classified by the maps") — a non-null value is the EXCEPTION marker (honest
-- loudness). Durable across theses: what a company DOES is a property of the security, not of a basket.
--
-- IDENTITY next to sector/origin/price_symbol, NEVER a fact (#1/#3 govern numbers, not identity
-- strings): never enters a fact_* table, never feeds a number on a call card, never gates the call path
-- (#4/#6 — MONITOR display only). Values are validated against domain.enums.BusinessType at the write
-- seam (securities/master.set_business_type); the read falls through to the derived classification on
-- an out-of-contract stored value rather than failing the scored read.
--
-- ``business_type_basis`` is the PROVENANCE string (who re-tagged — e.g. "operator:retag"), the
-- identity-basis discipline (like enriched_source / price_symbol_basis): a re-tag carries HOW it was
-- set, never masquerading as an operator-vouched fact. Additive + idempotent.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS business_type text;       -- the operator's re-tag, stored ONLY when it differs from the derived leaf (NULL = classified by the maps)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS business_type_basis text; -- provenance of the re-tag (operator:retag / ...) — identity-basis, never a fact's ratified_by
