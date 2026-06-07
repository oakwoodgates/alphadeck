from __future__ import annotations

import uuid
from datetime import date

from domain.config import DEFAULT_CONFIG
from domain.enums import Archetype, CatalystType, Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Thesis

ASOF = date(2026, 6, 2)
SID = uuid.UUID(int=0x1111)


def make_thesis(**overrides) -> Thesis:
    data = dict(
        id=uuid.UUID(int=0x2222),
        name="Psychedelic Therapy",
        narrative="Bullish psychedelic therapy for a decade; the regulatory regime is finally shifting.",
        ticker="DEVCO",
        basket=[
            BasketMember(
                ticker="DEVCO",
                role="Lead developer (launch-ready)",
                archetype=Archetype.LEADER,
                security_id=SID,
            )
        ],
        evidence=[
            Evidence(
                id=uuid.UUID(int=0xE1),
                kind="FORM 4",
                label="Cluster of open-market insider buys",
                ref="0001234567-26-000123",
                date_label="2 wks",
            )
        ],
        catalysts=[
            Catalyst(
                id=uuid.UUID(int=0xC1),
                label="Group earnings — revenue ramp vs guidance",
                kind="earnings",
                when_date=date(2026, 6, 11),
                when_label="~9d",
            ),
            Catalyst(
                id=uuid.UUID(int=0xC2),
                label="Launch-readiness milestone",
                kind="clinical",
                when_date=date(2026, 9, 1),
                when_label="~Q3",
            ),
        ],
        kill_criteria=[
            KillCriterion(
                id=uuid.UUID(int=0xC3), text="EO momentum fades without FDA follow-through"
            ),
            KillCriterion(
                id=uuid.UUID(int=0xC4), text="Lead developer dilutes ahead of the catalyst"
            ),
        ],
    )
    data.update(overrides)
    return Thesis(**data)


def insider_event(
    grade: Grade = Grade.CORE, score: float = 0.82, fired: bool = True, liveness: int | None = None
) -> SignalEvent:
    """Key 1 (Conviction) — warms but does not arm on its own. The liveness window is GRADED to match the
    detector (core = the multi-month hold horizon, flip = short); pass ``liveness`` to override."""
    if liveness is None:
        liveness = (
            DEFAULT_CONFIG.insider_core_alpha_liveness_days
            if grade is Grade.CORE
            else DEFAULT_CONFIG.insider_flip_alpha_liveness_days
        )
    return SignalEvent(
        detector="insider_conviction",
        security_id=SID,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.INSIDER,
        grade=grade,
        score=score,
        fired=fired,
        label="3 insiders incl. CEO+CFO bought $2.1M open-market (code P), 9d pre-earnings",
        alpha_liveness_days=liveness,
        provenance=[Provenance(source="form4", ref="0001234567-26-000123")],
        asof=ASOF,
    )


def breakout_event(
    grade: Grade = Grade.CORE, score: float = 0.70, fired: bool = True, liveness: int = 10
) -> SignalEvent:
    """Key 2 (Confirmation) — the deliberately-minimal volume-breakout detector."""
    return SignalEvent(
        detector="volume_breakout",
        security_id=SID,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=grade,
        score=score,
        fired=fired,
        label="Breakout from a 9-week base on 2.4x average volume",
        alpha_liveness_days=liveness,
        provenance=[Provenance(source="price", ref="price:DEVCO:2026-06-02")],
        asof=ASOF,
    )


def catalyst_event(
    grade: Grade = Grade.FLIP, score: float = 0.5, fired: bool = True, liveness: int = 400
) -> SignalEvent:
    """Key 1 (Conviction) — a catalyst. Liveness is the relevance HORIZON, DECOUPLED from grade, so a
    provisional (flip) but long-horizon catalyst is hold-worthy (a STARTER), unlike a fast insider flip.
    """
    return SignalEvent(
        detector="catalyst_conviction",
        security_id=SID,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.CATALYST,
        type=CatalystType.GOV_FUNDING,
        grade=grade,
        score=score,
        fired=fired,
        label="DOE Reactor Pilot Program OTA (provisional, long-horizon)",
        alpha_liveness_days=liveness,
        provenance=[Provenance(source="ratified", ref="https://usaspending.gov/award/DENE0009589")],
        asof=ASOF,
    )


def dilution_event(score: float = 0.80, fired: bool = True) -> SignalEvent:
    """A severe risk signal — should block the Armed call on timing without vetoing the thesis."""
    return SignalEvent(
        detector="dilution_clock",
        security_id=SID,
        role=Role.RISK_SIGNAL,
        kind=Kind.DILUTION_RISK,
        grade=None,
        score=score,
        fired=fired,
        label="Runway ~4 months at current burn; recent ATM shelf on file",
        alpha_liveness_days=None,
        provenance=[Provenance(source="xbrl", ref="cash:DEVCO:2026Q1")],
        asof=ASOF,
    )
