"""Migration 0214 — ``api_token.scopes``: every existing personal token becomes read-only with its role's scopes.

On the migrated test database: the column and its check exist. Then (the 0079 precedent: the runner applies each
file ONCE, so the test re-executes the FILE's SQL over the old shape) the column is dropped inside the test's
transaction, tokens of a plain user, a steward and a disabled steward are written (one revoked), and 0214's SQL
runs: each token holds its user's role scopes, revoked and disabled ones included; a second run changes nothing (the
table is COUNTED and checksummed, not just read). The check refuses a scope set an agent may not hold and keeps
NULL (a full token, minted later with ``--write``). Everything is rolled back at the end (DDL is transactional).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from tests.workspaces import new_workspace

_SQL = (MIGRATIONS_DIR / "0214_v2_api_token_scopes.sql").read_text(encoding="utf-8")


def _user(cur, workspace: uuid.UUID, role: str, *, disabled: bool = False) -> uuid.UUID:
    uid = uuid.uuid4()
    cur.execute(
        "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id, disabled_at) "
        "VALUES (%s, %s, 'u', 'x', %s, %s, CASE WHEN %s THEN now() END)",
        (uid, f"{uid.hex}@example.test", role, workspace, disabled),
    )
    return uid


def _token(cur, user_id: uuid.UUID, *, revoked: bool = False) -> uuid.UUID:
    """A token row in the pre-0214 shape (no scopes column)."""
    tid = uuid.uuid4()
    cur.execute(
        "INSERT INTO api_token (id, user_id, name, token_hash, revoked_at) "
        "VALUES (%s, %s, 'old', %s, CASE WHEN %s THEN now() END)",
        (tid, user_id, uuid.uuid4().hex, revoked),
    )
    return tid


def _scopes(cur) -> dict[uuid.UUID, list[str] | None]:
    cur.execute("SELECT id, scopes FROM api_token")
    return {r["id"]: r["scopes"] for r in cur.fetchall()}


def _checksum(cur) -> str:
    cur.execute(
        "SELECT count(*)::text || md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) AS h "
        "FROM api_token t"
    )
    return cur.fetchone()["h"]


def test_0214_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT data_type, udt_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'api_token' AND column_name = 'scopes'"
        )
        assert cur.fetchone() == {"data_type": "ARRAY", "udt_name": "_text", "is_nullable": "YES"}
        cur.execute(
            "SELECT count(*) AS n FROM pg_constraint "
            "WHERE conname = 'api_token_scopes_read_only' AND conrelid = 'api_token'::regclass"
        )
        assert cur.fetchone()["n"] == 1
        cur.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE filename = '0214_v2_api_token_scopes.sql'"
        )
        assert cur.fetchone()["n"] == 1


def test_0214_makes_every_existing_token_read_only_with_its_users_role(db):
    try:
        with db.cursor() as cur:
            cur.execute("TRUNCATE app_user CASCADE")
            cur.execute("ALTER TABLE api_token DROP COLUMN scopes")  # the pre-0214 shape
            plain = _user(cur, new_workspace(db, "0214 plain"), "user")
            steward = _user(cur, new_workspace(db, "0214 steward"), "super_admin")
            off = _user(cur, new_workspace(db, "0214 off"), "super_admin", disabled=True)
            ids = {
                "plain": _token(cur, plain),
                "plain revoked": _token(cur, plain, revoked=True),
                "steward": _token(cur, steward),
                "steward revoked": _token(cur, steward, revoked=True),
                "disabled steward": _token(cur, off),
            }

            cur.execute(_SQL)
            got = _scopes(cur)
            assert {name: got[tid] for name, tid in ids.items()} == {
                "plain": ["read"],
                "plain revoked": ["read"],
                "steward": ["read", "steward"],
                "steward revoked": ["read", "steward"],
                "disabled steward": ["read", "steward"],
            }

            before = _checksum(cur)
            cur.execute(
                _SQL
            )  # a second run: the runner never makes one, and it would change nothing
            assert _checksum(cur) == before
            cur.execute(
                "SELECT count(*) AS n FROM pg_constraint "
                "WHERE conname = 'api_token_scopes_read_only' AND conrelid = 'api_token'::regclass"
            )
            assert cur.fetchone()["n"] == 1
    finally:
        db.rollback()


@pytest.mark.parametrize(
    "scopes,allowed",
    [
        (None, True),  # a full token (the CLI's --write)
        (["read"], True),
        (["read", "steward"], True),
        (["write"], False),
        (["steward"], False),
        ([], False),
        (["read", "write"], False),
        (["read", None], False),
    ],
)
def test_0214_check_keeps_a_token_to_the_scopes_an_agent_may_hold(db, scopes, allowed):
    try:
        with db.cursor() as cur:
            uid = _user(cur, new_workspace(db, "0214 check"), "super_admin")
            insert = (
                "INSERT INTO api_token (id, user_id, name, token_hash, scopes) "
                "VALUES (%s, %s, 'x', %s, %s)"
            )
            args = (uuid.uuid4(), uid, uuid.uuid4().hex, scopes)
            if allowed:
                cur.execute(insert, args)
            else:
                with pytest.raises(psycopg.errors.CheckViolation):
                    cur.execute(insert, args)
    finally:
        db.rollback()
