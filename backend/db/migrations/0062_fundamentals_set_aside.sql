-- Alpha Deck — fundamentals_set_aside: an APPEND-ONLY side table naming stored `fact_fundamentals` VERSIONS the
-- read must skip — the revenue rows the data-quality screen would have refused, stored before it existed.
--
-- WHY. The revenue-label fix (#417, 2026-10-20) added a data-quality screen to the fundamentals ingest: a
-- fiscal-year total under a quarter context, an annual report's Q4-context row standing in for the year, and a
-- derived Q4 across an FY restatement are refused at ingest (`ingest.fundamentals._label_series`). Each judgment
-- uses only rows filed on or before the judged row's own filing (the review's A2 fix). But `fact_fundamentals` is
-- append-only, so the rows the OLD ingest stored before the screen existed are still read (the #417 report's A8:
-- ~80 rows in 39 companies, among them live-window names ATCH, BKKT, DOMH, FRHC, NLST, ROLR, TSNDF, AZTA). The
-- operator decided (2026-10-22): set them aside on READ — reversibly, rows kept, never deleted or rewritten.
--
-- WHY A SIDE TABLE, NOT A FACT COLUMN. `fact_fundamentals` carries the `no_update` trigger. A set-aside is a
-- JUDGMENT made later, by a named rule, that the operator may revoke — so it lives beside the row, append-only,
-- with its own history (the call_annotation pattern, 0045): who said what, when, under which code, on what
-- evidence. Nothing in `fact_fundamentals` changes, ever.
--
--   fact_id     — the ONE stored version set aside (the row's primary key). `security_id` / `metric_key` /
--                 `period_end` / `valid_from` / `supersedes` — the version's 0060 key, (tenant, security,
--                 metric, period, filing, supersedes) — and `tenant_id` are COPIED from that row by the one
--                 writer (`repositories/fundamentals_set_aside_repo.py` INSERTs ... SELECT FROM
--                 fact_fundamentals), so they cannot disagree with it; they make every row readable on its own.
--   action      — 'set_aside' : the read skips this version (`db.set_aside`), at every asof, on both clocks and
--                               in the replay mirror. Where it superseded an earlier version (a later filing of
--                               the same period, or a same-filing re-parse), the read falls back to that one —
--                               as if the bad row had never been stored.
--                 'revoke'    : withdraws ONE earlier set_aside (`revokes` names it). Revoking is appending.
--   reason      — the screen's reason code on a set_aside (`fy_total_in_quarter` / `year_in_q4_context` /
--                 `q4_restated_fy_basis`); the plain-English why on a revoke. Required on every row.
--   rule        — the code identity that made the judgment, e.g. 'fundamentals-quality-screen@<sha>' (the
--                 rule version: its family before the '@', the image after it).
--   author      — who: 'system:set_aside_fundamentals' (the CLI applying the screen) or 'operator:revoke'. Auth
--                 is deferred project-wide; the column is where a named author will land (the 0046 `basis`).
--   detail      — the evidence (jsonb): the stored value and accession, the figure the screen judged it against,
--                 the XBRL label whose rows it judged, the screened filing.
--   revokes     — NULL for set_aside; the set_aside a 'revoke' withdraws. A CHECK pins the pairing.
--   recorded_at — when this judgment was written (the honest database instant).
--
-- ACTIVE = a set_aside row that no revoke row references. A revoke can target only a set_aside of the SAME fact
-- (the trigger below), and at most one revoke per set_aside (the partial unique index) — so "active" is a single
-- NOT EXISTS, never a recursive walk.
--
-- ON DELETE CASCADE on fact_id: a repair that deletes a fact version (`pipeline.dedup_identical_versions`) takes
-- its judgments with it; nothing else deletes.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; trigger + function DROP-then-CREATE / OR REPLACE).

CREATE TABLE IF NOT EXISTS fundamentals_set_aside (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid        NOT NULL REFERENCES tenant (id),
    fact_id          uuid        NOT NULL REFERENCES fact_fundamentals (id) ON DELETE CASCADE,
    security_id      uuid        NOT NULL,
    metric_key       text        NOT NULL,
    period_end       date        NOT NULL,
    valid_from       date        NOT NULL,
    fact_supersedes  uuid        NULL,
    action           text        NOT NULL CHECK (action IN ('set_aside', 'revoke')),
    reason           text        NOT NULL CHECK (btrim(reason) <> ''),
    rule             text        NOT NULL CHECK (btrim(rule) <> ''),
    author           text        NOT NULL CHECK (btrim(author) <> ''),
    detail           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    revokes          uuid        NULL REFERENCES fundamentals_set_aside (id) ON DELETE CASCADE,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fundamentals_set_aside_revoke_pairing CHECK ((action = 'revoke') = (revokes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_fundamentals_set_aside_fact ON fundamentals_set_aside (fact_id);
CREATE INDEX IF NOT EXISTS ix_fundamentals_set_aside_security ON fundamentals_set_aside (security_id);
-- at most ONE revoke per set_aside: "active" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_fundamentals_set_aside_one_revoke
    ON fundamentals_set_aside (revokes) WHERE revokes IS NOT NULL;

-- A revoke withdraws a set_aside of the SAME fact version — never another revoke (no un-revoke chains: setting
-- a row aside again is a new set_aside), never a different version's judgment. Structural, so a hand-typed
-- INSERT cannot build a shape the active read misreads.
CREATE OR REPLACE FUNCTION fundamentals_set_aside_check_revoke() RETURNS trigger AS $$
DECLARE
    target_fact   uuid;
    target_action text;
BEGIN
    IF NEW.revokes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT fact_id, action INTO target_fact, target_action FROM fundamentals_set_aside WHERE id = NEW.revokes;
    IF target_action = 'revoke' THEN
        RAISE EXCEPTION 'fundamentals_set_aside: a revoke cannot revoke another revoke (%)', NEW.revokes;
    END IF;
    IF target_fact IS DISTINCT FROM NEW.fact_id THEN
        RAISE EXCEPTION 'fundamentals_set_aside: a revoke must name a set_aside of the same version (% vs %)',
            NEW.fact_id, target_fact;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_revoke ON fundamentals_set_aside;
CREATE TRIGGER check_revoke BEFORE INSERT ON fundamentals_set_aside
    FOR EACH ROW EXECUTE FUNCTION fundamentals_set_aside_check_revoke();

-- Append-only, the same real DB guard as `calls`, `call_annotation` and every fact table (raise_no_update()
-- from 0001): reversing a judgment is a new 'revoke' row, never an edit.
DROP TRIGGER IF EXISTS no_update ON fundamentals_set_aside;
CREATE TRIGGER no_update BEFORE UPDATE ON fundamentals_set_aside
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
