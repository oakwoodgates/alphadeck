from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from domain.call import CallCard, KeyState
from domain.enums import Grade, Kind, State, Verdict
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Position, Thesis

# API response contracts — the WIRE shape, kept distinct from domain/ so the frontend's generated TS
# types follow the API, not the domain schema. The one real transform vs. the domain CallCard: each
# provenance ref resolves to a clickable EDGAR URL (a presentation concern, not a domain one).

_EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_FILING_SOURCES = frozenset({"form4", "8-k"})  # provenance sources that map to an EDGAR filing


def _is_accession(ref: str) -> bool:
    parts = ref.split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def edgar_url(source: str, ref: str, cik: str | None) -> str | None:
    """Resolve a filing provenance ref to its EDGAR filing-index page, built from the ISSUER ``cik``
    (off security_master) — NOT the accession's prefix, which is the filing AGENT's CIK and only
    coincides with the issuer for some filers. Non-filing refs (e.g. price) or an unknown issuer
    CIK -> None.
    """
    if source in _FILING_SOURCES and cik and _is_accession(ref):
        nodash = ref.replace("-", "")
        return f"{_EDGAR_ARCHIVES}/{int(cik)}/{nodash}/{ref}-index.htm"
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
    # the name this trigger fired on — attributes it in a multi-name basket
    ticker: str | None = None
    sources: list[ProvenanceOut] = []


class CallCardResponse(BaseModel):
    """The CallCard as served — the domain card plus resolved provenance URLs."""

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None
    confirmation_grade: Grade | None = None  # core = volume-backed, flip = momentum-only
    entry_grade: Grade | None = None
    armed_security_id: UUID | None = None
    expression: str
    exit_by: date | None = None
    arm_until: date | None = None
    catalyst_surface: list[Catalyst] = []
    confidence: float | None = None  # the Armed card's bar; None for a not-yet card (§7)
    key_conviction: KeyState
    key_confirmation: KeyState
    triggers_fired: list[TriggerRefOut] = []
    risk_signals: list[TriggerRefOut] = []
    missing: list[str] = []
    counter_case: str = ""
    safe_sleeve: str | None = None

    @classmethod
    def from_card(
        cls,
        card: CallCard,
        cik_for: Mapping[UUID, str | None] | None = None,
        ticker_for: Mapping[UUID, str | None] | None = None,
    ) -> "CallCardResponse":
        ciks = cik_for or {}
        tickers = ticker_for or {}
        return cls(
            thesis_id=card.thesis_id,
            asof=card.asof,
            state=card.state,
            verdict=card.verdict,
            conviction_grade=card.conviction_grade,
            confirmation_grade=card.confirmation_grade,
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
                    ticker=tickers.get(t.security_id),
                    sources=[
                        ProvenanceOut(
                            source=p.source,
                            ref=p.ref,
                            url=edgar_url(p.source, p.ref, ciks.get(t.security_id)),
                            detail=p.detail,
                        )
                        for p in t.sources
                    ],
                )
                for t in card.triggers_fired
            ],
            risk_signals=[
                TriggerRefOut(
                    label=r.label,
                    kind=r.kind,
                    grade=r.grade,
                    ticker=tickers.get(r.security_id),
                    sources=[
                        ProvenanceOut(
                            source=p.source,
                            ref=p.ref,
                            url=edgar_url(p.source, p.ref, ciks.get(r.security_id)),
                            detail=p.detail,
                        )
                        for p in r.sources
                    ],
                )
                for r in card.risk_signals
            ],
            missing=list(card.missing),
            counter_case=card.counter_case,
            safe_sleeve=card.safe_sleeve,
        )


class ThesisSummary(BaseModel):
    """Lightweight list item for the Board (the full thesis comes from GET /theses/{id})."""

    id: UUID
    name: str
    ticker: str | None = None  # None for a multi-name theme thesis; the Board shows a basket marker
    basket_size: int = 0
    narrative: str

    @classmethod
    def from_thesis(cls, thesis: Thesis) -> "ThesisSummary":
        return cls(
            id=thesis.id,
            name=thesis.name,
            ticker=thesis.ticker,
            basket_size=len(thesis.basket),
            narrative=thesis.narrative,
        )


class ThesisDetail(BaseModel):
    """The full thesis for the Cockpit — a wire model (no tenant_id) so generated FE types never bind
    to the domain Thesis. Sub-objects reuse the domain value types (no transform needed, like the
    catalyst surface on CallCardResponse)."""

    id: UUID
    parent_id: UUID | None = None
    name: str
    narrative: str
    ticker: str | None = None
    basket: list[BasketMember] = []
    evidence: list[Evidence] = []
    catalysts: list[Catalyst] = []
    kill_criteria: list[KillCriterion] = []
    position: Position | None = None

    @classmethod
    def from_thesis(cls, t: Thesis) -> "ThesisDetail":
        return cls(
            id=t.id,
            parent_id=t.parent_id,
            name=t.name,
            narrative=t.narrative,
            ticker=t.ticker,
            basket=list(t.basket),
            evidence=list(t.evidence),
            catalysts=list(t.catalysts),
            kill_criteria=list(t.kill_criteria),
            position=t.position,
        )
