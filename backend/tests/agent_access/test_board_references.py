"""Board evidence references through the MCP tools: a synthetic Board capture whose card targets
advertise the backend's references, read and inspected by ``source_ref_id``. No DB, no browser."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from agent_access.catalog import Catalog
from agent_access.references import validate_references
from agent_access.service import DashboardService
from app.routers.dashboards.capture_adapters import board
from tests.dashboards.test_capture import _board_responses, _flag_entry, _synthetic_call

ASOF = "2026-10-30"
CLOCK = {
    "current_night": ASOF,
    "action_day": "2026-11-02",
    "market_today": "2026-10-31",
    "market_tz": "America/New_York",
}


def board_envelope():
    a, c = str(uuid4()), str(uuid4())
    theses = [
        {"id": a, "name": "Synthetic A", "archived": False},
        {"id": c, "name": "Synthetic C", "archived": False},
    ]
    members = [_flag_entry("SYNA", a), _flag_entry("SYNB", a, c)]
    responses = _board_responses(theses, {a: _synthetic_call(a), c: _synthetic_call(c)}, members)
    links, refs = board.relationships(responses, SimpleNamespace(check_budget=lambda: None))
    envelope = {
        "schema_version": "1",
        "dashboard": "board",
        "asof": ASOF,
        "captured_at": "2026-10-31T00:00:00Z",
        "context": {
            "clock_response": dict(CLOCK),
            "clock": "record",
            "known_at": None,
            "decisions_asof": ASOF,
            "countdown_origin": "2026-11-02",
            "scope": "deployment_visibility_not_authorization",
            "limitations": [],
        },
        "consistency": {
            "database": "repeatable_read_read_only",
            "external_artifacts": "best_effort_generation_guarded",
            "generation_before": "synthetic",
            "generation_after": "synthetic",
            "generation_stable": True,
        },
        "responses": [r.model_dump(mode="json") for r in responses],
        "navigation": [link.model_dump(mode="json") for link in links],
        "references": [r.model_dump(mode="json") for r in refs],
    }
    validate_references(envelope)
    return envelope, a, c


def advertised(envelope, thesis_id):
    """What the Board's React binding puts on a card's targets (frontend/src/board/Board.tsx)."""
    paths = [r["path"] for r in envelope["responses"]]
    rows = envelope["responses"][paths.index("/theses")]["body"]
    members = envelope["responses"][paths.index("/data-flags")]["body"]["members"]
    selectors = [("/theses", f"/{[t['id'] for t in rows].index(thesis_id)}")]
    selectors.append((f"/theses/{thesis_id}/call", None))
    selectors += [
        ("/data-flags", f"/members/{j}")
        for j, m in enumerate(members)
        if any(t["id"] == thesis_id for t in m["theses"])
    ]
    return [
        {"id": r["id"], "kind": r["kind"], "label": r["label"], "category": r["category"]}
        for path, pointer in selectors
        for r in envelope["references"]
        if paths[r["response_index"]] == path and pointer in (None, r["pointer"])
    ]


class BoardCapture:
    def __init__(self, envelope):
        self.envelope = envelope

    async def capture(self, descriptor, settings, asof, **kwargs):
        return json.loads(json.dumps(self.envelope))


class BoardRenderer:
    def __init__(self, envelope, a, c):
        self.targets = [
            {"id": "target-queue-a", "text": "SYNA core", "source_refs": advertised(envelope, a)},
            {"id": "target-card-a", "text": "Synthetic A", "source_refs": advertised(envelope, a)},
            {"id": "target-card-c", "text": "Synthetic C", "source_refs": advertised(envelope, c)},
        ]

    async def preflight(self):
        """No UI or browser behind this double."""

    async def render(self, envelope, snapshot_id, settings, target_id=None):
        view = {"status": "ready", "observation": {"text": "Board"}, "targets": self.targets}
        return view, b"png", {"asset_digest": "synthetic", "cropped": False}


def test_a_board_target_inspects_its_call_fields_row_and_flags_by_reference():
    envelope, a, c = board_envelope()
    svc = DashboardService(
        catalog=Catalog(), capture=BoardCapture(envelope), renderer=BoardRenderer(envelope, a, c)
    )

    async def run():
        read, _ = await svc.call("read_dashboard", {"dashboard": "board"})
        assert read["ok"], read
        snapshot = read["result"]["snapshot_id"]
        page = {t["id"]: t for t in read["result"]["targets"]}
        # a card's references ride whole on the page; 12 + its flag entries per card
        assert len(page["target-queue-a"]["source_refs"]) == 1 + 11 + 2
        assert len(page["target-card-c"]["source_refs"]) == 1 + 11 + 1

        async def inspect(target, ref_id):
            result, _ = await svc.call(
                "inspect_dashboard_item",
                {"snapshot_id": snapshot, "target_id": target, "source_ref_id": ref_id},
            )
            return result

        by_label = {r["label"]: r for r in page["target-queue-a"]["source_refs"]}
        paths = [r["path"] for r in envelope["responses"]]
        call_a = envelope["responses"][paths.index(f"/theses/{a}/call")]["body"]
        for field in board.CALL_DECISION_FIELDS:
            result = await inspect("target-queue-a", by_label[field]["id"])
            data = result["result"]["source_data"]
            assert json.loads(data["text"]) == call_a[field]
            assert (data["pointer"], data["category"]) == (f"/{field}", "call_result")
        row = (await inspect("target-queue-a", by_label["theses[0]"]["id"]))["result"]
        assert json.loads(row["source_data"]["text"])["id"] == a
        assert row["source_data"]["pointer"] == "/0"
        flag = (await inspect("target-card-a", by_label["SYNB"]["id"]))["result"]
        assert json.loads(flag["source_data"]["text"])["ticker"] == "SYNB"
        assert flag["source_data"]["category"] == "data_flag"
        # the shared flag entry is the same reference on the other card; A's own row is not
        shared = (await inspect("target-card-c", by_label["SYNB"]["id"]))["result"]
        assert shared["source_data"]["text"] == flag["source_data"]["text"]
        foreign = await inspect("target-card-c", by_label["verdict"]["id"])
        assert foreign["error"]["code"] == "unknown_reference"

    asyncio.run(run())
