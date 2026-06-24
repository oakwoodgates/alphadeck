"""The narrative→chain DECOMPOSE drafter — the PLUMBING (fail-open, wiring, blank-guard) with a fake client;
no network, no key, no DB.

NOTE: the no-number-in-the-prose bound rests on the PROMPT, which a fake client cannot exercise. It is
verified in the gate-2 MANUAL run (post a narrative, read the prose, confirm no figure), not here. These
tests guard everything *around* the prompt.
"""

from __future__ import annotations

import pytest

from llm.chain_decomposition import (
    DECOMPOSE_TOOL,
    decompose_narrative,
    research_tail_sweep,
)
from llm.client import LLMClient, LLMUnavailable
from llm.prompt_loader import load_prompt


class _FakeClient:
    """A stand-in for ``LLMClient`` that records each call and returns/raises whatever the test wants. Supports
    the forced-tool ``draft_structured`` (decompose) AND the auto-tool ``research`` (Slice 1) so a test can
    drive the two-step independently."""

    def __init__(
        self,
        *,
        returns=None,
        raises: Exception | None = None,
        research_returns=None,
        research_raises: Exception | None = None,
    ) -> None:
        self._returns = returns
        self._raises = raises
        self._research_returns = research_returns
        self._research_raises = research_raises
        self.calls: list[dict] = []
        self.research_calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"system": system, "user": user, "tool": tool})
        if self._raises is not None:
            raise self._raises
        return self._returns

    def research(self, *, system, user, tool):
        self.research_calls.append({"system": system, "user": user, "tool": tool})
        if self._research_raises is not None:
            raise self._research_raises
        return self._research_returns


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


# --- Slice 1: the research pass + the research_context wiring into decompose ---


def test_decompose_threads_research_context():
    """When ``research_context`` is given, it is appended to the decompose user message (so the model
    decomposes from research, not recall) — the narrative is still present, and the tool is still the
    value-free structured contract."""
    fake = _FakeClient(returns=_OK)
    out = decompose_narrative(
        fake, "psychedelic therapy", research_context="Compass Pathways (CMPS) — lead developer."
    )
    assert out == _OK
    user = fake.calls[0]["user"]
    assert "psychedelic therapy" in user  # the narrative
    assert "Compass Pathways (CMPS)" in user  # the research, threaded as context
    assert "Current research" in user  # the context marker
    assert fake.calls[0]["tool"] is DECOMPOSE_TOOL  # still the value-free structured contract


def test_decompose_without_research_context_is_recall_only():
    """``research_context`` defaults None -> the user message is the narrative alone (exactly today's
    behavior), with no context block."""
    fake = _FakeClient(returns=_OK)
    decompose_narrative(fake, "small modular nuclear")
    user = fake.calls[0]["user"]
    assert "small modular nuclear" in user
    assert "Current research" not in user


def test_research_offline_gate_raises_when_live_disabled():
    """``LLMClient.research`` mirrors ``draft_structured``'s offline gate: live disabled -> LLMUnavailable
    (which ``research_tail_sweep`` catches -> None). allow_live=False is hermetic — independent of any ambient
    key."""
    with pytest.raises(LLMUnavailable):
        LLMClient(allow_live=False).research(
            system="s", user="u", tool={"name": "web_search", "type": "web_search_20250305"}
        )


def test_real_client_research_offline_gate_fails_open(monkeypatch):
    """The real ``LLMClient.research`` raises ``LLMUnavailable`` with no key -> ``research_tail_sweep`` degrades
    to None (no network); the draft then runs the decompose on the EDGAR context alone (or recall-only).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert research_tail_sweep(LLMClient(allow_live=True), "a real narrative", ["X"]) is None


# --- Slice 3: the directed tail-sweep (the foreign/brand-new names EFTS can't see) ---


def test_tail_sweep_returns_synthesis_and_threads_the_found_list():
    fake = _FakeClient(research_returns="New foreign name: PharmAla Biotech.")
    out = research_tail_sweep(fake, "psychedelic therapy", ["Compass Pathways", "GH Research"])
    assert out == "New foreign name: PharmAla Biotech."
    assert len(fake.research_calls) == 1
    user = fake.research_calls[0]["user"]
    assert "psychedelic therapy" in user  # the narrative
    assert "Compass Pathways" in user and "GH Research" in user  # the found list is threaded in
    assert "do NOT re-list" in user  # framed as a directed sweep, not a bare exclusion
    assert fake.research_calls[0]["tool"]["name"] == "web_search"  # the server-side web_search tool


def test_tail_sweep_empty_found_list_still_runs():
    fake = _FakeClient(research_returns="x")
    assert research_tail_sweep(fake, "a narrative", []) == "x"
    assert "(none yet)" in fake.research_calls[0]["user"]


def test_tail_sweep_failopen():
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert research_tail_sweep(_FakeClient(research_raises=exc), "a narrative", ["X"]) is None
    assert research_tail_sweep(_FakeClient(research_returns=None), "a narrative", ["X"]) is None


def test_tail_sweep_blank_narrative_does_not_call_the_model():
    fake = _FakeClient(research_returns="x")
    assert research_tail_sweep(fake, "   ", ["X"]) is None
    assert fake.research_calls == []  # nothing to sweep -> never consult the model


def test_tail_sweep_prompt_keeps_no_number_and_the_redirect():
    """The prompt CONTRACT: no number (#3) + the directed-sweep redirect (not a bare exclusion)."""
    p = load_prompt("tail_sweep")
    assert "NO numbers" in p
    assert "Do NOT re-list" in p
