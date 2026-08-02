-- Alpha Deck — surfaced_terms on basket_member (Re-scope S1: the frozen seed-term provenance).
--
-- The discovery terms that SURFACED this name — factual provenance of the membership event, frozen when
-- the name enters the Basket (the promote freeze pass makes stored-wins a server property; the backfill
-- CLI's --overwrite is the only correction path). Killing the tag churn: today's matched_terms is
-- display-only, recomputed per draft against the CURRENT term set, so refining terms destroys the record
-- of WHY each name is in the basket. This column is that record, on the spine edge where the membership
-- lives.
--
-- This is OPERATIONAL provenance (like the rest of 0003/0008) — NOT a bitemporal fact: no
-- valid_from/recorded_at axes, no append-only trigger. Keyword STRINGS only — never a number, never a
-- call input, no reader in signals/ or calls/ (#3); it never creates membership or characterizes a name
-- (#2 — it rides an operator-ratified membership event, written only through the operator's own promote).
-- '{}' = honest empty: a hand-added name (an ETF sleeve, a manual ticker) was surfaced by no term.
-- Additive + idempotent (ALTER ... IF NOT EXISTS).

ALTER TABLE basket_member ADD COLUMN IF NOT EXISTS surfaced_terms text[] NOT NULL DEFAULT '{}';  -- the discovery terms that surfaced this name, frozen at Basket entry
