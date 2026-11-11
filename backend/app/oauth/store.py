"""The OAuth tables (migration 0080) as plain functions over a psycopg connection (dict rows).

No MCP SDK import: the login door (``app.auth``), the consent routes and ``/account/connections`` call these on any
install. The caller owns the transaction (commits or rolls back) everywhere. Every token and code is a random
``secrets.token_urlsafe(32)``; only its sha256 is stored (``hash_secret``), never the value.

Lifetimes (plan D9): a consent and a code live ten minutes, an access token one hour, a refresh token thirty days
from its issue, slid by every rotation.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
from pydantic import AnyHttpUrl

READ = "read"
STEWARD = "steward"
WRITE = "write"  # no token carries it in this phase: an OAuth token without it can never write
SCOPES = (READ, STEWARD)
SCOPE_LABELS = {
    READ: "Read your cards, calls, decisions and ratified facts",
    STEWARD: "Read the Admin readouts (the split list, the status)",
}

CONSENT_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(minutes=10)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
TOUCH_EVERY = timedelta(minutes=5)  # the gate's _TOUCH_EVERY
MAX_CLIENT_NAME = 60
MCP_PATH = "/mcp"
STATIC_CLIENTS = ("https://claude.ai/oauth/claude-code-client-metadata", "alphadeck-claude")


def hash_secret(value: str) -> str:
    """What a table stores for a token, a code or a client secret: its sha256, hex."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret() -> str:
    """A fresh 256-bit random token or code."""
    return secrets.token_urlsafe(32)


def issuer_for(origin: str) -> str:
    """The authorization server's issuer string: the SAME value the protected-resource document names in
    ``authorization_servers`` (pydantic's ``AnyHttpUrl`` spelling of the origin, trailing slash included), so a
    client that compares the two, or the ``iss`` on a redirect, sees identical strings."""
    return str(AnyHttpUrl(origin))


def connector_url(origin: str) -> str:
    """The one resource this server issues tokens for (``agent_tools.hosted.connector_url``'s value)."""
    return f"{origin}{MCP_PATH}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def grantable(role: str) -> frozenset[str]:
    """The scopes a user of ``role`` may grant an app: ``read`` always, ``steward`` only for a super admin."""
    return frozenset({READ, STEWARD}) if role == "super_admin" else frozenset({READ})


def requested_with_read(scopes: Iterable[str] | None) -> list[str]:
    """A request's scopes in the canonical order, ``read`` always present (added when the app asked for none)."""
    asked = set(scopes or ()) | {READ}
    return [s for s in SCOPES if s in asked] + sorted(asked - set(SCOPES))


# --- clients -----------------------------------------------------------------------------------------------------


def get_client(conn: psycopg.Connection, client_id: str) -> dict[str, Any] | None:
    """A client's row when it exists and is not disabled, else None (a disabled client is unknown everywhere)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM oauth_client WHERE client_id = %s AND disabled_at IS NULL", (client_id,)
        )
        return cur.fetchone()


def insert_dcr_client(
    conn: psycopg.Connection,
    *,
    client_id: str,
    client_name: str | None,
    redirect_uris: list[str],
    scope: str,
    token_endpoint_auth_method: str,
    client_secret: str | None,
    grant_types: list[str],
    metadata: dict[str, Any],
) -> None:
    """Store a self-registered client. The secret, if any, is stored as its sha256 only."""
    name = (client_name or "").strip()[:MAX_CLIENT_NAME] or None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_client (client_id, client_name, redirect_uris, scope, "
            "token_endpoint_auth_method, client_secret_hash, registration, grant_types, metadata) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'dcr', %s, %s)",
            (
                client_id,
                name,
                redirect_uris,
                scope,
                token_endpoint_auth_method,
                hash_secret(client_secret) if client_secret else None,
                grant_types,
                psycopg.types.json.Jsonb(metadata),
            ),
        )


def list_clients(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Every client (never a secret hash), static first, then newest first; with its live-grant count."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.client_id, c.client_name, c.registration, c.scope, c.token_endpoint_auth_method, "
            "c.redirect_uris, c.created_at, c.disabled_at, "
            "(SELECT count(*) FROM oauth_grant g WHERE g.client_id = c.client_id AND g.revoked_at IS NULL) "
            "AS live_grants FROM oauth_client c "
            "ORDER BY (c.registration <> 'static'), c.created_at DESC, c.client_id"
        )
        return list(cur.fetchall())


def disable_client(conn: psycopg.Connection, client_id: str) -> bool:
    """Disable a client: refused everywhere from the next request (its grants' tokens die with it at the gate).
    True when a live client was disabled."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_client SET disabled_at = now() WHERE client_id = %s AND disabled_at IS NULL",
            (client_id,),
        )
        return cur.rowcount == 1


