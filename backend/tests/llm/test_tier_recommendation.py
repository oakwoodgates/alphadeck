"""The tier RECOMMENDER (INVARIANT #10) — the PLUMBING with a fake client (no network, no key). The
recommendation QUALITY (are the reasons decision-useful?) rests on the prompt, which a fake can't exercise — a
live gate-2 measures that; these guard the wiring, the clean/dedup/drop-malformed, fail-open, and the tool
contract.
"""

from __future__ import annotations

from llm.client import LLMClient, LLMUnavailable
from llm.tier_recommendation import TIER_REC_TOOL, recommend_tiers


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


def test_recommend_tiers_returns_recs():
    fake = _Fake(
        returns={
            "recommendations": [
                {
                    "term": "psilocybin",
                    "tier": "signal",
                    "reason": "a specific psychedelic compound",
                },
                {
                    "term": "nuclear power",
                    "tier": "broad",
                    "reason": "common across unrelated energy filings",
                },
            ]
        }
    )
    out = recommend_tiers(fake, "small modular nuclear", ["psilocybin", "nuclear power"])
    assert out == [
        {"term": "psilocybin", "tier": "signal", "reason": "a specific psychedelic compound"},
        {
            "term": "nuclear power",
            "tier": "broad",
            "reason": "common across unrelated energy filings",
        },
    ]
    assert "small modular nuclear" in fake.calls[0]["user"]  # the narrative reaches the model
    assert (
        "psilocybin" in fake.calls[0]["user"] and "nuclear power" in fake.calls[0]["user"]
    )  # + the terms
    assert fake.calls[0]["tool"] is TIER_REC_TOOL  # the structured contract is wired


def test_recommend_tiers_drops_malformed_dup_and_out_of_enum():
    fake = _Fake(
        returns={
            "recommendations": [
                {"term": "psilocybin", "tier": "signal", "reason": "ok"},
                "not a dict",  # dropped
                {"term": "psilocybin", "tier": "broad", "reason": "dup"},  # dup key -> dropped
                {
                    "term": "ketamine",
                    "tier": "maybe",
                    "reason": "bad tier",
                },  # out-of-enum -> dropped
                {"term": "  ", "tier": "signal", "reason": "blank term"},  # empty term -> dropped
                {
                    "term": "ibogaine",
                    "tier": "BROAD",
                    "reason": "case-insensitive tier",
                },  # kept, lowercased
            ]
        }
    )
    out = recommend_tiers(fake, "x", ["psilocybin", "ketamine", "ibogaine"])
    assert out == [
        {"term": "psilocybin", "tier": "signal", "reason": "ok"},
        {"term": "ibogaine", "tier": "broad", "reason": "case-insensitive tier"},
    ]


def test_recommend_tiers_failopen():
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert recommend_tiers(_Fake(raises=exc), "a narrative", ["t"]) == []
    assert recommend_tiers(_Fake(returns=None), "a narrative", ["t"]) == []  # no tool call
    assert recommend_tiers(_Fake(returns="not a dict"), "a narrative", ["t"]) == []
    assert (
        recommend_tiers(_Fake(returns={"recommendations": []}), "a narrative", ["t"]) == []
    )  # empty


def test_blank_narrative_or_no_terms_does_not_call_the_model():
    fake = _Fake(returns={"recommendations": [{"term": "x", "tier": "signal", "reason": "r"}]})
    assert recommend_tiers(fake, "   ", ["x"]) == []  # blank narrative
    assert recommend_tiers(fake, "a narrative", []) == []  # no terms
    assert recommend_tiers(fake, "a narrative", ["  ", ""]) == []  # all-blank terms
    assert fake.calls == []  # nothing to recommend -> never consult the model


def test_real_client_offline_gate_failopen(monkeypatch):
    """No key: the REAL client's offline gate (LLMUnavailable) is caught -> [] (fail-open), never an error."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert recommend_tiers(LLMClient(allow_live=True), "a narrative", ["psilocybin"]) == []
