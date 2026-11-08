"""The hosted agent tools endpoint: the same server, mounted in the API at ``/mcp`` (Streamable HTTP, stateless).

Pure composition over the MCP SDK; no auth logic of its own. ``ALPHADECK_PUBLIC_ORIGIN`` set = ``app/main.py``
calls ``mount_hosted``; unset = nothing here is imported (docs/AGENT_TOOLS.md §Hosted endpoint).

* **The login door IS the verifier.** A bearer is valid when the API's own gate accepts it: ``MeVerifier`` asks
  ``GET /auth/me`` in-process (``httpx.ASGITransport``, no socket) with that bearer and nothing else, so a revoked
  token, a disabled user, the system token (403 at ``/auth/me``: it is not a user) and a session cookie alone are
  all 401 here. No cache: a revocation bites on the next request. A personal access token is ``client_id="pat"``
  with its role's scopes; an OAuth access token (docs/CONNECTORS.md) carries the connected app's client id and the
  scopes the user granted, from ``/auth/me``'s ``token`` object.
* **Every API read rides the caller's bearer.** ``hosted_client_for`` builds, per tool call, an ``ApiClient`` over
  the same in-process transport with the request's token; the API's gate then scopes it to the caller's own
  workspace. Nothing here names a tenant; the call closes the client when it ends (``server.track``).
* **Two routes, never a Mount.** A Starlette ``Mount("/mcp")`` answers ``POST /mcp`` with a 307 to ``/mcp/``, and a
  redirect strips the client's Authorization header. So ``/mcp`` is one ``Route`` (GET, POST, DELETE) wrapped
  exactly as the SDK's own ``streamable_http_app`` wraps it, plus the RFC 9728 protected-resource document at
  ``/.well-known/oauth-protected-resource/mcp``.
* **The principal per request.** ``_PrincipalMiddleware`` (inside ``AuthContextMiddleware``) sets
  ``CURRENT_PRINCIPAL`` from the verified token for the request's own task context; the SDK carries that context
  to the tool handler, so interleaved callers never see each other's.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import DEFAULT_TIMEOUT_S, ApiClient
from .errors import ToolFailure
from .principal import CURRENT_PRINCIPAL, READ, STEWARD, Principal, scopes_for_role
from .server import AlphaDeckServer, build_server, track
from .window import in_nightly_window

log = logging.getLogger("agent_tools.hosted")

# The nominal origin of every in-process request: it satisfies ``loopback_base`` and names no socket (the ASGI
# transport never opens one).
IN_PROCESS_BASE = "http://127.0.0.1:8000"
VERIFY_TIMEOUT_S = 10.0
MCP_PATH = "/mcp"


def connector_url(origin: str) -> str:
    """The URL a user gives an AI client."""
    return f"{origin}{MCP_PATH}"


def allowed_hosts(origin: str) -> list[str]:
    """The ``Host`` values the transport accepts. The SDK matches a value exactly, or ``base:*`` as ``base:`` plus
    any port (``mcp.server.transport_security``), so the bare host covers a proxy that sends ``Host $host`` and
    ``host:*`` covers a request carrying a port."""
    host = urlsplit(origin).hostname or ""
    if ":" in host:  # an IPv6 literal travels bracketed in a Host header
        host = f"[{host}]"
    return [host, f"{host}:*"]


class MeVerifier:
    """A bearer is valid when the API's own gate accepts it: ``GET /auth/me`` in-process with the bearer."""

    def __init__(self, api_app: ASGIApp, *, resource: str) -> None:
        self.api_app = api_app
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.api_app),
                base_url=IN_PROCESS_BASE,
                trust_env=False,
                follow_redirects=False,
                timeout=VERIFY_TIMEOUT_S,
                headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            ) as http:
                response = await http.get("/auth/me")
        except (
            Exception
        ) as exc:  # noqa: BLE001 - any failure is "not verified", logged by class only
            log.warning("verify: /auth/me raised %s", type(exc).__name__)
            return None
        status = response.status_code
        if status == 200:
            try:
                me = response.json()
                info = me.get("token") or {}
                if info.get("principal") == "oauth":
                    # an app connected through the OAuth sign-in: its own client id and the scopes the user
                    # granted it (the gate already capped the role by them)
                    return AccessToken(
                        token=token,
                        client_id=str(info["client_id"]),
                        scopes=sorted(str(s) for s in info["scopes"]),
                        subject=str(me["id"]),
                        resource=self.resource,
                        expires_at=None,
                    )
                return AccessToken(
                    token=token,
                    client_id="pat",
                    scopes=sorted(scopes_for_role(me["role"])),
                    subject=str(me["id"]),
                    resource=self.resource,
                    expires_at=None,
                )
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("verify: /auth/me body unreadable (%s)", type(exc).__name__)
                return None
        if status not in (401, 403):  # 403 = the system token: it is not a user
            log.warning("verify: /auth/me answered %s", status)
        return None


