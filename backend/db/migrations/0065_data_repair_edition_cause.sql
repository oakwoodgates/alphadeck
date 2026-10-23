-- Alpha Deck — the DATA REPAIR journal and the edition CAUSE set (editions S7, the automatic new-edition cut).
--
-- Only two things start an editions trial (`editions.cut`): a deploy that changes the rules (the engine
-- fingerprint) or a data repair run on purpose. Late data alone never does. These two tables are what the
-- second trigger and the trial's attribution need.
--
--   data_repair    ONE row per `--apply` (and per `--revoke`) of a repair CLI that rewrites FACT history — the
--                  journal the nightly editions job reads to know a repair happened since the current edition's
--                  cut. Written by the CLI itself (`repositories.data_repair_repo.journal`) in its own write
--                  transaction right after the repair committed, under the repair's lock; a dry run writes
--                  none. `tool` = the CLI module (e.g. `repair_benchmark_bars`), `action` = apply | revoke,
--                  `applied_at` = the database instant (the trial compares it with an edition's cut, both on the
--                  database clock), `summary` = one plain-English line (the new edition's label reads "data
--                  repair: <summary>"), `counts` = the tool's own counts, `rows_changed` = the fact rows it
--                  appended / deleted (0 = the run found nothing to do: journaled, but it starts no trial),
--                  `deletes` = it DELETES rows (dedup, activist, impossible dates) — a deletion is visible even
--                  to a trial pinned at an older cut, which the trial must know — and `provenance` = the
--                  repair's own provenance file under data/repairs/, when it writes one.
--   edition_cause  the (thesis, company, night) changes — and the (thesis, company, arm date) episodes — the
--                  cut's RULES trial proved: the current engine re-run on the previous edition's re-run nights
--                  with the data held at that edition's own cut, compared card by card. Stored with the NEW
--                  edition, so the "what changed" comparison (`scoreboard.diff`) fills `cause` from it — a change
--                  in the set gets the event's cause (`rule_change` declared / `unexplained` not declared /
--                  `not_separated` when a deleting repair rode the same trial), any other change gets the cause
--                  the trial's scope implies (`late_data`, `data_repair`, or `not_separated` on a night the
--                  trial did not re-test). Exactly one of `night` / `arm_date` is set.
--
-- Both carry `tenant_id` and are APPEND-ONLY with the same real DB guard every editions table uses
-- (`raise_no_update()` from 0001). `edition_cause` references `edition (id, tenant_id)` like the 0063 child tables
-- and cascades on a thesis delete like `edition_call`; `security_id` carries no FK (the edition freezes what it
-- was cut with). The `edition_event` kinds S7 writes (`closed`, `trial_no_change`) already exist in 0063.
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE).

CREATE TABLE IF NOT EXISTS data_repair (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq           bigint GENERATED ALWAYS AS IDENTITY,
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),
    tool          text        NOT NULL CHECK (btrim(tool) <> ''),
    action        text        NOT NULL CHECK (action IN ('apply', 'revoke')),
    applied_at    timestamptz NOT NULL DEFAULT now(),
    summary       text        NOT NULL CHECK (btrim(summary) <> ''),
    counts        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    rows_changed  int         NOT NULL CHECK (rows_changed >= 0),
    deletes       boolean     NOT NULL DEFAULT false,
    provenance    text        NULL
);
CREATE INDEX IF NOT EXISTS ix_data_repair_tenant_applied ON data_repair (tenant_id, applied_at);

CREATE TABLE IF NOT EXISTS edition_cause (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenant (id),
    edition_id   uuid NOT NULL,
    thesis_id    uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    security_id  uuid NOT NULL,
    night        date NULL,
    arm_date     date NULL,
    cause        text NOT NULL CHECK (cause IN ('rule_change', 'unexplained', 'not_separated')),
    CHECK ((night IS NULL) <> (arm_date IS NULL)),
    FOREIGN KEY (edition_id, tenant_id) REFERENCES edition (id, tenant_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_edition_cause_key ON edition_cause
    (edition_id, thesis_id, security_id, COALESCE(night, 'infinity'::date),
     COALESCE(arm_date, 'infinity'::date));

DROP TRIGGER IF EXISTS no_update ON data_repair;
CREATE TRIGGER no_update BEFORE UPDATE ON data_repair
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
DROP TRIGGER IF EXISTS no_update ON edition_cause;
CREATE TRIGGER no_update BEFORE UPDATE ON edition_cause
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
