"""The login door (auth PR 1b, docs/AUTH.md): users, server-side sessions, API tokens, and the two gate dependencies.

Three principals reach the API:

* **session** — a browser sign-in. ``POST /auth/login`` mints a random token, stores only its sha256 in
  ``user_session`` and hands the token to the browser in the ``alphadeck_session`` cookie (HttpOnly, SameSite=Lax).
  Idle expiry ``session_idle_days`` (slid forward on use, at most once per ``_TOUCH_EVERY``).
* **token** — ``Authorization: Bearer <token>`` against ``api_token`` (sha256 only), minted by
  ``python -m pipeline.users token create``: the MCP agent tools, the deploy script's probes, curl.
* **oauth** — ``Authorization: Bearer <token>`` against ``oauth_access_token`` (sha256 only): an app the user
  connected through our OAuth sign-in (docs/CONNECTORS.md). Two rules, both here in the gate: a token without the
  ``write`` scope (none carries it yet) answers 403 on every request that is not GET / HEAD / OPTIONS, and the
  role is ``super_admin`` only when the user is one AND the token carries ``steward``. It never reaches
  ``/account`` or ``/auth/change-password`` (a connected app never manages the account that connected it).
* **system** — the ``ALPHADECK_SYSTEM_TOKEN`` env secret (the cron's dashboard warm-up). It can only READ:
  ``GET`` under ``/dashboards/``, ``/clock`` and ``/health``; anything else answers 403.

``require_user`` is mounted on every router (``app/main.py``), never inside a handler, so an unauthenticated
request answers 401 before FastAPI validates a single parameter. ``require_super_admin`` (the data steward: Admin,
Backtest, the business-type re-tag) is ``require_user`` + the role, else 403.

Each user has one WORKSPACE (``app_user.workspace_id``, a ``kind='workspace'`` tenant): the caller's workspace is
where their theses, calls and ratified facts live (docs/TENANT_SCOPES.md). The functions below are the same ones ``pipeline.users`` (the CLI) calls; the caller owns the
transaction everywhere except the gate itself (see ``require_user``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request

from app.db_conn import get_conn
from domain.settings import get_settings

SESSION_COOKIE = "alphadeck_session"
ROLES = ("user", "super_admin")
MIN_PASSWORD_LEN = 12
# `last_seen_at` / `last_used_at` (and the sliding idle deadline) are written at most this often per session or
# token, so a page that fires twenty reads costs one UPDATE, not twenty.
_TOUCH_EVERY = timedelta(minutes=5)

Principal = Literal["session", "token", "oauth", "system"]

# The scopes a caller holds (docs/CONNECTORS.md): a user's session and personal tokens hold their role's (``read``,
# plus ``steward`` for a super admin); an OAuth token holds what the user granted the app; the system token holds
# ``read``. ``write`` is the scope a non-GET request would need from an OAuth token: none carries it in this phase.
READ_SCOPE = "read"
STEWARD_SCOPE = "steward"
WRITE_SCOPE = "write"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def scopes_for_role(role: str) -> frozenset[str]:
    """``{"read"}``, plus ``"steward"`` for a ``super_admin`` (``agent_tools.principal.scopes_for_role``'s rule)."""
    return (
        frozenset({READ_SCOPE, STEWARD_SCOPE}) if role == "super_admin" else frozenset({READ_SCOPE})
    )


# argon2id with the library's current defaults (RFC 9106 low-memory profile). The PHC string carries its own
# parameters, so a later change of defaults still verifies old hashes (and `check_needs_rehash` upgrades them).
_HASHER = PasswordHasher()
_DUMMY_HASH: str | None = None


@dataclass(frozen=True)
class CurrentUser:
    """The caller, as every gated route sees it."""

    id: UUID
    email: str
    display_name: str
    role: str
    workspace_id: UUID
    principal: Principal
    # empty = the role's own scopes (filled in below): only an OAuth token states its own
    scopes: frozenset[str] = frozenset()
    # the connected app, for an ``oauth`` principal only
    client_id: str | None = None

    def __post_init__(self) -> None:
        if not self.scopes:
            object.__setattr__(
                self,
                "scopes",
                (
                    frozenset({READ_SCOPE})
                    if self.principal == "system"
                    else scopes_for_role(self.role)
                ),
            )

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"


# The system principal: no row in app_user, a fixed nil id, a plain user's role, and NO workspace of its own:
# ``workspace_id`` is the nil UUID, a placeholder that names no tenant and is never read (``app.deps.get_workspace``
# answers a system request from the ``X-Alphadeck-Workspace`` header instead, so the cron's warm-up names one
# workspace per request).
SYSTEM_USER = CurrentUser(
    id=UUID(int=0),
    email="system",
    display_name="system",
    role="user",
    workspace_id=UUID(int=0),
    principal="system",
)


# --- hashing + tokens -------------------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """An argon2id PHC string for ``password``."""
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True when ``password`` matches ``password_hash``; False on a mismatch or an unreadable hash."""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _dummy_verify(password: str) -> None:
    """Spend one real argon2 verify on a throwaway hash, so an unknown email costs the same time as a wrong
    password and the response time does not reveal whether an account exists."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(16))
    verify_password(_DUMMY_HASH, password)


def new_token() -> str:
    """A fresh 256-bit random token (session cookie or API token)."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """What the database stores for a token: its sha256, hex."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_new_password(password: str) -> None:
    """The one password rule: at least ``MIN_PASSWORD_LEN`` characters. Raises ``ValueError``."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"a password needs at least {MIN_PASSWORD_LEN} characters")


def check_email(email: str) -> str:
    """The one email rule: trimmed, non-empty, with an ``@``. Returns the trimmed email; raises ``ValueError``."""
    email = email.strip()
    if not email or "@" not in email:
        raise ValueError("an email address is required")
    return email


def check_display_name(display_name: str) -> str:
    """The one display-name rule: trimmed and non-empty. Returns the trimmed name; raises ``ValueError``."""
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("a display name is required")
    return display_name


# --- users -------------------------------------------------------------------------------------------------------


def _user_from_row(row: dict[str, Any], principal: Principal) -> CurrentUser:
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        workspace_id=row["workspace_id"],
        principal=principal,
    )


def find_user_by_email(conn: psycopg.Connection, email: str) -> dict[str, Any] | None:
    """The ``app_user`` row for ``email`` (case-insensitive), or None."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM app_user WHERE lower(email) = lower(%s)", (email.strip(),))
        return cur.fetchone()


