"""The DA-filing term matcher (radar/matcher.py) — the collision discipline the tier split was
built around: word boundaries always; acronym-style tokens case-SENSITIVE; phrases
case-insensitive with whitespace-flexible gaps."""

from __future__ import annotations

from domain.enums import TermTier
from domain.thesis import TermSetEntry
from radar.matcher import compile_term, match_term_set


def entry(term: str, tier: TermTier = TermTier.BROAD) -> TermSetEntry:
    return TermSetEntry(term=term, tier=tier)


def test_acronym_never_matches_inside_a_word_or_lowercased():
    p = compile_term("IND")
    assert p.search("filed an IND with the FDA")
    assert not p.search("the INDEX rose")  # the assign_tier collision class
    assert not p.search("independent directors")
    assert not p.search("ind. applications")  # case-sensitive: the all-caps form is the signal


def test_phrase_is_case_insensitive_and_whitespace_flexible():
    p = compile_term("real-world assets")
    assert p.search("tokenized Real-World  Assets on chain")
    assert p.search("real-world\nassets")  # filings wrap lines
    assert not p.search("real-worldassets")  # no boundary collapse


def test_word_boundaries_on_plain_words():
    p = compile_term("psilocybin")
    assert p.search("a Psilocybin therapeutics company")
    assert not p.search("xpsilocybinx")


def test_match_term_set_partitions_by_tier_sorted_unique():
    term_set = [
        entry("psilocybin", TermTier.SIGNAL),
        entry("real-world assets", TermTier.BROAD),
        entry("DRAM", TermTier.BROAD),
        entry("iBuyer", TermTier.SIGNAL),
    ]
    text = "A Psilocybin platform for real-world assets. psilocybin again. dram shops."
    signal, broad = match_term_set(text, term_set)
    assert signal == ["psilocybin"]  # deduped; iBuyer absent
    assert broad == ["real-world assets"]  # DRAM stayed case-sensitive → no hit