def count_clients(conn: psycopg.Connection) -> dict[str, int]:
    """``{"static": n, "dcr": m}`` (disabled ones included: a count of what is registered)."""
    with conn.cursor() as cur:
        cur.execute("SELECT registration, count(*) AS n FROM oauth_client GROUP BY registration")
        got = {r["registration"]: int(r["n"]) for r in cur.fetchall()}
    return {"static": got.get("static", 0), "dcr": got.get("dcr", 0)}


# --- consents ----------------------------------------------------------------------------------------------------


def create_consent(
    conn: psycopg.Connection,
    *,
    client_id: str,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    scopes: list[str] | None,
    code_challenge: str,
    state: str | None,
    resource: str,
) -> UUID:
    """Store a pending request from ``/authorize`` (ten minutes). Returns its id."""
    cid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_consent (id, client_id, redirect_uri, redirect_uri_explicit, scopes_requested, "
            "code_challenge, state, resource, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + %s)",
            (
                cid,
                client_id,
                redirect_uri,
                redirect_uri_explicit,
                requested_with_read(scopes),
                code_challenge,
                state,
                resource,
                CONSENT_TTL,
            ),
        )
    return cid


def get_consent(
    conn: psycopg.Connection, consent_id: UUID, *, for_update: bool = False
) -> dict[str, Any] | None:
    """A consent with its client's name, registration and disabled stamp, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT k.*, c.client_name, c.registration, c.disabled_at AS client_disabled_at, "
            "k.expires_at <= now() AS expired "
            "FROM oauth_consent k JOIN oauth_client c ON c.client_id = k.client_id WHERE k.id = %s"
            + (" FOR UPDATE OF k" if for_update else ""),
            (consent_id,),
        )
        return cur.fetchone()


def consent_is_gone(row: dict[str, Any]) -> bool:
    """Expired, or its client disabled since (the 410 states; a decided consent is checked apart)."""
    return bool(row["expired"]) or row["client_disabled_at"] is not None


class ConsentDecided(Exception):
    """The consent was already answered (409)."""


class ConsentGone(Exception):
    """The consent expired or its client was disabled (410)."""


def _redirect(base: str, **params: str | None) -> str:
    # The SDK's own helper (RFC 6749 query parameters appended to the registered redirect URI); imported lazily,
    # since only a mounted authorization server ever creates a consent to decide.
    from mcp.server.auth.provider import construct_redirect_uri

    return construct_redirect_uri(base, **params)


def decide_consent(
    conn: psycopg.Connection,
    consent_id: UUID,
    *,
    user_id: UUID,
    role: str,
    decision: str,
    issuer: str,
) -> str:
    """Record the user's Allow / Deny and return the URL to send the browser to.

    Allow grants exactly the requested scopes this user may grant (never ``steward`` to a plain user), creates or
    updates the user's live grant for the client and mints the single-use code. Deny records the decision and
    sends ``error=access_denied`` (+ ``state``). Raises ``LookupError`` (no such consent), ``ConsentDecided``,
    ``ConsentGone``. The caller commits."""
    row = get_consent(conn, consent_id, for_update=True)
    if row is None:
        raise LookupError("no such consent")
    if row["decided_at"] is not None:
        raise ConsentDecided()
    if consent_is_gone(row):
        raise ConsentGone()
    granted = (
        [s for s in row["scopes_requested"] if s in grantable(role)]
        if decision == "allow"
        else None
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_consent SET user_id = %s, decision = %s, scopes_granted = %s, decided_at = now() "
            "WHERE id = %s",
            (user_id, decision, granted, consent_id),
        )
    if decision != "allow":
        return _redirect(
            row["redirect_uri"],
            error="access_denied",
            error_description="The user denied the request.",
            state=row["state"],
            iss=issuer,
        )
    grant_id = upsert_grant(conn, user_id=user_id, client_id=row["client_id"], scopes=granted or [])
    code = new_secret()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_code (code_hash, grant_id, client_id, scopes, redirect_uri, redirect_uri_explicit, "
            "code_challenge, resource, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + %s)",
            (
                hash_secret(code),
                grant_id,
                row["client_id"],
                granted,
                row["redirect_uri"],
                row["redirect_uri_explicit"],
                row["code_challenge"],
                row["resource"],
                CODE_TTL,
            ),
        )
    return _redirect(row["redirect_uri"], code=code, state=row["state"], iss=issuer)


# --- grants ------------------------------------------------------------------------------------------------------


def upsert_grant(
    conn: psycopg.Connection, *, user_id: UUID, client_id: str, scopes: list[str]
) -> UUID:
    """The user's one LIVE grant for the client: a second consent updates its scopes (and rotates nothing)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM oauth_grant WHERE user_id = %s AND client_id = %s AND revoked_at IS NULL "
            "FOR UPDATE",
            (user_id, client_id),
        )
        live = cur.fetchone()
        if live is not None:
            cur.execute("UPDATE oauth_grant SET scopes = %s WHERE id = %s", (scopes, live["id"]))
            return live["id"]
        gid = uuid.uuid4()
        cur.execute(
            "INSERT INTO oauth_grant (id, user_id, client_id, scopes) VALUES (%s, %s, %s, %s)",
            (gid, user_id, client_id, scopes),
        )
        return gid


