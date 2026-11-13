"""``python -m pipeline.users`` — the steward's user and token CLI (the login door, docs/AUTH.md).

There is no sign-up page and no email delivery: the super admin creates every account here::

    python -m pipeline.users create --email ana@example.com --display-name Ana [--super-admin]
    python -m pipeline.users create --email op@example.com --display-name Op --super-admin --founding
    python -m pipeline.users create --email cy@example.com --display-name Cy --workspace <uuid>
    python -m pipeline.users create --email bo@example.com --display-name Bo --password-stdin < pw.txt
    python -m pipeline.users disable --email bo@example.com        # sign-in refused; live sessions + tokens stop
    python -m pipeline.users set-role --email bo@example.com --role super_admin   # or --role user
    python -m pipeline.users enable --email bo@example.com         # the inverse
    python -m pipeline.users reset-password --email bo@example.com [--password-stdin]
    python -m pipeline.users list                                  # email, role, workspace, webhook, created, disabled, sessions
    python -m pipeline.users token create --email ana@example.com --name mcp    # prints the token ONCE
    python -m pipeline.users token revoke --id <token uuid>
    python -m pipeline.users oauth-client list                     # the OAuth clients (static + self-registered)
    python -m pipeline.users oauth-client disable --client-id <id> # refused everywhere from the next request

Every user has ONE workspace. ``create`` makes a NEW workspace named "<display name>'s workspace" by default;
``--workspace <uuid>`` attaches the user to an existing workspace and ``--founding`` to the founding workspace
(the operator's, ``...0002``, where migration 0078 moved the founding rows). The two flags exclude each other.
A user's Slack webhook (their own push destination, ``user_meta.slack_webhook_url``, migration 0079) is set by the
user on the Settings page (``/settings/notifications``), never here: ``list`` shows only whether it is set (a
``webhook`` flag per USER), never the URL.

A generated password (20 characters) or a minted token is printed ONCE and never stored in the clear: the
database keeps an argon2id hash (passwords) or a sha256 (tokens). ``list`` never prints a hash.

The functions take a connection and do NOT commit (the caller owns the transaction, the repo's rule); ``main``
commits once per command. ``create`` of an email that exists (case-insensitively) refuses — it never overwrites.
``set-role`` and ``disable`` refuse to leave no enabled super admin ("refused: this would leave no enabled super
admin; promote another user first"). To swap the super admin: ``set-role --email new --role super_admin``, then
``set-role --email old --role user``.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO
from uuid import UUID, uuid4

import psycopg

from app import auth
from app.oauth import store as oauth_store
from db.session import connect
from domain.scopes import FOUNDING_WORKSPACE_ID

GENERATED_PASSWORD_LEN = 20
_ALPHABET = string.ascii_letters + string.digits


class UserError(Exception):
    """A refused command (an unknown email, a duplicate, a bad password): one line, exit 1."""


def generate_password() -> str:
    """A random 20-character password (letters and digits)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(GENERATED_PASSWORD_LEN))


def _require_user(conn: psycopg.Connection, email: str) -> dict[str, Any]:
    row = auth.find_user_by_email(conn, email)
    if row is None:
        raise UserError(f"no user with email {email!r}")
    return row


def create_user(
    conn: psycopg.Connection,
    *,
    email: str,
    display_name: str,
    super_admin: bool = False,
    workspace_id: UUID | None = None,
    founding: bool = False,
    password: str | None = None,
) -> tuple[UUID, str | None]:
    """Create one user. Returns ``(id, generated password or None)``: a password is generated when none is given.

    The workspace: ``workspace_id`` (an existing ``kind='workspace'`` tenant), or ``founding`` (the founding
    workspace, ``FOUNDING_WORKSPACE_ID``), else a NEW workspace named "<display name>'s workspace" made with the
    user. A user can never sit on the data tenant (0078's ``app_user`` trigger refuses it)."""
    if workspace_id is not None and founding:
        raise UserError("--workspace and --founding exclude each other")
    generated = None
    if password is None:
        password = generated = generate_password()
    if auth.find_user_by_email(conn, email) is not None:
        raise UserError(f"a user with email {email!r} already exists")  # before a workspace is made
    if founding:
        workspace_id = FOUNDING_WORKSPACE_ID
    elif workspace_id is not None:
        _require_workspace(conn, workspace_id)
    else:
        workspace_id = _new_workspace(conn, f"{display_name.strip()}'s workspace")
    try:
        uid = auth.create_user(
            conn,
            email=email,
            display_name=display_name,
            password=password,
            role="super_admin" if super_admin else "user",
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise UserError(str(exc)) from exc
    return uid, generated


def _new_workspace(conn: psycopg.Connection, name: str) -> UUID:
    wid = uuid4()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, name))
    return wid


