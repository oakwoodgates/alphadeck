from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Archetype
from domain.thesis import BasketMember, Catalyst, Evidence, KillCriterion, Position, Thesis

# This module is the ONLY place raw DB rows become domain objects (and back). Raw rows never escape
# `repositories/`; callers always receive domain types (Thesis, CallCard, ...).


def _to_float(value: Decimal | float | None) -> float | None:
    return float(value) if value is not None else None


def row_to_thesis(
    t: dict[str, Any],
    basket: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    catalysts: list[dict[str, Any]],
    kills: list[dict[str, Any]],
) -> Thesis:
    return Thesis(
        id=t["id"],
        tenant_id=t["tenant_id"],
        parent_id=t["parent_id"],
        name=t["name"],
        narrative=t["narrative"],
        ticker=t["ticker"],
        basket=[_row_to_basket_member(b) for b in basket],
        evidence=[_row_to_evidence(e) for e in evidence],
        catalysts=[_row_to_catalyst(c) for c in catalysts],
        kill_criteria=[_row_to_kill(k) for k in kills],
        position=_row_to_position(t),
    )


def _row_to_position(t: dict[str, Any]) -> Position | None:
    if (
        t["position_entry_price"] is None
        and t["position_current_price"] is None
        and t["position_opened_on"] is None
    ):
        return None
    return Position(
        entry_price=_to_float(t["position_entry_price"]),
        current_price=_to_float(t["position_current_price"]),
        opened_on=t["position_opened_on"],
    )


def _row_to_basket_member(b: dict[str, Any]) -> BasketMember:
    return BasketMember(
        ticker=b["ticker"],
        role=b["role"],
        archetype=Archetype(b["archetype"]),
        security_id=b["security_id"],
        detail=b["detail"],
    )


def _row_to_evidence(e: dict[str, Any]) -> Evidence:
    return Evidence(
        id=e["id"], kind=e["kind"], label=e["label"], ref=e["ref"], date_label=e["date_label"]
    )


def _row_to_catalyst(c: dict[str, Any]) -> Catalyst:
    return Catalyst(
        id=c["id"],
        label=c["label"],
        kind=c["kind"],
        when_date=c["when_date"],
        when_label=c["when_label"],
    )


def _row_to_kill(k: dict[str, Any]) -> KillCriterion:
    return KillCriterion(id=k["id"], text=k["text"])


def thesis_to_row(thesis: Thesis) -> dict[str, Any]:
    pos = thesis.position
    return {
        "id": thesis.id,
        "tenant_id": thesis.tenant_id or DEFAULT_TENANT_ID,
        "parent_id": thesis.parent_id,
        "name": thesis.name,
        "narrative": thesis.narrative,
        "ticker": thesis.ticker,
        "position_entry_price": pos.entry_price if pos else None,
        "position_current_price": pos.current_price if pos else None,
        "position_opened_on": pos.opened_on if pos else None,
    }


def call_to_row(card: CallCard, tenant_id: UUID = DEFAULT_TENANT_ID) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "thesis_id": card.thesis_id,
        "asof": card.asof,
        "state": card.state.value,
        "verdict": card.verdict.value,
        "card": card.model_dump(mode="json"),
    }


def row_to_call(row: dict[str, Any]) -> CallCard:
    return CallCard.model_validate(row["card"])
