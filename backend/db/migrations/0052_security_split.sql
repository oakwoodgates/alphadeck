-- Alpha Deck — the SPLIT LIST (split plan S1, 2026-10-20): every stock split on a basket name, with the
-- evidence that confirmed it and every operator decision about it. One logical split per (tenant, SECURITY,
-- key_date); every status change or decision is a NEW version, never an UPDATE and never a DELETE.
--
-- WHY. Yahoo serves split histories on two scales (the split-tape diagnosis: 14 names broken today, a split
-- night reads as a +1,000% breakout). The list is what later slices read to put a tape on one scale (S2), flag
-- a number (S3), detect new splits nightly (S4) and hold the triggers on split night (S5). S1 creates and
-- fills it; NOTHING reads it yet.
--
-- THE SPLIT. ``ratio_from`` OLD shares -> ``ratio_to`` NEW shares, exactly as stated (16 -> 1 for a 1-for-16
-- reverse split, 1 -> 2 for a 2-for-1); ``price_factor`` = old / new is DERIVED (prices before the split are
-- multiplied by it, volumes divided). ``effective_date`` = the first session at the new price.
--
-- THE IDENTITY. ``key_date`` is the date the split was first listed under (the backfill keys on Polygon's
-- execution date) and never changes across its versions; ``effective_date`` is the version's VALUE and equals
-- it except after an operator confirms the other source's date (a re-dated split stays ONE logical split —
-- its confirm and its undo are each one version). The natural-key constraint carries security_id FROM BIRTH
-- (the 0037/0038 lesson): unique on (tenant, security, key_date, recorded_at).
--
-- STATUS: announced (both sources agree, first session still ahead) | confirmed | needs_review | rejected |
-- revoked. ``confirmed_via`` says how a confirmed split was confirmed — 'auto:sec+polygon' (two independent
-- sources agreed, the operator's 2026-10-20 rule) or 'operator' — and ``value_source`` whose values the row
-- carries (sec | polygon | both). ``action`` + ``author`` name who wrote THIS version: the backfill
-- ('system:backfill_splits') or the operator ('operator' — auth is deferred project-wide; the column is where
-- a named one lands).
--
-- EVIDENCE rides on every version, both sources side by side: the SEC filing (accession, form, filed date,
-- URL, the LOCATED passage verbatim, the document it is in, the extracted date and its kind, the extracted
-- ratio or the extractor's abstain reason) and Polygon's row (id, ticker, execution date, split_from/to).
--
-- BOTH CLOCKS. ``valid_from`` = when the split became public: the earliest confirming SEC filing's filed date,
-- or Polygon's execution date when no SEC filing states it. ``recorded_at`` = the DB's now(), NEVER backdated
-- (invariant #1): a replay as of night X sees only the versions recorded by night X.
--
-- NOT in db/bitemporal.py's _FACT_IDENTITY (yet): that registry also defines the replay mirror's table set
-- (replay/export.FACT_TABLES), and a frozen backtest mirror without this table's Parquet file would stop
-- opening. The slice that first READS the list on the call/replay path registers it with that compatibility.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).

CREATE TABLE IF NOT EXISTS security_split (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid        NOT NULL REFERENCES tenant (id),
    security_id            uuid        NOT NULL REFERENCES security_master (id),
    key_date               date        NOT NULL,
    effective_date         date        NOT NULL,
    ratio_from             numeric     NOT NULL CHECK (ratio_from > 0),
    ratio_to               numeric     NOT NULL CHECK (ratio_to > 0),
    price_factor           numeric     GENERATED ALWAYS AS (ratio_from / ratio_to) STORED,
    status                 text        NOT NULL
        CHECK (status IN ('announced', 'confirmed', 'needs_review', 'rejected', 'revoked')),
    confirmed_via          text        NULL CHECK (confirmed_via IN ('auto:sec+polygon', 'operator')),
    value_source           text        NOT NULL CHECK (value_source IN ('sec', 'polygon', 'both')),
    action                 text        NOT NULL
        CHECK (action IN ('backfill', 'confirm', 'reject', 'revoke', 'restore')),
    author                 text        NOT NULL,
    -- the SEC side
    sec_accession          text        NULL,
    sec_form               text        NULL,
    sec_filed              date        NULL,
    sec_url                text        NULL,
    sec_passage            text        NULL,
    sec_document           text        NULL,
    sec_effective_date     date        NULL,
    sec_date_kind          text        NULL CHECK (sec_date_kind IN ('trading', 'legal')),
    sec_ratio_from         numeric     NULL,
    sec_ratio_to           numeric     NULL,
    sec_abstain_reason     text        NULL,
    -- the Polygon side
    polygon_id             text        NULL,
    polygon_ticker         text        NULL,
    polygon_execution_date date        NULL,
    polygon_split_from     numeric     NULL,
    polygon_split_to       numeric     NULL,
    note                   text        NULL,
    valid_from             date        NOT NULL,
    valid_to               date        NULL,
    recorded_at            timestamptz NOT NULL DEFAULT now(),
    -- a confirmed split always says how it was confirmed (a revoked one keeps what it withdrew)
    CONSTRAINT security_split_confirmed_via CHECK (status <> 'confirmed' OR confirmed_via IS NOT NULL),
    CONSTRAINT security_split_natural_key UNIQUE (tenant_id, security_id, key_date, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_security_split_asof
    ON security_split (tenant_id, security_id, key_date, recorded_at);

-- Append-only, the same real DB guard as every fact table (raise_no_update() from 0001) ...
DROP TRIGGER IF EXISTS no_update ON security_split;
CREATE TRIGGER no_update BEFORE UPDATE ON security_split
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

-- ... and no DELETE either: a split that should not stand is a REJECTED or REVOKED version, kept on the list
-- (WB#2). (TRUNCATE, the test fixture's reset, fires no row trigger.)
CREATE OR REPLACE FUNCTION raise_no_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        '% is append-only; DELETE is not allowed (append a rejected or revoked version instead)',
        TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS no_delete ON security_split;
CREATE TRIGGER no_delete BEFORE DELETE ON security_split
    FOR EACH ROW EXECUTE FUNCTION raise_no_delete();
