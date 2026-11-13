"""The caller's network address (``app/client_address.py``, security PR 5 step 1, docs/SECURITY.md).

Behind nginx every request arrives from the nginx container. nginx overwrites ``X-Real-IP`` with the address it
saw, so the app believes that header ONLY from the nginx container itself (found by resolving the compose service
name). Anyone else (a direct hit on the published port 8000, the local dev loop) is keyed on its own address and
cannot fake one. An IPv6 address counts by its /64 (an attacker usually holds the whole block).
"""

from __future__ import annotations

import pytest

from app import client_address


@pytest.fixture(autouse=True)
def _fresh_cache():
    client_address.reset_cache()
    yield
    client_address.reset_cache()


def _scope(peer: str | None, real_ip: str | None = None) -> dict:
    headers = [(b"host", b"testserver")]
    if real_ip is not None:
        headers.append((b"x-real-ip", real_ip.encode("latin-1")))
    return {
        "type": "http",
        "client": (peer, 51000) if peer is not None else None,
        "headers": headers,
    }


def _nginx_at(monkeypatch, *addresses: str) -> list[str]:
    asked: list[str] = []

    def resolve(name: str) -> frozenset[str]:
        asked.append(name)
        return frozenset(addresses)

    monkeypatch.setattr(client_address, "_resolve", resolve)
    return asked


def test_the_header_from_the_nginx_container_is_believed(monkeypatch):
    asked = _nginx_at(monkeypatch, "172.18.0.5")
    assert client_address.client_address(_scope("172.18.0.5", "203.0.113.9")) == "203.0.113.9"
    assert asked == [client_address.NGINX_SERVICE]


def test_the_header_from_anyone_else_is_ignored(monkeypatch):
    _nginx_at(monkeypatch, "172.18.0.5")
    assert client_address.client_address(_scope("198.51.100.7", "203.0.113.9")) == "198.51.100.7"


def test_two_ipv6_addresses_in_one_64_are_one_key(monkeypatch):
    _nginx_at(monkeypatch, "172.18.0.5")
    a = client_address.client_address(_scope("172.18.0.5", "2001:db8:1:2::1"))
    b = client_address.client_address(_scope("172.18.0.5", "2001:db8:1:2:ffff:ffff:ffff:ffff"))
    c = client_address.client_address(_scope("172.18.0.5", "2001:db8:1:3::1"))
    assert a == b == "2001:db8:1:2::/64"
    assert c != a
    # a direct IPv6 peer is reduced the same way
    assert client_address.client_address(_scope("2001:db8:9:9::7")) == "2001:db8:9:9::/64"


def test_a_name_that_does_not_resolve_trusts_nothing(monkeypatch):
    _nginx_at(monkeypatch)  # resolves to nothing (the local dev loop, the test suite)
    assert client_address.client_address(_scope("172.18.0.5", "203.0.113.9")) == "172.18.0.5"


def test_the_real_resolver_failing_trusts_nothing(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("no such host")

    monkeypatch.setattr(client_address.socket, "getaddrinfo", boom)
    assert client_address._resolve("frontend") == frozenset()
    assert client_address.client_address(_scope("172.18.0.5", "203.0.113.9")) == "172.18.0.5"


def test_no_peer_is_unknown(monkeypatch):
    _nginx_at(monkeypatch, "172.18.0.5")
    assert client_address.client_address(_scope(None, "203.0.113.9")) == "unknown"


def test_a_garbage_header_from_nginx_keys_on_nginx(monkeypatch):
    """Fails toward strict: a header that is not an address keys the request on nginx's own (shared) address."""
    _nginx_at(monkeypatch, "172.18.0.5")
    assert client_address.client_address(_scope("172.18.0.5", "not-an-ip")) == "172.18.0.5"
    assert client_address.client_address(_scope("172.18.0.5")) == "172.18.0.5"


def test_no_header_means_no_lookup(monkeypatch):
    """A request without the header (a direct caller, the local dev loop, the suite) never resolves the nginx name:
    an unresolvable name can take seconds to fail."""
    asked = _nginx_at(monkeypatch, "172.18.0.5")
    assert client_address.client_address(_scope("172.18.0.5")) == "172.18.0.5"
    assert client_address.client_address(_scope("198.51.100.7")) == "198.51.100.7"
    assert asked == []


def test_the_lookup_is_cached_for_a_minute(monkeypatch):
    asked = _nginx_at(monkeypatch, "172.18.0.5")
    now = [1000.0]
    monkeypatch.setattr(client_address, "_clock", lambda: now[0])
    for _ in range(5):
        client_address.client_address(_scope("172.18.0.5", "203.0.113.9"))
    assert len(asked) == 1
    now[0] += client_address.CACHE_S + 1
    client_address.client_address(_scope("172.18.0.5", "203.0.113.9"))
    assert len(asked) == 2
