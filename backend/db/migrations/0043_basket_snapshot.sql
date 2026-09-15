-- Alpha Deck — basket_snapshot: the roster's point-in-time record (F12, the SECOND bitemporal leak).
--
-- WHY. `basket_member` is OPERATIONAL current state: full-replace on every promote, no time axes (0003).
-- The FORWARD nightly record is already honest — the assembled CallCard freezes its derived active subset
-- (armed_members / watch_members / triggers_fired) into `calls.card` jsonb, immutable. But a RECOMPUTE of a
-- call at a PAST date re-reads the roster through `thesis_repo.get` (`SELECT * FROM basket_member ...`, no
-- as-of filter), so it runs on TODAY's basket — the roster on a past night is otherwise unknowable. So this
-- is a "recomputes lie," not a "the record is wrong," problem: the record froze the truth; the recompute
-- reconstructs a false roster. (Cf. `calls.reconstructed`/0042, which marks the OTHER unknowable-past-roster
-- case — a backfilled night — as never-scored.)
--
-- THE FIX. An ADDITIVE snapshot table (NOT full-bitemporal `basket_member`): one jsonb roster per promote,
-- keyed by `taken_at`. `thesis_repo.get_asof(known_at)` reads the latest snapshot with `taken_at <= known_at`
-- (no lookahead, #1) and hydrates the basket from it; the single assembly funnel (`pipeline.call_for_thesis`)
-- swaps `get` -> `get_asof(known_at)`, so serve / nightly record / `pipeline.run` / backfill all become
-- roster-as-of-known_at from one line. Pre-snapshot (a thesis promoted before this table existed) falls back
-- to the live roster. The snapshot is roster METADATA, never a call input (#3): it feeds nothing in
-- `signals/` or `calls/`, and no CallCard field changes (that would break `record_if_changed` idempotency).
--
-- CONTENT_HASH + DEDUP. The Workbench full-replaces the basket on EVERY interactive edit (a narrative-only
-- edit re-promotes an unchanged roster), so the on-promote write SKIPS when the new roster's hash equals the
-- thesis's latest snapshot hash. The hash is `md5(members::text)` computed by Postgres in BOTH this seed and
-- the repo write, over LOGICALLY-IDENTICAL jsonb (same keys, same value types) — jsonb normalizes key order,
-- so the two agree and the first post-deploy promote of an unchanged roster dedups against this seed rather
-- than writing a redundant row.
--
-- SEED. One snapshot per EXISTING thesis (archived included — the record is not erased by archiving) at
-- migration time, `taken_at = now()`, members = the current `basket_member` roster. So `get_asof(now)` == the
-- live roster immediately (the live/today path stays BYTE-IDENTICAL). Idempotent: `WHERE NOT EXISTS` a
-- snapshot for the thesis makes a re-run a zero-row no-op (the runner tracks the file once; this is the
-- belt-and-braces second line). An empty basket seeds `[]` (COALESCE over the zero-row aggregate), never NULL.
--
-- Additive + idempotent (CREATE ... IF NOT EXISTS; the seed is NOT-EXISTS-guarded).

CREATE TABLE IF NOT EXISTS basket_snapshot (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    thesis_id    uuid        NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    taken_at     timestamptz NOT NULL,                       -- the transaction time this roster was written
    members      jsonb       NOT NULL,                       -- the full roster: one object per basket_member row
    content_hash text        NOT NULL                        -- md5(members::text); the dedup fingerprint
);

CREATE INDEX IF NOT EXISTS ix_basket_snapshot_thesis_taken
    ON basket_snapshot (thesis_id, taken_at DESC);

-- Deploy-seed: one snapshot per existing thesis (only where none exists yet -> idempotent).
INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash)
SELECT gen_random_uuid(), t.tenant_id, t.id, now(), m.members, md5(m.members::text)
FROM thesis t
JOIN LATERAL (
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'ordinal',        bm.ordinal,
                'ticker',         bm.ticker,
                'role',           bm.role,
                'security_id',    bm.security_id,
                'detail',         bm.detail,
                'segment',        bm.segment,
                'thesis_fit',     bm.thesis_fit,
                'conviction',     bm.conviction,
                'surfaced_terms', to_jsonb(COALESCE(bm.surfaced_terms, ARRAY[]::text[])),
                'authored_by',    bm.authored_by,
                'signed_off',     bm.signed_off
            )
            ORDER BY bm.ordinal
        ),
        '[]'::jsonb
    ) AS members
    FROM basket_member bm
    WHERE bm.thesis_id = t.id
) m ON true
WHERE NOT EXISTS (SELECT 1 FROM basket_snapshot s WHERE s.thesis_id = t.id);
