"""The Board's composite read, through the existing call and flags readers."""

import psycopg

from app.routers.dashboards.capture_contract import (
    CapturedResponse,
    CaptureRequest,
    limit_cards,
    response,
)
from app.routers.dashboards.capture_links import navigation, reference
from app.routers.data_flags import read_data_flags
from app.routers.theses import read_call
from app.schemas_api import ThesisSummary
from db.session import current_tenant_id
from repositories import thesis_repo

SLUG = "board"
INCLUDE_ARCHIVED = True
# The served call's decision fields (CallCardResponse), one reference each, so an agent reads a verdict or
# the missing conditions without reading the whole call. A field absent from the captured call gets none.
CALL_DECISION_FIELDS = (
    "state",
    "verdict",
    "conviction_grade",
    "confirmation_grade",
    "entry_grade",
    "armed_members",
    "missing",
    "counter_case",
    "risk_signals",
    "arm_until",
    "exit_by",
)


def read(conn: psycopg.Connection, request: CaptureRequest) -> list[CapturedResponse]:
    theses = thesis_repo.list_all(conn, include_archived=True)
    limit_cards(len(theses))
    responses = [
        response(
            "/theses",
            [
                ThesisSummary.from_thesis(t).model_dump(mode="json", exclude_unset=True)
                for t in theses
            ],
            include_archived=True,
        )
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


def relationships(responses: list[CapturedResponse], request: CaptureRequest):
    """Explicit fresh Cockpit reads; no parent database-snapshot continuity is implied."""
    links = {}
    for source in responses:
        if source.path == "/theses":
            for thesis in source.body:
                if not thesis["archived"]:
                    link = navigation("cockpit", thesis_id=thesis["id"], rail_open=True)
                    links[link.id] = link
        elif source.path.endswith("/call"):
            members = source.body.get("armed_members", [])
            if members:
                member = members[0]
                key = member.get("security_id") or member.get("ticker")
                if key:
                    link = navigation(
                        "cockpit",
                        thesis_id=source.body["thesis_id"],
                        selected_name=key,
                        rail_open=True,
                    )
                    links[link.id] = link
    return list(links.values()), card_references(responses, request)


def card_references(responses: list[CapturedResponse], request: CaptureRequest):
    """Each active card's evidence, as exact pointers into this capture's frozen responses.

    Per card, in ``/theses`` order: its row there (``/<i>``; the index is its queue order), its
    call's decision fields, and every ``/data-flags`` member entry whose ``theses`` holds it. Pointers
    only: nothing is read again or recomputed. A member entry two cards hold is ONE reference, which
    both cards' targets advertise. An archived card has no call and gets no reference.
    """
    at: dict[str, int] = {}
    for index, source in enumerate(responses):
        at.setdefault(source.path, index)
    if "/theses" not in at:
        return []
    flags_at = at.get("/data-flags")
    members = responses[flags_at].body.get("members", []) if flags_at is not None else []
    held: dict[str, list[int]] = {}
    for j, member in enumerate(members):
        for holder in member.get("theses", []):
            entries = held.setdefault(str(holder["id"]), [])
            if not entries or entries[-1] != j:
                entries.append(j)

    theses_at = at["/theses"]
    refs, flagged = [], set()
    for i, thesis in enumerate(responses[theses_at].body):
        request.check_budget()
        if thesis.get("archived"):
            continue
        thesis_id = str(thesis["id"])
        refs.append(
            reference(
                responses, theses_at, f"/{i}", category="thesis_summary", label=f"theses[{i}]"
            )
        )
        call_at = at.get(f"/theses/{thesis_id}/call")
        call = responses[call_at].body if call_at is not None else None
        if isinstance(call, dict):
            refs.extend(
                reference(responses, call_at, f"/{field}", category="call_result", label=field)
                for field in CALL_DECISION_FIELDS
                if field in call
            )
        for j in held.get(thesis_id, []):
            if j not in flagged:
                flagged.add(j)
                member = members[j]
                refs.append(
                    reference(
                        responses,
                        flags_at,
                        f"/members/{j}",
                        category="data_flag",
                        label=member.get("ticker") or member.get("security_id"),
                    )
                )
    return refs
