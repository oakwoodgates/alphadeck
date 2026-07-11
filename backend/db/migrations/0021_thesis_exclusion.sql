-- 0021 — thesis_exclusion: the operator's durable NO (#7, gate-1 ratified 2026-07-11).
--
-- Excluding a name in the editor was FE-only run state: Save simply didn't send it, and the next
-- re-draft re-surfaced every pruned name with nowhere to record WHY. This table makes the pruning
-- durable: the CURRENT exclusion set per thesis, each row optionally carrying the operator's
-- rejection reason ("rejected because X" — attribution-adjacent data the Scoreboard can later join:
-- names you rejected that went on to run).
--
-- THE #9 LINE: discovery/classify NEVER filters on this table — recall stays sacred; a re-draft
-- still surfaces excluded names. The EDITOR applies the set as pre-seeded, VISIBLY-greyed state
-- (one click re-includes — reversible, never vanished). Precision stays the operator's delete,
-- never a silent filter.
--
-- Sole writer: thesis_repo.set_exclusions (full-list replace — the operator edits the set as a
-- list; the term_set structural guard: upsert never names this table, so a promote can't wipe it).
-- Keyed by security_id (the canonical spine id); unresolved names (no master row) stay FE-only in
-- v1 — a flagged scope cut, not an accident.
CREATE TABLE IF NOT EXISTS thesis_exclusion (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenant (id),
    thesis_id     uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    security_id   uuid NOT NULL REFERENCES security_master (id),
    ticker        text,           -- denormalized display convenience (the list renders without a master join)
    reason        text,           -- the operator's "rejected because X" — optional, always
    recorded_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (thesis_id, security_id)
);

CREATE INDEX IF NOT EXISTS ix_thesis_exclusion_thesis ON thesis_exclusion (tenant_id, thesis_id);
