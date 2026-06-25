"""The LITE term-set producer — the operator's SEEDS are the only SIGNAL; keyword-gen contributes BREADTH.

The discovery precision bug was keyword-gen putting generic/regulatory/disease terms and short collision
abbreviations into the SIGNAL tier, so the "≥1 signal → PLACED" rule placed ~370 junk names (utilities on
"substance use disorder", Verisign on "MDMA"). Demoting the obvious junk helped but left the LLM still
*half-owning* SIGNAL — it kept promoting broad psychedelic PHRASES (neuroplastogen, hallucinogen, dissociative
anesthetic) the auto-guard couldn't tell from ``psilocybin`` without overfitting, and PLACED swung 75–150
run-to-run because keyword-gen is non-deterministic.

The fix removes the LAST place the LLM decides what's discriminating: **SIGNAL is reserved for operator
SEEDS** (anchored canonical compounds — the recall guarantor, deterministic run-to-run). Every keyword-gen
proposal is therefore only ever **BROAD** (it contributes to the ≥2-distinct corroboration net, never places a
company alone) or **DROPPED** (generic/regulatory noise + short collision abbreviations that would ≥2-combine
into junk). The LLM keeps the role it's good at — proposing breadth — and loses the authority it shouldn't
have. The guard collapses to a denylist; no per-thesis tuning of "is this term signal?".

The result is persisted on the thesis (``thesis_repo.set_term_set``); discovery READS it. The DROP denylist is
genuine, named constant DATA (not buried in branching) so the future operator-edit UI can read/present/extend
it — the guard sets the DEFAULT, the operator overrides (``authored_by`` carries it).
"""

from __future__ import annotations

import re
from typing import Any

from domain.enums import Authorship, TermTier
from domain.thesis import TermSetEntry
from llm.keyword_gen import generate_keywords

# DROP — pure generic / regulatory / process terms. No theme signal AND they enable ≥2-broad collisions (a
# generic biotech/pharma filing hits several), so they are removed ENTIRELY, not merely demoted to BROAD.
_DROP_TERMS: frozenset[str] = frozenset(
    {
        "clinical trial",
        "clinical trials",
        "fda approval",
        "fda",
        "ind",
        "nda",
        "drug development",
        "drug candidate",
        "therapeutic",
        "therapeutics",
        "therapy",
        "treatment",
        "mental health",
        "mental health treatment",
        "behavioral health",
        "pharmaceutical",
        "pharmaceuticals",
        "biotechnology",
        "biotech",
        "clinical",
        "regulatory approval",
        "phase 1",
        "phase 2",
        "phase 3",
        "phase i",
        "phase ii",
        "phase iii",
        "indication",
        "efficacy",
        "safety",
        "psychiatry",
        "clinical psychiatry",
        "neuropsychiatry",
        "psychiatric",
        "medicine",
        "healthcare",
        "drug",
        "therapeutic compound",
        "consciousness research",
    }
)

# A short single alphanumeric token (2-5 chars, any case) is a collision-prone abbreviation — MDMA / DMT / LSD,
# which are ALSO tickers / units / acronyms in unrelated filings. → DROP (not BROAD): as BROAD, two of them
# (e.g. LSD + MDMA in a mega-cap's filing) combine into a ≥2-distinct PLACEMENT — pure-noise junk (Goodyear,
# Northrop). Dropping them costs no recall: the operator seeds the real compounds (as SIGNAL, bypassing this
# guard), and compound-specific exposure rides the BROAD phrases ("MDMA-assisted therapy") via the ≥2 net.
# Hyphenated/longer specifics (5-MeO-DMT, 5-HT2A, psilocybin) don't match and stay BROAD.
_SHORT_ABBREV_RE = re.compile(r"^[A-Za-z0-9]{2,5}$")


def assign_tier(term: str) -> TermTier | None:
    """Tier ONE keyword-gen-PROPOSED term — ``None`` means DROP. SIGNAL is reserved for operator SEEDS, so an
    LLM proposal is never SIGNAL: it is **DROPPED** (generic/regulatory noise in the denylist, or a short
    collision abbreviation that would ≥2-combine into a junk placement) or, by default, **BROAD** (it counts
    only toward the ≥2-distinct corroboration net and never places a company alone). The denylist is the
    OVERRIDABLE default; the operator-edit UI later re-tiers any term (which is why ``authored_by`` exists).
    """
    raw = term.strip()
    norm = raw.lower()
    if not norm:
        return None
    if norm in _DROP_TERMS:  # generic / regulatory / process noise
        return None
    if _SHORT_ABBREV_RE.match(raw):  # a pure short collision abbreviation -> never count toward ≥2
        return None
    return TermTier.BROAD  # every surviving LLM proposal is breadth, never SIGNAL


