-- Alpha Deck — the SEC filer CATEGORY on the security master (Workbench FE-polish).
--
-- `category` is EDGAR's own filer-status string (e.g. "Large accelerated filer" / "Accelerated filer" /
-- "Non-accelerated filer" / "Smaller reporting company") — a rough MATURITY / SIZE tell, surfaced next to
-- sector/exchange as machine-parsed IDENTITY. It is descriptive, NEVER a fact (#1/#3 govern numbers, not
-- identity strings): it never enters a fact_* table, never feeds a number on a call card, and is never
-- promoted onto a basket_member (#2). Enriched just-in-time from the submissions JSON like sector/exchange/
-- status; NULL when the filer omits it (the honest fallback). Additive + idempotent.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS category text;  -- EDGAR filer category; NULL = un-enriched
