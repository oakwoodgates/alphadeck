"""The authorization server's routes: the MCP SDK's own, composed over ``AlphaDeckAuthProvider`` (docs/CONNECTORS.md).

``build_oauth_routes`` calls the SDK's ``create_auth_routes`` (``/.well-known/oauth-authorization-server``,
``/authorize``, ``/token``, ``/register``, ``/revoke``) and changes three things, none of them a handler:

* the METADATA route is replaced by ours: the SDK's ``build_metadata`` document plus ``none`` among the token and
  revocation auth methods (the static clients and most self-registered ones are public: PKCE, no secret) and
  ``authorization_response_iss_parameter_supported`` (every redirect from the consent page carries ``iss``). Its
  ``issuer`` is ``AnyHttpUrl(public_origin)``: the same value the protected-resource document names in
  ``authorization_servers``, so the two agree byte for byte;
* ``/token`` and ``/revoke`` run behind ``_PresentedSecret``: it reads the client secret the request presents
  (form field or Basic header) into ``provider.PRESENTED_SECRET``, because only its sha256 is stored and the SDK's
  authenticator compares plaintexts (``provider.py`` says how). On ``/revoke`` it also adds an empty
  ``client_secret`` field to a form that has none: the SDK's revocation request model REQUIRES the key (it is
  typed ``str | None`` with no default), so a public client's RFC 7009 request (``token`` + ``client_id``) would
  otherwise be a 400;
* ``/register`` runs behind ``_RegistrationThrottle``: at most 20 registrations per client address per hour and
  200 per day overall (429 beyond; in-process, like the login throttle). The non-https, non-loopback redirect rule
  is the provider's (``invalid_redirect_uri``).

The five routes are Starlette routes outside the API's route-gate walk, each authenticating its own way (the
metadata document is public; ``/token`` and ``/revoke`` authenticate the client; ``/authorize`` hands the decision
to the consent page, which needs the user's session). ``tests/app/test_auth_gate.py`` lists them.
"""

from __future__ import annotations

import base64
import binascii
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote

from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.routes import build_metadata, cors_middleware, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthMetadata
from pydantic import AnyHttpUrl
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import store
from .provider import PRESENTED_SECRET, AlphaDeckAuthProvider, Connection, connection_from

METADATA_PATH = "/.well-known/oauth-authorization-server"
AS_PATHS = (METADATA_PATH, "/authorize", "/token", "/register", "/revoke")
AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]
MAX_FORM_BYTES = 64 * 1024
REGISTRATIONS_PER_ADDRESS_HOUR = 20
REGISTRATIONS_PER_DAY = 200


def registration_options() -> ClientRegistrationOptions:
    return ClientRegistrationOptions(
        enabled=True, valid_scopes=list(store.SCOPES), default_scopes=[store.READ]
    )


def build_oauth_metadata(public_origin: str) -> OAuthMetadata:
    """The SDK's metadata document, plus the public-client auth method and the RFC 9207 ``iss`` flag."""
    metadata = build_metadata(
        AnyHttpUrl(public_origin),
        None,
        registration_options(),
        RevocationOptions(enabled=True),
    )
    metadata.token_endpoint_auth_methods_supported = list(AUTH_METHODS)
    metadata.revocation_endpoint_auth_methods_supported = list(AUTH_METHODS)
    metadata.authorization_response_iss_parameter_supported = True
    return metadata


