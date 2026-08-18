-- Activist-stake fact family (Band 03 S5) — the SC 13D/G ownership tape.
--
-- One row per (SUBJECT security, 13D/G filing): a beneficial-ownership schedule filed BY an outside
-- holder ABOUT a basket member, stored as a bitemporal, append-only, provenance-carrying fact. The
-- FORM TYPE is the classification (invariant #3 — 13D = ">5% with intent to influence", 13G =
-- passive; the SEC's own deterministic distinction, no NLP anywhere near the fire path); the
-- detector (signals/activist_stake.py) applies the fire POLICY on READ (13D-family originals fire a
-- Key-1 conviction; 13G-family rows fire NOTHING in v1 — the S3 1.01-flood lesson: passive index-
-- fund crossings are plumbing, not conviction), so the policy stays config and is NEVER baked into
-- stored rows — the evidence/policy seam.
--
-- Store EVERY 13D/G-family filing about the member, both naming eras, not just the detector cut
-- (#9 recall): the classic "SC 13D"-era strings AND the post-modernization "SCHEDULE 13D"-era
-- strings (EDGAR renamed the form type when the structured-XML requirement landed, cutover
-- ~2024-12-18 — measured on real subjects). 13G rows and amendments stay on the tape for the
-- deferred 13G->13D-switch and %-owned refinements — no re-ingest.
--
-- KNOWABILITY: valid_from = filed (the EDGAR acceptance date) — the stake CROSSING inside the
-- filing predates dissemination by up to 10 days (gold-doc trap #4), and the structured cover even
-- carries a dateOfEvent; NEITHER is ever the knowable moment. recorded_at is the DB's now(), NEVER
-- backdated (invariant #4).
--
-- filer_cik / filer_name identify THE ACTIVIST (the 0024 filer-identity capture pattern): parsed
-- from the structured primary_doc.xml (post-2024-12 era) or the filing's SGML FILED-BY header
-- (13D-family classic era); NULL when the identity fetch/parse fails or is out of the bounded
-- depth (old-era 13G rows) — an identity failure NEVER drops the row (#9). pct_owned is the
-- structured cover's percentOfClass — nullable, structured-era-only, EVIDENCE never the fire
-- decision (#3); the ingest re-versions the row when identity/pct resolve (append-if-changed — a
-- new version, never an UPDATE).
--
-- THE NATURAL-KEY CONSTRAINT CARRIES security_id FROM BIRTH (the 0037 lesson): one logical event is
-- (tenant, SECURITY, accession) — an issuer held as two master rows (share classes / dual listings)
-- legitimately stores the same filing once per security scope, and same-instant re-versions under
-- two securities must not collide. The as-of read (db/bitemporal.py _FACT_IDENTITY) dedups on
-- accession INSIDE its `WHERE security_id = …` scope; this constraint is that same grain + the
-- version axis (recorded_at).
CREATE TABLE IF NOT EXISTS fact_activist_stake (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    security_id  uuid NOT NULL REFERENCES security_master (id),  -- the SUBJECT (basket member), NOT NULL
    form         text NOT NULL,     -- 'SC 13D'|'SC 13D/A'|'SC 13G'|'SC 13G/A'|'SCHEDULE 13D'|… (both eras)
    filer_cik    text,              -- the activist's CIK (10-digit), NULL = identity unresolved
    filer_name   text,              -- the activist's conformed name, NULL = identity unresolved
    pct_owned    numeric,           -- structured cover percentOfClass; NULL = pre-structured era / unparsed
    accession    text NOT NULL,     -- the filing identity (an accession never changes)
    filed        date NOT NULL,     -- the EDGAR filing/acceptance date
    source_ref   text NOT NULL,     -- the EDGAR filing-index URL (#6: every row traces to its filing)
    valid_from   date NOT NULL,     -- = filed (knowability; never the in-document event date — trap #4)
    valid_to     date,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_activist_stake_asof
    ON fact_activist_stake (tenant_id, security_id, valid_from, recorded_at);

DROP TRIGGER IF EXISTS no_update ON fact_activist_stake;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_activist_stake
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();  -- append-only (0001's shared guard)