class _PrincipalMiddleware:
    """Sets ``CURRENT_PRINCIPAL`` from the verified token for this request (and resets it after)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        access = get_access_token()
        principal = (
            Principal(frozenset(access.scopes), access.subject, access.client_id)
            if access is not None
            else None
        )
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT_PRINCIPAL.reset(reset)


class _NoStandaloneStream:
    """``GET /mcp`` answers 405. Stateless, the server never sends a message the client did not ask for, and the
    SDK would hold a GET open as an idle event stream until the client hangs up; the MCP transport spec lets a
    server answer that GET with 405 instead. POST and DELETE pass through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "GET":
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [(b"allow", b"POST, DELETE"), (b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Hosted:
    """The mounted endpoint: ``routes`` to add to a host app and ``lifespan()`` to enter once in its lifespan."""

    routes: list[Route]
    server: AlphaDeckServer
    session_manager: StreamableHTTPSessionManager
    connector_url: str
    resource_metadata_url: str

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Run the session manager's task group (the SDK allows one ``run()`` per manager)."""
        async with self.session_manager.run():
            yield


def build_hosted(
    api_app: ASGIApp,
    *,
    public_origin: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    trace: bool = False,
    market_tz: str = "America/New_York",
    clock: Callable[[], datetime] = _now,
) -> Hosted:
    """Compose the endpoint over ``api_app`` (the API itself: the verifier and every tool read go through it
    in-process). ``clock`` is injectable for the nightly-window note's tests."""
    resource_url = connector_url(public_origin)
    metadata_url = str(build_resource_metadata_url(AnyHttpUrl(resource_url)))
    verifier = MeVerifier(api_app, resource=resource_url)

    def hosted_client_for() -> ApiClient:
        access = get_access_token()
        if (
            access is None
        ):  # cannot happen behind RequireAuthMiddleware; the guard is for a mis-mount
            raise ToolFailure("internal_error", "The request carries no verified token.")
        return track(
            ApiClient(
                IN_PROCESS_BASE,
                timeout=timeout,
                transport=httpx.ASGITransport(app=api_app),
                token=access.token,
            )
        )

    server = build_server(
        hosted_client_for,
        trace=trace,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(public_origin),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=[READ],
            validate_token_resource=True,
        ),
        token_verifier=verifier,
    )
    server.nightly_window = lambda: in_nightly_window(clock(), market_tz)
    # Built here, not through ``MCPServer.session_manager`` (a read-only property that exists only after the SDK's
    # own ``streamable_http_app()`` built one, and takes no settings): the same constructor that method calls.
    session_manager = StreamableHTTPSessionManager(
        app=server._lowlevel_server,
        stateless=True,
        json_response=False,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts(public_origin),
            allowed_origins=[public_origin],
        ),
    )
    # The SDK's own order (its middleware list [AuthenticationMiddleware, AuthContextMiddleware], outermost first):
    # authentication sets scope["user"], THEN the auth context reads it into get_access_token().
    endpoint: Any = AuthenticationMiddleware(
        AuthContextMiddleware(
            RequireAuthMiddleware(
                _NoStandaloneStream(_PrincipalMiddleware(StreamableHTTPASGIApp(session_manager))),
                required_scopes=[READ],
                resource_metadata_url=AnyHttpUrl(metadata_url),
            )
        ),
        backend=BearerAuthBackend(verifier, resource_server_url=AnyHttpUrl(resource_url)),
    )
    routes = [
        Route(MCP_PATH, endpoint=endpoint, methods=["GET", "POST", "DELETE"]),
        *create_protected_resource_routes(
            resource_url=AnyHttpUrl(resource_url),
            authorization_servers=[AnyHttpUrl(public_origin)],
            scopes_supported=[READ, STEWARD],
        ),
    ]
    return Hosted(
        routes=routes,
        server=server,
        session_manager=session_manager,
        connector_url=resource_url,
        resource_metadata_url=metadata_url,
    )


def mount_hosted(app: Any, settings: Any) -> Hosted:
    """Append the endpoint's routes to the API (after every router) and keep it on ``app.state.hosted``; the API's
    lifespan enters ``hosted.lifespan()``."""
    hosted = build_hosted(app, public_origin=settings.public_origin, market_tz=settings.market_tz)
    app.router.routes.extend(hosted.routes)
    app.state.hosted = hosted
    return hosted