async def _send_json(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _PresentedSecret:
    """Reads the client secret a POST presents (``client_secret`` in the form, or a Basic header) into
    ``PRESENTED_SECRET`` for the request, then replays the body to the SDK's handler unchanged."""

    def __init__(self, app: ASGIApp, *, add_empty_secret: bool = False) -> None:
        self.app = app
        self.add_empty_secret = add_empty_secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_FORM_BYTES:
                await _send_json(send, 413, b'{"error":"invalid_request"}')
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        body = b"".join(chunks)
        secret = _secret_from(scope, body)
        if (
            self.add_empty_secret
            and _is_form(scope)
            and "client_secret"
            not in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        ):
            body = body + (b"&" if body else b"") + b"client_secret="
            scope = {
                **scope,
                "headers": [
                    (k, str(len(body)).encode() if k == b"content-length" else v)
                    for k, v in scope.get("headers") or []
                ],
            }
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        reset = PRESENTED_SECRET.set(secret)
        try:
            await self.app(scope, replay, send)
        finally:
            PRESENTED_SECRET.reset(reset)


def _is_form(scope: Scope) -> bool:
    headers = dict(scope.get("headers") or [])
    content_type = headers.get(b"content-type", b"").decode("latin-1").lower()
    return content_type.startswith("application/x-www-form-urlencoded")


def _secret_from(scope: Scope, body: bytes) -> str | None:
    headers = dict(scope.get("headers") or [])
    raw = headers.get(b"authorization", b"").decode("latin-1")
    if raw.startswith("Basic "):
        try:
            decoded = base64.b64decode(raw[6:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if ":" in decoded:
            return unquote(decoded.split(":", 1)[1]) or None
        return None
    if _is_form(scope):
        values = parse_qs(body.decode("utf-8", "replace")).get("client_secret")
        return values[0] if values else None
    return None


@dataclass
class RegistrationThrottle:
    """In-process counters of successful registrations (the login throttle's pattern)."""

    per_address_hour: int = REGISTRATIONS_PER_ADDRESS_HOUR
    per_day: int = REGISTRATIONS_PER_DAY
    clock: Callable[[], float] = time.monotonic
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _by_address: dict[str, deque[float]] = field(default_factory=dict)
    _all: deque[float] = field(default_factory=deque)

    def allowed(self, address: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._by_address.setdefault(address, deque())
            while q and now - q[0] > 3600:
                q.popleft()
            while self._all and now - self._all[0] > 86400:
                self._all.popleft()
            return len(q) < self.per_address_hour and len(self._all) < self.per_day

    def record(self, address: str) -> None:
        now = self.clock()
        with self._lock:
            self._by_address.setdefault(address, deque()).append(now)
            self._all.append(now)

    def reset(self) -> None:
        with self._lock:
            self._by_address.clear()
            self._all.clear()


class _RegistrationThrottle:
    def __init__(self, app: ASGIApp, throttle: RegistrationThrottle) -> None:
        self.app = app
        self.throttle = throttle

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        address = client[0] if client else "unknown"
        if not self.throttle.allowed(address):
            await _send_json(
                send,
                429,
                b'{"error":"invalid_client_metadata","error_description":"too many registrations; try later"}',
            )
            return
        status: dict[str, int] = {}

        async def watch(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        await self.app(scope, receive, watch)
        if status.get("code") == 201:
            self.throttle.record(address)


@dataclass
class OAuthServer:
    routes: list[Route]
    provider: AlphaDeckAuthProvider
    metadata: OAuthMetadata
    throttle: RegistrationThrottle


def build_oauth_routes(
    public_origin: str,
    provider: AlphaDeckAuthProvider,
    *,
    throttle: RegistrationThrottle | None = None,
) -> OAuthServer:
    """The SDK's authorization-server routes over ``provider``, its metadata route replaced by ours."""
    throttle = throttle or RegistrationThrottle()
    routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(public_origin),
        client_registration_options=registration_options(),
        revocation_options=RevocationOptions(enabled=True),
    )
    metadata = build_oauth_metadata(public_origin)
    out: list[Route] = []
    for route in routes:
        if route.path == METADATA_PATH:
            route = Route(
                METADATA_PATH,
                endpoint=cors_middleware(MetadataHandler(metadata).handle, ["GET", "OPTIONS"]),
                methods=["GET", "OPTIONS"],
            )
        elif route.path == "/token":
            route.app = _PresentedSecret(route.app)
        elif route.path == "/revoke":
            route.app = _PresentedSecret(route.app, add_empty_secret=True)
        elif route.path == "/register":
            route.app = _RegistrationThrottle(route.app, throttle)
        out.append(route)
    assert sorted(r.path for r in out) == sorted(AS_PATHS), [r.path for r in out]
    return OAuthServer(routes=out, provider=provider, metadata=metadata, throttle=throttle)


def mount_oauth(app: Any, settings: Any, connection: Connection | None = None) -> OAuthServer:
    """Append the authorization server's routes to the API (after the hosted endpoint's) and keep it on
    ``app.state.oauth``."""
    if connection is None:
        from db.session import connect_request

        connection = connection_from(connect_request)
    provider = AlphaDeckAuthProvider(connection, public_origin=settings.public_origin)
    server = build_oauth_routes(settings.public_origin, provider)
    app.router.routes.extend(server.routes)
    app.state.oauth = server
    return server
