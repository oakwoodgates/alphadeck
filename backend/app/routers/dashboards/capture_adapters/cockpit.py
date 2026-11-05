"""One Cockpit's canonical reads and provenance, frozen in a fresh read-only capture.

This deliberately preserves the existing Cockpit's mixed date semantics: fact computations
are dated, while displayed spine metadata and the operator log are current. The call reader
itself owns its historical roster/decision rules. No provenance URL is fetched here.
"""

import psycopg
from fastapi import HTTPException

from app.routers.dashboards.capture_contract import CapturedResponse, CaptureRequest
from app.routers.dashboards.capture_contract import response as frozen_response
from app.routers.dashboards.capture_links import navigation, reference
from app.routers.data_flags import read_thesis_data_flags
from app.routers.fund_radar import read_fund_launches
from app.routers.theses import read_call, read_decisions, read_display_signals, read_thesis_detail
from app.routers.workbench import read_scored
from repositories import thesis_repo

SLUG = "cockpit"
INCLUDE_ARCHIVED = True
THESIS_SCOPED = True
LIMITATIONS = (
    "Cockpit narrative, displayed roster/identity, authored evidence/catalysts/kill criteria, feed metadata, position and decision log are current snapshot metadata. Dated call, scored, display and flag facts follow the requested public-clock view. The call's historical roster can differ from the displayed current roster.",
    "Authored evidence references, scored provenance, display basis and data flags are distinguished from call-signal inputs. External provenance URLs are references only; their documents were not fetched or captured.",
    "Fund launches are display-only context on the public clock (stages use only facts public by the as-of), never a call input; their in-ETFs flag reads the current basket.",
)


def read(conn: psycopg.Connection, request: CaptureRequest) -> list[CapturedResponse]:
    thesis = thesis_repo.get(conn, request.thesis_id)
    if thesis is None:
        raise HTTPException(404, detail="thesis not found")
    path = f"/theses/{thesis.id}"
    view = (request.context.known_at, request.context.clock)
    responses = []

    def collect(path, compute, **query):
        request.check_budget()
        responses.append(frozen_response(path, compute(), **query))

    collect(path, lambda: read_thesis_detail(conn, thesis, today=request.market_clock.today))
    collect(
        path + "/call",
        lambda: read_call(conn, thesis, request.asof, mclock=request.market_clock, view=view),
        asof=request.asof,
    )
    collect(
        path + "/display-signals",
        lambda: read_display_signals(conn, thesis, request.asof, view=view),
        asof=request.asof,
    )
    collect(
        path + "/data-flags",
        lambda: read_thesis_data_flags(conn, thesis, request.asof, view=view),
        asof=request.asof,
    )
    collect(
        f"/workbench{path}/scored",
        lambda: read_scored(conn, thesis, request.asof, view=view),
        asof=request.asof,
    )
    collect(path + "/decisions", lambda: read_decisions(conn, thesis))
    # last, so the earlier responses keep their positions; the query is exactly what useFundLaunches sends
    collect(
        path + "/fund-launches",
        lambda: read_fund_launches(conn, thesis, request.asof, 90),
        asof=request.asof,
        days=90,
    )
    return responses


def relationships(responses: list[CapturedResponse], request: CaptureRequest):
    links = {}
    refs = []
    thesis_path = f"/theses/{request.thesis_id}"

    for index, source in enumerate(responses):
        request.check_budget()
        body = source.body

        def add(pointer, category, label=None):
            refs.append(reference(responses, index, pointer, category=category, label=label))

        def trigger(pointer, trigger_body):
            add(pointer, "call_signal", trigger_body.get("label"))
            for j, item in enumerate(trigger_body.get("sources", [])):
                add(f"{pointer}/sources/{j}", "call_input", item.get("source"))

        if source.path == thesis_path:
            add("", "thesis_metadata", "Current thesis metadata")
            for member in body.get("basket", []):
                key = member.get("security_id") or member.get("ticker")
                if key:
                    link = navigation(
                        "cockpit",
                        thesis_id=str(request.thesis_id),
                        selected_name=key,
                        rail_open=True,
                    )
                    links[link.id] = link
            for i, item in enumerate(body.get("evidence", [])):
                add(f"/evidence/{i}", "authored_evidence", item.get("label"))
        elif source.path == thesis_path + "/call":
            add("", "call_result", "Computed call")
            for field in ("triggers_fired", "risk_signals"):
                for i, item in enumerate(body.get(field, [])):
                    trigger(f"/{field}/{i}", item)
            for field in ("armed_members", "watch_members"):
                for i, member in enumerate(body.get(field, [])):
                    for j, item in enumerate(member.get("triggers", [])):
                        trigger(f"/{field}/{i}/triggers/{j}", item)
        elif source.path == thesis_path + "/display-signals":
            for i, member in enumerate(body.get("members", [])):
                for j, item in enumerate(member.get("signals", [])):
                    add(f"/members/{i}/signals/{j}", "display_basis", item.get("label"))
        elif source.path == thesis_path + "/data-flags":
            for i, member in enumerate(body.get("members", [])):
                for j, flag in enumerate(member.get("flags", [])):
                    pointer = f"/members/{i}/flags/{j}"
                    add(pointer, "data_flag", flag.get("label"))
                    for k, item in enumerate(flag.get("evidence", [])):
                        add(f"{pointer}/evidence/{k}", "data_flag", item.get("label"))
        elif source.path == thesis_path + "/fund-launches":
            # display-only context (never a call signal or input): one reference per fund the card fits
            for i, filing in enumerate(body.get("filings", [])):
                for j, fund in enumerate(filing.get("funds", [])):
                    add(
                        f"/filings/{i}/funds/{j}",
                        "display_basis",
                        f"Fund launch: {fund.get('name')}",
                    )
        elif source.path == f"/workbench{thesis_path}/scored":
            for i, member in enumerate(body.get("members", [])):
                add(f"/members/{i}", "scored_member", member.get("ticker"))
                for field in ("purity", "runway", "catalysts", "dilution", "market_cap"):
                    for j, item in enumerate(member.get(field, {}).get("provenance", [])):
                        add(
                            f"/members/{i}/{field}/provenance/{j}",
                            "scored_provenance",
                            item.get("source"),
                        )
    return list(links.values()), refs
