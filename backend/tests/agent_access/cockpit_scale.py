"""A Cockpit capture at REAL scale, built in code: invented names only, no committed JSON.

The shapes follow the dev measurement of 2026-11-06 (docs/MCP.md, "Known limits"): the larger of two
real Cockpit follows froze an observation of about 398K characters (annotations about 232K, chart
path data about 74K, controls about 32K, reference links about 25K, text about 17K, one table of 77
rows about 16K, no notes) and 504 targets with one reference each where they have any, bindings of
about 172 characters (median) to 410 (max), DOM details of about 790 characters (median) and one
target of about 34K. Following a card's "Open Cockpit" link on the Board scale fixture
(`board_scale`) captures that card's own call, with references from the REAL Cockpit adapter
(`capture_adapters.cockpit.relationships`), so the explain-call path runs end to end over stdio.
The rendered view is a stand-in for the browser (no Chromium), shaped as `observe.ts` reports it.
"""

import copy
import hashlib
import random
from types import SimpleNamespace
from uuid import UUID

import board_scale
from board_scale import ASOF, CLOCK

from agent_access.capture import validate_capture
from agent_access.catalog import Catalog
from agent_access.references import context_hash
from agent_access.service import DashboardService
from app.routers.dashboards.capture_adapters import cockpit
from app.routers.dashboards.capture_contract import response

MEMBERS, EVIDENCE, TARGETS = 77, 12, 504
TABLE_HEADERS = [
    "Ticker",
    "Name",
    "Type",
    "Last",
    "vs 50d",
    "1d",
    "7d",
    "30d",
    "90d",
    "1y",
    "Off 52w",
    "RVOL 8",
    "RVOL 20",
    "Insider 30d",
    "Insider 90d",
    "Market cap",
    "ADV",
    "Entry by",
    "Exit by",
    "State",
]


def _hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _member_id(number: int) -> str:
    return str(UUID(int=(6 << 96) + number))


def cockpit_envelope(board: dict, thesis_id: str) -> dict:
    """The Cockpit capture for one Board card: its thesis detail, its own call and display signals."""
    paths = [item["path"] for item in board["responses"]]
    row = next(
        r for r in board["responses"][paths.index("/theses")]["body"] if r["id"] == thesis_id
    )
    call = board["responses"][paths.index(f"/theses/{thesis_id}/call")]["body"]
    detail = {
        "id": thesis_id,
        "name": row["name"],
        "narrative": row["narrative"],
        "basket": [
            {"security_id": _member_id(n), "ticker": f"SM{n:03d}", "name": f"Synthetic member {n}"}
            for n in range(MEMBERS)
        ],
        "evidence": [
            {"label": f"Synthetic authored evidence {k}: a sourced claim about the theme"}
            for k in range(EVIDENCE)
        ],
    }
    signals = {
        "members": [
            {
                "security_id": _member_id(n),
                "ticker": f"SM{n:03d}",
                "signals": [
                    {"kind": kind, "label": f"SM{n:03d} {kind} reading"}
                    for kind in ("sma", "returns", "rvol")
                ],
            }
            for n in range(MEMBERS)
        ]
    }
    responses = [
        response("/clock", dict(CLOCK)),
        response(f"/theses/{thesis_id}", detail),
        response(f"/theses/{thesis_id}/call", copy.deepcopy(call), asof=ASOF),
        response(f"/theses/{thesis_id}/display-signals", signals, asof=ASOF),
    ]
    request = SimpleNamespace(thesis_id=UUID(thesis_id), check_budget=lambda: None)
    links, refs = cockpit.relationships(responses, request)
    data = copy.deepcopy({key: value for key, value in board.items() if key != "responses"})
    data["context"]["limitations"] = board["context"]["limitations"] + list(cockpit.LIMITATIONS)
    data.update(
        dashboard="cockpit",
        responses=[r.model_dump(mode="json") for r in responses],
        navigation=[link.model_dump(mode="json") for link in links],
        references=[r.model_dump(mode="json") for r in refs],
    )
    return validate_capture(data, "cockpit", Catalog().describe("cockpit"))


