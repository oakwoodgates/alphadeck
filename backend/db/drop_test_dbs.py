"""Opt-in cleanup for per-worktree test DBs — drop the ``alphadeck_test*`` DBs a deleted worktree leaves.

NEVER automatic: a concurrent worktree may hold one of these DBs open, so dropping is a deliberate,
operator-run maintenance step (``python -m db.drop_test_dbs``, a sibling of ``db.migrate``). A DB currently
in use is logged and SKIPPED — this never force-terminates a peer worktree's sessions. The demo
``alphadeck`` cannot match the ``alphadeck_test%`` pattern, and is refused by exact name as a
belt-and-braces second line. Importing this module has no side effects and opens no connection.
"""

from __future__ import annotations

import argparse
import logging
import sys

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from db.session import database_url
from db.testdb import _with_dbname

log = logging.getLogger("alphadeck.testdb")

_TEST_DB_PREFIX = "alphadeck_test"


def _list_test_dbs(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s ORDER BY datname",
            (_TEST_DB_PREFIX + "%",),
        )
        return [row["datname"] for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drop per-worktree alphadeck_test* databases.")
    parser.add_argument("--dry-run", action="store_true", help="list the DBs without dropping them")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # CREATE/DROP DATABASE can't run in a txn -> autocommit on the 'postgres' maintenance DB (same creds).
    maint = _with_dbname(database_url(), "postgres")
    with psycopg.connect(maint, autocommit=True, row_factory=dict_row) as conn:
        names = _list_test_dbs(conn)
        if not names:
            log.info("no alphadeck_test* databases found")
            return 0
        for name in names:
            if name == "alphadeck":  # belt: the LIKE can't match it, refuse by name anyway
                continue
            if args.dry_run:
                log.info("would drop %s", name)
                continue
            try:
                conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                log.info("dropped %s", name)
            except psycopg.errors.ObjectInUse:  # never force-terminate a peer worktree's sessions
                log.info("skipped %s (in use by another session)", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
