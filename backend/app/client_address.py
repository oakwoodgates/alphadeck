"""Who is calling: the ONE place that reads the caller's network address (security PR 5, docs/SECURITY.md).

Behind nginx every request reaches the backend from the nginx container, so the direct peer (``scope["client"]``)
is nginx for everyone. nginx overwrites ``X-Real-IP`` with the address it saw (``frontend/nginx.conf``, every
proxied location), so the header is believed ONLY when the direct peer IS the nginx container: its address comes
from resolving the compose service name ``frontend``, cached for a minute. Any other peer (a direct hit on the
published port 8000, the local dev loop, the test client) is keyed on its own address and cannot fake one by
sending the header. When the name does not resolve (the local dev loop, tests) no header is trusted at all.

It fails toward strict, never toward spoofable: a missing or malformed header from nginx, or a stale lookup, keys
the request on nginx's own address (every caller then shares one budget, as before this module existed).

An IPv6 address is reduced to its /64 (``2001:db8:1:2::/64``): one subscriber usually holds the whole block, so a
throttle keyed on the full address would be dodged by changing the low bits. A missing peer is ``"unknown"``.

``tests/app/test_client_address_guard.py`` fails if any other module under ``app/`` reads the address.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
from collections.abc import Mapping
from typing import Any

# The compose service name of the nginx container (docker-compose.yml ``frontend``).
NGINX_SERVICE = "frontend"
CACHE_S = 60.0
_HEADER = b"x-real-ip"

_lock = threading.Lock()
_cached: tuple[float, frozenset[str]] | None = None
_clock = time.monotonic


def _resolve(name: str) -> frozenset[str]:
    """Every address ``name`` resolves to, or nothing when it does not resolve."""
    try:
        infos = socket.getaddrinfo(name, None)
    except (OSError, UnicodeError):
        return frozenset()
    return frozenset(str(info[4][0]) for info in infos)


def _trusted_proxies() -> frozenset[str]:
    global _cached
    now = _clock()
    with _lock:
        if _cached is not None and now - _cached[0] < CACHE_S:
            return _cached[1]
    found = _resolve(NGINX_SERVICE)
    with _lock:
        _cached = (now, found)
    return found


def reset_cache() -> None:
    """Forget the nginx lookup (tests)."""
    global _cached
    with _lock:
        _cached = None


def network_key(address: str) -> str:
    """An IPv4 address as is; an IPv6 address as its /64; anything else (a test client's name) as is."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


def _header(scope: Mapping[str, Any]) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == _HEADER:
            text = value.decode("latin-1").strip()
            try:
                return str(ipaddress.ip_address(text))
            except ValueError:
                return None
    return None


def client_address(scope: Mapping[str, Any]) -> str:
    """The caller's address for throttling: the nginx-supplied ``X-Real-IP`` when the peer is nginx, else the
    peer itself; IPv6 reduced to its /64; ``"unknown"`` when there is no peer."""
    peer = scope.get("client")
    if not peer:
        return "unknown"
    host = str(peer[0])
    forwarded = _header(scope)
    # Only a request that carries the header needs the nginx lookup (an unresolvable name can take seconds).
    if forwarded is not None and host in _trusted_proxies():
        return network_key(forwarded)
    return network_key(host)


__all__ = ["CACHE_S", "NGINX_SERVICE", "client_address", "network_key", "reset_cache"]
