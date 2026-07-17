-- Alpha Deck — the call-of-record's INGEST-HEALTH provenance (cron R2b).
--
-- A call-of-record can be recorded while resting on a PARTIAL ingest (some names errored, some clean) — the
-- run still records (only a TOTAL failure or a --no-live run is withheld, R2a). Without a marker on the row,
-- the Scoreboard cannot tell a call backed by a clean ingest from one resting on names that failed to refresh,
-- and would score them identically. These two columns carry that health, per call.
--
--   ingest_fresh  — TRUE  = every name's back-half ingest for this run succeeded (a clean call)
--                   FALSE = at least one name errored (a PARTIAL ingest — the call still recorded, marked)
--                   NULL  = legacy (recorded before this column) / unknown — never coerced to a judgement
--   ingest_errors — how many names errored on the run that produced this call (0 on a clean call)
--
-- CRITICAL — PROVENANCE, NOT a scoring input. Like `vouched` and `ratified_by`, the as-of/scoring reads never
-- filter or branch on these; they are read (later) only by the Scoreboard to segment/discount calls that rest
-- on a partial ingest. A NULL / TRUE / FALSE call all score identically.
--
-- Deliberately OFF the CallCard. The card feeds `record_if_changed`'s change-compare (`_canonical`); a freshness
-- field IN the card would make a stale->fresh flip read as a "change" and append spurious rows. So these live
-- as plain `calls` COLUMNS, threaded to `append`/`record_if_changed` SEPARATELY from the card, never inside it.
-- (This is why the thaw/late-ingest marker is NOT here — that one is DERIVABLE from the facts, so the Scoreboard
-- derives it; ingest-health is a property of the RUN, not derivable after the fact, so it must be stamped.)
--
-- Additive + idempotent; append-compatible with the `no_update` row trigger (it guards row UPDATEs, not schema).

ALTER TABLE calls ADD COLUMN IF NOT EXISTS ingest_fresh  boolean;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS ingest_errors integer;
