"""The lite term-set producer + the deterministic tiering guard (no DB, no network — a fake keyword LLM).

The load-bearing property: SIGNAL is reserved for operator SEEDS, so a keyword-gen proposal is NEVER SIGNAL —
the guard only DROPS junk (generic/regulatory terms + short collision abbreviations) or, by default, demotes it
to BROAD (it counts toward the ≥2-distinct net but never places a company alone). The LLM's own SIGNAL/BROAD
split — the thing that placed ~370 junk names and then swung PLACED 75–150 run-to-run — is discarded entirely.
"""

from __future__ import annotations

import pytest

from domain.enums import Authorship, TermTier
from domain.thesis import TermSetEntry
from workbench.term_set import assign_tier, produce_term_set, stamp_edited_term_set


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


# --- regenerate-PRESERVE: operator_terms survive verbatim, only system_drafted re-rolls (the #9 core) ---


def test_produce_term_set_preserves_operator_terms_verbatim():
    """A re-roll preserves EVERY operator-authored entry verbatim (term + tier + authorship). The load-bearing
    case: a SIGNAL→BROAD DEMOTION must come back BROAD (not re-promoted to SIGNAL), and an operator_edited
    PROMOTION must stay SIGNAL — while a prior-roll system_drafted BROAD is re-rolled, not frozen.
    """
    demoted = TermSetEntry(
        term="ketamine",
        tier=TermTier.BROAD,
        authored_by=Authorship.OPERATOR_EDITED,
        source="keyword_gen",
    )
    promoted = TermSetEntry(
        term="ibogaine",
        tier=TermTier.SIGNAL,
        authored_by=Authorship.OPERATOR_EDITED,
        source="keyword_gen",
    )
    seed = TermSetEntry(
        term="psilocybin", tier=TermTier.SIGNAL, authored_by=Authorship.OPERATOR_SET, source="seed"
    )
    fake = _FakeKw({"signal": [], "broad": ["psychedelic"]})  # a fresh keyword-gen roll
    by = {e.term: e for e in produce_term_set(fake, "x", operator_terms=[demoted, promoted, seed])}
    assert by["ketamine"].tier is TermTier.BROAD  # demotion survives — NOT re-promoted to SIGNAL
    assert by["ketamine"].authored_by is Authorship.OPERATOR_EDITED
    assert (
        by["ibogaine"].tier is TermTier.SIGNAL
        and by["ibogaine"].authored_by is Authorship.OPERATOR_EDITED
    )
    assert (
        by["psilocybin"].tier is TermTier.SIGNAL
        and by["psilocybin"].authored_by is Authorship.OPERATOR_SET
    )
    assert by["psychedelic"].authored_by is Authorship.SYSTEM_DRAFTED  # the re-rolled augmentation


def test_produce_term_set_preserved_operator_term_wins_seed_dedup():
    """A new seed that collides with a preserved operator_edited DEMOTION does NOT silently re-promote it: the
    operator's explicit BROAD wins (re-promoting is the edit endpoint's job). Recall-safe — the term stays.
    """
    demoted = TermSetEntry(
        term="ketamine",
        tier=TermTier.BROAD,
        authored_by=Authorship.OPERATOR_EDITED,
        source="keyword_gen",
    )
    (e,) = produce_term_set(_FakeKw(None), "x", seeds=["ketamine"], operator_terms=[demoted])
    assert e.tier is TermTier.BROAD  # the preserved demotion wins over the same-term seed
    assert e.authored_by is Authorship.OPERATOR_EDITED


def test_produce_term_set_operator_terms_listed_first():
    """Preserved operator entries precede the keyword-gen augmentation (so they win the dedup, and the UI shows
    the operator's anchors first)."""
    seed = TermSetEntry(
        term="psilocybin", tier=TermTier.SIGNAL, authored_by=Authorship.OPERATOR_SET, source="seed"
    )
    entries = produce_term_set(
        _FakeKw({"signal": [], "broad": ["ketamine"]}), "x", operator_terms=[seed]
    )
    assert entries[0].term == "psilocybin"  # operator entry first
    assert entries[-1].term == "ketamine"  # keyword-gen augmentation after


