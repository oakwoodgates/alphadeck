-- Alpha Deck — RESOLVED PRICE SYMBOL on the security master (the OTC vendor-symbol fix).
--
-- The problem (measured live): security_master.ticker is the SEC's canonical ticker (e.g. FDCT), but the
-- price vendor (Yahoo, the live EOD default) and TradingView index the FULL history under a DIFFERENT vendor
-- symbol (FDCTD: 251 bars vs FDCT's 16 — SILENT, not a 404). Pricing under the SEC ticker silently STARVES
-- every history-window signal and breaks the TradingView export. The fix keeps the SEC ticker as canon
-- (NEVER overwritten — the SEC form is identity) and ADDS a resolved price symbol beside it.
--
-- ``price_symbol`` is the vendor symbol to PRICE under, stored ONLY when it DIFFERS from ``ticker``. NULL is
-- the common, healthy case: "priced under the canonical ticker" — so a non-null value is the EXCEPTION marker
-- (honest loudness — the minority of OTC names that need a different vendor symbol carry one; everyone else
-- reads NULL). IDENTITY next to sector/exchange/origin, NEVER a fact (#1/#3 govern numbers, not identity
-- strings): it never enters a fact_* table, never feeds a number on a call card, never gates the call path.
-- The suffix rule is impossible (FDCT->FDCTD, VREOD->VREOF, CURLD->CURLF — different letters), so the value
-- is RESOLVED (deterministic Yahoo symbol-search + US/name filter + history confirmation), never guessed (#3).
--
-- ``price_symbol_basis`` is the PROVENANCE string (how the symbol was resolved — e.g. "resolver:auto ...",
-- "operator:adopt") stored alongside, the identity-basis discipline (like ``enriched_source``): a resolved
-- symbol carries HOW it was resolved, never masquerading as an operator-vouched fact. Additive + idempotent.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS price_symbol text;       -- the vendor symbol to PRICE under, stored ONLY when it differs from ticker (NULL = priced under the canonical ticker)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS price_symbol_basis text; -- provenance of the resolution (resolver:auto / operator:adopt / ...) — identity-basis, never a fact's ratified_by
