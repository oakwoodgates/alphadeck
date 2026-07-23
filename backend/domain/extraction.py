"""The scoring-fact extraction candidate model (the hybrid, Slice hybrid-1).

An ``ExtractedFact`` is what the FILING-PARSER produces per scoring fact — NOT a persisted fact. It is the
three-tier hybrid made concrete:

- ``AUTO`` — the extractor reproduces the value from companyfacts; pre-fill, confirm-and-go.
- ``FLAG`` — the raw value + a DETECTED risk + the LOCATED passage; the operator ratifies the composition in
  seconds. *The extractor never decides — it puts the right passage in front of the decision.*
- ``HUMAN`` — interpretation-bound (purity). The extractor LOCATES the evidence and the operator authors the
  value; it is NEVER auto-valued (purity is the operator's exposure-concentration edge).

Nothing here persists. On the operator's confirm (hybrid-2), the existing ``ingest_*`` writers append the
real fact — ``source_ref`` and ``note`` flow straight from this candidate.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from domain.base import DomainModel


class Tier(StrEnum):
    AUTO = "auto"  # reproduces the value (companyfacts) — pre-fill, confirm-and-go
    FLAG = "flag"  # raw value + a detected risk + a located passage — operator ratifies
    HUMAN = "human"  # interpretation-bound (purity) — located only, NEVER auto-valued


class LocatedPassage(DomainModel):
    """A deterministically-retrieved passage that backs a fact — the evidence put in front of the operator
    (or, later, S5). Retrieval only: a keyword/section match, never a model's reading."""

    kind: str  # "cash-flow" | "cover" | "balance-sheet" | "segment" | "business-description"
    source_ref: str  # the filing URL the excerpt came from
    anchor: str  # the keyword / heading that matched
    excerpt: str  # the surrounding text (deterministic window)
    # The anchor match's character offset in the CLEANED filing text — recorded for AUDITABILITY (the
    # annual-cover path stamps it), never filtered on: an offset bound is a tuned magic number that
    # drops real covers (CAJPY's genuine cover sits ~52k chars deep behind the inline-XBRL context
    # block). None on the passages that predate it.
    offset: int | None = None


class ExtractedFact(DomainModel):
    """One candidate scoring fact for a security, with its tier, raw value(s), located evidence, and flags.
    The shape mirrors the three ``ingest_*`` writers: shares -> ``value``; cash_burn -> ``cash_usd`` +
    ``quarterly_burn_usd``; revenue_mix (purity) -> no value (HUMAN)."""

    fact_type: str  # "revenue_mix" | "shares_outstanding" | "cash_burn"
    tier: Tier
    source: str  # the provenance kind the ingest_* will store ("10-q" | "10-q-cover" | "10-k-segment" | ...)
    source_ref: str  # the filing URL
    event_date: date  # the filing period / cover date -> the fact's valid_from (no lookahead)
    note: str = ""  # the composition / derivation -> ingest_*.note
    # raw figures — None for HUMAN (purity) and for any value the extractor would only LOCATE, not parse
    value: float | None = None  # shares_outstanding
    cash_usd: float | None = None  # cash_burn
    quarterly_burn_usd: float | None = None  # cash_burn
    # One OBSERVED condition, one label (a flag is evidence, #6 — never a catch-all). A flag marks an
    # EXCEPTION needing judgment; COMPOSITION (a cleanly-derived quarter, the marketable-securities
    # basis) rides the note as provenance, never a flag — a flag true of ~every filer carries no
    # information (the re-tier; honest loudness):
    # shares -> "dual-class" | "stale-cover" | "no-companyfacts"
    # cash   -> "ytd-raw" | "possible-one-time" | "stale-cash"
    #           | "no-companyfacts" | "no-cashflow-column" | "no-cash-instant"  (missing-data: value None)
    flags: list[str] = Field(default_factory=list)
    located_passages: list[LocatedPassage] = Field(default_factory=list)
    # How an UNVERIFIED value estimate was produced, when one is present (SURFACE 1b): "llm_proposed" (the
    # grounded purity seam), and — as the surface grows — "computed" / "parsed". None = no estimate value
    # (a plain located-only HUMAN candidate, or the deterministic AUTO/FLAG value whose tier already says how).
    # Display-only provenance so the operator (and the UI) can see WHERE a proposed number came from before
    # they confirm/override; it never makes the value a fact (only the operator's ratify does).
    estimate_source: str | None = None


class ExtractionResult(DomainModel):
    """The extract endpoint's envelope (Retrieval Slice 1): the candidates plus an HONEST reason when
    there are none. The three empty states are DISTINCT (interaction #2 — "we couldn't read it" must
    never masquerade as "there is nothing"; the old bare ``[]`` + the FE's *"nothing to extract or
    ratify here"* copy was false for 44 of the 48 dark names):

    - ``facts`` non-empty → covered. For an annual-cover name that is SHARES ONLY — cash + purity are
      still not covered for it (the FE says so rather than implying the data doesn't exist).
    - ``empty_reason="no-annual-filing"`` → no 10-K/10-Q AND no 20-F/40-F: genuinely nothing on EDGAR
      the extractor can read (SKHY, a brand-new F-1/DRS listing). The only case where "nothing to
      extract" is true.
    - ``empty_reason="cover-not-located"`` → an annual filing EXISTS but its cover instruction could
      not be matched (PBM): the name is UNREAD, not empty — it stays a visible candidate for the next
      pass, and companyfacts alone is deliberately NOT served (a fact without its located passage
      would break the no-passage-no-fact contract).
    """

    facts: list[ExtractedFact] = Field(default_factory=list)
    empty_reason: str | None = None  # "no-annual-filing" | "cover-not-located" | None
