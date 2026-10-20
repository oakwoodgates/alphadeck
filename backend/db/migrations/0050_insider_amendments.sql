-- Alpha Deck — Form 4/A amendments (R8-A): the 4/A stored AS FILED, an APPEND-ONLY link to the original it
-- amends, and the operator's APPEND-ONLY decisions — the three inputs every as-of read of fact_insider_txn
-- resolves against (domain/insider_amendments.py, shared by Postgres and the replay mirror).
--
-- WHY. The Form 4 leg matched the form type exactly, so a 4/A was never ingested: an amended-away buy kept
-- firing (FLS: a director buy the broker cancelled inside the settlement window stayed a CORE trigger) and a
-- corrected one never fired (ATCH: five 4/As recoding A -> P). MEASURED 2026-10-17: 556 4/As against 39,001
-- Form 4s over two years across the 687 member CIKs (1.4%).
--
-- WHY NOT fact_insider_txn. `accession` is in its natural key (0037), so a 4/A's rows written there would sit
-- BESIDE the original's: a clerical amendment double-counts (the three RPAY 4/As add ~$6.6M), and a zero-row
-- void removes nothing. And every direct reader of that table (the Scoreboard overlays, the provenance
-- metrics, the repair CLIs, the dedup tool) would have to learn to skip them. A SEPARATE table cannot be
-- double-counted by anything: nothing reads it except the resolver and the Admin panel.
--
--   insider_amendment — the 4/A as filed: every reporting owner, dateOfOriginalSubmission (the original's
--     FILING date — the link key), periodOfReport, remarks + footnotes (the evidence the operator reads),
--     its Table I rows (jsonb, the parse_form4 row shape), the EDGAR filing date and acceptance instant.
--   insider_amendment_link — which stored Form 4 accession(s) it amends, how the cascade found them
--     (orig_date_owner / owner_txn_date / none), what it changed (the disposition) and the system status
--     (applied = an auto-applied 1:1 correction; pending = routed to the operator; inert = nothing to do).
--     `voided_accessions` = the duplicates a 1->N void suppresses (TDG); `candidate_accessions` = what an
--     AMBIGUOUS link could target. Append-only: a re-link is a new row with a later recorded_at.
--   insider_amendment_decision — the operator's ratification: 'apply' (ratify the amendment; for a void /
--     duplicates the suppression holds), 'keep' (the original stands — the AZTA power-of-attorney 4/A),
--     'link' (an ambiguous amendment's original, picked by the operator; the disposition computed against
--     it is STORED here so a read never recomputes it), 'revoke' (the Undo of one of them, naming it).
--
-- PIT. Every row carries `recorded_at` (our ingest / decision instant) and the resolver counts a row iff
-- `recorded_at <= known_at` — so a read pinned before a link, or before a ratification, sees exactly what
-- the system held then (#1). `accepted` (the SEC acceptance instant) is display + ordering only, never a
-- gate. A backfill of the existing 4/As therefore stamps recorded_at = now and moves no past replay.
--
-- `basis` names the author ('operator:apply' / 'operator:keep' / 'operator:link' / 'operator:revoke'):
-- auth is deferred project-wide, so "the operator" is the only author today.
--
-- Numbered 0050 with 0047 / 0048 reserved by other in-flight changes: the runner (db/migrate.py) applies
-- every unapplied file in filename order and records each by NAME, so a gap is harmless. Depends only on
-- 0001 (tenant, security_master, raise_no_update()).
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).