def _bound(text: str, total: int | None = None) -> dict:
    total = len(text) if total is None else total
    return {"text": text[:12000], "truncated": total > 12000, "total_characters": total}


def _geometry(rng: random.Random, y: float) -> dict:
    return {
        "visible": True,
        "clipped": False,
        "in_viewport": y < 1080,
        "bounds": {
            "x": round(rng.uniform(0, 1200), 2),
            "y": round(y, 2),
            "width": 238.75,
            "height": 34.5,
        },
    }


def _target(rng, kind, key, label, text, *, refs=(), links=(), y=0.0, **details):
    return {
        "id": "target-" + _hex(kind + key),
        "label": label[:200],
        **_bound(text),
        **_geometry(rng, y),
        "tables": details.pop("tables", []),
        "links": list(links),
        "source_refs": [
            {"id": r["id"], "kind": r["kind"], "label": r["label"], "category": r["category"]}
            for r in refs
        ],
        "reference_links": details.get("reference_links", []),
        "reference_link_count": len(details.get("reference_links", [])),
        "reference_links_truncated": False,
    }


def _table(rng: random.Random) -> dict:
    return {
        "headers": list(TABLE_HEADERS),
        "rows": [
            [f"SM{n:03d}", f"Synthetic member {n}", "miner"]
            + [f"{rng.uniform(-30, 30):.1f}%" for _ in range(len(TABLE_HEADERS) - 5)]
            + ["Nov 6", "watch"]
            for n in range(MEMBERS)
        ],
        "row_count": MEMBERS,
        "rows_truncated": False,
    }


def _filler(rng: random.Random, words: int) -> str:
    """Rendered-looking words: the UI's middle dots and line breaks both widen the JSON encoding."""
    vocabulary = ("synthetic", "signal", "window", "member", "filing", "volume", "trend", "·", "\n")
    return " ".join(rng.choice(vocabulary) for _ in range(words))


