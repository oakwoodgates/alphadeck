"""The purity-estimate drafter — the PLUMBING (purity-only, grounding-input, fail-open, decline, range guard)
with a fake client; no network, no key, no DB.

NOTE: the BOUND — that the proposed % comes ONLY from figures in the passage, never recalled from memory —
rests on the PROMPT, which a fake client cannot exercise. It is verified in the gate-2 LIVE run (draft purity
on a real name, e.g. LEU's enrichment segment, and confirm the % READS from the carried passage). These tests
guard everything *around* the prompt.
"""

from __future__ import annotations

from datetime import date

from domain.extraction import ExtractedFact, LocatedPassage, Tier
from llm.client import LLMClient, LLMUnavailable
from llm.purity_estimate import _build_user, propose_purity


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


def _purity(**over) -> ExtractedFact:
    base = dict(
        fact_type="revenue_mix",
        tier=Tier.HUMAN,
        source="10-k-segment",
        source_ref="https://sec.gov/leu-10k#seg",
        event_date=date(2025, 12, 31),
        located_passages=[
            LocatedPassage(
                kind="segment",
                source_ref="https://sec.gov/leu-10k#seg",
                anchor="reportable segment",
                excerpt="LEU (enrichment) segment revenue of $346.2M of $448.7M total revenue, FY2025.",
            )
        ],
    )
    base.update(over)
    return ExtractedFact(**base)


_GOOD = {
    "segment": "LEU (enrichment)",
    "pct": 77.0,
    "reason": "Enrichment $346.2M of $448.7M total from the passage.",
    "grounded": True,
}


def test_grounded_proposal_returns_the_estimate():
    fake = _FakeClient(returns=_GOOD)
    prop = propose_purity(fake, "nuclear fuel enrichment", _purity())
    assert prop is not None
    assert prop.pct == 77.0 and prop.segment == "LEU (enrichment)"
    assert "346.2M" in prop.reason
    assert len(fake.calls) == 1  # the model WAS consulted for a purity candidate with a passage


def test_user_message_feeds_the_narrative_and_the_passage():
    """Grounding INPUT: the thesis narrative AND the located segment passage both reach the model."""
    user = _build_user("nuclear fuel enrichment", _purity())
    assert "nuclear fuel enrichment" in user  # the narrative selects the on-thesis segment
    assert "346.2M of $448.7M" in user  # the located passage is the ONLY source for a number


def test_model_declines_is_none_not_fabrication():
    # grounded=false -> None (stay HUMAN; the operator authors it from the filing)
    assert propose_purity(_FakeClient(returns={**_GOOD, "grounded": False}), "n", _purity()) is None


def test_pct_out_of_range_is_rejected():
    for bad in (-5.0, 140.0):
        assert propose_purity(_FakeClient(returns={**_GOOD, "pct": bad}), "n", _purity()) is None


def test_missing_segment_is_rejected():
    assert propose_purity(_FakeClient(returns={**_GOOD, "segment": "  "}), "n", _purity()) is None


def test_llm_error_and_none_output_are_fail_open():
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert propose_purity(_FakeClient(raises=exc), "n", _purity()) is None
    assert propose_purity(_FakeClient(returns=None), "n", _purity()) is None


def test_non_purity_or_no_passage_never_consults_the_model():
    """Purity-only + needs a passage to ground in: a non-revenue_mix candidate or a purity candidate with no
    located passage short-circuits WITHOUT calling the model."""
    fake = _FakeClient(returns=_GOOD)
    shares = ExtractedFact(
        fact_type="shares_outstanding",
        tier=Tier.FLAG,
        source="10-q-cover",
        source_ref="https://sec.gov/x",
        event_date=date(2026, 3, 31),
        value=1_000_000,
        located_passages=[
            LocatedPassage(
                kind="cover", source_ref="https://sec.gov/x", anchor="Class A", excerpt="…"
            )
        ],
    )
    assert propose_purity(fake, "n", shares) is None
    assert propose_purity(fake, "n", _purity(located_passages=[])) is None
    assert fake.calls == []  # the model was NOT consulted


def test_real_client_offline_gate_fails_open(monkeypatch):
    """A real, offline ``LLMClient`` raises ``LLMUnavailable`` inside the seam -> ``propose_purity`` degrades to
    None end-to-end (no key set)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert propose_purity(LLMClient(allow_live=True), "nuclear", _purity()) is None