# --- stamp_edited_term_set: the manual-save authorship diff (pure, no DB, no LLM) ---


def _stored() -> list[TermSetEntry]:
    return [
        TermSetEntry(
            term="psilocybin",
            tier=TermTier.SIGNAL,
            authored_by=Authorship.OPERATOR_SET,
            source="seed",
        ),
        TermSetEntry(
            term="ketamine",
            tier=TermTier.BROAD,
            authored_by=Authorship.SYSTEM_DRAFTED,
            source="keyword_gen",
        ),
        TermSetEntry(
            term="ibogaine",
            tier=TermTier.BROAD,
            authored_by=Authorship.SYSTEM_DRAFTED,
            source="keyword_gen",
        ),
    ]


def test_stamp_untouched_system_drafted_stays_rerollable():
    """The load-bearing branch: an untouched system_drafted BROAD term keeps its authorship + source, so a
    later regenerate can still re-roll it (it is NOT frozen by passing through the save path)."""
    out = stamp_edited_term_set(
        _stored(), [("psilocybin", TermTier.SIGNAL), ("ketamine", TermTier.BROAD)]
    )
    by = {e.term: e for e in out}
    assert (
        by["ketamine"].authored_by is Authorship.SYSTEM_DRAFTED
        and by["ketamine"].source == "keyword_gen"
    )
    assert by["psilocybin"].authored_by is Authorship.OPERATOR_SET  # untouched seed unchanged too


def test_stamp_promote_and_demote_become_operator_edited():
    out = stamp_edited_term_set(
        _stored(),
        [
            ("psilocybin", TermTier.BROAD),
            ("ketamine", TermTier.SIGNAL),
        ],  # demote a seed, promote a broad
    )
    by = {e.term: e for e in out}
    assert (
        by["psilocybin"].tier is TermTier.BROAD
        and by["psilocybin"].authored_by is Authorship.OPERATOR_EDITED
    )
    assert (
        by["ketamine"].tier is TermTier.SIGNAL
        and by["ketamine"].authored_by is Authorship.OPERATOR_EDITED
    )
    assert by["ketamine"].source == "keyword_gen"  # origin provenance preserved on a re-tier


def test_stamp_added_term_is_operator_set():
    (added,) = [e for e in stamp_edited_term_set([], [("DMT", TermTier.SIGNAL)])]
    assert added.tier is TermTier.SIGNAL
    assert added.authored_by is Authorship.OPERATOR_SET and added.source == "operator"


def test_stamp_removed_term_is_dropped():
    out = stamp_edited_term_set(
        _stored(), [("psilocybin", TermTier.SIGNAL)]
    )  # ketamine + ibogaine removed
    assert [e.term for e in out] == ["psilocybin"]


def test_stamp_allows_digits_in_terms():
    (e,) = stamp_edited_term_set(
        [], [("5-MeO-DMT", TermTier.BROAD)]
    )  # #3 bans a numeric FACT, not a keyword
    assert e.term == "5-MeO-DMT"


def test_stamp_persists_operator_casing_but_matches_case_insensitively():
    """A casing-only change with unchanged tier is treated as untouched (EFTS is case-insensitive), persisting
    the operator's submitted casing."""
    (e,) = stamp_edited_term_set(_stored()[:1], [("Psilocybin", TermTier.SIGNAL)])
    assert e.term == "Psilocybin"  # operator's casing
    assert e.authored_by is Authorship.OPERATOR_SET  # matched the stored seed -> untouched


def test_stamp_empty_list_clears_the_set():
    assert stamp_edited_term_set(_stored(), []) == []


def test_stamp_rejects_empty_term():
    with pytest.raises(ValueError, match="empty"):
        stamp_edited_term_set([], [("   ", TermTier.SIGNAL)])


def test_stamp_rejects_case_insensitive_duplicate():
    with pytest.raises(ValueError, match="duplicate"):
        stamp_edited_term_set([], [("psilocybin", TermTier.SIGNAL), ("Psilocybin", TermTier.BROAD)])
