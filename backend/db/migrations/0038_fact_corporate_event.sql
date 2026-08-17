-- Corporate-event fact family (Band 03 S3) — the 8-K item-code tape.
--
-- One row per (security, 8-K filing): the filing's SEC item codes, stored as a bitemporal,
-- append-only, provenance-carrying fact. The item-code taxonomy is the SEC's own deterministic
-- classification (invariant #3 — no NLP, no LLM anywhere near the fire path); the detectors
-- (signals/corporate_catalyst.py + signals/corporate_risk.py) apply the item-code POLICY MAP
-- (CallConfig.corporate_event_items) on READ, so the policy (grade/score/liveness per item) stays
-- tunable config and is NEVER baked into stored rows — the evidence/policy seam.
--
-- Store EVERY 8-K with its items, not just the detector cut (#9 recall): an item outside today's
-- cut stays on the tape for the deferred cadence/uplisting/reverse-split slices — no re-ingest.
--
-- KNOWABILITY: valid_from = filed (the EDGAR acceptance date) — an 8-K is knowable exactly when
-- EDGAR disseminates it, so no-lookahead is essentially free here (the cleanest bitemporal source
-- on the signal menu). recorded_at is the DB's now(), NEVER backdated (invariant #4).
--
-- items is NULL when the submissions JSON has not resolved the filing's item codes yet (the SPAC
-- radar's Rev-2 honesty rule, fact_spac_event 0030); the ingest re-versions the row when they
-- resolve (append-if-changed — a new version, never an UPDATE).
--
-- THE NATURAL-KEY CONSTRAINT CARRIES security_id FROM BIRTH (the 0037 lesson): one logical event is
-- (tenant, SECURITY, accession) — an issuer held as two master rows (share classes / dual listings)
-- legitimately stores the same filing once per security scope, and same-instant re-versions under
-- two securities must not collide. The as-of read (db/bitemporal.py _FACT_IDENTITY) dedups on
-- accession INSIDE its `WHERE security_id = …` scope; this constraint is that same grain + the
-- version axis (recorded_at).
CREATE TABLE IF NOT EXISTS fact_corporate_event (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    security_id  uuid NOT NULL REFERENCES security_master (id),  -- basket-bounded, NOT NULL
    form         text NOT NULL,     -- '8-K' | '8-K/A'
    items        text[],            -- parsed item codes (e.g. {1.01,9.01}); NULL = not-yet-resolved
    accession    text NOT NULL,     -- the filing identity (an accession never changes)
    filed        date NOT NULL,     -- the EDGAR filing/acceptance date
    source_ref   text NOT NULL,     -- the EDGAR filing-index URL (#6: every row traces to its filing)
    valid_from   date NOT NULL,     -- = filed (event time = knowability; no-lookahead #1)
    valid_to     date,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_corporate_event_asof
    ON fact_corporate_event (tenant_id, security_id, valid_from, recorded_at);

DROP TRIGGER IF EXISTS no_update ON fact_corporate_event;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_corporate_event
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();  -- append-only (0001's shared guard)
