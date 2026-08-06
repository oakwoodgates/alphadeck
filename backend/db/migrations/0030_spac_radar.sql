-- Alpha Deck — the SPAC Radar's two fact tables (spac-radar slices 1+2; docs/temp/spac-radar-options.md).
--
-- fact_spac_event: one blank-check TRANSITION FILING seen on the EDGAR daily index (425 / S-4 / merger
-- proxy / 8-K with item codes / extension proxy) for a known-shell CIK. FACTS ONLY: form + items + dates +
-- the EDGAR link — the deal STATE (searching → announced → terminated | completed) is derived READ-TIME
-- (radar/state.py), never stored, so the state rules can be retuned with zero backfill. security_id is the
-- canonical master join when the CIK is in the master, NULL otherwise (recall #9: an off-master shell's
-- event is still shown, never dropped).
--
-- fact_spac_match: one thesis's term-set hits against one DA-class filing's text (radar/matcher.py —
-- deterministic word-boundary matching, #3: never an LLM read). Snapshot provenance: the matched term
-- STRINGS as of match time; a term-set edit later does not rewrite history (a re-run appends a new
-- version if the hits changed).
--
-- Both append-only (no_update trigger); a changed re-observation is a NEW version (later recorded_at);
-- reads take DISTINCT ON (natural key) ... recorded_at DESC. Idempotency tests COUNT THE TABLE.

CREATE TABLE IF NOT EXISTS fact_spac_event (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    cik          text NOT NULL,
    security_id  uuid REFERENCES security_master (id),  -- NULL = not in the master (still shown)
    company_name text NOT NULL,
    form         text NOT NULL,
    items        text[],            -- 8-K item codes when resolvable (e.g. {1.01,9.01}); NULL = unknown
    filed        date NOT NULL,
    accession    text NOT NULL,     -- the natural key (an accession never changes)
    source_ref   text NOT NULL,     -- the EDGAR filing index URL (#6: every row traces to its filing)
    valid_from   date NOT NULL,     -- = filed (event time, no-lookahead #1)
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_spac_event_cik ON fact_spac_event (tenant_id, cik, filed);
CREATE INDEX IF NOT EXISTS ix_spac_event_filed ON fact_spac_event (tenant_id, filed);

DROP TRIGGER IF EXISTS no_update ON fact_spac_event;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_spac_event
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

CREATE TABLE IF NOT EXISTS fact_spac_match (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenant (id),
    thesis_id      uuid NOT NULL REFERENCES thesis (id),
    cik            text NOT NULL,
    accession      text NOT NULL,
    matched_signal text[] NOT NULL,  -- SIGNAL-tier term strings that hit (may be empty)
    matched_broad  text[] NOT NULL,  -- BROAD-tier term strings that hit (may be empty)
    truncated      boolean NOT NULL DEFAULT false,  -- the doc was capped before matching (no silent caps)
    source_ref     text NOT NULL,    -- the matched document's URL (#6)
    valid_from     date NOT NULL,    -- = the filing's filed date
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, accession, thesis_id, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_spac_match_acc ON fact_spac_match (tenant_id, accession);
CREATE INDEX IF NOT EXISTS ix_spac_match_thesis ON fact_spac_match (tenant_id, thesis_id, valid_from);

DROP TRIGGER IF EXISTS no_update ON fact_spac_match;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_spac_match
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
