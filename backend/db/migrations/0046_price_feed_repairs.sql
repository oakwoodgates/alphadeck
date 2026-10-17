-- Alpha Deck — the price-feed REPAIR log (T2 + Q8): two APPEND-ONLY side tables that record the operator's
-- ratified repairs of a basket name's price feed, so every repair has an EXACT, auditable inverse (WB#1).
--
-- WHY. The nightly tape monitor (pipeline/tape_health.py) classifies a stopped price tape as CLOSED
-- (delisted), RENAMED (EDGAR lists the company under a symbol we don't price — HLLK -> SDCO) or a feed gap;
-- T2's splice probe (pipeline/rename_probe.py) proves a renamed name's successor symbol carries OUR tape,
-- and Q8 surfaces the UNPRICEABLE class (a member with zero stored bars that is neither closed nor renamed —
-- WONDF). Each needs an operator decision; nothing is auto-applied (#3). Two decisions write:
--
--   price_symbol_change — the operator pointed the price leg at a new vendor symbol (Adopt a verified
--     successor, or Set a symbol they typed), or UNDID such a change. `security_master.price_symbol` is
--     UPDATE-in-place identity (0032 — nothing reads the master as-of) and keeps NO history, so an undo
--     could not know what to restore. This log carries the PRIOR and NEW values of every column the change
--     touched (price_symbol, price_symbol_basis and — when an Adopt refreshed it — name), so an undo puts
--     the master back BYTE-FOR-BYTE and leaves the history behind. Reversing is APPENDING an `undo` row.
--
--   price_feed_ack — the operator acknowledged an unpriceable name: "keep it, unpriced". An acknowledged
--     name goes QUIET (no page, a quiet count on Admin, still listed — #9), and its price 404 counts as
--     expected (T3). A `revoke` row withdraws one acknowledgment (the call_annotation pattern, 0045).
--
-- `basis` names the author on every row ('operator:adopt' / 'operator:set' / 'operator:undo' /
-- 'operator:acknowledge' / 'operator:revoke'): auth is deferred project-wide, so "the operator" is the
-- only author today, and the column is where a named one will land.
--
-- `tenant_id` is COPIED from the master row by the one writer (repositories/price_feed_repo.py INSERTs ...
-- SELECT FROM security_master), so it cannot disagree with it. ON DELETE CASCADE on security_id: a master
-- row that goes takes its repair history with it; nothing else deletes.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; triggers DROP-then-CREATE, functions OR REPLACE).

CREATE TABLE IF NOT EXISTS price_symbol_change (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),
    security_id   uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    action        text        NOT NULL CHECK (action IN ('adopt', 'set', 'undo')),
    -- the master's values BEFORE and AFTER this row's write — exactly what an undo restores / checks
    prior_symbol  text        NULL,
    prior_basis   text        NULL,
    new_symbol    text        NULL,
    new_basis     text        NULL,
    name_changed  boolean     NOT NULL DEFAULT false,
    prior_name    text        NULL,
    new_name      text        NULL,
    basis         text        NOT NULL,                 -- the author: operator:adopt / operator:set / operator:undo
    detail        jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- the evidence: the splice probe, the validation fetch
    undoes        uuid        NULL REFERENCES price_symbol_change (id) ON DELETE CASCADE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT price_symbol_change_undo_pairing CHECK ((action = 'undo') = (undoes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_price_symbol_change_security ON price_symbol_change (security_id, created_at);
-- at most ONE undo per change: "standing" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_price_symbol_change_one_undo
    ON price_symbol_change (undoes) WHERE undoes IS NOT NULL;

-- An undo reverses an adopt/set of the SAME security — never another undo (no redo chains: re-adopting is
-- a new adopt), never a different security's change. Structural, so a hand-typed INSERT cannot build a
-- shape the "standing change" read misreads.
CREATE OR REPLACE FUNCTION price_symbol_change_check_undo() RETURNS trigger AS $$
DECLARE
    target_security uuid;
    target_action   text;
BEGIN
    IF NEW.undoes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT security_id, action INTO target_security, target_action
      FROM price_symbol_change WHERE id = NEW.undoes;
    IF target_action = 'undo' THEN
        RAISE EXCEPTION 'price_symbol_change: an undo cannot undo another undo (%)', NEW.undoes;
    END IF;
    IF target_security IS DISTINCT FROM NEW.security_id THEN
        RAISE EXCEPTION 'price_symbol_change: an undo must name a change of the same security (% vs %)',
            NEW.security_id, target_security;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_undo ON price_symbol_change;
CREATE TRIGGER check_undo BEFORE INSERT ON price_symbol_change
    FOR EACH ROW EXECUTE FUNCTION price_symbol_change_check_undo();

-- Append-only, the same real DB guard as `calls` / `call_annotation` (raise_no_update() from 0001)
DROP TRIGGER IF EXISTS no_update ON price_symbol_change;
CREATE TRIGGER no_update BEFORE UPDATE ON price_symbol_change
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();


CREATE TABLE IF NOT EXISTS price_feed_ack (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    security_id  uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    action       text        NOT NULL CHECK (action IN ('acknowledge', 'revoke')),
    reason       text        NOT NULL,
    basis        text        NOT NULL,                  -- the author: operator:acknowledge / operator:revoke
    detail       jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- the evidence on the night it was acknowledged
    revokes      uuid        NULL REFERENCES price_feed_ack (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT price_feed_ack_revoke_pairing CHECK ((action = 'revoke') = (revokes IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_price_feed_ack_security ON price_feed_ack (security_id, created_at);
-- at most ONE revoke per acknowledgment: "active" stays a single NOT EXISTS
CREATE UNIQUE INDEX IF NOT EXISTS ux_price_feed_ack_one_revoke
    ON price_feed_ack (revokes) WHERE revokes IS NOT NULL;

CREATE OR REPLACE FUNCTION price_feed_ack_check_revoke() RETURNS trigger AS $$
DECLARE
    target_security uuid;
    target_action   text;
BEGIN
    IF NEW.revokes IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT security_id, action INTO target_security, target_action
      FROM price_feed_ack WHERE id = NEW.revokes;
    IF target_action = 'revoke' THEN
        RAISE EXCEPTION 'price_feed_ack: a revoke cannot revoke another revoke (%)', NEW.revokes;
    END IF;
    IF target_security IS DISTINCT FROM NEW.security_id THEN
        RAISE EXCEPTION 'price_feed_ack: a revoke must name an acknowledgment of the same security (% vs %)',
            NEW.security_id, target_security;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS check_revoke ON price_feed_ack;
CREATE TRIGGER check_revoke BEFORE INSERT ON price_feed_ack
    FOR EACH ROW EXECUTE FUNCTION price_feed_ack_check_revoke();

DROP TRIGGER IF EXISTS no_update ON price_feed_ack;
CREATE TRIGGER no_update BEFORE UPDATE ON price_feed_ack
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
