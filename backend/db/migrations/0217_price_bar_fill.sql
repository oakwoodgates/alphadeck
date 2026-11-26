-- Alpha Deck — price_bar_fill: the PROVENANCE of a price bar the vendor never served (platform fixes W6-3b,
-- 2026-11-25).
--
-- WHY. Yahoo serves a NULL close for sessions around a split (MEASURED 2026-11-25: every post-split session of
-- SNDK's 8-for-1 of 11-20 and SCNI's 1-for-4 of 11-19; the first post-split session of DFSC, EMBJ, SGRX, COSM),
-- so the price leg stored nothing and the tape stopped. The price leg (`ingest/prices/ingest_security.py`,
-- `ingest/prices/gap_fill.py`) now fills such a session from Massive.com's raw (as-traded) bar, under a rule (on
-- or after the newest split in force's first session, never on/after a pending split's, only where Massive.com
-- traded, never over a vendor or a stored bar). The bar itself is an ordinary `fact_price_eod` version; this
-- table says which versions came from Massive.com, so a filled bar is never read as split evidence
-- (`pipeline/detect_splits.py` `tape_at` / `tape_window`) and the steward can see it.
--
-- One row per filled bar, written in the SAME transaction as the bar: `bar_recorded_at` = that bar's
-- `recorded_at` (the transaction's now()), so (tenant_id, security_id, d, bar_recorded_at) names exactly one
-- `fact_price_eod` version. `fact_price_eod` is NOT altered (it is mirrored and replayed). A later vendor close for
-- the date re-versions the bar the ordinary way; this row then still explains the earlier version.
--
--   source         — who served the bar: 'massive' (Massive.com) only, by CHECK.
--   vendor_symbol  — the symbol Massive.com was read under (a class share's dash a dot: BRK.B).
--   raw_close / raw_volume — Massive.com's values as served (exact decimals).
--   factor         — the split-list scale applied to them: 1 by the rule's construction (on or after the newest
--                    split's first session nothing is rescaled); recorded so a reader never has to assume it.
--   reason         — why the rule filled it: split_window | vendor_silent | all.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS). ON DELETE CASCADE: provenance never blocks a security_master
-- delete.

CREATE TABLE IF NOT EXISTS price_bar_fill (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid        NOT NULL REFERENCES tenant (id),
    security_id      uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    d                date        NOT NULL,
    bar_recorded_at  timestamptz NOT NULL,
    source           text        NOT NULL CHECK (source = 'massive'),
    vendor_symbol    text        NOT NULL,
    raw_close        numeric,
    raw_volume       numeric,
    factor           numeric     NOT NULL,
    reason           text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, security_id, d, bar_recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_price_bar_fill_security ON price_bar_fill (tenant_id, security_id, d);
