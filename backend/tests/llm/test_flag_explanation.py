"""The FLAG-explanation drafter — the PLUMBING (fail-open, FLAG-only, grounding-input, say-so) with a fake
client; no network, no key, no DB.

NOTE: decision #1 — that the explanation names components + direction but never the FINAL adjusted value —
rests on the PROMPT, which a fake client cannot exercise. It is verified in the gate-2 MANUAL run (the
"no final figure" check), not here. These tests guard everything *around* the prompt.
"""

from __future__ import annotations

from datetime import date

from domain.extraction import ExtractedFact, LocatedPassage, Tier
from llm.client import LLMClient, LLMUnavailable
from llm.flag_explanation import _build_user, explain_flag


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


def _flag_burn(**over) -> ExtractedFact:
    base = dict(
        fact_type="cash_burn",
        tier=Tier.FLAG,
        source="10-q-cashflow",
        source_ref="https://sec.gov/smr-10q#p1",
        event_date=date(2026, 3, 31),
        cash_usd=890_000_000.0,
        quarterly_burn_usd=314_678_000.0,
        flags=["possible-one-time"],
        located_passages=[
            LocatedPassage(
                kind="cash-flow-line",
                source_ref="https://sec.gov/smr-10q#p1",
                anchor="264,195",
                excerpt="Partnership milestone payment of 264,195 included in operating cash use.",
            )
        ],
    )
    base.update(over)
    return ExtractedFact(**base)


def test_grounded_flag_returns_the_explanation():
    fake = _FakeClient(
        returns={
            "explanation": "The $314.7M cash use includes a one-time ~$264M milestone; recurring is lower.",
            "grounded": True,
        }
    )
    text, grounded = explain_flag(fake, _flag_burn())
    assert grounded is True
    assert "milestone" in text
    assert len(fake.calls) == 1  # the model WAS consulted for a FLAG candidate


def test_user_message_feeds_the_passage_flag_and_figure():
    """Grounding INPUT: the located excerpt, the flag, and the figure under review all reach the model."""
    user = _build_user(_flag_burn())
    assert "Partnership milestone payment of 264,195" in user  # the located passage
    assert "possible-one-time" in user  # the detected flag
    assert "314,678,000" in user  # the figure under review (formatted)


def test_model_declines_is_say_so_not_fabrication():
    # grounded=false -> the no-ground say-so path (empty, the UI shows "read the passage")
    text, grounded = explain_flag(
        _FakeClient(returns={"explanation": "", "grounded": False}), _flag_burn()
    )
    assert (text, grounded) == ("", False)
    # grounded=true but empty text is also treated as no-explanation (defensive)
    text2, g2 = explain_flag(
        _FakeClient(returns={"explanation": "  ", "grounded": True}), _flag_burn()
    )
    assert (text2, g2) == ("", False)


def test_llm_error_is_fail_open():
    for exc in (LLMUnavailable("no key"), TimeoutError("hung"), RuntimeError("boom")):
        assert explain_flag(_FakeClient(raises=exc), _flag_burn()) == ("", False)


def test_none_output_is_fail_open():
    assert explain_flag(_FakeClient(returns=None), _flag_burn()) == ("", False)


def test_non_flag_tier_is_not_explained():
    """FLAG-only seam: AUTO and HUMAN candidates short-circuit WITHOUT consulting the model (the AUTO value
    is clean; HUMAN/purity is the operator's edge — never model-nudged)."""
    auto = _flag_burn(tier=Tier.AUTO, flags=[])
    human = ExtractedFact(
        fact_type="revenue_mix",
        tier=Tier.HUMAN,
        source="10-k-segment",
        source_ref="https://sec.gov/10k",
        event_date=date(2025, 12, 31),
        located_passages=[
            LocatedPassage(
                kind="segment", source_ref="https://sec.gov/10k", anchor="seg", excerpt="…"
            )
        ],
    )
    fake = _FakeClient(returns={"explanation": "should never be used", "grounded": True})
    assert explain_flag(fake, auto) == ("", False)
    assert explain_flag(fake, human) == ("", False)
    assert fake.calls == []  # the model was NOT consulted for non-FLAG tiers


def test_flag_without_passage_is_fail_open_without_calling_model():
    fake = _FakeClient(returns={"explanation": "x", "grounded": True})
    assert explain_flag(fake, _flag_burn(located_passages=[])) == ("", False)
    assert fake.calls == []  # nothing to ground in -> never consult the model


def test_real_client_offline_gate_raises_then_fails_open(monkeypatch):
    """The real ``LLMClient`` raises ``LLMUnavailable`` when it can't make a live call — and ``explain_flag``
    turns that into the fail-open empty result end-to-end (no key set)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import pytest

    with pytest.raises(LLMUnavailable):
        LLMClient(allow_live=False).draft_structured(system="s", user="u", tool={"name": "t"})
    with pytest.raises(LLMUnavailable):
        LLMClient(allow_live=True).draft_structured(system="s", user="u", tool={"name": "t"})
    # end-to-end: a real, offline client -> the drafter degrades to no-explanation
    assert explain_flag(LLMClient(allow_live=True), _flag_burn()) == ("", False)
