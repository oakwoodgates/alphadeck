"""Read-only bounded capture bridge, with no persistence, authentication or tenant isolation.

Database reads share a repeatable-read, read-only transaction; adapters bypass shared caches.
The generation guard detects external cron artifacts moving. Its DB components are frozen by
PostgreSQL and cannot detect concurrent writes (nor need to). Unchanged generation is NOT the
proof of database consistency. Run and repair files remain best effort.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from psycopg.pq import TransactionStatus

from app.deps import get_conn
from app.routers.clock import ClockOut
from app.routers.dashboards.capture_adapters import adapters
from app.routers.dashboards.capture_contract import (
    MAX_SECONDS,
    CaptureConsistency,
    CaptureContext,
    CaptureRequest,
    DashboardCapture,
    limit_cards,
    response,
)
from app.routers.dashboards.capture_links import context_hash, validate_relationships
from dashboards.cache import generation_token
from domain.market_time import (
    countdown_origin,
    market_clock,
    market_tz,
    serve_decisions_asof,
    serve_view,
)

router = APIRouter(prefix="/capture")
MAX_BYTES = 8 * 1024 * 1024


def _generation(conn: psycopg.Connection) -> str:
    value = json.dumps(jsonable_encoder(generation_token(conn)), sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()


@router.get("", response_model=DashboardCapture)
def capture_dashboard(
    dashboard: str = Query(..., pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
    asof: date | None = Query(None),
    group_by: Literal["link", "card", "business_type", "sector"] = Query("link"),
    include_silent: bool = Query(False),
    window: Literal["1y", "90d", "30d"] = Query("1y"),
    thesis_id: UUID | None = Query(None),
    expected_context_hash: str | None = Query(None, pattern=r"^[0-9a-fA-F]{64}$"),
    conn: psycopg.Connection = Depends(get_conn),
) -> DashboardCapture:
    """Capture a registered dashboard through explicit read-only adapters; no authentication supplied.

    Database reads share one snapshot. External artifacts are best-effort guarded: 409 on an
    observed change. 413 rejects excess cards/bytes, 504 rejects an exceeded cooperative total work
    budget (checked between reads, with a per-statement SQL timeout). Never returns a partial capture.
    """
    adapter = adapters().get(dashboard)
    if adapter is None:
        raise HTTPException(422, detail="dashboard has no registered capture adapter")
    thesis_scoped = getattr(adapter, "THESIS_SCOPED", False)
    if thesis_scoped != (thesis_id is not None):
        raise HTTPException(422, detail="thesis_id is required only for a thesis-scoped capture")
    deadline = time.monotonic() + MAX_SECONDS
    mclock = market_clock()
    asof = asof or mclock.night
    known_at, clock = serve_view(asof, night=mclock.night)
    context = CaptureContext(
        clock_response=ClockOut(
            current_night=mclock.night,
            action_day=mclock.action_day,
            market_today=mclock.today,
            market_tz=market_tz().key,
        ),
        clock=clock,
        known_at=known_at,
        decisions_asof=serve_decisions_asof(asof, mclock),
        countdown_origin=countdown_origin(asof, mclock),
        limitations=[
            "Existing deployment visibility is preserved; this endpoint adds no authentication or tenant isolation.",
            "Board summaries and active/archive membership are current metadata even at a historical as-of; calls and price facts follow the public clock.",
            "Board flags use the deployment tenant; cross-card dashboard benchmark and identity scope follows the existing default-tenant readers.",
            "Local run and repair artifacts are outside the database snapshot. An unchanged generation is not proof of atomic artifact reads or a completed cron pass.",
        ]
        + list(getattr(adapter, "LIMITATIONS", ())),
    )
    if (
        expected_context_hash is not None
        and context_hash(asof.isoformat(), context) != expected_context_hash.lower()
    ):
        raise HTTPException(409, detail="context_changed")
    request = CaptureRequest(
        asof, context, mclock, group_by, include_silent, window, deadline, thesis_id
    )
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise HTTPException(503, detail="capture requires a fresh database transaction")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '120s'")
        before = _generation(conn)
        if not thesis_scoped:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM thesis"
                    if adapter.INCLUDE_ARCHIVED
                    else "SELECT count(*) AS n FROM thesis WHERE archived_at IS NULL"
                )
                limit_cards(cur.fetchone()["n"])
        request.check_budget()
        responses = adapter.read(conn, request)
        request.check_budget()
        after = _generation(conn)
        if after != before:
            raise HTTPException(
                409, detail="capture generation changed; retry the complete capture"
            )
        responses = [response("/clock", context.clock_response), *responses]
        hook = getattr(adapter, "relationships", None)
        navigation, references = hook(responses, request) if hook else ([], [])
        validate_relationships(responses, navigation, references)
        result = DashboardCapture(
            dashboard=dashboard,
            asof=asof,
            captured_at=datetime.now(timezone.utc),
            context=context,
            consistency=CaptureConsistency(generation_before=before, generation_after=after),
            responses=responses,
            navigation=navigation,
            references=references,
        )
        if len(result.model_dump_json().encode()) > MAX_BYTES:
            raise HTTPException(
                413, detail=f"capture exceeds {MAX_BYTES} bytes; nothing was truncated"
            )
        request.check_budget()
        return result
