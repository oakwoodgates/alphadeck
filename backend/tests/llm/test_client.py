"""The LLMClient truncation guard — a forced tool call cut off at max_tokens must warn LOUD (#9), never return a
silently-empty tool input as if the model produced nothing. Stubs the SDK response (no key / network).
"""

from __future__ import annotations

import logging

from llm.client import LLMClient

_TOOL = {"name": "draft_value_chain"}


class _Block:
    def __init__(self, *, type: str, name: str | None = None, input: dict | None = None) -> None:
        self.type = type
        self.name = name
        self.input = input


class _Resp:
    def __init__(self, stop_reason: str, content: list[_Block]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _FakeStream:
    """Mimics the SDK's messages.stream(...) context manager — draft_structured STREAMS now (long generations
    get server-dropped non-streaming); get_final_message() returns the accumulated Message."""

    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def get_final_message(self):
        return self._resp


class _FakeMessages:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    def stream(self, **_kw):
        return _FakeStream(self._resp)


class _FakeAnthropic:
    def __init__(self, resp: _Resp) -> None:
        self.messages = _FakeMessages(resp)


def _client_returning(resp: _Resp) -> LLMClient:
    c = LLMClient(allow_live=True, api_key="x")
    c._live_client = lambda: _FakeAnthropic(resp)  # bypass the real SDK (no key/network)
    return c


def test_draft_structured_warns_loud_on_max_tokens_truncation(caplog):
    # the real failure: a truncated forced tool call returns an EMPTY input {} — behavior unchanged, but LOUD now
    resp = _Resp("max_tokens", [_Block(type="tool_use", name="draft_value_chain", input={})])
    with caplog.at_level(logging.WARNING, logger="alphadeck.llm"):
        out = _client_returning(resp).draft_structured(system="s", user="u", tool=_TOOL)
    assert out == {}  # the empty truncated input still returns (fail-open contract unchanged)
    assert any(
        "max_tokens" in r.getMessage() and "draft_value_chain" in r.getMessage()
        for r in caplog.records
    )


def test_draft_structured_no_warn_on_clean_tool_use(caplog):
    resp = _Resp(
        "tool_use", [_Block(type="tool_use", name="draft_value_chain", input={"segments": [1]})]
    )
    with caplog.at_level(logging.WARNING, logger="alphadeck.llm"):
        out = _client_returning(resp).draft_structured(system="s", user="u", tool=_TOOL)
    assert out == {"segments": [1]}
    assert not any("max_tokens" in r.getMessage() for r in caplog.records)
