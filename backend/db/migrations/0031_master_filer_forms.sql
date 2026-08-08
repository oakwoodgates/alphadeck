-- Alpha Deck — FILER-FORM ingredients on the security master (the foreign-filer explainability tell).
--
-- Two raw locator ingredients machine-parsed from the EDGAR submissions JSON, stored so "is this a
-- §16-exempt foreign filer that files NO Form 4?" is DERIVED ON READ by a pure function
-- (securities/filer_coverage.py) — no computed label/bool is stored, so the rule can be tuned in code with
-- zero re-enrich (the origin-chip discipline, 0028). IDENTITY next to sector/exchange/category/origin,
-- NEVER a fact (#1/#3 govern numbers, not identity strings): never enters a fact_* table, never feeds a
-- number on a call card, never promoted onto a basket_member (#2), never a call input. NULL = un-enriched
-- (the honest abstain — the derived tell reads None, never a guessed regime).
--
-- The derivation (composed on read, both ingredients required): an issuer is a §16-exempt foreign filer that
-- files NO Form 4 iff (a recent 20-F or 40-F is present) AND (no recent 10-K/10-Q is present). The domestic
-- veto is load-bearing — it kills the Energy-Fuels (UUUU) false positive: a legacy 40-F on file, but recent
-- 10-K/10-Q filings mean it DOES file Form 4, so the tell abstains. Additive + idempotent.
--
-- Existing files_foreign_forms (0028) stays in place — now SUBSUMED by recent_foreign_form (which carries the
-- specific form, not just a bool). A later cleanup, not this migration.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS files_domestic_forms boolean; -- 10-K/10-Q present in filings.recent.form (the domestic veto — kills the legacy-foreign-form false positive)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS recent_foreign_form text;     -- the newer of 20-F / 40-F in filings.recent.form ("20-F" FPI · "40-F" Canadian-MJDS); NULL = neither present