CREATE TABLE IF NOT EXISTS insider_amendment (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                   uuid        NOT NULL REFERENCES tenant (id),
    security_id                 uuid        NOT NULL REFERENCES security_master (id),
    accession                   text        NOT NULL,
    issuer_cik                  text        NULL,
    issuer_name                 text        NULL,
    owners                      jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{cik, name}] every reporting owner
    date_of_original_submission date        NULL,
    period_of_report            date        NULL,
    remarks                     text        NULL,
    footnotes                   jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- [{id, text}]
    rows                        jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- Table I as filed (parse_form4 rows)
    derivative_count            integer     NOT NULL DEFAULT 0,             -- Table II rows (display only)
    filed                       date        NULL,                           -- the EDGAR filing date
    accepted                    timestamptz NULL,                           -- the SEC acceptance instant (display)
    recorded_at                 timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT insider_amendment_natural_key UNIQUE (tenant_id, security_id, accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_insider_amendment_security
    ON insider_amendment (tenant_id, security_id, recorded_at);

DROP TRIGGER IF EXISTS no_update ON insider_amendment;
CREATE TRIGGER no_update BEFORE UPDATE ON insider_amendment
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();


CREATE TABLE IF NOT EXISTS insider_amendment_link (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid        NOT NULL REFERENCES tenant (id),
    security_id          uuid        NOT NULL REFERENCES security_master (id),
    amendment_accession  text        NOT NULL,
    original_accessions  text[]      NOT NULL DEFAULT '{}',
    voided_accessions    text[]      NOT NULL DEFAULT '{}',
    candidate_accessions text[]      NOT NULL DEFAULT '{}',
    owner_ciks           text[]      NOT NULL DEFAULT '{}',
    method               text        NOT NULL
        CHECK (method IN ('orig_date_owner', 'owner_txn_date', 'none')),
    disposition          text        NOT NULL
        CHECK (disposition IN ('no_rows', 'clerical', 'unlinked', 'correction', 'void', 'duplicates',
                               'partial', 'consolidation', 'addition', 'restatement', 'ambiguous')),
    status               text        NOT NULL CHECK (status IN ('applied', 'pending', 'inert')),
    detail               jsonb       NOT NULL DEFAULT '{}'::jsonb,           -- the why (the cascade's reading)
    accepted             timestamptz NULL,                                   -- the amendment's (display)
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT insider_amendment_link_natural_key
        UNIQUE (tenant_id, security_id, amendment_accession, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_insider_amendment_link_security
    ON insider_amendment_link (tenant_id, security_id, recorded_at);

DROP TRIGGER IF EXISTS no_update ON insider_amendment_link;
CREATE TRIGGER no_update BEFORE UPDATE ON insider_amendment_link
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();


CREATE TABLE IF NOT EXISTS insider_amendment_decision (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid        NOT NULL REFERENCES tenant (id),
    security_id          uuid        NOT NULL REFERENCES security_master (id),
    amendment_accession  text        NOT NULL,
    action               text        NOT NULL CHECK (action IN ('apply', 'keep', 'link', 'revoke')),
    -- 'link' only: the candidate original the operator picked, and the disposition computed against it
    target_accession     text        NULL,
    disposition          text        NULL,
    original_accessions  text[]      NOT NULL DEFAULT '{}',
    voided_accessions    text[]      NOT NULL DEFAULT '{}',
    reason               text        NOT NULL,
    basis                text        NOT NULL,                  -- the author: operator:apply / keep / link / revoke
    detail               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    revokes              uuid        NULL REFERENCES insider_amendment_decision (id),
    recorded_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT insider_amendment_decision_revoke_pairing CHECK ((action = 'revoke') = (revokes IS NOT NULL)),
    CONSTRAINT insider_amendment_decision_link_pairing
        CHECK ((action = 'link') = (target_accession IS NOT NULL AND disposition IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_insider_amendment_decision_security
    ON insider_amendment_decision (tenant_id, security_id, recorded_at);
-- at most ONE revoke per decision: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_insider_amendment_decision_one_revoke
    ON insider_amendment_decision (revokes) WHERE revokes IS NOT NULL;

-- A revoke reverses a decision of the SAME amendment of the SAME security — never another revoke (no redo
-- chains: deciding again is a new decision). Structural, so a hand-typed INSERT cannot build a shape the
-- "standing decision" read misreads.
CREATE OR REPLACE FUNCTION insider_amendment_decision_check_revoke() RETURNS trigger AS $$
DECLARE
    target_security  uuid;
    target_accession text;
    target_action    text;
BEGIN
    IF NEW.revokes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT security_id, amendment_accession, action INTO target_security, target_accession, target_action
      FROM insider_amendment_decision WHERE id = NEW.revokes;
    IF target_action = 'revoke' THEN
        RAISE EXCEPTION 'insider_amendment_decision: a revoke cannot revoke another revoke (%)', NEW.revokes;
    END IF;
    IF target_security IS DISTINCT FROM NEW.security_id
       OR target_accession IS DISTINCT FROM NEW.amendment_accession THEN
        RAISE EXCEPTION 'insider_amendment_decision: a revoke must name a decision of the same amendment (%)',
            NEW.revokes;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_revoke ON insider_amendment_decision;
CREATE TRIGGER check_revoke BEFORE INSERT ON insider_amendment_decision
    FOR EACH ROW EXECUTE FUNCTION insider_amendment_decision_check_revoke();

DROP TRIGGER IF EXISTS no_update ON insider_amendment_decision;
CREATE TRIGGER no_update BEFORE UPDATE ON insider_amendment_decision
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
