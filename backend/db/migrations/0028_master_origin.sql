-- Alpha Deck — ORIGIN ingredients on the security master (the Workbench origin chip).
--
-- Four raw locator ingredients machine-parsed from the EDGAR submissions JSON, stored so "where is this
-- name from?" is DERIVED ON READ by a pure function (securities/origin.py) — no computed label or bool is
-- ever stored, so the ladder can be tuned in code with zero re-enrich. IDENTITY next to sector/exchange/
-- category, NEVER a fact (#1/#3 govern numbers, not identity strings): never enters a fact_* table, never
-- feeds a number on a call card, never promoted onto a basket_member (#2), never a call input. NULL =
-- un-enriched (the honest fallback — the chip renders nothing, never a guessed origin). SEC quirk baked
-- into the ingredients: for US entities addresses.business.stateOrCountryDescription holds the US STATE
-- abbreviation ("CA"), not "United States"; for the China-ADR class it is often null while business city
-- ("SHANGHAI") is the only populated locator — hence city rides as its own column. Additive + idempotent.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS incorporation text;          -- stateOfIncorporationDescription (e.g. "Cayman Islands"; "CA" for a US filer)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS business_city text;          -- addresses.business.city (e.g. "SHANGHAI" — often the only populated locator)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS business_country text;       -- addresses.business.stateOrCountryDescription (US state abbrev for US filers; null for the China-ADR class)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS files_foreign_forms boolean; -- 20-F/40-F present in filings.recent.form (a stored ingredient for the future upgrade; not read by today's ladder)
