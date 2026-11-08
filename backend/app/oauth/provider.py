"""``AlphaDeckAuthProvider``: the MCP SDK's ``OAuthAuthorizationServerProvider`` over the 0080 tables.

The SDK's handlers do the protocol (request parsing, PKCE S256, redirect-URI and scope checks, client
authentication, the RFC error bodies); this class only answers their questions from ``app.oauth.store``. Its DB work
is synchronous and runs on a worker thread (``anyio.to_thread``), one short transaction per call, through the
injected ``connection`` context manager (prod: a fresh connection closed after; tests: the suite's connection).

* ``get_client`` returns a ``LoopbackTolerantClient``: the SDK's exact redirect-URI match, except that a registered
  loopback URI (``http://localhost/...`` or ``http://127.0.0.1/...``) matches the same scheme, host and path on ANY
  port (Claude Code's rule).
* A client secret is stored as its sha256 only, while the SDK's ``ClientAuthenticator`` compares the PRESENTED
  secret with ``client.client_secret``. So the token and revocation routes run behind ``server._PresentedSecret``,
  which reads the presented secret into ``PRESENTED_SECRET`` for the request; ``get_client`` hands the SDK that
  same value only when its sha256 is the stored hash (else a random value no request can match), and the SDK's
  own comparison then decides.
* ``authorize`` never decides: it stores a pending consent and sends the browser to the consent page
  (``/oauth/consent/<id>``), where the signed-in user allows or denies (``app/routers/oauth_consent.py``).

Logs carry client ids and user-id prefixes only, never a token, a code or a secret.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from contextvars import ContextVar
from typing import TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import anyio
import psycopg
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from . import store

log = logging.getLogger("app.oauth")

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
# The secret a request presented at /token or /revoke (``server._PresentedSecret`` sets it for the request).
PRESENTED_SECRET: ContextVar[str | None] = ContextVar(
    "alphadeck_oauth_presented_secret", default=None
)

Connection = Callable[[], AbstractContextManager[psycopg.Connection]]
T = TypeVar("T")


def _is_loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS


def _sans_port(url: str) -> tuple[str, str, str, str]:
    parts = urlsplit(url)
    return (parts.scheme, parts.hostname or "", parts.path or "/", parts.query)


class LoopbackTolerantClient(OAuthClientInformationFull):
    """The SDK's client record with one redirect rule added: a registered loopback URI matches any port."""

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if (
            redirect_uri is not None
            and self.redirect_uris
            and redirect_uri not in self.redirect_uris
        ):
            asked = str(redirect_uri)
            if _is_loopback(asked) and any(
                _is_loopback(str(r)) and _sans_port(str(r)) == _sans_port(asked)
                for r in self.redirect_uris
            ):
                return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


class _Code(AuthorizationCode):
    grant_id: str


class _Refresh(RefreshToken):
    grant_id: str


def check_redirect_uris(uris: list[str]) -> None:
    """Registration hygiene: every redirect URI is https, or http on a loopback host. Raises ``RegistrationError``."""
    for uri in uris:
        parts = urlsplit(uri)
        if parts.scheme == "https" and parts.hostname:
            continue
        if _is_loopback(uri):
            continue
        raise RegistrationError(
            "invalid_redirect_uri", "a redirect URI must be https, or http on localhost"
        )


