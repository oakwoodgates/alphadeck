-- Alpha Deck — the Scoreboard EDITIONS store (editions S3): frozen re-runs of TODAY's rules over history,
-- kept beside the immutable `calls` log and never written to it.
--
-- WHAT AN EDITION IS. Edition 0 is the original record: the `calls` log, read exactly as the Scoreboard always
-- read it. It is VIRTUAL — never stored here, so `edition.number` starts at 1 (a CHECK pins it). Edition 1+ is a
-- re-run of the current call logic from the night each company joined its basket, judged on the PUBLIC clock
-- (`db.public_clock`) and frozen at its CUT (`data_known_by`: the newest row the re-run could hold), published
-- by `python -m editions.run publish --apply`.
--
--   edition        one row per edition. `causes` names why it was cut (`initial` for Edition 1; S7 adds
--                  rule_change / data_repair / code_drift), `reason` the evidence (the engine fingerprint
--                  parts, the run's counts), `engine_fingerprint` + `config_hash` + `code_sha` which rules
--                  and code produced it (`code_sha` NULL when unknown, never fabricated), `data_known_by` its
--                  cut, `first_night` its earliest page. Immutable once written.
--   edition_event  the edition's life events (`published` / `closed` / `paged` / `trial_no_change`). A closure is
--                  an APPENDED event, never an UPDATE of `edition` (the bitemporal rule).
--   edition_entry  the join dates the edition was cut with, one per (thesis, company), frozen with it:
--                  `entry_date`, `estimated` (the option-A estimate for a 0043 deploy-seed member), `method`
--                  (snapshot | first_pull | own_batch | thesis_created | no_pull, plus "+card" / "+seed_cap"),
--                  `evidence`.
--   edition_call   one frozen PAGE per (edition, thesis, night): the engine's full CallCard (the same jsonb
--                  shape `calls.card` stores), its state/verdict denormalized, the page's own watermark and
--                  fingerprint, and `page_kind` (`rerun` = the publish; S5 adds `nightly` / `catchup`).
--                  UNIQUE (edition_id, thesis_id, asof): one page per night, so a re-run can never grow it.
--
-- Left out on purpose: `edition_outcome` (the ink, S5 — scores are computed on read until then) and
-- `data_repair` (the repair journal, S7).
--
-- Every table carries `tenant_id` and is APPEND-ONLY with the same real DB guard `calls` and `call_annotation`
-- use (`raise_no_update()` from 0001). The child tables reference `edition (id, tenant_id)` together, so a
-- child row can never sit under another tenant's edition. `thesis_id` cascades on a thesis delete exactly like
-- `calls.thesis_id`; `security_id` carries no FK (like the card's own ids: the edition freezes what it was cut
-- with). Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE).

CREATE TABLE IF NOT EXISTS edition (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid        NOT NULL REFERENCES tenant (id),
    number              int         NOT NULL CHECK (number >= 1),  -- 0 is the virtual original record
    parent_id           uuid        NULL REFERENCES edition (id),
    label               text        NOT NULL CHECK (btrim(label) <> ''),
    causes              text[]      NOT NULL CHECK (
                            cardinality(causes) >= 1
                            AND causes <@ ARRAY['initial', 'rule_change', 'data_repair', 'code_drift']::text[]
                        ),
    reason              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    engine_fingerprint  text        NOT NULL,
    config_hash         text        NOT NULL,
    code_sha            text        NULL,
    data_known_by       timestamptz NOT NULL,
    first_night         date        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT edition_number_per_tenant UNIQUE (tenant_id, number),
    CONSTRAINT edition_id_tenant UNIQUE (id, tenant_id)
);

CREATE TABLE IF NOT EXISTS edition_event (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    edition_id  uuid        NOT NULL,
    kind        text        NOT NULL CHECK (kind IN ('published', 'closed', 'paged', 'trial_no_change')),
    at          timestamptz NOT NULL DEFAULT now(),
    detail      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (edition_id, tenant_id) REFERENCES edition (id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_edition_event_edition ON edition_event (edition_id, at);

CREATE TABLE IF NOT EXISTS edition_entry (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid    NOT NULL REFERENCES tenant (id),
    edition_id   uuid    NOT NULL,
    thesis_id    uuid    NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    security_id  uuid    NOT NULL,
    entry_date   date    NOT NULL,
    estimated    boolean NOT NULL,
    method       text    NOT NULL CHECK (btrim(method) <> ''),
    evidence     jsonb   NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (edition_id, tenant_id) REFERENCES edition (id, tenant_id) ON DELETE CASCADE,
    CONSTRAINT edition_entry_one_per_company UNIQUE (edition_id, thesis_id, security_id)
);

CREATE TABLE IF NOT EXISTS edition_call (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq                 bigint GENERATED ALWAYS AS IDENTITY,
    tenant_id           uuid        NOT NULL REFERENCES tenant (id),
    edition_id          uuid        NOT NULL,
    thesis_id           uuid        NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    asof                date        NOT NULL,
    state               text        NOT NULL,
    verdict             text        NOT NULL,
    card                jsonb       NOT NULL,
    data_known_by       timestamptz NOT NULL,
    engine_fingerprint  text        NOT NULL,
    page_kind           text        NOT NULL CHECK (page_kind IN ('rerun', 'nightly', 'catchup')),
    computed_at         timestamptz NOT NULL,
    FOREIGN KEY (edition_id, tenant_id) REFERENCES edition (id, tenant_id) ON DELETE CASCADE,
    CONSTRAINT edition_call_one_page_per_night UNIQUE (edition_id, thesis_id, asof)
);

-- Append-only: reversing anything about an edition is a new edition or an appended event, never an edit.
DROP TRIGGER IF EXISTS no_update ON edition;
CREATE TRIGGER no_update BEFORE UPDATE ON edition
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
DROP TRIGGER IF EXISTS no_update ON edition_event;
CREATE TRIGGER no_update BEFORE UPDATE ON edition_event
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
DROP TRIGGER IF EXISTS no_update ON edition_entry;
CREATE TRIGGER no_update BEFORE UPDATE ON edition_entry
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
DROP TRIGGER IF EXISTS no_update ON edition_call;
CREATE TRIGGER no_update BEFORE UPDATE ON edition_call
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
