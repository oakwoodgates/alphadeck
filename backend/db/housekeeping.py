"""The nightly prune of expired sign-in rows (security PR 5, docs/AUTH.md §Housekeeping): ``python -m db.housekeeping
--prune [--dry-run]``.

Nothing else ever deletes from the sign-in tables, so the rows an unauthenticated caller can make (a pending sign-in
request, a self-registered client) and the ones every sign-in leaves behind (sessions, codes, tokens) would pile up
for ever. The rules (plan D5 and D6), each a number of days past the row's expiry or revocation
(``ALPHADECK_PRUNE_*_DAYS``):

- ``oauth_code``, ``oauth_access_token``, ``oauth_refresh_token``: expired more than ``prune_oauth_days`` (1) ago. A
  reused code or refresh token presented after that is simply invalid (it expired long before) instead of also
  revoking its grant;
- ``oauth_consent``: UNDECIDED and expired more than ``prune_oauth_days`` ago. A decided one is kept at any age: it
  records who allowed what, and only a signed-in user can make one;
- ``oauth_client``: SELF-REGISTERED (``registration='dcr'``) clients older than ``prune_dcr_client_days`` (7) with no
  grant (live or revoked) and no remaining consent. Only unauthenticated callers make those. A built-in or
  steward-added (``static``) client is never deleted;
- ``user_session``: expired, or revoked, more than ``prune_session_days`` (7) ago (no screen shows them);
- ``user_action_count`` (the daily allowance counts): market days more than ``prune_allowance_days`` (7) ago.

Never deleted: ``oauth_grant`` and ``api_token`` (Settings shows revoked ones grayed: "pruning hides, it never
vanishes"), and ``app_user``.

One transaction, in that order (a client's consents go before it); one printed line with the count per table.
``--dry-run`` runs the same deletes and rolls them back, so its counts are exact. The cron sidecar runs it after
each nightly pass, right after the change-log prune, bounded by ``timeout`` and fail-open
(``scripts/daily_cron.sh`` ``prune_expired``).
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import psycopg

from domain import market_time
from domain.settings import get_settings


def _delete(conn: psycopg.Connection, sql: str, *params: object) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def prune(conn: psycopg.Connection, *, today: date | None = None) -> dict[str, int]:
    """Delete the expired sign-in rows in the caller's transaction (the caller commits or rolls back); returns
    the count deleted per table, in the order deleted."""
    s = get_settings()
    oauth = timedelta(days=s.prune_oauth_days)
    session = timedelta(days=s.prune_session_days)
    dcr = timedelta(days=s.prune_dcr_client_days)
    market_day = (today or market_time.market_today()) - timedelta(days=s.prune_allowance_days)
    out: dict[str, int] = {}
    for table in ("oauth_code", "oauth_access_token", "oauth_refresh_token"):
        out[table] = _delete(
            conn,
            f"DELETE FROM {table} WHERE expires_at < now() - %s",  # noqa: S608 - a fixed table name
            oauth,
        )
    out["oauth_consent"] = _delete(
        conn,
        "DELETE FROM oauth_consent WHERE decided_at IS NULL AND expires_at < now() - %s",
        oauth,
    )
    out["oauth_client"] = _delete(
        conn,
        "DELETE FROM oauth_client c WHERE c.registration = 'dcr' AND c.created_at < now() - %s "
        "AND NOT EXISTS (SELECT 1 FROM oauth_grant g WHERE g.client_id = c.client_id) "
        "AND NOT EXISTS (SELECT 1 FROM oauth_consent k WHERE k.client_id = c.client_id)",
        dcr,
    )
    out["user_session"] = _delete(
        conn,
        "DELETE FROM user_session WHERE expires_at < now() - %s OR revoked_at < now() - %s",
        session,
        session,
    )
    out["user_action_count"] = _delete(
        conn, "DELETE FROM user_action_count WHERE market_day < %s", market_day
    )
    return out


def _line(counts: dict[str, int]) -> str:
    return ", ".join(f"{table} {n}" for table, n in counts.items())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Delete expired sign-in rows (sessions, OAuth codes, tokens, stale requests, unused "
        "self-registered clients, old allowance counts)."
    )
    p.add_argument("--prune", action="store_true", help="delete the rows past each cut")
    p.add_argument(
        "--dry-run", action="store_true", help="print what would be deleted; delete nothing"
    )
    args = p.parse_args(argv)
    if not args.prune:
        p.error("nothing to do: pass --prune")
    from db.session import connect

    conn = connect()
    try:
        counts = prune(conn)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    if args.dry_run:
        print(f"housekeeping (dry run): would delete {_line(counts)}")
    else:
        print(f"housekeeping: deleted {_line(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
