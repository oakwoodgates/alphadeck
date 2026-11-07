"""``python -m pipeline.users`` — the steward's user and token CLI (the login door, docs/AUTH.md).

There is no sign-up page and no email delivery: the super admin creates every account here::

    python -m pipeline.users create --email ana@example.com --display-name Ana [--super-admin]
    python -m pipeline.users create --email bo@example.com --display-name Bo --password-stdin < pw.txt
    python -m pipeline.users disable --email bo@example.com        # sign-in refused; live sessions + tokens stop
    python -m pipeline.users enable --email bo@example.com         # the inverse
    python -m pipeline.users reset-password --email bo@example.com [--password-stdin]
    python -m pipeline.users list                                  # email, role, workspace, created, disabled, sessions
    python -m pipeline.users token create --email ana@example.com --name mcp    # prints the token ONCE
    python -m pipeline.users token revoke --id <token uuid>

A generated password (20 characters) or a minted token is printed ONCE and never stored in the clear: the
database keeps an argon2id hash (passwords) or a sha256 (tokens). ``list`` never prints a hash.

The functions take a connection and do NOT commit (the caller owns the transaction, the repo's rule); ``main``
commits once per command. ``create`` of an email that exists (case-insensitively) refuses — it never overwrites.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from collections.abc import Callable, Sequence
from typing import Any, TextIO
from uuid import UUID

import psycopg

from app import auth
from db.session import DEFAULT_TENANT_ID, connect

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
    password: str | None = None,
) -> tuple[UUID, str | None]:
    """Create one user. Returns ``(id, generated password or None)``: a password is generated when none is given.

    The default workspace is tenant ...0001 (``DEFAULT_TENANT_ID``), the one workspace of this release. PR 2
    (workspaces) changes this default to the founding workspace."""
    generated = None
    if password is None:
        password = generated = generate_password()
    try:
        uid = auth.create_user(
            conn,
            email=email,
            display_name=display_name,
            password=password,
            role="super_admin" if super_admin else "user",
            workspace_id=workspace_id or DEFAULT_TENANT_ID,
        )
    except ValueError as exc:
        raise UserError(str(exc)) from exc
    return uid, generated


def set_disabled(conn: psycopg.Connection, email: str, disabled: bool) -> UUID:
    row = _require_user(conn, email)
    auth.set_disabled(conn, row["id"], disabled)
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
    """Every user with its live-session count. No hash ever leaves this function."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.email, u.display_name, u.role, u.workspace_id, u.created_at, u.disabled_at, "
            "(SELECT count(*) FROM user_session s WHERE s.user_id = u.id AND s.revoked_at IS NULL "
            " AND s.expires_at > now()) AS sessions, "
            "(SELECT count(*) FROM api_token t WHERE t.user_id = u.id AND t.revoked_at IS NULL) AS tokens "
            "FROM app_user u ORDER BY u.created_at, u.email"
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
    c.add_argument("--workspace", type=UUID, default=None, help="default: tenant ...0001")
    c.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    for name in ("disable", "enable"):
        d = sub.add_parser(name, help=f"{name} a user")
        d.add_argument("--email", required=True)
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
                    f"{u['email']}\t{u['role']}\tworkspace {u['workspace_id']}\t"
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