def revoke_grant(conn: psycopg.Connection, grant_id: UUID) -> None:
    """Revoke a grant and every live access and refresh token under it. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_grant SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (grant_id,),
        )
        cur.execute(
            "UPDATE oauth_access_token SET revoked_at = now() WHERE grant_id = %s AND revoked_at IS NULL",
            (grant_id,),
        )
        cur.execute(
            "UPDATE oauth_refresh_token SET revoked_at = now() WHERE grant_id = %s AND revoked_at IS NULL",
            (grant_id,),
        )


def get_grant(conn: psycopg.Connection, grant_id: UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM oauth_grant WHERE id = %s", (grant_id,))
        return cur.fetchone()


def list_connections(conn: psycopg.Connection, user_id: UUID) -> list[dict[str, Any]]:
    """The user's grants with the client's name and registration: live first, then revoked, newest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT g.id, g.client_id, g.scopes, g.created_at, g.last_used_at, g.revoked_at, c.client_name, "
            "c.registration FROM oauth_grant g JOIN oauth_client c ON c.client_id = g.client_id "
            "WHERE g.user_id = %s ORDER BY (g.revoked_at IS NOT NULL), g.created_at DESC, g.id",
            (user_id,),
        )
        return list(cur.fetchall())


def _touch_grant(cur: psycopg.Cursor, grant_id: UUID, last: datetime | None) -> None:
    if last is None or last < _now() - TOUCH_EVERY:
        cur.execute("UPDATE oauth_grant SET last_used_at = now() WHERE id = %s", (grant_id,))


# --- codes -------------------------------------------------------------------------------------------------------


def load_code(conn: psycopg.Connection, code: str) -> dict[str, Any] | None:
    """A code that is unknown, used or expired is None. A USED code presented again revokes its grant (OAuth 2.1
    §4.1.2): someone is replaying it."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT k.*, g.user_id, g.revoked_at AS grant_revoked_at, k.expires_at <= now() AS expired "
            "FROM oauth_code k JOIN oauth_grant g ON g.id = k.grant_id WHERE k.code_hash = %s",
            (hash_secret(code),),
        )
        row = cur.fetchone()
    if row is None:
        return None
    if row["used_at"] is not None:
        revoke_grant(conn, row["grant_id"])
        return None
    if row["expired"] or row["grant_revoked_at"] is not None:
        return None
    return row


def use_code(conn: psycopg.Connection, code: str) -> bool:
    """Mark a code used. False when it was used already (a race): the grant is then revoked."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_code SET used_at = now() WHERE code_hash = %s AND used_at IS NULL RETURNING grant_id",
            (hash_secret(code),),
        )
        if cur.fetchone() is not None:
            return True
        cur.execute("SELECT grant_id FROM oauth_code WHERE code_hash = %s", (hash_secret(code),))
        row = cur.fetchone()
    if row is not None:
        revoke_grant(conn, row["grant_id"])
    return False


# --- tokens ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    scopes: list[str]
    expires_in: int


def issue_tokens(
    conn: psycopg.Connection, *, grant_id: UUID, scopes: list[str], resource: str
) -> IssuedTokens:
    """Mint an access token (one hour) and a refresh token (thirty days) on the grant."""
    access, refresh = new_secret(), new_secret()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_access_token (token_hash, grant_id, scopes, resource, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + %s)",
            (hash_secret(access), grant_id, scopes, resource, ACCESS_TTL),
        )
        cur.execute(
            "INSERT INTO oauth_refresh_token (token_hash, grant_id, scopes, resource, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + %s)",
            (hash_secret(refresh), grant_id, scopes, resource, REFRESH_TTL),
        )
    return IssuedTokens(access, refresh, scopes, int(ACCESS_TTL.total_seconds()))


