-- Alpha Deck — security_onboarding: the STAGED COLD-INGEST queue (X1, 2026-10-21).
--
-- WHY. A COLD name (its first ingest: the whole Form 4 history, the 13D/G identities, 8-Ks, prices,
-- fundamentals) costs 300-600 s (MEASURED 2026-10-21: median 396 s, n=10; prod's X5 average was ~110 s), a warm
-- one ~3.5 s. The 2026-09-01 pass that onboarded many
-- names at once took 21,632 s; the bounded pass now stops at 5,400 s, so a big onboarding inside the 22:30 pass
-- would time the night out. The history load therefore moves to its OWN job (`pipeline/onboarding.py`), run by
-- the cron sidecar OUTSIDE the nightly window, resumable, in batches. This table is that job's memory.
--
-- ONE ROW PER (tenant, security). Onboarding is about a COMPANY's facts, which are per security and shared by
-- every thesis that holds it — never per basket row. A row exists only for a security the onboarding path was
-- asked to load (a Workbench promote that added a name no other live basket holds, or the CLI); a security
-- with NO row is "legacy" and the nightly treats it exactly as before X1.
--
--   status       — 'queued' (waiting, or a retryable failure), 'in_progress' (a runner started it; a crash
--                  leaves it here and the next run RESUMES it), 'done' (every HISTORY leg completed — the name
--                  is warm), 'failed' (the history legs failed `Settings.onboard_max_attempts` times — loud on
--                  Admin; the operator's Retry puts it back to 'queued').
--   legs_done    — the per-name CHECKPOINT: the legs that completed (a resumed run skips them). The history
--                  legs gate 'done' (form4 incl. 4/A, form8k, schedule13); the tail legs (price, fund_shares,
--                  fundamentals) run for completeness and never block it — the nightly runs them every night.
--   attempts     — how many runs FAILED a history leg for this name (a yield to the nightly is not a failure).
--   last_error   — the latest leg failure(s), operator-facing text; NULL once a run finishes it cleanly.
--   source       — who enqueued it: 'promote' (the Workbench) or 'cli'.
--   thesis_id    — the thesis whose promote enqueued it (display only; SET NULL if that thesis goes).
--   enqueued_at / last_attempt_at / finished_at / updated_at — the timeline (diagnostics + the daily cap).
--
-- WHAT IT IS NOT. Not a fact and not history: OPERATIONAL state, updated in place like
-- `price_restatement_hold` (0047). Idempotent by construction: the primary key makes an enqueue an
-- INSERT ... ON CONFLICT DO NOTHING (a re-enqueue never duplicates and never resets a 'done' row), and every
-- fact the job writes goes through the incremental, append-if-changed ingest legs (a resumed run appends
-- nothing twice — the tests COUNT THE TABLES).
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS). ON DELETE CASCADE from security_master: operational state
-- never blocks a master delete.

CREATE TABLE IF NOT EXISTS security_onboarding (
    tenant_id        uuid        NOT NULL REFERENCES tenant (id),
    security_id      uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    status           text        NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'in_progress', 'done', 'failed')),
    legs_done        text[]      NOT NULL DEFAULT '{}',
    attempts         integer     NOT NULL DEFAULT 0,
    last_error       text,
    source           text        NOT NULL,
    thesis_id        uuid        REFERENCES thesis (id) ON DELETE SET NULL,
    enqueued_at      timestamptz NOT NULL DEFAULT now(),
    last_attempt_at  timestamptz,
    finished_at      timestamptz,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, security_id)
);

-- the job's work list (the not-done rows, oldest first) and the Admin counts
CREATE INDEX IF NOT EXISTS security_onboarding_status_idx
    ON security_onboarding (status, enqueued_at);
