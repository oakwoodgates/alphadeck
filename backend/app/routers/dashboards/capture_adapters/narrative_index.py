"""Narrative Index and every detail from the same uncached IndexInputs."""

import time

import psycopg

from app.routers.dashboards import narrative_index as narrative
from app.routers.dashboards.capture_contract import (
    CapturedResponse,
    CaptureRequest,
    limit_cards,
    response,
)

SLUG = "narrative-index"
INCLUDE_ARCHIVED = False


def read(conn: psycopg.Connection, request: CaptureRequest) -> list[CapturedResponse]:
    started = time.perf_counter()
    context = request.context
    inputs = narrative.read_inputs(
        conn, request.asof, context.known_at, context.clock, request.include_silent
    )
    request.check_budget()
    page = narrative.compute_narrative_index(inputs, asof=request.asof, window=request.window)
    limit_cards(page.live_cards)
    meta = {"cache": "computed", "compute_ms": round((time.perf_counter() - started) * 1000)}
    out = narrative.NarrativeIndexOut.model_validate({**narrative._page_dict(page), **meta})
    query = {
        "asof": request.asof,
        "window": request.window,
        "include_silent": request.include_silent,
    }
    responses = [response("/dashboards/narrative-index", out, **query)]
    for card in page.cards:
        request.check_budget()
        detail = narrative.card_detail(
            inputs, card.thesis_id, asof=request.asof, window=request.window
        )
        detail_page = narrative._page_dict(detail.page)
        detail_out = narrative.NarrativeCardDetailOut.model_validate(
            {
                **{
                    k: detail_page[k]
                    for k in ("asof", "window", "include_silent", "sessions", "dates", "benchmarks")
                },
                "card": detail_page["cards"][0],
                "contributors": list(detail.contributors),
                "members": [narrative._payload(m) for m in detail.members],
                **meta,
            }
        )
        responses.append(
            response(f"/dashboards/narrative-index/{card.thesis_id}", detail_out, **query)
        )
    return responses