def create_user(
    conn: psycopg.Connection,
    *,
    email: str,
    display_name: str,
    password: str,
    role: str = "user",
    workspace_id: UUID,
) -> UUID:
    """Insert one user. Refuses (``ValueError``) a second user with the same email, case-insensitively — it never
    overwrites. ``workspace_id`` must name a ``kind='workspace'`` tenant (migration 0078's ``app_user`` trigger
    refuses the data tenant) and is REQUIRED: no default attaches a user to anyone's workspace. The caller commits.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    email = check_email(email)
    display_name = check_display_name(display_name)
    check_new_password(password)
    if find_user_by_email(conn, email) is not None:
        raise ValueError(f"a user with email {email!r} already exists")
    uid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (uid, email, display_name, hash_password(password), role, workspace_id),
        )
    return uid


def update_profile(
    conn: psycopg.Connection, user_id: UUID, *, display_name: str, email: str
) -> dict[str, Any]:
    """Change a user's display name and email (the Settings page's Profile tab). The rules are ``create_user``'s
    (``check_display_name``, ``check_email``; ``ValueError``). Another user's email, case-insensitively, raises
    ``psycopg.errors.UniqueViolation`` from the ``lower(email)`` index (the route answers 409; no pre-check that
    could race); changing only the case of your own email is allowed. Sessions and tokens are untouched (the
    session is the cookie, not the email). Returns the updated ``app_user`` row. The caller commits.
    """
    email = check_email(email)
    display_name = check_display_name(display_name)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app_user SET email = %s, display_name = %s WHERE id = %s RETURNING *",
            (email, display_name, user_id),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("no such user")
    return row


def set_password(conn: psycopg.Connection, user_id: UUID, password: str) -> None:
    """Replace a user's password hash (the rule applies). The caller commits."""
    check_new_password(password)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE app_user SET password_hash = %s WHERE id = %s",
            (hash_password(password), user_id),
        )


def set_disabled(conn: psycopg.Connection, user_id: UUID, disabled: bool) -> None:
    """Disable (stamp ``disabled_at``) or re-enable (clear it) a user. A disabled user's sessions and tokens stop
    working at once — the gate checks ``disabled_at`` on every request. The caller commits."""
    with conn.cursor() as cur:
        if disabled:
            cur.execute(
                "UPDATE app_user SET disabled_at = now() WHERE id = %s AND disabled_at IS NULL",
                (user_id,),
            )
        else:
            cur.execute("UPDATE app_user SET disabled_at = NULL WHERE id = %s", (user_id,))


