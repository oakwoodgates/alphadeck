"""``GET /record/start`` (V2 W8a): where the signed-in user's workspace's record starts.

A workspace's record starts on the first night it holds an honest call: V1's first night for the founding
workspace (the V2 cutover keeps V1's history, operator decision 2026-11-19), the night of its first nightly call
for a newer workspace, so workspaces start on different nights. The history dashboards say so ("The record starts
on ...", W8c).
This route answers for the caller's OWN workspace only (``get_workspace``): another workspace's nights, and
the data tenant's, never reach it. It reads ``calls_repo`` only and imports no dashboard module (the leaf rule,
``tests/dashboards/test_isolation.py``). Read-only.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_conn, get_workspace
from repositories import calls_repo

router = APIRouter(prefix="/record", tags=["record"])


class RecordStartOut(BaseModel):
    """``first_night`` = the first night the caller's workspace holds an honest (non-reconstructed) call of
    record; ``null`` = no record yet (it starts with the first nightly call), never an error or a zero date.
    """

    first_night: date | None


@router.get("/start", response_model=RecordStartOut)
def get_record_start(
    conn: psycopg.Connection = Depends(get_conn),
    current_tenant: UUID = Depends(get_workspace),
) -> RecordStartOut:
    """The first night on the caller's workspace's record (``null`` before its first nightly call)."""
    return RecordStartOut(first_night=calls_repo.record_start(conn, tenant_id=current_tenant))
