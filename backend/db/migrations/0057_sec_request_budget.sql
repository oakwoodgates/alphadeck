-- Alpha Deck — sec_request_budget: ONE SEC request budget shared by EVERY process (X2, 2026-10-21).
--
-- WHY. SEC's fair-access ceiling is 10 requests/second per egress IP, and EDGAR etiquette is a correctness
-- requirement here, not a courtesy (docs/DATA_SOURCES.md §EDGAR etiquette). Before this table each
-- `EdgarClient` carried its OWN in-process 8 req/s limiter, so the cron's nightly pass, the backend's Workbench
-- discovery fan-out and any CLI each ran a separate 8/s — two of them at once could exceed 10/s. The nightly
-- is about to fetch concurrently (`pipeline/fetch_ahead.py`), which makes overlapping with a discovery run
-- likely, so the budget has to be global: held where every process already meets, in Postgres.
--
-- WHAT IT IS. ONE row (`id = 1`): `next_slot`, the earliest instant the NEXT SEC request may start. A request
-- RESERVES its slot with one atomic upsert (`ingest/sec_budget.py`):
--     next_slot := GREATEST(next_slot, now()) + 1/rate      RETURNING the reserved slot
-- The row lock serializes reservations across connections, so any two reserved slots are at least 1/rate
-- apart whichever processes took them — the cap holds by construction, not by agreement between processes.
-- The caller then sleeps until its slot (outside the database) and sends.
--
-- WHAT IT IS NOT. Not a fact and not history — operational state, overwritten on every SEC request; losing
-- the row costs nothing (the upsert re-creates it). No `tenant_id`, DELIBERATELY (the one table without one
-- besides `tenant`): the budget belongs to the deployment's egress IP, which every tenant shares — a
-- per-tenant row would multiply the cap by the tenant count, the exact failure this table exists to stop.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS sec_request_budget (
    id         smallint    PRIMARY KEY CHECK (id = 1),
    next_slot  timestamptz NOT NULL
);
