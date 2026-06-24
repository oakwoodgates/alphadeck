"""The lite term-set producer + the deterministic tiering guard (no DB, no network — a fake keyword LLM).

The load-bearing property: SIGNAL is reserved for operator SEEDS, so a keyword-gen proposal is NEVER SIGNAL —
the guard only DROPS junk (generic/regulatory terms + short collision abbreviations) or, by default, demotes it
to BROAD (it counts toward the ≥2-distinct net but never places a company alone). The LLM's own SIGNAL/BROAD
split — the thing that placed ~370 junk names and then swung PLACED 75–150 run-to-run — is discarded entirely.
"""

from __future__ import annotations

import pytest

from domain.enums import TermTier
from workbench.term_set import assign_tier, produce_term_set


@pytest.mark.parametrize(
    "term, expected",
    [
        # BROAD — every surviving keyword-gen proposal (specific compounds, mechanisms, phrases). NONE is
        # SIGNAL: that tier is the operator's seeds alone. They contribute only via the ≥2-distinct net.
        ("psilocybin", TermTier.BROAD),
        ("ibogaine", TermTier.BROAD),
        ("esketamine", TermTier.BROAD),
        (
            "neuroplastogen",
            TermTier.BROAD,
        ),  # the broad psychedelic PHRASE that swung PLACED — now BROAD
        (
            "5-MeO-DMT",
            TermTier.BROAD,
        ),  # hyphenated specific -> NOT the short-abbrev rule, so it survives
        ("5-HT2A", TermTier.BROAD),
        ("psychedelic", TermTier.BROAD),
        ("psychedelic medicine", TermTier.BROAD),
        ("depression", TermTier.BROAD),
        (
            "substance use disorder",
            TermTier.BROAD,
        ),  # the term that placed Con Edison — BROAD, never alone
        (
            "post-traumatic stress disorder",
            TermTier.BROAD,
        ),  # the full indication phrase survives (the bare "PTSD" abbrev drops, below)
        ("ketamine", TermTier.BROAD),
        ("psychedelic therapy", TermTier.BROAD),
        ("ketamine-assisted therapy", TermTier.BROAD),
        # DROP — short collision abbreviations (any case): pure noise, and two would ≥2-combine into a junk
        # placement (Goodyear/Northrop hitting LSD+MDMA). The operator seeds the real compounds as SIGNAL; a
        # bare disease abbrev (PTSD/OCD) drops too — collision-prone — while its full phrase stays BROAD.
        ("MDMA", None),
        ("DMT", None),
        ("LSD", None),
        ("dmt", None),  # lowercase too
        ("PTSD", None),  # 4-char abbrev -> collision-prone -> DROP (full phrase above stays BROAD)
        ("OCD", None),
        # DROP — pure generic / regulatory / process terms
        ("clinical trial", None),
        ("FDA approval", None),
        ("drug development", None),
        ("therapeutic", None),
        ("mental health treatment", None),
        ("therapeutic compound", None),
    ],
)
def test_assign_tier(term, expected):
    assert assign_tier(term) == expected


def test_assign_tier_never_returns_signal():
    """The Option-3 invariant, pinned: no keyword-gen term — however specific — is ever SIGNAL. SIGNAL is the
    operator's seeds alone (set in ``produce_term_set``, bypassing this guard)."""
    for term in ("psilocybin", "5-MeO-DMT", "neuroplastogen", "psychedelic", "ketamine", "MDMA"):
        assert assign_tier(term) is not TermTier.SIGNAL


def test_assign_tier_normalizes_case_and_whitespace():
    assert assign_tier("  Clinical Trial  ") is None  # DROP match is case/space-insensitive
    assert assign_tier("Psilocybin") is TermTier.BROAD  # a survivor is BROAD, never SIGNAL
    assert assign_tier("   ") is None  # blank -> drop


class _FakeKw:
    """A keyword-gen LLM stand-in: ``draft_structured`` returns canned ``{signal, broad}`` (or None)."""

    def __init__(self, returns):
        self._returns = returns

    def draft_structured(self, *, system, user, tool):
        return self._returns


def test_produce_term_set_discards_the_llm_tiering():
    """THE producer guarantee (Option 3): the LLM's SIGNAL/BROAD split is discarded — no keyword-gen term is
    ever SIGNAL. The LLM here put compounds + junk in its SIGNAL tier; the producer DROPS the generic/collision
    ones and demotes EVERY survivor (psilocybin included) to BROAD. SIGNAL is the operator's seeds alone.
    """
    fake = _FakeKw(
        {
            "signal": ["psilocybin", "MDMA", "clinical trial", "substance use disorder"],
            "broad": ["psychedelic"],
        }
    )
    by_term = {e.term: e.tier for e in produce_term_set(fake, "psychedelic therapy")}
    assert (
        by_term["psilocybin"] is TermTier.BROAD
    )  # LLM said SIGNAL -> guard demotes (never SIGNAL)
    assert by_term["psychedelic"] is TermTier.BROAD
    assert by_term["substance use disorder"] is TermTier.BROAD  # contributes only via the ≥2 net
    assert "MDMA" not in by_term  # dropped (pure collision abbrev)
    assert "clinical trial" not in by_term  # dropped entirely (generic + collision-enabling)
    assert all(t is not TermTier.SIGNAL for t in by_term.values())  # no LLM term is SIGNAL


def test_produce_term_set_entries_are_drafted_with_provenance():
    entries = produce_term_set(_FakeKw({"signal": ["psilocybin"], "broad": []}), "x")
    assert (
        entries[0].authored_by.value == "system_drafted"
    )  # the guard's default; operator overrides later
    assert entries[0].source == "keyword_gen"
    assert entries[0].tier is TermTier.BROAD  # a keyword-gen term is BROAD, never SIGNAL


def test_produce_term_set_failopen_no_candidates():
    assert (
        produce_term_set(_FakeKw(None), "x") == []
    )  # generate_keywords -> None (no key) -> empty set


def test_produce_term_set_seeds_are_operator_signal_and_win_dedup():
    """Operator-seeded compounds are anchored as OPERATOR_SET SIGNAL (the recall guarantor) and win the dedup
    over a same-term keyword-gen candidate — even one the guard would have demoted to BROAD."""
    fake = _FakeKw({"signal": [], "broad": ["ibogaine"]})  # the LLM put it broad
    entries = produce_term_set(fake, "x", seeds=["ibogaine"])
    (e,) = entries
    assert e.term == "ibogaine"
    assert e.tier is TermTier.SIGNAL  # seed bypasses the guard
    assert (
        e.authored_by.value == "operator_set" and e.source == "seed"
    )  # operator-anchored, distinct authorship
