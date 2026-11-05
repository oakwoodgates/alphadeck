-- Alpha Deck — the ETFs link: move every stored fund row into it.
--
-- WHY. ETFs is the third fixed silent link (after Context, before Bench): it holds a card's funds, and a row
-- there never counts toward the call (`domain.thesis.voting_thesis`). Until now a fund added by "Surface an
-- ETF" landed with no link (the Discovered floor), which VOTES — so a fund's own price breakout could warm a
-- card, lead the laggard read and, on a card with a live theme conviction, take the broadcast and arm the card
-- on the fund itself. From this change the promote refuses a fund anywhere but ETFs (and anything but a fund in
-- ETFs). This migration puts the stored rows where that rule says they go.
--
-- WHAT. A fund = a basket row whose `security_master` row is `instrument_kind = 'etf'`. Every such row whose
-- link is not exactly 'ETFs' is moved there; everything else about the row (role, description, authorship,
-- sign-off, conviction, the context role) is kept. Segments need no change: the hydrate mapper drops any stored
-- segment named like a fixed link and appends the three fixed ones, so every card reads ETFs back on load.
--
-- REFUSES, never deletes (a human resolves, then re-runs):
--   1. a card that holds the same fund in MORE THAN ONE row — moving both into ETFs would make a duplicate the
--      promote refuses, and choosing which row to keep is not a migration's call;
--   2. a row that is NOT a fund (or has no security at all) sitting in a link named "ETFs" in any case — that
--      link becomes the fixed silent one, so the row would quietly stop counting toward the call.
--
-- THE LIVE CALL READS THE ROSTER SNAPSHOT (`basket_snapshot`, 0043: `thesis_repo.get_asof(now)` reads the
-- latest one), so a card whose rows moved gets ONE new snapshot of its roster, `taken_at = now()`, built in the
-- exact shape `thesis_repo._member_to_snapshot` writes (the `context_role` key only when set, so the content
-- hash matches what the next promote of the same roster computes — no redundant row later). Earlier snapshots
-- are history and are left alone: a past night is still judged on the placement the card had then (#1).
--
-- Idempotent: a second run finds no fund outside ETFs, moves nothing and writes no snapshot.

DO $$
DECLARE
    offenders text;
BEGIN
    -- 1. the same fund in more than one row of one card
    SELECT string_agg(format('thesis %s: %s (%s rows)', d.thesis_id, d.ticker, d.n), '; ')
    INTO offenders
    FROM (
        SELECT bm.thesis_id, min(bm.ticker) AS ticker, count(*) AS n
        FROM basket_member bm
        JOIN security_master sm ON sm.id = bm.security_id
        WHERE sm.instrument_kind = 'etf'
        GROUP BY bm.thesis_id, bm.security_id
        HAVING count(*) > 1
    ) d;
    IF offenders IS NOT NULL THEN
        RAISE EXCEPTION
            '0073_etfs_link: a card holds the same fund in more than one row; keep one row per fund on each '
            'card, then re-run: %', offenders;
    END IF;

    -- 2. a row that is not a fund in a link named like ETFs
    SELECT string_agg(format('thesis %s: %s (in %s)', bm.thesis_id, bm.ticker, bm.segment), '; ')
    INTO offenders
    FROM basket_member bm
    LEFT JOIN security_master sm ON sm.id = bm.security_id
    WHERE lower(btrim(bm.segment)) = 'etfs'
      AND (sm.id IS NULL OR sm.instrument_kind IS DISTINCT FROM 'etf');
    IF offenders IS NOT NULL THEN
        RAISE EXCEPTION
            '0073_etfs_link: a row that is not a fund sits in a link named ETFs, which is now the fixed funds '
            'link; move it to another link first, then re-run: %', offenders;
    END IF;
END $$;

-- the cards whose rows move (read BEFORE the move, so the snapshot step below knows which to re-snapshot)
DROP TABLE IF EXISTS pg_temp._etfs_link_moved;
CREATE TEMP TABLE _etfs_link_moved ON COMMIT DROP AS
SELECT DISTINCT bm.thesis_id
FROM basket_member bm
JOIN security_master sm ON sm.id = bm.security_id
WHERE sm.instrument_kind = 'etf'
  AND bm.segment IS DISTINCT FROM 'ETFs';

UPDATE basket_member bm
SET segment = 'ETFs'
FROM security_master sm
WHERE sm.id = bm.security_id
  AND sm.instrument_kind = 'etf'
  AND bm.segment IS DISTINCT FROM 'ETFs';

-- one roster snapshot per moved card, in `_member_to_snapshot`'s shape; skipped when it equals the card's
-- latest snapshot (the repo write's own dedup)
INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash)
SELECT gen_random_uuid(), t.tenant_id, t.id, now(), m.members, md5(m.members::text)
FROM thesis t
JOIN _etfs_link_moved mv ON mv.thesis_id = t.id
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
            || CASE
                   WHEN bm.context_role IS NULL THEN '{}'::jsonb
                   ELSE jsonb_build_object('context_role', bm.context_role)
               END
            ORDER BY bm.ordinal
        ),
        '[]'::jsonb
    ) AS members
    FROM basket_member bm
    WHERE bm.thesis_id = t.id
) m ON true
WHERE md5(m.members::text) IS DISTINCT FROM (
    SELECT s.content_hash FROM basket_snapshot s
    WHERE s.thesis_id = t.id ORDER BY s.taken_at DESC, s.id DESC LIMIT 1
);
