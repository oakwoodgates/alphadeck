"""Migration 0079 — ``user_meta`` + the workspace webhook carried to its enabled users, then the column dropped.

The runner applies each file ONCE, so (the 0073 / 0078 precedent) this re-executes the FILE's SQL over the old
shape: ``tenant.notify_webhook`` re-added inside the test's transaction, a workspace with a webhook and two users
(one disabled), a workspace with no webhook and one user. Proven: exactly one ``user_meta`` row, for the enabled
user, carrying the URL; nothing for the workspace with no webhook; a second run adds nothing (COUNT THE TABLE);
the ``tenant`` column is gone. Everything is rolled back at the end (DDL is transactional in Postgres).
"""

from __future__ import annotations

import uuid

from db.migrate import MIGRATIONS_DIR
from tests.workspaces import new_workspace

_SQL = (MIGRATIONS_DIR / "0079_user_meta.sql").read_text(encoding="utf-8")
_URL = "https://hooks.slack.com/services/TEST0000/TEST0000/invented-for-tests"


def _user(cur, workspace: uuid.UUID, *, disabled: bool = False) -> uuid.UUID:
    uid = uuid.uuid4()
    cur.execute(
        "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id, disabled_at) "
        "VALUES (%s, %s, 'u', 'x', 'user', %s, CASE WHEN %s THEN now() END)",
        (uid, f"{uid.hex}@example.test", workspace, disabled),
    )
    return uid


def _has_column(cur) -> bool:
    cur.execute(
        "SELECT count(*) AS n FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'tenant' AND column_name = 'notify_webhook'"
    )
    return cur.fetchone()["n"] == 1


def _meta_count(cur) -> int:
    cur.execute("SELECT count(*) AS n FROM user_meta")
    return cur.fetchone()["n"]


def test_0079_carries_each_workspace_webhook_to_its_enabled_users(db):
    try:
        with db.cursor() as cur:
            assert not _has_column(cur)  # the runner already applied 0079 to the test database
            cur.execute("TRUNCATE app_user CASCADE")
            cur.execute(
                "ALTER TABLE tenant ADD COLUMN notify_webhook text NULL"
            )  # the pre-0079 shape
            with_hook = new_workspace(db, "ws with a webhook")
            without = new_workspace(db, "ws without one")
            cur.execute("UPDATE tenant SET notify_webhook = %s WHERE id = %s", (_URL, with_hook))
            enabled = _user(cur, with_hook)
            _user(cur, with_hook, disabled=True)
            _user(cur, without)

            cur.execute(_SQL)
            cur.execute("SELECT user_id, slack_webhook_url FROM user_meta")
            rows = cur.fetchall()
            assert rows == [{"user_id": enabled, "slack_webhook_url": _URL}]
            assert not _has_column(cur)

            cur.execute(_SQL)  # a second run: the column is gone, nothing is copied, nothing fails
            assert _meta_count(cur) == 1
            assert not _has_column(cur)
    finally:
        db.rollback()


def test_0079_with_no_webhook_anywhere_copies_nothing(db):
    try:
        with db.cursor() as cur:
            cur.execute("TRUNCATE app_user CASCADE")
            cur.execute("ALTER TABLE tenant ADD COLUMN notify_webhook text NULL")
            ws = new_workspace(db, "ws without one")
            _user(cur, ws)
            cur.execute(_SQL)
            assert _meta_count(cur) == 0
            assert not _has_column(cur)
    finally:
        db.rollback()
