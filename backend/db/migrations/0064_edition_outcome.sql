-- Alpha Deck — the Scoreboard editions INK (editions S5): an edition's FINAL score per arm episode, written
-- once and never re-scored.
--
-- WHAT IT IS. A stored edition (0063) holds frozen PAGES; until now every episode derived from them was scored
-- ON READ against today's prices, so a later price correction could still move a number an edition had
-- already shown as final. `edition_outcome` freezes that number. One row per (edition, thesis, company, arm
-- date) — the Scoreboard's own episode key (`replay.schema.Episode`: thesis_id, security_id, arm_date):
--
--   kind = 'matured'           the episode's signal window ENDED (its `exit_by` — the window end, never the
--                              de-arm: the operator's decision) and the market tape has printed a full
--                              session past it, so the window's closes are all in. Written by the nightly page
--                              job (`python -m editions.run nightly --apply`) the first night that is true.
--   kind = 'running_at_close'  the edition was CLOSED (a newer edition cut) while the episode was still
--                              running: its score as of the edition's last night (`ink_running_at_close`).
--
-- `outcome` is the full `replay.schema.Outcome` the Scoreboard computed (the same jsonb shape it serves);
-- `night` is the as-of its prices were capped at, `prices_known_by` the database instant they were read, and
-- `exit_by` the window end, denormalized for the read. The Scoreboard's edition read
-- (`scoreboard/record.py`) uses an inked outcome when present and scores on read otherwise; the episode's own
-- fields (close reason, de-arm) still come from the pages.
--
-- APPEND-ONLY with the same real DB guard as every edition table (`raise_no_update()` from 0001), and
-- UNIQUE on the episode key, so inking is idempotent: a second ink of the same episode appends nothing (the
-- writer uses ON CONFLICT DO NOTHING; the tests count the table). `tenant_id` on every row; the edition is
-- referenced with its tenant, like the 0063 child tables; a thesis delete cascades like `edition_call`.
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE).

CREATE TABLE IF NOT EXISTS edition_outcome (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    seq              bigint GENERATED ALWAYS AS IDENTITY,
    tenant_id        uuid        NOT NULL REFERENCES tenant (id),
    edition_id       uuid        NOT NULL,
    thesis_id        uuid        NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    security_id      uuid        NOT NULL,
    arm_date         date        NOT NULL,
    exit_by          date        NULL,
    kind             text        NOT NULL CHECK (kind IN ('matured', 'running_at_close')),
    outcome          jsonb       NOT NULL,
    night            date        NOT NULL,
    prices_known_by  timestamptz NOT NULL,
    inked_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (edition_id, tenant_id) REFERENCES edition (id, tenant_id) ON DELETE CASCADE,
    CONSTRAINT edition_outcome_one_per_episode UNIQUE (edition_id, thesis_id, security_id, arm_date)
);

DROP TRIGGER IF EXISTS no_update ON edition_outcome;
CREATE TRIGGER no_update BEFORE UPDATE ON edition_outcome
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
