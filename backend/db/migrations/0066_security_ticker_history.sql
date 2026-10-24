-- Alpha Deck — the security TICKER HISTORY (the master refresh, 2026-10-24): an APPEND-ONLY side table that
-- records every ticker change the SEC company-list refresh applied to a master row IN PLACE.
--
-- WHY. `security_master` is UPDATE-in-place identity with no history of its own (0032 — nothing reads the
-- master as-of). The refresh (`python -m pipeline.populate_master`, `securities/master_refresh.py`) used to
-- key on (cik, ticker), so a renamed ticker INSERTED a second row for the same company: the next Workbench Save
-- re-pointed the member to that new, empty row (canonicalize picks the newest primary) and every fact keyed by
-- `security_id` stayed behind on the old one. The refresh now RENAMES the company's primary row in place (the
-- id stays, so the tape, every fact, the basket links and the sign-off stay attached) — and this table keeps
-- what that UPDATE overwrites: the old ticker, the new one, when the refresh saw it and which file said so.
--
-- READERS: the Workbench company search matches a FORMER ticker (typing "SVAC" finds the renamed company), and
-- the promote's identity-coherence guard aligns a basket label that is the bound row's former ticker instead of
-- refusing it as drift (`securities/coherence.py`, kind `renamed`). Display identity only — never a fact, never
-- a call input (#1/#3).
--
-- `source` names the file the refresh read (`sec:company_tickers_exchange`); `seen_at` is the honest instant
-- the refresh applied the change (stamped now, never backdated — the store's own bookkeeping, not a public
-- date). `tenant_id` is copied from the master row by the one writer. ON DELETE CASCADE on security_id: a
-- master row that goes takes its history with it; nothing else deletes.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the trigger DROP-then-CREATE). Depends only on 0001
-- (tenant, security_master, raise_no_update()).

CREATE TABLE IF NOT EXISTS security_ticker_history (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    security_id uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    old_ticker  text        NOT NULL,
    new_ticker  text        NOT NULL,
    seen_at     timestamptz NOT NULL DEFAULT now(),
    source      text        NOT NULL,
    CONSTRAINT security_ticker_history_changes CHECK (old_ticker <> new_ticker)
);

CREATE INDEX IF NOT EXISTS ix_security_ticker_history_security
    ON security_ticker_history (security_id, seen_at);
-- the former-ticker lookups (the company search, the coherence guard) read by (tenant, old ticker)
CREATE INDEX IF NOT EXISTS ix_security_ticker_history_old
    ON security_ticker_history (tenant_id, old_ticker);

-- Append-only, the same real DB guard as `calls` / `price_symbol_change` (raise_no_update() from 0001)
DROP TRIGGER IF EXISTS no_update ON security_ticker_history;
CREATE TRIGGER no_update BEFORE UPDATE ON security_ticker_history
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();
