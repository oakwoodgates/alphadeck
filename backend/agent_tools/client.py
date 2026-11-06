"""``ApiClient``: GET-only, loopback-only HTTP to the Alpha Deck API, with every failure mapped to a code.

``trust_env=False`` (no proxy or netrc from the environment), ``follow_redirects=False``, one timeout for the whole
request (default 60 s: the cross-card ``/data-flags`` read and a cold dashboard take up to ~35 s MEASURED). The
client never reads a body into an error message: only the API's own ``detail`` string, cut at 300 characters.
"""

from __future__ import annotations

import ipaddress
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .errors import ToolFailure

DEFAULT_TIMEOUT_S = 60.0
DETAIL_LIMIT = 300


def loopback_base(value: str) -> str:
    """The base URL, if it is a loopback HTTP(S) origin with an explicit port; else ``ValueError``.

    The same rule as the legacy capture server's (copied, not imported): no credentials, no path, no query.
    """
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Base URLs must be loopback HTTP(S) origins with an explicit port.")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Base URLs must be loopback HTTP(S) origins with an explicit port.")
    host = parsed.hostname or ""
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or not parsed.port:
        raise ValueError("Base URLs must be loopback HTTP(S) origins with an explicit port.")
    return value.rstrip("/")


def segment(value: str) -> str:
    """One path segment, percent-encoded (an id from the API or a validated UUID)."""
    return quote(str(value), safe="")


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail[:DETAIL_LIMIT]
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        # FastAPI's validation list: the first problem's location and message (never the rejected input)
        first = detail[0]
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        return f"{loc}: {first.get('msg', '')}"[:DETAIL_LIMIT]
    return f"HTTP {response.status_code}"


class ApiClient:
    """GET the loopback API. ``get`` returns ``(json, seconds)``; a 404 raises ``not_found`` when the caller names
    one (``unknown_thesis``, ``unknown_ticker``), else ``api_rejected``."""

    def __init__(
        self,
        base: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = loopback_base(base)
        self.timeout = timeout
        self._http = httpx.AsyncClient(
            base_url=self.base,
            timeout=httpx.Timeout(timeout),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
            headers={"accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        not_found: ToolFailure | None = None,
    ) -> tuple[Any, float]:
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ToolFailure("internal_error", "Refused a non-canonical API path.")
        query = {
            k: (str(v).lower() if isinstance(v, bool) else str(v))
            for k, v in (params or {}).items()
            if v is not None
        }
        started = time.monotonic()
        try:
            response = await self._http.get(path, params=query)
        except httpx.TimeoutException:
            raise ToolFailure(
                "api_timeout", f"The API did not answer within {self.timeout:g} s."
            ) from None
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError):
            raise ToolFailure(
                "backend_unavailable", "The Alpha Deck API is not reachable at its base URL."
            ) from None
        except httpx.HTTPError:
            raise ToolFailure(
                "backend_unavailable", "The request to the Alpha Deck API failed."
            ) from None
        elapsed = time.monotonic() - started
        status = response.status_code
        if status == 404 and not_found is not None:
            raise not_found
        if status >= 500:
            raise ToolFailure("api_error", f"The API answered HTTP {status}.")
        if status >= 300:
            raise ToolFailure("api_rejected", _detail(response))
        try:
            return response.json(), elapsed
        except ValueError:
            raise ToolFailure(
                "api_error", "The API answered with a body that is not JSON."
            ) from None