def load_refresh(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """A live refresh token with its grant's client, user and scopes, else None. A token already ROTATED (or
    revoked) presented again revokes the whole grant (OAuth 2.1 §6.1 replay detection)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.*, g.client_id, g.user_id, g.scopes AS grant_scopes, g.revoked_at AS grant_revoked_at, "
            "u.disabled_at AS user_disabled_at "
            "FROM oauth_refresh_token r JOIN oauth_grant g ON g.id = r.grant_id "
            "JOIN app_user u ON u.id = g.user_id WHERE r.token_hash = %s",
            (hash_secret(token),),
        )
        row = cur.fetchone()
    if row is None:
        return None
    if row["rotated_to"] is not None or row["revoked_at"] is not None:
        revoke_grant(conn, row["grant_id"])
        return None
    if row["grant_revoked_at"] is not None or row["user_disabled_at"] is not None:
        return None
    return row


class Replayed(Exception):
    """The refresh token was rotated by a concurrent request: the grant is revoked."""


def rotate_refresh(
    conn: psycopg.Connection, token: str, *, scopes: list[str], resource: str
) -> IssuedTokens:
    """Use a refresh token once: mint a new access token (``scopes``) and a successor refresh token (the old
    one's scopes, a fresh thirty days), and stamp the old one's ``rotated_to``. Raises ``Replayed`` when it was
    rotated meanwhile (the grant is then revoked)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT grant_id, scopes FROM oauth_refresh_token WHERE token_hash = %s "
            "AND rotated_to IS NULL AND revoked_at IS NULL FOR UPDATE",
            (hash_secret(token),),
        )
        old = cur.fetchone()
    if old is None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT grant_id FROM oauth_refresh_token WHERE token_hash = %s",
                (hash_secret(token),),
            )
            row = cur.fetchone()
        if row is not None:
            revoke_grant(conn, row["grant_id"])
        raise Replayed()
    access, refresh = new_secret(), new_secret()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_access_token (token_hash, grant_id, scopes, resource, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + %s)",
            (hash_secret(access), old["grant_id"], scopes, resource, ACCESS_TTL),
        )
        cur.execute(
            "INSERT INTO oauth_refresh_token (token_hash, grant_id, scopes, resource, expires_at) "
            "VALUES (%s, %s, %s, %s, now() + %s)",
            (hash_secret(refresh), old["grant_id"], old["scopes"], resource, REFRESH_TTL),
        )
        cur.execute(
            "UPDATE oauth_refresh_token SET rotated_to = %s WHERE token_hash = %s",
            (hash_secret(refresh), hash_secret(token)),
        )
        cur.execute("SELECT last_used_at FROM oauth_grant WHERE id = %s", (old["grant_id"],))
        _touch_grant(cur, old["grant_id"], cur.fetchone()["last_used_at"])
    return IssuedTokens(access, refresh, scopes, int(ACCESS_TTL.total_seconds()))


def load_access(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """A live access token (unexpired, unrevoked, its grant live, its user enabled, its client enabled) joined
    with its grant's client and user, else None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.token_hash, t.scopes, t.resource, t.expires_at, t.last_used_at, t.grant_id, "
            "g.client_id, g.user_id, g.last_used_at AS grant_last_used "
            "FROM oauth_access_token t JOIN oauth_grant g ON g.id = t.grant_id "
            "JOIN app_user u ON u.id = g.user_id JOIN oauth_client c ON c.client_id = g.client_id "
            "WHERE t.token_hash = %s AND t.revoked_at IS NULL AND t.expires_at > now() "
            "AND g.revoked_at IS NULL AND u.disabled_at IS NULL AND c.disabled_at IS NULL",
            (hash_secret(token),),
        )
        return cur.fetchone()


def revoke_access(conn: psycopg.Connection, token: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE oauth_access_token SET revoked_at = now() WHERE token_hash = %s AND revoked_at IS NULL",
            (hash_secret(token),),
        )


def gate_lookup(conn: psycopg.Connection, token: str) -> dict[str, Any] | None:
    """The login door's fourth branch: the bearer as a live OAuth access token, joined with its user's row
    (``u.*`` plus the token's ``scopes`` and the grant's ``client_id``); touches ``last_used_at`` on the token
    and the grant at most every five minutes. None when it is not one."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT u.*, t.token_hash AS oauth_token_hash, t.scopes AS oauth_scopes, "
            "t.last_used_at AS oauth_token_last_used, g.id AS oauth_grant_id, g.client_id AS oauth_client_id, "
            "g.last_used_at AS oauth_grant_last_used "
            "FROM oauth_access_token t JOIN oauth_grant g ON g.id = t.grant_id "
            "JOIN app_user u ON u.id = g.user_id JOIN oauth_client c ON c.client_id = g.client_id "
            "WHERE t.token_hash = %s AND t.revoked_at IS NULL AND t.expires_at > now() "
            "AND g.revoked_at IS NULL AND u.disabled_at IS NULL AND c.disabled_at IS NULL",
            (hash_secret(token),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        last = row["oauth_token_last_used"]
        if last is None or last < _now() - TOUCH_EVERY:
            cur.execute(
                "UPDATE oauth_access_token SET last_used_at = now() WHERE token_hash = %s",
                (row["oauth_token_hash"],),
            )
        _touch_grant(cur, row["oauth_grant_id"], row["oauth_grant_last_used"])
    return row