# --- sessions ----------------------------------------------------------------------------------------------------


def create_session(conn: psycopg.Connection, user_id: UUID) -> str:
    """Mint a session for ``user_id``; returns the token (the cookie value). Only its hash is stored."""
    token = new_token()
    idle = timedelta(days=get_settings().session_idle_days)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO user_session (id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, now() + %s)",
            (uuid.uuid4(), user_id, token_hash(token), idle),
        )
    return token


def revoke_session(conn: psycopg.Connection, token: str) -> None:
    """Revoke the session this cookie value names (a no-op when it names none). The caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_session SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
            (token_hash(token),),
        )


def revoke_other_sessions(conn: psycopg.Connection, user_id: UUID, keep_token: str | None) -> int:
    """Revoke every live session of ``user_id`` except the one ``keep_token`` names. Returns how many."""
    keep = token_hash(keep_token) if keep_token else ""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE user_session SET revoked_at = now() "
            "WHERE user_id = %s AND revoked_at IS NULL AND token_hash <> %s",
            (user_id, keep),
        )
        return cur.rowcount


# --- API tokens --------------------------------------------------------------------------------------------------


def create_api_token(conn: psycopg.Connection, user_id: UUID, name: str) -> tuple[UUID, str]:
    """Mint a named bearer token for ``user_id``; returns ``(id, token)``. The token is shown once and only its
    hash is stored. The caller commits."""
    if not name.strip():
        raise ValueError("a token name is required")
    token = new_token()
    tid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO api_token (id, user_id, name, token_hash) VALUES (%s, %s, %s, %s)",
            (tid, user_id, name.strip(), token_hash(token)),
        )
    return tid, token


def list_api_tokens(conn: psycopg.Connection, user_id: UUID) -> list[dict[str, Any]]:
    """``user_id``'s API tokens (never a hash): live ones first, then revoked, newest first within each."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at FROM api_token WHERE user_id = %s "
            "ORDER BY (revoked_at IS NOT NULL), created_at DESC, id",
            (user_id,),
        )
        return list(cur.fetchall())


def count_live_api_tokens(conn: psycopg.Connection, user_id: UUID) -> int:
    """How many of ``user_id``'s API tokens are not revoked."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM api_token WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        )
        return cur.fetchone()["n"]


def get_api_token(conn: psycopg.Connection, token_id: UUID) -> dict[str, Any] | None:
    """One API token's row by id (never its hash), or None. The caller checks ``user_id`` before acting on it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, name, created_at, last_used_at, revoked_at FROM api_token WHERE id = %s",
            (token_id,),
        )
        return cur.fetchone()