class AlphaDeckAuthProvider:
    """The SDK's authorization-server provider protocol over the 0080 tables."""

    def __init__(self, connection: Connection, *, public_origin: str) -> None:
        self.connection = connection
        self.public_origin = public_origin
        self.resource = store.connector_url(public_origin)

    async def _db(self, fn: Callable[[psycopg.Connection], T]) -> T:
        def run() -> T:
            with self.connection() as conn:
                try:
                    out = fn(conn)
                except BaseException:
                    conn.rollback()
                    raise
                conn.commit()
                return out

        return await anyio.to_thread.run_sync(run)

    # --- clients ---------------------------------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        presented = PRESENTED_SECRET.get()
        row = await self._db(lambda c: store.get_client(c, client_id))
        if row is None:
            return None
        secret = None
        if row["client_secret_hash"]:
            ok = presented is not None and secrets.compare_digest(
                store.hash_secret(presented), row["client_secret_hash"]
            )
            # the SDK compares the presented secret with this value: hand it back only when it is the stored one
            secret = presented if ok else secrets.token_hex(32)
        return LoopbackTolerantClient(
            client_id=row["client_id"],
            client_name=row["client_name"],
            redirect_uris=row["redirect_uris"],
            scope=row["scope"],
            token_endpoint_auth_method=row["token_endpoint_auth_method"],
            grant_types=row["grant_types"],
            client_secret=secret,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        check_redirect_uris(uris)
        metadata = client_info.model_dump(mode="json", exclude_none=True, exclude={"client_secret"})
        await self._db(
            lambda c: store.insert_dcr_client(
                c,
                client_id=client_info.client_id,
                client_name=client_info.client_name,
                redirect_uris=uris,
                scope=client_info.scope or store.READ,
                token_endpoint_auth_method=client_info.token_endpoint_auth_method or "none",
                client_secret=client_info.client_secret,
                grant_types=list(client_info.grant_types),
                metadata=metadata,
            )
        )
        log.info("oauth: registered client %s", client_info.client_id)

    # --- the authorization request ----------------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        resource = params.resource
        if resource is not None and resource.rstrip("/") != self.resource:
            raise AuthorizeError(
                "invalid_target", "this server issues tokens only for its /mcp endpoint"
            )
        consent_id = await self._db(
            lambda c: store.create_consent(
                c,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                scopes=params.scopes,
                code_challenge=params.code_challenge,
                state=params.state,
                resource=self.resource,
            )
        )
        return f"{self.public_origin}/oauth/consent/{consent_id}"

    # --- codes ----------------------------------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> _Code | None:
        row = await self._db(lambda c: store.load_code(c, authorization_code))
        if row is None:
            return None
        return _Code(
            code=authorization_code,
            scopes=list(row["scopes"]),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=AnyUrl(row["redirect_uri"]),
            redirect_uri_provided_explicitly=row["redirect_uri_explicit"],
            resource=row["resource"],
            subject=str(row["user_id"]),
            grant_id=str(row["grant_id"]),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: _Code
    ) -> OAuthToken:
        def run(conn: psycopg.Connection) -> store.IssuedTokens | None:
            if not store.use_code(conn, authorization_code.code):
                return None
            return store.issue_tokens(
                conn,
                grant_id=UUID(authorization_code.grant_id),
                scopes=authorization_code.scopes,
                resource=authorization_code.resource or self.resource,
            )

        issued = await self._db(run)
        if issued is None:
            raise TokenError("invalid_grant", "authorization code was already used")
        log.info(
            "oauth: code exchanged (client %s, user %s)",
            client.client_id,
            (authorization_code.subject or "")[:8],
        )
        return _token(issued)

    # --- refresh tokens -----------------------------------------------------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> _Refresh | None:
        row = await self._db(lambda c: store.load_refresh(c, refresh_token))
        if row is None:
            return None
        return _Refresh(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            subject=str(row["user_id"]),
            grant_id=str(row["grant_id"]),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: _Refresh, scopes: list[str]
    ) -> OAuthToken:
        def run(conn: psycopg.Connection) -> store.IssuedTokens:
            grant = store.get_grant(conn, UUID(refresh_token.grant_id))
            allowed = set(grant["scopes"]) if grant is not None else set()
            outside = [s for s in scopes if s not in allowed]
            if outside:
                raise TokenError("invalid_scope", f"scope {outside[0]} is not granted")
            return store.rotate_refresh(
                conn,
                refresh_token.token,
                scopes=list(scopes),
                resource=refresh_token.resource or self.resource,
            )

        try:
            issued = await self._db(run)
        except store.Replayed:
            raise TokenError("invalid_grant", "refresh token was already used") from None
        return _token(issued)

    # --- access tokens + revocation ---------------------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await self._db(lambda c: store.load_access(c, token))
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            subject=str(row["user_id"]),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, _Refresh):
            await self._db(lambda c: store.revoke_grant(c, UUID(token.grant_id)))
            log.info("oauth: grant revoked through /revoke (client %s)", token.client_id)
        else:
            await self._db(lambda c: store.revoke_access(c, token.token))


def _token(issued: store.IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        token_type="Bearer",
        expires_in=issued.expires_in,
        scope=" ".join(issued.scopes),
        refresh_token=issued.refresh_token,
    )


def connection_from(factory: Callable[[], psycopg.Connection]) -> Connection:
    """A ``connection`` for the provider that opens one connection per call and closes it after."""
    from contextlib import contextmanager

    @contextmanager
    def cm() -> Iterator[psycopg.Connection]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    return cm


__all__ = [
    "AlphaDeckAuthProvider",
    "LoopbackTolerantClient",
    "PRESENTED_SECRET",
    "check_redirect_uris",
    "connection_from",
]
