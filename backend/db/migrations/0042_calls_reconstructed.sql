-- Alpha Deck — calls.reconstructed: the explicit "this row was written by a backfill" marker, plus the
-- one-time stamp of the legacy reconstructed rows.
--
-- WHY. `pipeline.backfill` (#331) reconstructs a MISSED night's call-of-record with `known_at` pinned. It
-- pins the transaction clock faithfully, but two other axes are NOT faithful: it ran every non-archived
-- thesis for every night, including theses that did not exist yet (37 of the 144 rows on the dev copy,
-- 20 of them warming/armed — two produced arm episodes for a thesis that did not exist), and EVERY
-- reconstructed row ran on TODAY's basket (`basket_member` is full-replace, no timestamps — which names
-- were in a basket on a past night is unknowable). So a reconstructed row can never be shown honest, and
-- the Scoreboard must not let one open or close an arm episode. That needs a marker the record path can
-- filter on. Until now the only tell was DERIVED — `ingest_fresh IS NULL` (the backfill's honest NULL
-- ingest stamp) AND `(recorded_at AT TIME ZONE 'UTC')::date - asof > 1` (a nightly row lands at lag 0/1;
-- every reconstructed row landed >= 5 days late — MEASURED on the dev copy 2026-09-12: 144 rows, min lag
-- 5; the 39 honest NULL-stamp rows max lag 1; identical under the session cast, UTC, and New York) — exact
-- on the existing rows, but with a forward hole: a backfill run the morning after a missed night lands at
-- lag 1 and would read as nightly. The day is pinned to UTC (the zone the rule was measured in) rather
-- than the bare `::date` cast, which follows the connection's TimeZone and could shift a lag by a day.
--
--   reconstructed — TRUE  = written by `pipeline.backfill` (a reconstruction; reported, never scored)
--                   FALSE = the nightly cron / a manual append (the honest record) — the DEFAULT; the cron
--                           never sets it
--
-- PROVENANCE, NOT a scoring input on the WRITE side: like `ingest_fresh` it lives OFF the CallCard (a field
-- IN the card would fake a change in `record_if_changed`'s `_canonical` compare), threaded to
-- `append`/`record_if_changed` separately. On the READ side it is the ONE filter the Scoreboard's record
-- path applies (`latest_for_thesis(include_reconstructed=False)`, `scoreboard/record.py`): a reconstructed
-- row never defines an episode boundary. Every other reader keeps the default (sees every row).
--
-- THE LEGACY STAMP. The rows the backfill already wrote carry no marker, so this migration stamps them by
-- the exact derived rule above — and asserts the stamped count equals the derived count, so a partial
-- update can never pass silently. `calls` carries the `no_update` BEFORE UPDATE trigger ("never rewrite a
-- recorded call"). This UPDATE rewrites nothing the call SAYS — card, state, verdict, asof, recorded_at
-- are untouched; it sets a provenance flag on rows that were always reconstructions — so the trigger is
-- disabled for this ONE statement and re-enabled in the same transaction. The migration runner executes
-- the whole file in ONE transaction: a failed assertion (or anything else) rolls back the stamp AND the
-- trigger-disable together, so there is no committed state with the guard off. The alternative — leaving
-- the derived rule alive inside the scoring read for legacy rows — would keep a heuristic (with the lag-1
-- hole) in exactly the path this column exists to make explicit; a one-time stamp retires it.
--
-- Idempotent: `NOT reconstructed` in the predicate makes a re-fire over stamped rows a zero-row no-op
-- (the test re-executes the file over legacy-shaped rows; the runner tracks it once).

ALTER TABLE calls ADD COLUMN IF NOT EXISTS reconstructed boolean NOT NULL DEFAULT false;

DO $$
DECLARE
    expected bigint;
    stamped  bigint;
BEGIN
    SELECT count(*) INTO expected
      FROM calls
     WHERE NOT reconstructed
       AND ingest_fresh IS NULL
       AND (recorded_at AT TIME ZONE 'UTC')::date - asof > 1;

    ALTER TABLE calls DISABLE TRIGGER no_update;
    UPDATE calls
       SET reconstructed = true
     WHERE NOT reconstructed
       AND ingest_fresh IS NULL
       AND (recorded_at AT TIME ZONE 'UTC')::date - asof > 1;
    GET DIAGNOSTICS stamped = ROW_COUNT;
    ALTER TABLE calls ENABLE TRIGGER no_update;

    IF stamped <> expected THEN
        RAISE EXCEPTION
            'calls.reconstructed legacy stamp: derived % row(s) but stamped % — rolling back',
            expected, stamped;
    END IF;
    RAISE NOTICE 'calls.reconstructed: stamped % legacy reconstructed row(s)', stamped;
END
$$;
