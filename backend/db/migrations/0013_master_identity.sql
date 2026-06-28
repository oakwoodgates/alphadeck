-- Alpha Deck — security_master identity enrichment (Workbench enrichment, Slice 1).
--
-- Descriptive IDENTITY parsed from EDGAR's own submissions JSON (sicDescription / exchanges / listing
-- presence). This is NOT a fact: it never enters a fact_* table, never feeds a number on a call card, and is
-- never operator-ratified — #1/#3 govern signal/call NUMBERS, not identity strings. It rides the master (the
-- canonical, machine-sourced record of WHAT A COMPANY IS) alongside cik/ticker/name. The trail stays honest
-- by construction: identity carries an ENRICHMENT BASIS (enriched_source/enriched_at), NEVER the facts'
-- ratified_by — so machine-parsed identity can't be confused with an operator-vouched fact.
--
-- UPDATE-in-place like the broadener's name-update (the master is identity-mutable, NOT append-only; nothing
-- reads it as-of, so re-enrichment overwriting a stale value leaks into no point-in-time read). The id stays
-- stable, so the fact tables that FK security_id never orphan. Additive + idempotent.
--
-- `status` is a LISTING-PRESENCE HEURISTIC (a current ticker AND exchange in submissions -> 'active', else
-- 'inactive'), NOT a formal delisting feed — it must NEVER present to the operator as a "delisted" verdict.

ALTER TABLE security_master ADD COLUMN IF NOT EXISTS sector          text;        -- <- submissions.sicDescription
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS exchange        text;        -- <- submissions.exchanges[0]
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS status          text;        -- 'active' | 'inactive' (heuristic)
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS enriched_source text;        -- enrichment BASIS, e.g. 'submissions:CIK0001849056'
ALTER TABLE security_master ADD COLUMN IF NOT EXISTS enriched_at     timestamptz; -- when WE machine-parsed it (not ratified)
