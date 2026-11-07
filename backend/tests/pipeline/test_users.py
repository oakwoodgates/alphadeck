"""``pipeline.users`` — the steward's user and token CLI (the login door, docs/AUTH.md), tested through its functions
and its ``run`` entry on the test connection."""

from __future__ import annotations

import io
from uuid import uuid4

import pytest

from app import auth
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID
from pipeline import users


@pytest.fixture
def conn(db):
    with db.cursor() as cur:
        cur.execute(
            "TRUNCATE app_user CASCADE"
        )  # the auth tables are not in the shared fixture's truncate
    db.commit()
    yield db
    db.rollback()
    with db.cursor() as cur:  # the workspaces `create` made (the shared fixture keeps tenant rows)
        cur.execute("TRUNCATE app_user CASCADE")
        cur.execute("DELETE FROM tenant WHERE name LIKE %s", ("%'s workspace",))
    db.commit()


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a fixed name
        return cur.fetchone()["n"]


def test_create_generates_a_password_once_and_stores_only_its_hash(conn):
    uid, pw = users.create_user(conn, email="Ana@Example.test", display_name="Ana")
    conn.commit()
    assert pw is not None and len(pw) == users.GENERATED_PASSWORD_LEN
    row = auth.find_user_by_email(conn, "ana@example.test")
    assert row["id"] == uid and row["role"] == "user"
    # the default: a NEW workspace of the user's own, never the founding one and never the data tenant
    assert row["workspace_id"] not in (FOUNDING_WORKSPACE_ID, DATA_TENANT_ID)
    with conn.cursor() as cur:
        cur.execute("SELECT name, kind FROM tenant WHERE id = %s", (row["workspace_id"],))
        ws = cur.fetchone()
    assert ws == {"name": "Ana's workspace", "kind": "workspace"}
    assert row["password_hash"].startswith("$argon2id$") and pw not in row["password_hash"]
    assert auth.verify_password(row["password_hash"], pw)


def test_a_second_create_of_the_same_email_refuses_and_never_overwrites(conn):
    users.create_user(
        conn, email="bo@example.test", display_name="Bo", password="first passphrase!"
    )
    conn.commit()
    with pytest.raises(users.UserError, match="already exists"):
        users.create_user(conn, email="BO@example.test", display_name="Other", password="x" * 20)
    conn.rollback()
    row = auth.find_user_by_email(conn, "bo@example.test")
    assert row["display_name"] == "Bo" and auth.verify_password(
        row["password_hash"], "first passphrase!"
    )
    assert _count(conn, "app_user") == 1


def test_create_super_admin_on_a_named_workspace_and_a_short_password_refuses(conn):
    uid, pw = users.create_user(
        conn,
        email="st@example.test",
        display_name="Steward",
        super_admin=True,
        workspace_id=FOUNDING_WORKSPACE_ID,
        password="a long enough passphrase",
    )
    assert pw is None
    assert auth.find_user_by_email(conn, "st@example.test")["role"] == "super_admin"
    with pytest.raises(users.UserError, match="at least 12"):
        users.create_user(conn, email="x@example.test", display_name="X", password="short")


def test_disable_enable_and_reset_password(conn):
    uid, _ = users.create_user(conn, email="c@example.test", display_name="C")
    auth.create_session(conn, uid)
    auth.create_session(conn, uid)
    conn.commit()
    users.set_disabled(conn, "c@example.test", True)
    assert auth.find_user_by_email(conn, "c@example.test")["disabled_at"] is not None
    users.set_disabled(conn, "c@example.test", False)
    assert auth.find_user_by_email(conn, "c@example.test")["disabled_at"] is None
    new = users.reset_password(conn, "c@example.test")
    conn.commit()
    assert auth.verify_password(
        auth.find_user_by_email(conn, "c@example.test")["password_hash"], new
    )
    listed = users.list_users(conn)
    assert listed[0]["sessions"] == 0  # a reset signs every session out
    assert "password_hash" not in listed[0]
    with pytest.raises(users.UserError, match="no user"):
        users.set_disabled(conn, "nobody@example.test", True)


def test_tokens_mint_once_and_revoke(conn):
    users.create_user(conn, email="t@example.test", display_name="T")
    tid, token = users.create_token(conn, "t@example.test", "mcp")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT token_hash, name FROM api_token WHERE id = %s", (tid,))
        row = cur.fetchone()
    assert row["name"] == "mcp" and row["token_hash"] == auth.token_hash(token) != token
    users.revoke_token(conn, tid)
    with pytest.raises(users.UserError, match="no live token"):
        users.revoke_token(conn, tid)
    with pytest.raises(users.UserError, match="no live token"):
        users.revoke_token(conn, uuid4())