def revoke_api_token(conn: psycopg.Connection, token_id: UUID) -> bool:
    """Revoke one API token by id. True when a live token was revoked. The caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_token SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (token_id,),
        )
        return cur.rowcount == 1


# --- the login throttle (in-process, like the draft-job registry) ------------------------------------------------

_throttle_lock = threading.Lock()
_failures: dict[tuple[str, str], deque[float]] = {}
_locked_until: dict[tuple[str, str], float] = {}


def throttle_key(email: str, client_ip: str) -> tuple[str, str]:
    return (email.strip().lower(), client_ip)


def is_locked_out(key: tuple[str, str], *, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _throttle_lock:
        until = _locked_until.get(key)
        if until is None:
            return False
        if now >= until:
            del _locked_until[key]
            return False
        return True


def record_failure(key: tuple[str, str], *, now: float | None = None) -> None:
    """Count one failed sign-in; ``login_max_failures`` inside ``login_window_s`` locks the key out for
    ``login_lockout_s``."""
    s = get_settings()
    now = time.monotonic() if now is None else now
    with _throttle_lock:
        q = _failures.setdefault(key, deque())
        q.append(now)
        while q and now - q[0] > s.login_window_s:
            q.popleft()
        if len(q) >= s.login_max_failures:
            _locked_until[key] = now + s.login_lockout_s
            q.clear()


def clear_failures(key: tuple[str, str]) -> None:
    with _throttle_lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)


def reset_throttle() -> None:
    """Forget every failure and lockout (tests)."""
    with _throttle_lock:
        _failures.clear()
        _locked_until.clear()


# --- the gate ----------------------------------------------------------------------------------------------------

_UNAUTHORIZED = HTTPException(
    status_code=401, detail="sign in required", headers={"WWW-Authenticate": "Bearer"}
)


def _session_user(conn: psycopg.Connection, token: str) -> tuple[CurrentUser, str] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.*, s.id AS session_id, s.last_seen_at AS session_last_seen "
            "FROM user_session s JOIN app_user u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now() "
            "AND u.disabled_at IS NULL",
            (token_hash(token),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        if row["session_last_seen"] < datetime.now(timezone.utc) - _TOUCH_EVERY:
            cur.execute(
                "UPDATE user_session SET last_seen_at = now(), expires_at = now() + %s WHERE id = %s",
                (timedelta(days=get_settings().session_idle_days), row["session_id"]),
            )
    return _user_from_row(row, "session"), row["session_id"]


def _token_user(conn: psycopg.Connection, token: str) -> CurrentUser | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.*, t.id AS token_id, t.last_used_at AS token_last_used "
            "FROM api_token t JOIN app_user u ON u.id = t.user_id "
            "WHERE t.token_hash = %s AND t.revoked_at IS NULL AND u.disabled_at IS NULL",
            (token_hash(token),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        last = row["token_last_used"]
        if last is None or last < datetime.now(timezone.utc) - _TOUCH_EVERY:
            cur.execute(
                "UPDATE api_token SET last_used_at = now() WHERE id = %s", (row["token_id"],)
            )
    return _user_from_row(row, "token")


def _oauth_user(conn: psycopg.Connection, token: str) -> CurrentUser | None:
    """The bearer as a live OAuth access token (``app.oauth.store.gate_lookup``), with its two rules' inputs: the
    token's scopes, and the role capped by them."""
    from app.oauth import store as oauth_store  # no MCP SDK import: the store is plain SQL

    row = oauth_store.gate_lookup(conn, token)
    if row is None:
        return None
    scopes = frozenset(row["oauth_scopes"])
    role = "super_admin" if row["role"] == "super_admin" and STEWARD_SCOPE in scopes else "user"
    return CurrentUser(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=role,
        workspace_id=row["workspace_id"],
        principal="oauth",
        scopes=scopes,
        client_id=row["oauth_client_id"],
    )


def oauth_may(method: str, path: str, scopes: frozenset[str]) -> str | None:
    """Why an ``oauth`` principal may NOT make this request (the 403 detail), or None when it may. A token without
    ``write`` reads only; no OAuth token manages the account that connected it."""
    if path == "/account" or path.startswith("/account/") or path == "/auth/change-password":
        return "a connected app cannot manage your account"
    if method.upper() not in _SAFE_METHODS and WRITE_SCOPE not in scopes:
        return "this connected app can only read"
    return None


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization")
    if not raw:
        return None
    scheme, _, value = raw.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def system_may(method: str, path: str) -> bool:
    """What the system token may reach: GET under /dashboards/, /clock and /health. Nothing else."""
    if method.upper() != "GET":
        return False
    return path.startswith("/dashboards/") or path in ("/clock", "/health")


def require_user(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> CurrentUser:
    """The gate on every router: the session cookie, else a bearer API token, else a bearer OAuth access token
    (403 on any request ``oauth_may`` refuses), else the system token; else 401.

    The gate ENDS the transaction it opened (``commit``): its own reads and the occasional ``last_seen_at`` /
    ``last_used_at`` touch are committed before the route runs, so the route starts on an idle connection exactly
    as it did before the gate existed (a route's ``with conn.transaction()`` stays a top-level transaction, never a
    savepoint inside the gate's read)."""
    try:
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie:
            found = _session_user(conn, cookie)
            if found is not None:
                return found[0]
        bearer = _bearer(request)
        if bearer:
            user = _token_user(conn, bearer)
            if user is not None:
                return user
            user = _oauth_user(conn, bearer)
            if user is not None:
                refused = oauth_may(request.method, request.url.path, user.scopes)
                if refused is not None:
                    raise HTTPException(status_code=403, detail=refused)
                return user
            system = get_settings().system_token
            if system and hmac.compare_digest(bearer.encode("utf-8"), system.encode("utf-8")):
                if not system_may(request.method, request.url.path):
                    raise HTTPException(
                        status_code=403, detail="the system token can only read dashboards"
                    )
                return SYSTEM_USER
        raise _UNAUTHORIZED
    finally:
        conn.commit()


def require_super_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    """``require_user`` + the steward role, else 403 (Admin, Backtest, the business-type re-tag)."""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="stewards only")
    return user
