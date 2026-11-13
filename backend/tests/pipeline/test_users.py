"""``pipeline.users`` — the steward's user and token CLI (the login door, docs/AUTH.md), tested through its functions
and its ``run`` entry on the test connection."""

from __future__ import annotations

import io
from uuid import uuid4

import pytest

from app import auth
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID
from pipeline import users
from repositories import user_meta_repo


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


def test_list_shows_the_workspace_and_a_per_user_webhook_flag_from_user_meta(conn):
    out: list[str] = []
    users.run(
        ["create", "--email", "l@example.test", "--display-name", "Lee test"], conn, emit=out.append
    )
    users.run(
        ["create", "--email", "m@example.test", "--display-name", "Mo test"], conn, emit=out.append
    )
    lee = auth.find_user_by_email(conn, "l@example.test")
    out.clear()
    users.run(["list"], conn, emit=out.append)
    assert f"workspace {lee['workspace_id']} (Lee test's workspace)" in out[0]
    assert all("webhook" not in line for line in out)
    url = "https://hooks.slack.com/services/TEST0000/TEST0000/invented-for-tests"
    user_meta_repo.set_slack_webhook(conn, lee["id"], url)
    conn.commit()
    out.clear()
    users.run(["list"], conn, emit=out.append)
    lee_line = next(line for line in out if line.startswith("l@example.test"))
    mo_line = next(line for line in out if line.startswith("m@example.test"))
    assert "webhook" in lee_line and "webhook" not in mo_line  # per USER, not per workspace
    assert all(url not in line for line in out)  # the URL is a secret: never printed
    user_meta_repo.set_slack_webhook(conn, lee["id"], None)
    conn.commit()
    out.clear()
    users.run(["list"], conn, emit=out.append)
    assert all("webhook" not in line for line in out)


def test_the_workspace_set_webhook_subcommand_is_gone(conn):
    with pytest.raises(SystemExit):
        users.run(
            ["workspace", "set-webhook", "--workspace", str(uuid4()), "--clear"],
            conn,
            emit=lambda _line: None,
        )
    assert not hasattr(users, "set_webhook")


# --- set-role and the last-super-admin guard (security cleanup Part B item 4, D9) ------------------------------


def _role(conn, email: str) -> str:
    return auth.find_user_by_email(conn, email)["role"]


def _make(conn, email: str, *, super_admin: bool = False) -> None:
    users.create_user(
        conn,
        email=email,
        display_name=email.split("@")[0],
        super_admin=super_admin,
        password="a long enough passphrase",
    )
    conn.commit()


def test_set_role_promote_then_demote_swaps_the_super_admin(conn):
    _make(conn, "old@example.test", super_admin=True)
    _make(conn, "new@example.test")
    out: list[str] = []
    assert (
        users.run(
            ["set-role", "--email", "new@example.test", "--role", "super_admin"],
            conn,
            emit=out.append,
        )
        == 0
    )
    assert (
        users.run(
            ["set-role", "--email", "old@example.test", "--role", "user"], conn, emit=out.append
        )
        == 0
    )
    assert _role(conn, "new@example.test") == "super_admin"
    assert _role(conn, "old@example.test") == "user"
    assert out[-1].startswith("set role")


def test_demoting_the_only_super_admin_is_refused_and_changes_nothing(conn):
    _make(conn, "only@example.test", super_admin=True)
    out: list[str] = []
    code = users.run(
        ["set-role", "--email", "only@example.test", "--role", "user"], conn, emit=out.append
    )
    assert code == 1
    assert out == ["refused: this would leave no enabled super admin; promote another user first"]
    assert _role(conn, "only@example.test") == "super_admin"


def test_demoting_one_of_two_super_admins_works(conn):
    _make(conn, "a@example.test", super_admin=True)
    _make(conn, "b@example.test", super_admin=True)
    users.set_role(conn, "a@example.test", "user")
    conn.commit()
    assert (
        _role(conn, "a@example.test") == "user" and _role(conn, "b@example.test") == "super_admin"
    )


def test_a_disabled_super_admin_does_not_count_as_the_one_that_remains(conn):
    _make(conn, "live@example.test", super_admin=True)
    _make(conn, "gone@example.test", super_admin=True)
    users.set_disabled(conn, "gone@example.test", True)
    conn.commit()
    with pytest.raises(users.UserError, match="no enabled super admin"):
        users.set_role(conn, "live@example.test", "user")
    conn.rollback()
    assert _role(conn, "live@example.test") == "super_admin"


def test_disabling_the_last_enabled_super_admin_is_refused(conn):
    """D9: ``disable`` carries the same guard as ``set-role``."""
    _make(conn, "only@example.test", super_admin=True)
    _make(conn, "plain@example.test")
    out: list[str] = []
    code = users.run(["disable", "--email", "only@example.test"], conn, emit=out.append)
    assert code == 1
    assert out == ["refused: this would leave no enabled super admin; promote another user first"]
    assert auth.find_user_by_email(conn, "only@example.test")["disabled_at"] is None
    # a plain user can still be disabled, and so can one of two super admins
    assert users.run(["disable", "--email", "plain@example.test"], conn, emit=out.append) == 0
    _make(conn, "second@example.test", super_admin=True)
    assert users.run(["disable", "--email", "only@example.test"], conn, emit=out.append) == 0


