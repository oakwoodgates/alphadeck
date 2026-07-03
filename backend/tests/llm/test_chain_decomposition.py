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
    NARRATE_TOOL,
    decompose_narrative,
    narrate_placements,
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


# --- Bug 2: the prose-fill narration step (per-name thesis-fit for the reconciler-appended names) ---

# the model replies by REF (the list number), never by re-typing the name — the join key can't drift.
_NARR = {
    "placements": [
        {"ref": 1, "prose": "lead psilocybin developer"},
        {"ref": 2, "prose": "5-MeO-DMT for treatment-resistant depression"},
    ]
}


def test_narrate_maps_ref_to_name_and_threads_inputs():
    fake = _FakeClient(returns=_NARR)
    out = narrate_placements(
        fake,
        "psychedelic therapy",
        [
            {"name": "Compass Pathways", "ticker": "CMPS", "segment": "developers"},
            {"name": "GH Research"},
        ],
    )
    # ref 1/2 -> the items' exact names (the stable join key); each carries prose + the off_thesis opinion
    assert out == {
        "Compass Pathways": {"prose": "lead psilocybin developer", "off_thesis": False},
        "GH Research": {
            "prose": "5-MeO-DMT for treatment-resistant depression",
            "off_thesis": False,
        },
    }
    assert len(fake.calls) == 1
    user = fake.calls[0]["user"]
    assert "psychedelic therapy" in user  # the narrative
    assert (
        "1. Compass Pathways (CMPS)" in user and "segment: developers" in user
    )  # NUMBERED line + ticker + segment threaded
    assert fake.calls[0]["tool"] is NARRATE_TOOL  # the narrate structured contract (value-free)


def test_narrate_carries_off_thesis_opinion_and_defaults_false():
    """off_thesis rides each name's narration (the narrator's already-made opinion, surfaced as a bit); ABSENT or
    non-bool defaults False — never flag on missing data (#9 fail-open)."""
    fake = _FakeClient(
        returns={
            "placements": [
                {
                    "ref": 1,
                    "prose": "boilerplate-only mention, no operational tie",
                    "off_thesis": True,
                },
                {"ref": 2, "prose": "lead developer"},  # no off_thesis -> default False
            ]
        }
    )
    out = narrate_placements(fake, "n", [{"name": "Kroger"}, {"name": "Compass"}])
    assert out["Kroger"] == {
        "prose": "boilerplate-only mention, no operational tie",
        "off_thesis": True,
    }
    assert out["Compass"] == {"prose": "lead developer", "off_thesis": False}


def test_narrate_failopen_returns_empty():
    """FAIL-OPEN (#9-safe): any failure -> {} (the names keep prose=""; a narration failure never drops a name)."""
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert narrate_placements(_FakeClient(raises=exc), "n", [{"name": "X"}]) == {}
    assert narrate_placements(_FakeClient(returns=None), "n", [{"name": "X"}]) == {}  # no tool call
    assert narrate_placements(_FakeClient(returns="not a dict"), "n", [{"name": "X"}]) == {}


def test_narrate_no_items_or_blank_narrative_does_not_call_the_model():
    fake = _FakeClient(returns=_NARR)
    assert narrate_placements(fake, "a narrative", []) == {}  # nothing to narrate
    assert narrate_placements(fake, "   ", [{"name": "X"}]) == {}  # blank narrative
    assert fake.calls == []  # never consulted the model


def test_narrate_prompt_forbids_numbers():
    """The no-number bound (#3) rests on the prompt — a fake can't exercise it; pin the contract text."""
    assert "NEVER a number" in load_prompt("chain_narrate")


def test_narrate_prompt_documents_off_thesis():
    """The off_thesis judgment rests on the prompt (grounded, prose-must-state-the-reason) — pin the contract."""
    p = load_prompt("chain_narrate")
    assert "off_thesis" in p and "no discernible connection" in p


class _BatchEcho:
    """Replies by REF for each 'N. Name' line in the user message (so a batched narrate joins every name however
    it's split), recording each call — the harness for the batching/truncation guard. Prose echoes the name so a
    test can assert the ref->name join is correct."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.calls = 0
        self._fail_on = fail_on

    def draft_structured(self, *, system, user, tool):
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("boom")  # simulate ONE batch failing (max_tokens / SDK error)
        placements = []
        for ln in user.splitlines():
            num, dot, rest = ln.partition(". ")
            if dot and num.strip().isdigit():
                name = rest.split(" (")[0].split(" — segment")[0].strip()
                placements.append({"ref": int(num), "prose": f"why {name} fits"})
        return {"placements": placements}


def test_narrate_batches_a_large_list_so_one_call_cannot_truncate_to_nothing():
    """The live gate-2 failure: ~123 names in ONE call -> max_tokens truncation -> 0 parsed -> every prose empty.
    The fix BATCHES, so each call is small and EVERY name gets prose. (Pins call-count = ceil(n/batch).)
    """
    import math

    from llm.chain_decomposition import _NARRATE_BATCH

    items = [{"name": f"Co{i}"} for i in range(_NARRATE_BATCH * 2 + 3)]  # spans 3 batches
    fake = _BatchEcho()
    out = narrate_placements(fake, "a narrative", items)
    assert len(out) == len(items)  # EVERY name narrated — none lost to truncation
    assert out["Co0"]["prose"] == "why Co0 fits"
    assert fake.calls == math.ceil(
        len(items) / _NARRATE_BATCH
    )  # split into batches, not one giant call


def test_narrate_one_failing_batch_does_not_lose_the_others():
    """Per-batch fail-open (#9): a single batch erroring (e.g. max_tokens) skips ONLY its names — the other
    batches still fill — instead of the old all-or-nothing where one failure emptied everything."""
    from llm.chain_decomposition import _NARRATE_BATCH

    items = [{"name": f"Co{i}"} for i in range(_NARRATE_BATCH * 3)]  # exactly 3 full batches
    out = narrate_placements(_BatchEcho(fail_on=2), "a narrative", items)  # batch 2 fails
    assert (
        len(out) == _NARRATE_BATCH * 2
    )  # batches 1 + 3 filled; batch 2's names kept empty (not all lost)
