"""Uncached Rotation Map capture using the dashboard's own read and compute."""

import time
from dataclasses import fields

import psycopg

from app.routers.dashboards import rotation_map as rotation
from app.routers.dashboards.capture_contract import (
    CapturedResponse,
    CaptureRequest,
    limit_cards,
    response,
)
from securities import master

SLUG = "rotation-map"
INCLUDE_ARCHIVED = False


def read(conn: psycopg.Connection, request: CaptureRequest) -> list[CapturedResponse]:
    started = time.perf_counter()
    context = request.context
    inputs = rotation.read_inputs(conn, request.asof, context.known_at, context.clock)
    request.check_budget()
    limit_cards(inputs.live_cards)
    identity = (
        master.identity_for(conn, set(inputs.tapes))
        if request.group_by in ("business_type", "sector")
        else {}
    )
    result = rotation.compute_rotation_map(
        inputs,
        identity,
        asof=request.asof,
        group_by=request.group_by,
        include_silent=request.include_silent,
    )
    out = rotation.RotationMapOut.model_validate(
        {
            **{f.name: getattr(result, f.name) for f in fields(result)},
            "spy": dict(result.spy) if result.spy is not None else None,
            "min_priced": rotation.MIN_PRICED,
            "cross_days": rotation.CROSS_DAYS,
            "cache": "computed",
            "compute_ms": round((time.perf_counter() - started) * 1000),
        }
    )
    return [
        response(
            "/dashboards/rotation-map",
            out,
            asof=request.asof,
            group_by=request.group_by,
            include_silent=request.include_silent,
        )
    ]
