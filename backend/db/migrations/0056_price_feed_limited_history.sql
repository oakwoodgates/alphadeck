-- Alpha Deck — a KNOWN FEED LIMITATION on the price-feed decision log (2026-10-21): a second kind of
-- operator acknowledgment on ``price_feed_ack`` (0046), ``action = 'limited_history'``.
--
-- WHAT IT RECORDS. The operator's ratified statement that the price VENDOR serves no history for a name's
-- symbol: its chart returns only the current session's bar, under every range, so the tape is built one
-- live bar per night and can never be restated, repaired or backfilled from the vendor (a night the pass
-- missed stays a hole). MEASURED 2026-10-21 on SCE-PG (a Southern California Edison preferred in a basket):
-- Yahoo returns exactly ONE bar under every range (2y, ytd; ``firstTradeDate`` 2026-07-17), the named-bar
-- repair reported ``no_vendor_bar`` for its pre-settle 08-27 and 10-13 bars, and 8 session bars between
-- 2026-08-20 and 10-20 that SPY has (08-31, 09-01, 09-04, 09-08, 09-09, 09-29, 09-30, 10-12) are permanent
-- holes in its stored tape. The operator chose to MARK it rather than find another source.
--
-- WHY A NEW ``action`` VALUE, NOT A ``kind`` COLUMN. Every existing reader of this table selects
-- ``action = 'acknowledge'`` — the nightly pass's quiet-unpriceable set (Q8) and its expected-404 excuse
-- (T3), the Admin "acknowledged" list, the acknowledge idempotency read and the revoke target. A new
-- action value is therefore INVISIBLE to the Q8/T3 quieting machinery BY CONSTRUCTION: a limited-history
-- mark can never quiet a stale row or excuse a vendor 404 through a reader that forgot a filter. A ``kind``
-- column with a default would have needed a new filter on every one of those readers, and the one that
-- forgot it would fail QUIET. The mark quiets nothing on the nightly path; it only lets the readers that
-- would otherwise be loud SOLELY because the vendor lacks history say so (repositories/price_feed_repo.py).
--
-- The rest of 0046's shape carries over unchanged: append-only (the ``no_update`` trigger), the inverse is
-- a ``revoke`` row naming the mark (``revokes``; the pairing CHECK, the one-revoke-per-row unique index and
-- the same-security / never-a-revoke trigger all apply as they are), ``basis`` names the author
-- (``operator:limited-history`` / ``operator:revoke``), ``reason`` is required, ``detail`` keeps the
-- vendor pull that evidenced the mark.
--
-- Additive + idempotent: only the action CHECK widens (DROP IF EXISTS, then ADD under the same name
-- Postgres gave 0046's inline constraint — MEASURED on prod 2026-10-21: ``price_feed_ack_action_check``).
-- Every stored row (``acknowledge`` / ``revoke``) satisfies the wider check.

ALTER TABLE price_feed_ack DROP CONSTRAINT IF EXISTS price_feed_ack_action_check;
ALTER TABLE price_feed_ack ADD CONSTRAINT price_feed_ack_action_check
    CHECK (action IN ('acknowledge', 'limited_history', 'revoke'));
