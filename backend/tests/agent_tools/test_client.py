"""``ApiClient``: the loopback rule, GET only, no redirects or environment, timeouts and the error mapping."""

from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("mcp")

from agent_tools.client import ApiClient, loopback_base  # noqa: E402
from agent_tools.errors import ToolFailure  # noqa: E402


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:8001",
        "http://localhost:8001/",
        "https://[::1]:8443",
        "http://127.0.0.2:9000",
    ],
)
def test_loopback_origins_with_a_port_are_accepted(value):
    assert loopback_base(value) == value.rstrip("/")


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1",  # no port
        "http://10.0.0.5:8000",
        "http://example.com:8000",
        "http://user:pw@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
        "http://127.0.0.1:8000/?x=1",
        "ftp://127.0.0.1:21",
        "not a url",
    ],
)
def test_non_loopback_or_non_origin_bases_are_refused(value):
    with pytest.raises(ValueError):
        loopback_base(value)


def _get(handler, path="/clock", **kwargs):
    async def go():
        client = ApiClient(
            "http://127.0.0.1:8001", transport=httpx.MockTransport(handler), timeout=5
        )
        try:
            return await client.get(path, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_get_sends_a_get_with_bool_and_none_params_normalized():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["query"] = dict(request.url.params)
        return httpx.Response(200, json={"ok": True})

    body, seconds = _get(handler, "/theses", params={"with_call": True, "asof": None, "n": 3})
    assert body == {"ok": True} and seconds >= 0
    assert seen == {"method": "GET", "query": {"with_call": "true", "n": "3"}}


def test_a_redirect_is_not_followed():
    def handler(request):
        return httpx.Response(307, headers={"location": "http://example.com:80/"})

    with pytest.raises(ToolFailure) as exc:
        _get(handler)
    assert exc.value.code == "api_rejected"


def test_a_404_raises_the_callers_failure_else_api_rejected():
    def handler(request):
        return httpx.Response(404, json={"detail": "thesis not found"})

    with pytest.raises(ToolFailure) as exc:
        _get(handler, not_found=ToolFailure("unknown_thesis", "No thesis has this id."))
    assert exc.value.code == "unknown_thesis"
    with pytest.raises(ToolFailure) as exc:
        _get(handler)
    assert (exc.value.code, exc.value.message) == ("api_rejected", "thesis not found")


def test_a_validation_detail_is_summarized_and_cut():
    def handler(request):
        return httpx.Response(
            422, json={"detail": [{"loc": ["query", "asof"], "msg": "x" * 500, "input": "secret"}]}
        )

    with pytest.raises(ToolFailure) as exc:
        _get(handler)
    assert exc.value.code == "api_rejected"
    assert exc.value.message.startswith("query.asof: ") and len(exc.value.message) == 300
    assert "secret" not in exc.value.message


@pytest.mark.parametrize(
    "raised,code",
    [
        (httpx.ConnectError("refused"), "backend_unavailable"),
        (httpx.RemoteProtocolError("dropped"), "backend_unavailable"),
        (httpx.ReadTimeout("slow"), "api_timeout"),
        (httpx.ConnectTimeout("slow"), "api_timeout"),
    ],
)
def test_transport_failures_map_to_retryable_codes(raised, code):
    def handler(request):
        raise raised

    with pytest.raises(ToolFailure) as exc:
        _get(handler)
    assert exc.value.code == code and exc.value.retryable is True


def test_a_5xx_is_a_retryable_api_error_and_a_non_json_body_too():
    with pytest.raises(ToolFailure) as exc:
        _get(lambda r: httpx.Response(503, text="down"))
    assert (exc.value.code, exc.value.retryable) == ("api_error", True)
    with pytest.raises(ToolFailure) as exc:
        _get(lambda r: httpx.Response(200, text="<html>"))
    assert exc.value.code == "api_error"


def test_a_non_canonical_path_is_refused_before_any_request():
    called = []

    def handler(request):
        called.append(request)
        return httpx.Response(200, json={})

    for path in ("clock", "//evil", "/theses/../admin"):
        with pytest.raises(ToolFailure):
            _get(handler, path)
    assert called == []


def test_the_client_ignores_the_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://10.9.9.9:3128")
    client = ApiClient("http://127.0.0.1:8001")
    assert client._http._trust_env is False
    assert client._http.follow_redirects is False
    asyncio.run(client.aclose())


def test_the_timeout_is_the_clients_default_sixty_seconds():
    client = ApiClient("http://127.0.0.1:8001")
    assert client.timeout == 60.0
    assert client._http.timeout.read == 60.0
    asyncio.run(client.aclose())
