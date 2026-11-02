"""The Board's composite read, through the existing call and flags readers."""

import psycopg

from app.routers.dashboards.capture_contract import (
    CapturedResponse,
    CaptureRequest,
    limit_cards,
    response,
)
from app.routers.data_flags import read_data_flags
from app.routers.theses import read_call
from app.schemas_api import ThesisSummary
from db.session import current_tenant_id
from repositories import thesis_repo

SLUG = "board"
INCLUDE_ARCHIVED = True


def read(conn: psycopg.Connection, request: CaptureRequest) -> list[CapturedResponse]:
    theses = thesis_repo.list_all(conn, include_archived=True)
    limit_cards(len(theses))
    responses = [
        response("/theses", [ThesisSummary.from_thesis(t) for t in theses], include_archived=True)
    ]
    view = (request.context.known_at, request.context.clock)
    for thesis in theses:
        request.check_budget()
        if thesis.archived_at is None:
            call = read_call(conn, thesis, request.asof, mclock=request.market_clock, view=view)
            responses.append(response(f"/theses/{thesis.id}/call", call, asof=request.asof))
    request.check_budget()
    flags = read_data_flags(conn, asof=request.asof, tenant_id=current_tenant_id(), view=view)
    responses.append(response("/data-flags", flags, asof=request.asof))
    return responses
