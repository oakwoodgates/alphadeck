"""Wire contract and bounded work context shared by discoverable capture adapters."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.routers.clock import ClockOut
from domain.market_time import MarketClock

MAX_CARDS = 100
MAX_SECONDS = 120


class CaptureContext(BaseModel):
    clock_response: ClockOut
    clock: Literal["record", "public"]
    known_at: datetime | None
    decisions_asof: date
    countdown_origin: date
    scope: Literal["deployment_visibility_not_authorization"] = (
        "deployment_visibility_not_authorization"
    )
    limitations: list[str]


class CaptureConsistency(BaseModel):
    database: Literal["repeatable_read_read_only"] = "repeatable_read_read_only"
    external_artifacts: Literal["best_effort_generation_guarded"] = "best_effort_generation_guarded"
    generation_before: str
    generation_after: str
    generation_stable: Literal[True] = True


class CapturedResponse(BaseModel):
    method: Literal["GET"] = "GET"
    path: str
    query: dict[str, str] = Field(default_factory=dict)
    status: Literal[200] = 200
    body: Any


class CaptureDestination(BaseModel):
    dashboard: str
    settings: dict[str, Any] = Field(default_factory=dict)


class CaptureNavigation(BaseModel):
    id: str
    kind: Literal["fresh_capture"] = "fresh_capture"
    destination: CaptureDestination


class CaptureReference(BaseModel):
    id: str
    kind: Literal["captured_json"] = "captured_json"
    response_index: int = Field(ge=0)
    pointer: str
    label: str | None = None
    category: str


class DashboardCapture(BaseModel):
    schema_version: Literal["1"] = "1"
    dashboard: str
    asof: date
    captured_at: datetime
    context: CaptureContext
    consistency: CaptureConsistency
    responses: list[CapturedResponse]
    navigation: list[CaptureNavigation] = Field(default_factory=list)
    references: list[CaptureReference] = Field(default_factory=list)


@dataclass(frozen=True)
class CaptureRequest:
    asof: date
    context: CaptureContext
    market_clock: MarketClock
    group_by: Literal["link", "card", "business_type", "sector"]
    include_silent: bool
    window: Literal["1y", "90d", "30d"]
    deadline: float
    thesis_id: UUID | None = None

    def check_budget(self) -> None:
        """Cooperative total bound between reads; SQL also has its own timeout."""
        if time.monotonic() > self.deadline:
            raise HTTPException(
                504, detail="capture exceeded its total work budget; no partial capture returned"
            )


def response(path: str, body: Any, **query: Any) -> CapturedResponse:
    return CapturedResponse(
        path=path,
        query={k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in query.items()},
        body=jsonable_encoder(body),
    )


def limit_cards(count: int) -> None:
    if count > MAX_CARDS:
        raise HTTPException(413, detail=f"capture exceeds {MAX_CARDS} cards; nothing was truncated")