def produce_term_set(
    keyword_llm: Any,
    narrative: str,
    *,
    seeds: list[str] | None = None,
    operator_terms: list[TermSetEntry] | None = None,
) -> list[TermSetEntry]:
    """Produce the thesis's tiered term set: keyword-gen PROPOSES candidates (the LLM brainstorm — its OWN
    signal/broad split is discarded), the deterministic ``assign_tier`` guard sets each DEFAULT tier, dropped
    terms are removed, and the survivors become ``system_drafted`` ``TermSetEntry``s. PURE (the LLM via the
    passed client; no DB). Fail-open: no candidates (no key / blank narrative) → ``[]``.

    Three sources, three authorities, in PRECEDENCE order (first wins the case-insensitive dedup):

    1. ``operator_terms`` — EXISTING operator-authored entries (``operator_set`` seeds AND ``operator_edited``
       promotions/demotions), preserved **VERBATIM** (term, tier, AND authorship). This is the regenerate-PRESERVE
       guarantee: a re-roll re-generates ONLY the ``system_drafted`` keyword-gen terms; every operator decision
       survives — including a SIGNAL→BROAD demotion, which must come back BROAD, NOT silently re-promoted to
       SIGNAL by a same-term seed below.
    2. ``seeds`` — the OPERATOR-anchored canonical compounds NEW this call (the recall guarantor — keyword-gen is
       non-deterministic, so a compound it fails to propose is simply never discovered; seeds make the SIGNAL set
       COMPLETE + deterministic). Each becomes SIGNAL / ``OPERATOR_SET``.
    3. keyword-gen proposals — guard-tiered (BROAD or DROP), ``system_drafted`` (re-rollable).
    """
    out: list[TermSetEntry] = []
    seen: set[str] = set()

    # 1. preserved operator entries — verbatim (term/tier/authorship), win the dedup
    for e in operator_terms or []:
        norm = e.term.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(e)

    # 2. new seeds -> SIGNAL / operator_set (a seed colliding with a preserved demotion is skipped: the
    #    operator's explicit BROAD wins — re-promoting is the edit endpoint's job, not a regenerate side effect)
    for s in seeds or []:
        norm = s.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(
            TermSetEntry(
                term=s.strip(),
                tier=TermTier.SIGNAL,
                authored_by=Authorship.OPERATOR_SET,
                source="seed",
            )
        )

    # 3. keyword-gen proposals -> guard-tiered, system_drafted (the only re-rollable tier)
    kws = generate_keywords(keyword_llm, narrative)
    if kws is not None:
        signal, broad = kws
        for term in (*signal, *broad):
            norm = term.strip().lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            tier = assign_tier(term)
            if tier is None:
                continue  # dropped by the guard
            out.append(
                TermSetEntry(
                    term=term.strip(),
                    tier=tier,
                    authored_by=Authorship.SYSTEM_DRAFTED,
                    source="keyword_gen",
                )
            )
    return out


def stamp_edited_term_set(
    stored: list[TermSetEntry], edits: list[tuple[str, TermTier]]
) -> list[TermSetEntry]:
    """Stamp authorship on an operator's MANUALLY-edited term set (the ``PUT .../terms/edit`` save path) by
    diffing the submitted ``edits`` (term + tier only) against the ``stored`` set. PURE (no DB, no LLM) — the
    LLM stays OUT of the save path, same principle as LLM-out-of-promote.

    Authorship is the SERVER's to assign, never the body's (a naive client must not be able to mark a term
    ``operator_edited`` and freeze it against regenerate). The load-bearing rule is the UNCHANGED-tier branch:
    an untouched ``system_drafted`` keyword-gen term keeps its authorship, so a later regenerate can re-roll it —
    only operator-TOUCHED entries become operator-authored:

    - not in ``stored`` (operator ADDED) → ``operator_set``, ``source="operator"`` (a net-new term is an
      explicit operator choice regardless of tier; it is then preserved across regenerate).
    - in ``stored``, tier UNCHANGED → carry the stored ``authored_by`` + ``source`` VERBATIM.
    - in ``stored``, tier CHANGED (promote/demote) → ``operator_edited``, preserve the stored ``source``
      (honest origin provenance — a promoted keyword-gen term keeps ``source="keyword_gen"``).
    - removed (in ``stored``, absent from ``edits``) → simply not emitted (a visible operator choice, #9).

    Matching is by normalized key (``strip().lower()``); the operator's submitted casing is persisted (EFTS is
    case-insensitive, so a casing-only change is cosmetic → treated as unchanged-tier). Raises ``ValueError`` on
    an empty term or a case-insensitive duplicate within the submitted list (the router maps it to a 422). Digits
    are allowed — terms legitimately contain them (``5-MeO-DMT``, ``5-HT2A``); #3 bans a numeric FACT, not a
    keyword. An empty ``edits`` returns ``[]`` (the operator cleared the set — a visible, deliberate state).
    """
    by_key = {e.term.strip().lower(): e for e in stored}
    out: list[TermSetEntry] = []
    seen: set[str] = set()
    for term, tier in edits:
        clean = term.strip()
        key = clean.lower()
        if not key:
            raise ValueError("a term cannot be empty")
        if key in seen:
            raise ValueError(f"duplicate term: {term!r}")
        seen.add(key)
        prior = by_key.get(key)
        if prior is None:  # operator added
            authored, source = Authorship.OPERATOR_SET, "operator"
        elif prior.tier == tier:  # untouched -> carry verbatim (keeps system_drafted re-rollable)
            authored, source = prior.authored_by, prior.source
        else:  # re-tiered (promote/demote) -> operator_edited, keep origin provenance
            authored, source = Authorship.OPERATOR_EDITED, prior.source
        out.append(TermSetEntry(term=clean, tier=tier, authored_by=authored, source=source))
    return out
