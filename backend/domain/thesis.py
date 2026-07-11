from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import DomainModel
from domain.enums import Archetype, Authorship, TermTier


class TermSetEntry(DomainModel):
    """One discovery keyword in the thesis's persisted, tiered term set — the SIGNAL/BROAD input the EDGAR
    precision filter reads (it decides which EFTS hits PLACE a company).

    ``term`` is the EFTS phrase; ``tier`` is SIGNAL or BROAD (see ``TermTier``). ``authored_by`` + ``source``
    carry provenance so the future operator-edit UI (confirm / override a term's tier) is a PURE addition on
    the same object: the deterministic ``/terms`` guard writes ``system_drafted``; an operator override becomes
    ``operator_set`` / ``operator_edited``. It is STRUCTURE / config — never a fact or a number (#3).
    """

    term: str
    tier: TermTier
    authored_by: Authorship = (
        Authorship.SYSTEM_DRAFTED
    )  # the guard's default; the operator overrides later
    source: str | None = None  # candidate provenance, e.g. "keyword_gen" / "operator" / "seed"


class Segment(DomainModel):
    """A link in the thesis's value chain (the Workbench decomposition) — STRUCTURE, not a score.

    The per-segment candidate count is DERIVED on read (``len`` of members in the link), never stored.
    """

    label: str  # the link, e.g. "Reactor developers"
    descriptor: str | None = None  # the operator's per-segment tag, e.g. "catalyst-rich"


class BasketMember(DomainModel):
    ticker: str
    role: str  # the name's role in the thesis (operator/decomposition prose)
    archetype: Archetype | None = (
        None  # the name's risk class — decided ONCE, on the finalize screen (the DDRail hint → the
    )
    # operator applies/overrides, #10). NULL = not yet characterized: placement NEVER stamps a default
    # and save NEVER coerces one (item F) — a defaulted archetype on a saved member would read as an
    # operator decision that never happened. Un-decided is un-decided all the way through the spine.
    security_id: UUID | None = None
    detail: str | None = None  # the board/cockpit "met" cell (e.g. "mkt $1.2B")
    segment: str | None = None  # the value-chain link this name sits in (a Thesis.segments label)
    thesis_fit: str | None = (
        None  # WHY it sits in that link — the drafted/edited thesis-fit reasoning (S5); never a fact/number
    )
    conviction: int | None = Field(
        default=None, ge=1, le=5
    )  # the operator's per-name weight (1=starter … 5=full); NULL = unset (never 0). Stored METADATA — it
    # never feeds the meters/verdict/grade (#4: the system sizes from signals, it doesn't judge the idea);
    # carried to the Board / SCORE later. Operator-authored by definition (no LLM recommendation).
    # NAMING GUARD: this OPERATOR conviction (a size weight, stored metadata) is DISTINCT from SIGNAL
    # conviction in `calls/` (conviction_kinds / conviction_grade / key_conviction — deterministic call
    # triggers). They must NEVER cross: wiring operator conviction into the call is a #4 violation.
    authored_by: Authorship = (
        Authorship.OPERATOR_SET
    )  # who placed it (the Workbench authorship seam)


class Evidence(DomainModel):
    """Immutable reference to a filing / data point."""

    id: UUID
    kind: str  # display label e.g. "8-K", "FORM 4", "DATA", "EO"
    label: str
    ref: str  # URL / EDGAR accession
    date_label: str | None = None


class Catalyst(DomainModel):
    id: UUID
    label: str
    kind: str | None = None  # display kind e.g. "earnings", "regulatory"
    when_date: date | None = None  # drives the catalyst_surface filter; None = fuzzy/undated
    when_label: str | None = None  # display string e.g. "~3wk", "Q3"


class KillCriterion(DomainModel):
    id: UUID
    text: str


class ExcludedName(DomainModel):
    """One durably-excluded name (#7): the operator's NO, with the optional why. Applied by the
    EDITOR as pre-seeded greyed state — discovery never filters on it (#9, recall sacred)."""

    security_id: UUID
    ticker: str | None = None  # denormalized display convenience
    reason: str | None = None  # "rejected because X" — optional, always


class Position(DomainModel):
    """Populated once the operator logs a fill — its presence drives the Managing state."""

    entry_price: float | None = None
    current_price: float | None = None
    opened_on: date | None = None


class Thesis(DomainModel):
    """First-class object (invariant #2): narrative, basket, evidence, catalysts, kill criteria, expression."""

    id: UUID
    name: str
    narrative: str  # the operator's words, preserved
    tenant_id: UUID | None = None
    parent_id: UUID | None = None  # nullable now; the umbrella/segment hierarchy lands in M5
    ticker: str | None = None
    basket: list[BasketMember] = Field(default_factory=list)
    segments: list[Segment] = Field(
        default_factory=list
    )  # the value-chain links (Workbench structure)
    term_set: list[TermSetEntry] = Field(
        default_factory=list
    )  # the persisted SIGNAL/BROAD discovery terms (written by /terms, READ by discovery)
    evidence: list[Evidence] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    position: Position | None = None
    # archive, never delete (board hygiene): set ONLY by thesis_repo.set_archived — upsert never
    # names the column, so a promote can neither archive nor resurrect (the term_set guard).
    archived_at: datetime | None = None
    # the durable exclusion set (#7): loaded with the thesis, written ONLY by set_exclusions (the
    # same structural guard — a promote can't wipe the operator's pruning).
    exclusions: list[ExcludedName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _segments_consistent(self) -> "Thesis":
        """Every placed ``member.segment`` must name one of the thesis's segments — the chain stays
        internally consistent (a name can't sit in a link that isn't in the chain)."""
        labels = {s.label for s in self.segments}
        for m in self.basket:
            if m.segment is not None and m.segment not in labels:
                raise ValueError(
                    f"basket member {m.ticker!r} placed in unknown segment {m.segment!r}"
                )
        return self
