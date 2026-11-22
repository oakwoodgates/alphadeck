"""In-process MCP SDK client helper for prompt-plan tests (no stdio subprocess per test).

The real stdio subprocess + loopback transport is exercised by ``tests/agent_tools/test_server.py``;
prompt-plan tests run against ``build_server`` + ``stdio_principal`` + ``FakeApi().transport()`` directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from mcp.client.client import Client

from agent_tools import server as server_mod
from agent_tools.client import ApiClient
from tests.agent_tools.fake_api import FakeApi

T = TypeVar("T")


def run_in_process(api: FakeApi | None, steps: Callable[[Client], Awaitable[T]]) -> T:
    """Run ``steps(session)`` against an in-process ``AlphaDeckServer`` backed by ``FakeApi``."""

    async def go() -> T:
        client = ApiClient("http://127.0.0.1:8001", transport=(api or FakeApi()).transport())
        token = server_mod.CURRENT_PRINCIPAL.set(await server_mod.stdio_principal(client))
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return await steps(session)
        finally:
            server_mod.CURRENT_PRINCIPAL.reset(token)
            await client.aclose()

    return asyncio.run(go())
