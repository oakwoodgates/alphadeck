-- Alpha Deck — the INSTRUMENT KIND on the security master (ETF Sleeve, Slice 1 — the foundation brick).
--
-- `instrument_kind` marks what an instrument IS: the default `'equity'` (a common-stock operating company)
-- vs the first non-default `'etf'` (a fund the operator surfaces as the low-torque `fund` sleeve). It is the
-- modular primitive every future callable kind (SPAC, …) reuses WITHOUT another migration+backfill — an
-- app-validated `InstrumentKind` StrEnum (domain/enums.py), NOT a boolean, NOT a Postgres ENUM.
--
-- Follows the master's existing identity-column pattern (sector 0013, category 0016): a plain enriched
-- column, bitemporal-NEUTRAL (identity, never a versioned fact_* — #1/#3 govern NUMBERS, not identity). It is
-- descriptive: it never enters a fact_* table, never feeds a number on a call card, and never gates the call
-- path (the events-keyed assembler already makes a `fund` firing nothing a no-op — #4/#6: the sleeve is an
-- EXPRESSION, never a call input). Population is OPERATOR-DECLARED only (set to `'etf'` by the surface-ETF
-- flow); there is no auto-detect and no bulk backfill — every existing row stays `'equity'` via the DEFAULT.
--
-- NOT NULL DEFAULT `'equity'` (vs 0016's nullable `category`): `instrument_kind` has a MEANINGFUL default
-- (most rows ARE equities), so a non-null column avoids a NULL-coalesce on every future read. Additive +
-- idempotent.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS instrument_kind text NOT NULL DEFAULT 'equity';  -- 'equity' | 'etf'
