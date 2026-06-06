from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from domain.call import CallCard, KeyState
from domain.enums import Grade, Kind, State, Verdict
from domain.thesis import Catalyst, Thesis

# API response contracts — the WIRE shape, kept distinct from domain/ so the frontend's generated TS
# types follow the API, not the domain schema. The one real transform vs. the domain CallCard: each
# provenance ref resolves to a clickable EDGAR URL (a presentation concern, not a domain one).

_EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def edgar_url(source: str, ref: str) -> str | None:
    """Resolve a provenance ref to a clickable URL. A Form 4 accession (CIK-YY-SEQ) -> its EDGAR
    filing-index page (the accession's first segment is the filer CIK). Other refs (e.g. price) have
    no canonical URL.
    """
    if source == "form4":
        parts = ref.split("-")
        if len(parts) == 3 and parts[0].isdigit():
            cik = int(parts[0])
            nodash = ref.replace("-", "")
            return f"{_EDGAR_ARCHIVES}/{cik}/{nodash}/{ref}-index.htm"
    return None


class ProvenanceOut(BaseModel):
    source: str
    ref: str
    url: str | None = None  # resolved clickable link (None when not resolvable)
    detail: dict[str, Any] = {}


class TriggerRefOut(BaseModel):
    label: str
    kind: Kind
    grade: Grade | None = None
    sources: list[ProvenanceOut] = []


class CallCardResponse(BaseModel):
    """The CallCard as served — the domain card plus resolved provenance URLs."""

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None
    entry_grade: Grade | None = None
    armed_security_id: UUID | None = None
    expression: str
    exit_by: date | None = None
    arm_until: date | None = None
    catalyst_surface: list[Catalyst] = []
    confidence: float
    key_conviction: KeyState
    key_confirmation: KeyState
    triggers_fired: list[TriggerRefOut] = []
    missing: list[str] = []
    counter_case: str = ""
    safe_sleeve: str | None = None

    @classmethod
    def from_card(cls, card: CallCard) -> "CallCardResponse":
        return cls(
            thesis_id=card.thesis_id,
            asof=card.asof,
            state=card.state,
            verdict=card.verdict,
            conviction_grade=card.conviction_grade,
            entry_grade=card.entry_grade,
            armed_security_id=card.armed_security_id,
            expression=card.expression,
            exit_by=card.exit_by,
            arm_until=card.arm_until,
            catalyst_surface=list(card.catalyst_surface),
            confidence=card.confidence,
            key_conviction=card.key_conviction,
            key_confirmation=card.key_confirmation,
            triggers_fired=[
                TriggerRefOut(
                    label=t.label,
                    kind=t.kind,
                    grade=t.grade,
                    sources=[
                        ProvenanceOut(
                            source=p.source,
                            ref=p.ref,
                            url=edgar_url(p.source, p.ref),
                            detail=p.detail,
                        )
                        for p in t.sources
                    ],
                )
                for t in card.triggers_fired
            ],
            missing=list(card.missing),
            counter_case=card.counter_case,
            safe_sleeve=card.safe_sleeve,
        )


class ThesisSummary(BaseModel):
    """Lightweight list item for the Board (the full thesis comes from GET /theses/{id})."""

    id: UUID
    name: str
    ticker: str | None = None
    narrative: str

    @classmethod
    def from_thesis(cls, thesis: Thesis) -> "ThesisSummary":
        return cls(id=thesis.id, name=thesis.name, ticker=thesis.ticker, narrative=thesis.narrative)
