"""The narrative→chain DECOMPOSE drafter — the PLUMBING (fail-open, wiring, blank-guard) with a fake client;
no network, no key, no DB.

NOTE: the no-number-in-the-prose bound rests on the PROMPT, which a fake client cannot exercise. It is
verified in the gate-2 MANUAL run (post a narrative, read the prose, confirm no figure), not here. These
tests guard everything *around* the prompt.
"""

from __future__ import annotations

from llm.chain_decomposition import DECOMPOSE_TOOL, decompose_narrative
from llm.client import LLMClient, LLMUnavailable


class _FakeClient:
    """A stand-in for ``LLMClient`` that records the call and returns/raises whatever the test wants."""

    def __init__(self, *, returns=None, raises: Exception | None = None) -> None:
        self._returns = returns
        self._raises = raises
        self.calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"system": system, "user": user, "tool": tool})
        if self._raises is not None:
            raise self._raises
        return self._returns


_OK = {
    "segments": [
        {
            "label": "Reactor developers",
            "placements": [{"name": "Oklo", "ticker": "OKLO", "prose": "lead SMR developer"}],
        }
    ]
}


def test_decompose_returns_the_tool_output():
    fake = _FakeClient(returns=_OK)
    out = decompose_narrative(fake, "small modular nuclear is about to rip")
    assert out == _OK
    assert len(fake.calls) == 1  # the model WAS consulted
    assert "small modular nuclear" in fake.calls[0]["user"]  # the narrative reaches the model
    assert fake.calls[0]["tool"] is DECOMPOSE_TOOL  # the structured-output contract is wired


def test_decompose_narrative_failopen():
    """Every failure path -> None (fail-open): no key / live disabled / timeout / SDK error, and no tool call.
    The draft endpoint turns None into an empty draft; hand-authoring is untouched."""
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert decompose_narrative(_FakeClient(raises=exc), "a real narrative") is None
    assert (
        decompose_narrative(_FakeClient(returns=None), "a real narrative") is None
    )  # no tool call
    assert decompose_narrative(_FakeClient(returns="not a dict"), "a real narrative") is None


def test_blank_narrative_does_not_call_the_model():
    fake = _FakeClient(returns=_OK)
    assert decompose_narrative(fake, "   ") is None
    assert fake.calls == []  # nothing to decompose -> never consult the model


def test_real_client_offline_gate_fails_open(monkeypatch):
    """The real ``LLMClient`` raises ``LLMUnavailable`` with no key -> ``decompose_narrative`` degrades to
    None end-to-end (no network)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert decompose_narrative(LLMClient(allow_live=True), "a real narrative") is None
