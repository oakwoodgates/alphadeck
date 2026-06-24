"""The thesis→keyword generator (discovery Slice 2a) — the PLUMBING with a fake client (no network, no key).
The keyword QUALITY rests on the prompt, which a fake can't exercise (a live gate-2 / the bake-off measures
that); these guard the wiring, the clean/dedup, fail-open, and the prompt CONTRACT (no number + the tool).
"""

from __future__ import annotations

from llm.client import LLMClient, LLMUnavailable
from llm.keyword_gen import KEYWORD_TOOL, generate_keywords
from llm.prompt_loader import load_prompt


class _Fake:
    """A stand-in for ``LLMClient`` recording the call and returning/raising what the test wants."""

    def __init__(self, *, returns=None, raises: Exception | None = None) -> None:
        self._returns = returns
        self._raises = raises
        self.calls: list[dict] = []

    def draft_structured(self, *, system, user, tool):
        self.calls.append({"system": system, "user": user, "tool": tool})
        if self._raises is not None:
            raise self._raises
        return self._returns


def test_generate_keywords_returns_tiers():
    fake = _Fake(returns={"signal": ["psilocybin", "ibogaine"], "broad": ["MDMA", "ketamine"]})
    out = generate_keywords(fake, "psychedelic therapy thesis")
    assert out == (["psilocybin", "ibogaine"], ["MDMA", "ketamine"])
    assert "psychedelic therapy" in fake.calls[0]["user"]  # the narrative reaches the model
    assert fake.calls[0]["tool"] is KEYWORD_TOOL  # the structured contract is wired


def test_generate_keywords_cleans_and_dedups():
    fake = _Fake(
        returns={"signal": ["Psilocybin", " psilocybin ", "", 5, "ibogaine"], "broad": None}
    )
    # dedup case-insensitive, strip, drop blanks / non-strings; a non-list tier -> []
    assert generate_keywords(fake, "x") == (["Psilocybin", "ibogaine"], [])


def test_generate_keywords_failopen():
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert generate_keywords(_Fake(raises=exc), "a narrative") is None
    assert generate_keywords(_Fake(returns=None), "a narrative") is None  # no tool call
    assert generate_keywords(_Fake(returns="not a dict"), "a narrative") is None
    assert (
        generate_keywords(_Fake(returns={"signal": [], "broad": []}), "a narrative") is None
    )  # empty


def test_blank_narrative_does_not_call_the_model():
    fake = _Fake(returns={"signal": ["x"]})
    assert generate_keywords(fake, "   ") is None
    assert fake.calls == []  # nothing to generate -> never consult the model


def test_real_client_offline_gate_failopen(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert generate_keywords(LLMClient(allow_live=True), "a real narrative") is None


def test_keyword_prompt_keeps_no_number_and_the_tool():
    """The prompt CONTRACT: no number (INVARIANT #3) + the forced structured-tool call."""
    p = load_prompt("keyword_gen")
    assert "never a source of a number" in p
    assert "thesis_keywords tool" in p
