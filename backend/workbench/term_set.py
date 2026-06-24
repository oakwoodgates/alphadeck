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
    keyword_llm: Any, narrative: str, *, seeds: list[str] | None = None
) -> list[TermSetEntry]:
    """Produce the thesis's tiered term set: keyword-gen PROPOSES candidates (the LLM brainstorm — its OWN
    signal/broad split is discarded), the deterministic ``assign_tier`` guard sets each DEFAULT tier, dropped
    terms are removed, and the survivors become ``system_drafted`` ``TermSetEntry``s. PURE (the LLM via the
    passed client; no DB). Fail-open: no candidates (no key / blank narrative) → ``[]``.

    ``seeds`` are the OPERATOR-anchored canonical compounds (the recall guarantor — keyword-gen is
    non-deterministic, so the compounds it fails to propose this run are simply never discovered; seeds make the
    SIGNAL set COMPLETE + deterministic). A seed is always SIGNAL and ``OPERATOR_SET`` (distinct from the guard's
    ``system_drafted`` keyword-gen entries — exactly what ``authored_by`` is for, and what lets a regenerate
    PRESERVE the seeds while re-rolling the LLM proposals). Seeds are listed FIRST, so a seed wins the
    case-insensitive dedup over a same-term keyword-gen candidate (keeping the SIGNAL + operator authorship).
    """
    candidates: list[tuple[str, str]] = [(s, "seed") for s in (seeds or [])]
    kws = generate_keywords(keyword_llm, narrative)
    if kws is not None:
        signal, broad = kws
        candidates += [(t, "keyword_gen") for t in (*signal, *broad)]

    out: list[TermSetEntry] = []
    seen: set[str] = set()
    for term, src in candidates:
        norm = term.strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        if src == "seed":
            tier, authored = (
                TermTier.SIGNAL,
                Authorship.OPERATOR_SET,
            )  # operator-anchored, bypasses the guard
        else:
            tier, authored = (
                assign_tier(term),
                Authorship.SYSTEM_DRAFTED,
            )  # guard-tiered LLM proposal
        if tier is None:
            continue  # dropped by the guard
        out.append(TermSetEntry(term=term.strip(), tier=tier, authored_by=authored, source=src))
    return out