def test_the_cli_entry_commits_prints_secrets_once_and_refuses_cleanly(conn):
    out: list[str] = []
    rc = users.run(
        ["create", "--email", "cli@example.test", "--display-name", "Cli", "--super-admin"],
        conn,
        emit=out.append,
    )
    assert rc == 0 and sum("shown once" in line for line in out) == 1
    out.clear()
    rc = users.run(
        ["create", "--email", "cli@example.test", "--display-name", "Again"], conn, emit=out.append
    )
    assert rc == 1 and out[0].startswith("refused:")
    out.clear()
    rc = users.run(
        ["reset-password", "--email", "cli@example.test", "--password-stdin"],
        conn,
        emit=out.append,
        stdin=io.StringIO("a stdin passphrase\n"),
    )
    assert rc == 0 and not any("shown once" in line for line in out)
    assert auth.verify_password(
        auth.find_user_by_email(conn, "cli@example.test")["password_hash"], "a stdin passphrase"
    )
    out.clear()
    assert (
        users.run(
            ["token", "create", "--email", "cli@example.test", "--name", "ops"],
            conn,
            emit=out.append,
        )
        == 0
    )
    assert sum("shown once" in line for line in out) == 1
    out.clear()
    assert users.run(["list"], conn, emit=out.append) == 0
    assert len(out) == 1 and "cli@example.test" in out[0] and "super_admin" in out[0]
    assert "argon2" not in out[0] and "tokens 1" in out[0]


def test_two_default_creates_get_two_separate_workspaces(conn):
    users.create_user(conn, email="a1@example.test", display_name="Same test")
    users.create_user(conn, email="a2@example.test", display_name="Same test")
    a = auth.find_user_by_email(conn, "a1@example.test")["workspace_id"]
    b = auth.find_user_by_email(conn, "a2@example.test")["workspace_id"]
    assert a != b


def test_founding_and_an_explicit_workspace_attach_and_the_flags_exclude_each_other(conn):
    uid, _ = users.create_user(conn, email="f@example.test", display_name="F", founding=True)
    assert auth.find_user_by_email(conn, "f@example.test")["workspace_id"] == FOUNDING_WORKSPACE_ID
    users.create_user(
        conn, email="w@example.test", display_name="W", workspace_id=FOUNDING_WORKSPACE_ID
    )
    assert auth.find_user_by_email(conn, "w@example.test")["workspace_id"] == FOUNDING_WORKSPACE_ID
    with pytest.raises(users.UserError, match="exclude each other"):
        users.create_user(
            conn,
            email="x@example.test",
            display_name="X",
            workspace_id=FOUNDING_WORKSPACE_ID,
            founding=True,
        )
    with pytest.raises(users.UserError, match="not a workspace"):
        users.create_user(conn, email="y@example.test", display_name="Y", workspace_id=uuid4())
    with pytest.raises(users.UserError, match="not a workspace"):  # the data tenant is no workspace
        users.create_user(
            conn, email="z@example.test", display_name="Z", workspace_id=DATA_TENANT_ID
        )


def test_a_refused_duplicate_creates_no_workspace(conn):
    users.create_user(conn, email="d@example.test", display_name="Dup test")
    before = _count(conn, "tenant")
    with pytest.raises(users.UserError, match="already exists"):
        users.create_user(conn, email="D@example.test", display_name="Dup test")
    assert _count(conn, "tenant") == before


def test_list_shows_the_workspace_and_set_webhook_sets_and_clears_it(conn):
    out: list[str] = []
    users.run(
        ["create", "--email", "l@example.test", "--display-name", "Lee test"], conn, emit=out.append
    )
    ws = auth.find_user_by_email(conn, "l@example.test")["workspace_id"]
    out.clear()
    users.run(["list"], conn, emit=out.append)
    assert f"workspace {ws} (Lee test's workspace)" in out[0] and "webhook" not in out[0]
    url = "https://hooks.example.test/x"
    assert (
        users.run(
            ["workspace", "set-webhook", "--workspace", str(ws), "--url", url],
            conn,
            emit=out.append,
        )
        == 0
    )
    with conn.cursor() as cur:
        cur.execute("SELECT notify_webhook FROM tenant WHERE id = %s", (ws,))
        assert cur.fetchone()["notify_webhook"] == url
    out.clear()
    users.run(["list"], conn, emit=out.append)
    assert "webhook" in out[0] and url not in out[0]  # the URL is a secret: never printed
    users.run(
        ["workspace", "set-webhook", "--workspace", str(ws), "--clear"], conn, emit=out.append
    )
    with conn.cursor() as cur:
        cur.execute("SELECT notify_webhook FROM tenant WHERE id = %s", (ws,))
        assert cur.fetchone()["notify_webhook"] is None
    assert (
        users.run(
            ["workspace", "set-webhook", "--workspace", str(DATA_TENANT_ID), "--url", url],
            conn,
            emit=out.append,
        )
        == 1
    )