def test_set_role_refuses_an_unknown_email_or_role(conn):
    _make(conn, "u@example.test")
    with pytest.raises(users.UserError, match="no user"):
        users.set_role(conn, "nobody@example.test", "super_admin")
    with pytest.raises(users.UserError, match="role must be one of"):
        users.set_role(conn, "u@example.test", "admin")
    with pytest.raises(SystemExit):  # argparse refuses a role outside the choices
        users.run(
            ["set-role", "--email", "u@example.test", "--role", "admin"], conn, emit=lambda _: None
        )
    assert _role(conn, "u@example.test") == "user"


# --- oauth-client add (security PR 5 step 7) ---------------------------------------------------------------------

_CLIENT = "steward-added-test-client"


@pytest.fixture
def clean_clients(conn):
    def wipe():
        with conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_client WHERE client_id LIKE %s", ("steward-added-%",))
        conn.commit()

    wipe()
    yield conn
    conn.rollback()
    wipe()


def _client_row(conn, client_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM oauth_client WHERE client_id = %s", (client_id,))
        return cur.fetchone()


def test_oauth_client_add_inserts_one_public_client_and_prints_no_secret(clean_clients):
    conn = clean_clients
    before = _count(conn, "oauth_client")
    out: list[str] = []
    argv = ["oauth-client", "add", "--client-id", _CLIENT, "--name", "Research Desk"]
    argv += [
        "--redirect-uri",
        "https://desk.example.test/cb",
        "--redirect-uri",
        "http://localhost/cb",
    ]
    assert users.run(argv, conn, emit=out.append) == 0
    assert _count(conn, "oauth_client") == before + 1
    row = _client_row(conn, _CLIENT)
    assert row["registration"] == "static"  # D8: the steward vetted it ("A known client.")
    assert row["client_name"] == "Research Desk"
    assert row["redirect_uris"] == ["https://desk.example.test/cb", "http://localhost/cb"]
    assert row["token_endpoint_auth_method"] == "none" and row["client_secret_hash"] is None
    assert row["scope"] == "read steward"
    assert row["grant_types"] == ["authorization_code", "refresh_token"]
    assert row["disabled_at"] is None
    assert not any("secret" in line.lower() for line in out)
    assert any(_CLIENT in line for line in out)


def test_oauth_client_add_confidential_prints_the_secret_once_and_stores_its_hash(clean_clients):
    from app.oauth import store as oauth_store

    conn = clean_clients
    out: list[str] = []
    argv = ["oauth-client", "add", "--client-id", _CLIENT, "--name", "Desk"]
    argv += ["--redirect-uri", "https://desk.example.test/cb", "--confidential"]
    assert users.run(argv, conn, emit=out.append) == 0
    shown = [line for line in out if line.startswith("client secret (shown once): ")]
    assert len(shown) == 1
    secret = shown[0].split(": ", 1)[1]
    assert len(secret) >= 32
    row = _client_row(conn, _CLIENT)
    assert row["token_endpoint_auth_method"] == "client_secret_post"
    assert row["client_secret_hash"] == oauth_store.hash_secret(secret) != secret
    listed: list[str] = []
    users.run(["oauth-client", "list"], conn, emit=listed.append)
    assert not any(secret in line or row["client_secret_hash"] in line for line in listed)


def test_oauth_client_add_refuses_a_plain_http_redirect_and_writes_nothing(clean_clients):
    conn = clean_clients
    before = _count(conn, "oauth_client")
    out: list[str] = []
    argv = ["oauth-client", "add", "--client-id", _CLIENT, "--name", "Desk"]
    argv += ["--redirect-uri", "http://evil.example.test/cb"]
    assert users.run(argv, conn, emit=out.append) == 1
    assert out and out[-1].startswith("refused:")
    assert _count(conn, "oauth_client") == before
    assert _client_row(conn, _CLIENT) is None


def test_oauth_client_add_refuses_an_id_that_exists(clean_clients):
    conn = clean_clients
    argv = ["oauth-client", "add", "--client-id", _CLIENT, "--name", "Desk"]
    argv += ["--redirect-uri", "https://desk.example.test/cb"]
    assert users.run(argv, conn, emit=lambda _: None) == 0
    before = _count(conn, "oauth_client")
    out: list[str] = []
    again = ["oauth-client", "add", "--client-id", _CLIENT, "--name", "Other"]
    again += ["--redirect-uri", "https://other.example.test/cb"]
    assert users.run(again, conn, emit=out.append) == 1
    assert "already exists" in out[-1]
    builtin = ["oauth-client", "add", "--client-id", "alphadeck-claude", "--name", "X"]
    builtin += ["--redirect-uri", "https://x.example.test/cb"]
    assert users.run(builtin, conn, emit=out.append) == 1
    assert _count(conn, "oauth_client") == before
    assert _client_row(conn, _CLIENT)["client_name"] == "Desk"  # never overwritten