def view(data: dict) -> dict:
    """The rendered Cockpit as observe() reports it: the main thesis area (the one ~34K target),
    the call card, its triggers and their sources, authored evidence, member rows, display
    indicators and group rows; and an observation at the measured scale."""
    rng = random.Random(11)
    thesis_id = data["responses"][1]["body"]["id"]
    refs = {}
    for ref in data["references"]:
        refs.setdefault(ref["category"], []).append(ref)
    links = {nav["destination"]["settings"].get("selected_name"): nav for nav in data["navigation"]}
    targets = []

    def add(kind, key, label, text, **kwargs):
        targets.append(_target(rng, kind, key, label, text, y=80.0 + 36 * len(targets), **kwargs))

    sec = "https://www.sec.gov/Archives/edgar/data/9900001/000990000126000001/0009900001-26-000001-index.htm"
    add(
        "cockpit-thesis",
        thesis_id,
        data["responses"][1]["body"]["name"],
        _filler(rng, 6000),
        refs=refs["thesis_metadata"],
        tables=[_table(rng)],
        reference_links=[{"label": f"Filing {k}", "url": sec} for k in range(60)],
    )
    call = data["responses"][2]["body"]
    add(
        "call",
        thesis_id,
        f"CALL · {call['verdict'].replace('_', ' ').upper()} · {call['conviction_grade'].upper()} "
        + _filler(rng, 30),
        "CALL " + _filler(rng, 400),
        refs=refs["call_result"],
    )
    for ref in refs["call_signal"]:
        add(
            "trigger",
            ref["id"],
            ref["label"] + " · " + _filler(rng, 20),
            _filler(rng, 80),
            refs=[ref],
        )
    for ref in refs["call_input"]:
        add(
            "source",
            ref["id"],
            "Source · " + (ref["label"] or "unknown"),
            "Source · " + _filler(rng, 70),
            refs=[ref],
            reference_links=[{"label": "EDGAR filing", "url": sec}],
        )
    for ref in refs["authored_evidence"]:
        add("thesis-evidence", ref["id"], ref["label"], _filler(rng, 70), refs=[ref])
    for n in range(MEMBERS):
        nav = links[_member_id(n)]
        add(
            "cockpit-member",
            str(n),
            f"Synthetic member {n}",
            _filler(rng, 70),
            links=[{"id": nav["id"], "kind": nav["kind"], "label": "Open this name in Cockpit"}],
        )
    for ref in refs["display_basis"]:
        # No aria-label: observe() labels it with its own text, cut at 200 characters.
        text = ref["label"] + " · " + _filler(rng, 70)
        add("display-evidence", ref["id"], text, text, refs=[ref])
    while len(targets) < TARGETS:
        add("cockpit-group", str(len(targets)), f"Synthetic group {len(targets)}", _filler(rng, 60))
    annotations = [
        {
            "text": _filler(rng, 6),
            "title": "Synthetic hover: " + _filler(rng, 4),
            "label": None,
            **_geometry(rng, 40.0 + 12 * k),
        }
        for k in range(1000)
    ]
    charts = [
        {
            "label": f"Synthetic price chart {k}",
            "text": "",
            "paths": [
                {
                    "class": "line",
                    "d": "M0,0" + "".join(f"L{i},{rng.randint(0, 99)}" for i in range(530)),
                }
            ],
            **_geometry(rng, 600.0 + 40 * k),
        }
        for k in range(20)
    ]
    return {
        "status": "ready",
        "observation": {
            "title": "Cockpit",
            **_bound(_filler(rng, 2500), total=40_000),
            "tables": [_table(rng)],
            "text_semantics": "Browser innerText; CSS-clipped text may remain in the string.",
            "notes": [],
            "note_count": 0,
            "notes_truncated": False,
            "annotations": annotations,
            "annotation_count": 1180,
            "annotations_truncated": True,
            "charts": charts,
            "chart_count": len(charts),
            "charts_truncated": False,
            "reference_links": [
                {"label": f"Filing {k}", "url": sec, **_geometry(rng, 900.0 + 8 * k)}
                for k in range(104)
            ],
            "reference_link_count": 104,
            "reference_links_truncated": False,
            "controls": [
                {
                    "label": f"Synthetic control {k}: sort or filter",
                    "value": "desc",
                    "checked": None,
                    "pressed": "false",
                    "open": None,
                }
                for k in range(290)
            ],
            "target_count": len(targets),
            "targets_truncated": False,
            "fonts": {"status": "loaded", "family": "synthetic"},
        },
        "targets": targets,
    }


class ScaleCapture:
    """The Board scale capture, and a fresh Cockpit capture for any card it links to."""

    def __init__(self):
        self.board = board_scale.envelope()

    async def capture(self, descriptor, settings, asof, *, expected_context_hash=None):
        assert asof in {None, ASOF}
        if descriptor["slug"] == "board":
            return copy.deepcopy(self.board)
        assert descriptor["slug"] == "cockpit"
        data = cockpit_envelope(self.board, settings["thesis_id"])
        assert expected_context_hash in {None, context_hash(data)}
        return data


class ScaleRenderer:
    async def preflight(self):
        """No UI or browser behind this stand-in."""

    async def render(self, data, snapshot_id, settings, target_id=None):
        rendered = board_scale.view(data) if data["dashboard"] == "board" else view(data)
        rendered.update(snapshot_id=snapshot_id, settings=copy.deepcopy(settings))
        return rendered, b"\x89PNG synthetic", {"asset_digest": "synthetic", "cropped": False}


def scale_service(reply_ceiling: int | None = None) -> DashboardService:
    extra = {} if reply_ceiling is None else {"reply_ceiling": reply_ceiling}
    return DashboardService(capture=ScaleCapture(), renderer=ScaleRenderer(), **extra)