def _require_workspace(conn: psycopg.Connection, workspace_id: UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT kind FROM tenant WHERE id = %s", (workspace_id,))
        row = cur.fetchone()
    if row is None or row["kind"] != "workspace":
        raise UserError(f"{workspace_id} is not a workspace")


def set_disabled(conn: psycopg.Connection, email: str, disabled: bool) -> UUID:
    """Disable or re-enable a user. Refuses to disable the last enabled super admin (the caller rolls back)."""
    row = _require_user(conn, email)
    try:
        auth.set_disabled(conn, row["id"], disabled)
    except auth.LastSuperAdmin as exc:
        raise UserError(str(exc)) from exc
    return row["id"]


def set_role(conn: psycopg.Connection, email: str, role: str) -> UUID:
    """Set a user's role (``user`` or ``super_admin``), effective on the user's next request. Refuses a change that
    would leave no enabled super admin (the caller rolls back): to swap the super admin, promote the new one first,
    then demote the old one."""
    row = _require_user(conn, email)
    try:
        auth.set_role(conn, row["id"], role)
    except ValueError as exc:  # an unknown role, or LastSuperAdmin
        raise UserError(str(exc)) from exc
    return row["id"]


def reset_password(conn: psycopg.Connection, email: str, password: str | None = None) -> str | None:
    """Set a new password (generated when none is given; returned so it can be printed once) and sign every
    session of the user out."""
    row = _require_user(conn, email)
    generated = None
    if password is None:
        password = generated = generate_password()
    try:
        auth.set_password(conn, row["id"], password)
    except ValueError as exc:
        raise UserError(str(exc)) from exc
    auth.revoke_other_sessions(conn, row["id"], None)
    return generated


def list_users(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Every user with its live-session count and whether the user set a Slack webhook. No hash and no webhook
    URL ever leaves this function."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.email, u.display_name, u.role, u.workspace_id, t.name AS workspace_name, "
            "(m.slack_webhook_url IS NOT NULL) AS webhook, u.created_at, u.disabled_at, "
            "(SELECT count(*) FROM user_session s WHERE s.user_id = u.id AND s.revoked_at IS NULL "
            " AND s.expires_at > now()) AS sessions, "
            "(SELECT count(*) FROM api_token t WHERE t.user_id = u.id AND t.revoked_at IS NULL) AS tokens "
            "FROM app_user u JOIN tenant t ON t.id = u.workspace_id "
            "LEFT JOIN user_meta m ON m.user_id = u.id ORDER BY u.created_at, u.email"
        )
        return list(cur.fetchall())


def create_token(conn: psycopg.Connection, email: str, name: str) -> tuple[UUID, str]:
    row = _require_user(conn, email)
    try:
        return auth.create_api_token(conn, row["id"], name)
    except ValueError as exc:
        raise UserError(str(exc)) from exc


def revoke_token(conn: psycopg.Connection, token_id: UUID) -> None:
    if not auth.revoke_api_token(conn, token_id):
        raise UserError(f"no live token with id {token_id}")


# --- the CLI ---------------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pipeline.users", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a user (prints a generated password once)")
    c.add_argument("--email", required=True)
    c.add_argument("--display-name", required=True)
    c.add_argument("--super-admin", action="store_true", help="the data steward role")
    c.add_argument(
        "--workspace",
        type=UUID,
        default=None,
        help="attach to this existing workspace (default: a NEW workspace for the user)",
    )
    c.add_argument(
        "--founding", action="store_true", help="attach to the founding workspace ...0002"
    )
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    for name in ("disable", "enable"):
        d = sub.add_parser(name, help=f"{name} a user")
        d.add_argument("--email", required=True)
    sr = sub.add_parser(
        "set-role", help="change a user's role (refuses to leave no enabled super admin)"
    )
    sr.add_argument("--email", required=True)
    sr.add_argument("--role", required=True, choices=auth.ROLES)
    r = sub.add_parser("reset-password", help="set a new password and sign every session out")
    r.add_argument("--email", required=True)
    r.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list", help="every user (no hashes)")
    t = sub.add_parser("token", help="API tokens")
    tsub = t.add_subparsers(dest="token_cmd", required=True)
    tc = tsub.add_parser("create", help="mint a bearer token (prints it once)")
    tc.add_argument("--email", required=True)
    tc.add_argument("--name", required=True)
    tr = tsub.add_parser("revoke", help="revoke a token by id")
    tr.add_argument("--id", type=UUID, required=True)
    o = sub.add_parser("oauth-client", help="OAuth clients (docs/CONNECTORS.md)")
    osub = o.add_subparsers(dest="oauth_cmd", required=True)
    osub.add_parser("list", help="every OAuth client (never a secret)")
    od = osub.add_parser(
        "disable", help="refuse a client everywhere (its connections stop working)"
    )
    od.add_argument("--client-id", required=True)
    return p


def _read_password(stdin: TextIO) -> str:
    return stdin.readline().rstrip("\r\n")


def run(
    argv: Sequence[str],
    conn: psycopg.Connection,
    *,
    emit: Callable[[str], None] = print,
    stdin: TextIO = sys.stdin,
) -> int:
    """Run one command on ``conn`` and commit it. Exit 0, or 1 on a refused command (rolled back)."""
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "create":
            pw = _read_password(stdin) if args.password_stdin else None
            uid, generated = create_user(
                conn,
                email=args.email,
                display_name=args.display_name,
                super_admin=args.super_admin,
                workspace_id=args.workspace,
                founding=args.founding,
                password=pw,
            )
            conn.commit()
            emit(f"created user {uid} ({'super_admin' if args.super_admin else 'user'})")
            if generated is not None:
                emit(f"password (shown once): {generated}")
        elif args.cmd in ("disable", "enable"):
            uid = set_disabled(conn, args.email, args.cmd == "disable")
            conn.commit()
            emit(f"{args.cmd}d user {uid}")
        elif args.cmd == "set-role":
            uid = set_role(conn, args.email, args.role)
            conn.commit()
            emit(f"set role of user {uid} to {args.role}")
        elif args.cmd == "reset-password":
            pw = _read_password(stdin) if args.password_stdin else None
            generated = reset_password(conn, args.email, pw)
            conn.commit()
            emit("password reset; every session of the user is signed out")
            if generated is not None:
                emit(f"password (shown once): {generated}")
        elif args.cmd == "list":
            for u in list_users(conn):
                emit(
                    f"{u['email']}\t{u['role']}\tworkspace {u['workspace_id']} ({u['workspace_name']})"
                    f"{' webhook' if u['webhook'] else ''}\t"
                    f"created {u['created_at']:%Y-%m-%d}\t"
                    f"{'disabled ' + format(u['disabled_at'], '%Y-%m-%d') if u['disabled_at'] else 'enabled'}\t"
                    f"sessions {u['sessions']}\ttokens {u['tokens']}"
                )
        elif args.cmd == "token" and args.token_cmd == "create":
            tid, token = create_token(conn, args.email, args.name)
            conn.commit()
            emit(f"token {tid} ({args.name})")
            emit(f"token (shown once): {token}")
        elif args.cmd == "token" and args.token_cmd == "revoke":
            revoke_token(conn, args.id)
            conn.commit()
            emit(f"revoked token {args.id}")
        elif args.cmd == "oauth-client" and args.oauth_cmd == "list":
            for c in oauth_store.list_clients(conn):
                emit(
                    f"{c['client_id']}	{c['client_name'] or '-'}	{c['registration']}	"
                    f"scope {c['scope']}	auth {c['token_endpoint_auth_method']}	"
                    f"created {c['created_at']:%Y-%m-%d}	"
                    f"{'disabled ' + format(c['disabled_at'], '%Y-%m-%d') if c['disabled_at'] else 'enabled'}	"
                    f"connections {c['live_grants']}"
                )
            conn.commit()
        elif args.cmd == "oauth-client" and args.oauth_cmd == "disable":
            if not oauth_store.disable_client(conn, args.client_id):
                raise UserError(f"no enabled OAuth client {args.client_id!r}")
            conn.commit()
            emit(f"disabled OAuth client {args.client_id}")
    except UserError as exc:
        conn.rollback()
        emit(f"refused: {exc}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    conn = connect()
    try:
        return run(sys.argv[1:] if argv is None else argv, conn)
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
