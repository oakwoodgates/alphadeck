-- Alpha Deck — price_restatement_hold: the ONE pending vendor revert per security that the price leg is
-- holding back (Q11 / T5, 2026-10-17).
--
-- WHY. The vendor (Yahoo) flips some histories between the split-adjusted and the unadjusted basis from one
-- pull to the next (MEASURED on prod: MPU, SEGG, BURU, BTOC, ...), and a flip to the unadjusted basis puts a
-- x6..x40 cliff on the tape that the breakout detector reads as a +1,800% breakout. The thesis price leg
-- (`ingest/prices/ingest_security.py`) now HOLDS a pass's older restated bars when every one of them goes
-- back to a value the store already held and replaced (a REVERT — `ingest/prices/restatement.py`), and
-- stores them only when a pull on a LATER market date says the same thing. This table is the memory of
-- "what the previous pull said" — nothing else.
--
-- WHAT IT IS NOT. Not a fact and not history: `fact_price_eod` stays strictly append-only (INSERT only;
-- its `no_update` trigger is untouched) and nothing here is ever read by a detector, a call or a replay.
-- This is OPERATIONAL state, so it is replaced and deleted freely: one row per held security (the primary
-- key), upserted when a refreshed pull starts or restarts a hold, deleted when the hold resolves (applied,
-- bounced back, or superseded by a first-time re-base). Losing a row only costs one more night of holding.
--
--   pull_date  — the MARKET date of the pull that saw the revert (a later market date can confirm it; two
--                pulls on the same night never confirm each other).
--   bars       — what that pull said, per held bar date: {"YYYY-MM-DD": {"close": x, "volume": y}, ...}.
--   updated_at — when the row was last written (diagnostics only).
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS). ON DELETE CASCADE: operational state never blocks a
-- security_master delete.

CREATE TABLE IF NOT EXISTS price_restatement_hold (
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    security_id  uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    pull_date    date        NOT NULL,
    bars         jsonb       NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, security_id)
);
