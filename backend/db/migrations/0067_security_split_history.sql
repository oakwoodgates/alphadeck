-- Alpha Deck — security_split_history: which companies have had their SPLIT HISTORY filled (2026-10-24).
--
-- WHY. The split list (`security_split`, 0052) was filled ONCE, by the S1 backfill of 2026-10-20 over the basket
-- union of that day (Polygon's split reference since 2024-10-01, each split checked against the company's own SEC
-- filing). The nightly only looks back 30 days (Polygon) / 60 days (SEC filings), and nothing filled the history of
-- a company that JOINED a basket afterwards. MEASURED on prod 2026-10-24 (read-only): 32 splits Polygon lists since
-- 2025-03 on 29 companies were missing from the list — every one on a company that joined a basket after the
-- backfill (CRWD 4-for-1, POWL 3-for-1, LHSW 1-for-16, ABTS 1-for-15 …); companies in a basket at the backfill:
-- 0 missing. `pipeline/split_history.py` now runs the S1 backfill, scoped to each live basket member with NO row
-- here, outside the nightly window (inside the staged onboarding job's runs) — fast-lane promotes included.
--
-- ONE ROW PER (tenant, security): a company's split history is the company's, shared by every thesis that holds
-- it. OPERATIONAL state (like `security_onboarding`, 0059), updated in place only for a retry; never a fact.
--
--   since           — the reference pull's first execution date (2024-10-01, S1's `DEFAULT_SINCE`).
--   source          — who filled it: `s1_backfill` (this migration's seed: covered by the 2026-10-20 backfill or a
--                     later backfill run), `newcomer` (the job), `cli` (`python -m pipeline.backfill_splits --apply`).
--   complete        — false when the fill could not read everything (the company's SEC filing index, or a filing
--                     in a split's window): the job retries it, at most `pipeline/split_history.MAX_ATTEMPTS` runs.
--   attempts        — the runs that filled it (1 for a complete first fill).
--   polygon_splits  — the Polygon split dates the fill decided for this company; appended — the list versions it
--                     appended (0 when the list already held them, or there were none).
--   note            — what was incomplete, operator-facing.
--
-- THE SEED (the grandfathered companies — never re-filled by the job; the split list already holds their history,
-- or the operator's own backfill run fills it):
--   1. every security in a live basket at the S1 backfill's apply instant — the earliest `recorded_at` of a
--      `system:backfill_splits` version — read from that instant's basket snapshot (0043) per thesis; a thesis
--      archived since counts (it was live then), a thesis created later does not;
--   2. every security that has any `system:backfill_splits` version (a later backfill run covered it).
-- A fresh database (no backfill version) seeds nothing, so every member is filled by the job. Idempotent:
-- CREATE ... IF NOT EXISTS and an ON CONFLICT DO NOTHING seed. ON DELETE CASCADE from security_master.

CREATE TABLE IF NOT EXISTS security_split_history (
    tenant_id       uuid        NOT NULL REFERENCES tenant (id),
    security_id     uuid        NOT NULL REFERENCES security_master (id) ON DELETE CASCADE,
    since           date        NOT NULL,
    source          text        NOT NULL CHECK (source IN ('s1_backfill', 'newcomer', 'cli')),
    complete        boolean     NOT NULL DEFAULT true,
    attempts        integer     NOT NULL DEFAULT 1,
    polygon_splits  integer     NOT NULL DEFAULT 0,
    appended        integer     NOT NULL DEFAULT 0,
    note            text,
    filled_at       timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, security_id)
);

WITH cutoff AS (
    SELECT min(recorded_at) AS at FROM security_split WHERE author = 'system:backfill_splits'
),
roster AS (
    SELECT DISTINCT t.tenant_id, (m.value ->> 'security_id')::uuid AS security_id
    FROM cutoff c
    JOIN thesis t ON c.at IS NOT NULL AND (t.archived_at IS NULL OR t.archived_at > c.at)
    JOIN LATERAL (
        SELECT bs.members FROM basket_snapshot bs
         WHERE bs.thesis_id = t.id AND bs.taken_at <= c.at
         ORDER BY bs.taken_at DESC
         LIMIT 1
    ) s ON true
    CROSS JOIN LATERAL jsonb_array_elements(s.members) AS m(value)
    WHERE m.value ->> 'security_id' IS NOT NULL
),
covered AS (
    SELECT tenant_id, security_id FROM roster
    UNION
    SELECT DISTINCT tenant_id, security_id FROM security_split WHERE author = 'system:backfill_splits'
)
INSERT INTO security_split_history (tenant_id, security_id, since, source, note, filled_at)
SELECT cv.tenant_id, cv.security_id, DATE '2024-10-01', 's1_backfill',
       'covered by the S1 backfill (2026-10-20) or a later backfill run — seeded by migration 0067',
       (SELECT at FROM cutoff)
FROM covered cv
JOIN security_master sm ON sm.id = cv.security_id AND sm.tenant_id = cv.tenant_id
ON CONFLICT (tenant_id, security_id) DO NOTHING;
