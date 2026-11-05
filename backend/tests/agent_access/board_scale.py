"""A Board capture at REAL scale, built in code: synthetic names only, no committed JSON.

The sizes follow the prod measurement of 2026-11-05 (docs/MCP.md, "Known limits"): 66 active cards, 42 of
them armed, calls of about 50K characters, a `/theses` list of about 88K and `/data-flags` of about 160K
with each flag entry held by about 1.3 cards. The capture's references come from the REAL Board adapter
(`capture_adapters.board.relationships`) and the targets carry them the way `frontend/src/board/Board.tsx`
binds them, so a budget measured here is the tools' own reply size, not a guess. The rendered view is a
stand-in for the browser (no Chromium): its observation is calibrated to the prod read reply (about 50K
characters with 24 small targets and a 50-row response listing, so about 28K of observation).
"""

import copy
import hashlib
import json
import random
from uuid import UUID

from agent_access.capture import validate_capture
from agent_access.catalog import Catalog
from agent_access.service import DashboardService
from app.routers.dashboards.capture_adapters import board
from app.routers.dashboards.capture_contract import response
from app.schemas_api import CallCardResponse

CARDS, ARMED, ARCHIVED, FLAG_ENTRIES = 66, 42, 4, 232
ASOF = "2026-10-30"
CLOCK = {
    "current_night": ASOF,
    "action_day": "2026-11-02",
    "market_today": "2026-10-31",
    "market_tz": "America/New_York",
}


def _id(lane: int, number: int) -> str:
    return str(UUID(int=(lane << 96) + number))


def _ticker(number: int) -> str:
    return f"SY{number:03d}"


def _source(rng: random.Random, number: int) -> dict:
    accession = f"0099{number:06d}-26-{rng.randrange(100000):06d}"
    return {
        "source": "edgar_form4",
        "ref": accession,
        "url": f"https://www.sec.gov/Archives/edgar/data/{9_900_000 + number}/{accession.replace('-', '')}/{accession}-index.htm",
        "detail": {
            "insider": f"Synthetic Insider {number:04d}",
            "role": "Director",
            "shares": rng.randrange(1000, 90000),
            "price": round(rng.uniform(1, 80), 2),
            "value_usd": rng.randrange(10000, 900000),
            "transaction_date": "2026-10-21",
            "code": "P",
        },
    }


def _trigger(rng: random.Random, ticker: str, kind: str, sources: int, number: int) -> dict:
    return {
        "label": f"Synthetic {kind} trigger on {ticker}: {sources} filings in the window",
        "kind": kind,
        "grade": rng.choice(["core", "flip"]),
        "event_date": "2026-10-2" + str(rng.randrange(10)),
        "ticker": ticker,
        "sources": [_source(rng, number * 10 + s) for s in range(sources)],
    }


def _member(rng: random.Random, number: int, armed: bool) -> dict:
    ticker = _ticker(number % 1000)
    return {
        "security_id": _id(3, number),
        "ticker": ticker,
        "verdict": "starter_entry" if armed else "not_yet",
        "conviction_grade": "core",
        "confirmation_grade": rng.choice(["core", "flip", None]),
        "entry_grade": "flip",
        "confidence": round(rng.uniform(0.2, 0.9), 2),
        "exit_by": "2027-01-15",
        "arm_until": "2026-11-06",
        "lapsing": False,
        "entry_window_closing": rng.random() < 0.2,
        "theme_armed": False,
        "triggers": [
            _trigger(rng, ticker, kind, 3, number * 3 + k)
            for k, kind in enumerate(("insider", "technical_breakout"))
        ],
    }


def call_body(thesis_id: str, card: int, armed: bool) -> dict:
    """A served call of about 50K characters, shaped (and validated) as CallCardResponse."""
    rng = random.Random(card)
    base = card * 100
    body = {
        "thesis_id": thesis_id,
        "asof": ASOF,
        "state": "armed" if armed else "warming",
        "verdict": "starter_entry" if armed else "not_yet",
        "conviction_grade": "core",
        "confirmation_grade": "flip" if armed else None,
        "entry_grade": "flip" if armed else None,
        "armed_security_id": _id(3, base) if armed else None,
        "expression": "Synthetic expression: research context for the basket's liquid names.",
        "exit_by": "2027-01-15",
        "arm_until": "2026-11-06" if armed else None,
        "catalyst_surface": [
            {
                "id": _id(4, base + k),
                "label": f"Synthetic catalyst {k}",
                "kind": "regulatory",
                "when_date": "2026-12-01",
                "when_label": "~5wk",
            }
            for k in range(2)
        ],
        "confidence": 0.62 if armed else None,
        "key_conviction": {"turned": True, "label": "conviction", "detail": "Synthetic detail."},
        "key_confirmation": {"turned": armed, "label": "confirmation", "detail": None},
        "triggers_fired": [
            _trigger(rng, _ticker((base + k) % 1000), "insider", 3, base + 50 + k)
            for k in range(14)
        ],
        "risk_signals": [
            _trigger(rng, _ticker((base + k) % 1000), "dilution_risk", 3, base + 80 + k)
            for k in range(4)
        ],
        "missing": [
            "Synthetic missing condition: a volume-backed confirmation",
            "Synthetic missing condition: a second insider",
            "Synthetic missing condition: a dated catalyst inside the window",
        ],
        "counter_case": "Synthetic counter-case. " * 16,
        "safe_sleeve": None,
        "armed_members": [_member(rng, base + k, True) for k in range(7)] if armed else [],
        "watch_members": [_member(rng, base + 20 + k, False) for k in range(4 if armed else 11)],
    }
    return CallCardResponse.model_validate(body).model_dump(mode="json")


def _flag_entry(rng: random.Random, number: int, holders: list[dict]) -> dict:
    accession = f"0098{number:06d}-26-000123"
    return {
        "security_id": _id(5, number),
        "ticker": _ticker(500 + number % 500),
        "marked": True,
        "flags": [
            {
                "kind": "stale_tape",
                "label": "Price feed stopped",
                "level": "in_doubt",
                "reason": "No new price bar since Oct 20; the vendor has not printed a session for "
                "this symbol in 11 sessions.",
                "clears": "Clears when a new bar arrives.",
                "since": "2026-10-20",
                "affects": ["price"],
                "columns": ["last", "ret_1m"],
                "dates": [],
                "evidence": [
                    {
                        "label": "8-K filed Oct 21",
                        "url": f"https://www.sec.gov/Archives/edgar/data/{9_800_000 + number}/"
                        f"{accession.replace('-', '')}/{accession}-index.htm",
                    }
                ],
                "call_affected": rng.random() < 0.1,
            }
        ],
        "splits": [],
        "notes": [],
        "theses": [{"id": h["id"], "name": h["name"]} for h in holders],
    }


def _hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def envelope() -> dict:
    """The validated capture: /clock, /theses (70 rows), 66 calls, /data-flags; real Board references."""
    rng = random.Random(7)
    rows = []
    for index in range(CARDS + ARCHIVED):
        rows.append(
            {
                "id": _id(1, index),
                "name": f"Synthetic card {index:02d}",
                "ticker": None,
                "basket_size": 12,
                "narrative": f"Synthetic narrative {index}. " + "A synthetic theme sentence. " * 40,
                "archived": index % 17 == 16,  # four archived rows, interleaved
            }
        )
    active = [row for row in rows if not row["archived"]]
    armed_ids = {row["id"] for row in rng.sample(active, ARMED)}
    calls = {
        row["id"]: call_body(row["id"], card, row["id"] in armed_ids)
        for card, row in enumerate(active)
    }
    members = [
        _flag_entry(rng, number, rng.sample(active, 2 if rng.random() < 0.3 else 1))
        for number in range(FLAG_ENTRIES)
    ]
    responses = [
        response("/clock", dict(CLOCK)),
        response("/theses", rows, include_archived=True),
        *(response(f"/theses/{tid}/call", body, asof=ASOF) for tid, body in calls.items()),
        response(
            "/data-flags",
            {"thesis_id": None, "asof": ASOF, "names": 610, "members": members},
            asof=ASOF,
        ),
    ]

    class Budget:
        def check_budget(self):
            pass

    links, refs = board.relationships(responses, Budget())
    data = {
        "schema_version": "1",
        "dashboard": "board",
        "asof": ASOF,
        "captured_at": "2026-10-31T02:00:00Z",
        "context": {
            "clock_response": dict(CLOCK),
            "clock": "record",
            "known_at": None,
            "decisions_asof": ASOF,
            "countdown_origin": "2026-11-02",
            "scope": "deployment_visibility_not_authorization",
            "limitations": ["Synthetic limitation note."],
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
    return validate_capture(data, "board", Catalog().describe("board"))


def _bound(text: str) -> dict:
    return {"text": text[:12000], "truncated": len(text) > 12000, "total_characters": len(text)}


def _target(kind: str, thesis_id: str, label: str, text: str, links: list, refs: list, y: float):
    return {
        "id": "target-" + _hex(kind + thesis_id),
        "label": label,
        **_bound(text),
        "visible": True,
        "clipped": False,
        "in_viewport": y < 1080,
        "bounds": {"x": 412.5, "y": y, "width": 238.75, "height": 34.5},
        "tables": [],
        "links": links,
        "source_refs": refs,
        "reference_links": [],
        "reference_link_count": 0,
        "reference_links_truncated": False,
    }


def view(data: dict) -> dict:
    """The rendered Board as observe() reports it: the queue (armed, /theses order), then the
    Warming and Armed columns. Each queue/card target advertises what Board.tsx binds to it."""
    paths = [item["path"] for item in data["responses"]]
    rows = data["responses"][paths.index("/theses")]["body"]
    members = data["responses"][paths.index("/data-flags")]["body"]["members"]

    def refs(thesis_id: str) -> list:
        selectors = [("/theses", "/" + str([row["id"] for row in rows].index(thesis_id)))]
        selectors.append((f"/theses/{thesis_id}/call", None))
        selectors += [
            ("/data-flags", f"/members/{j}")
            for j, member in enumerate(members)
            if any(holder["id"] == thesis_id for holder in member["theses"])
        ]
        return [
            {"id": r["id"], "kind": r["kind"], "label": r["label"], "category": r["category"]}
            for path, pointer in selectors
            for r in data["references"]
            if paths[r["response_index"]] == path and pointer in (None, r["pointer"])
        ]

    def links(thesis_id: str, selected: str | None) -> list:
        return [
            {
                "id": nav["id"],
                "kind": nav["kind"],
                "label": "Open Cockpit" if selected is None else "Open this name in Cockpit",
            }
            for nav in data["navigation"]
            if nav["destination"]["settings"]["thesis_id"] == thesis_id
            and nav["destination"]["settings"].get("selected_name") == selected
        ]

    calls = {
        item["body"]["thesis_id"]: item["body"]
        for item in data["responses"]
        if item["path"].endswith("/call")
    }
    active = [row for row in rows if not row["archived"]]
    armed = [row for row in active if calls[row["id"]]["state"] == "armed"]
    warming = [row for row in active if calls[row["id"]]["state"] == "warming"]
    targets = []
    for row in armed:
        head = calls[row["id"]]["armed_members"][0]
        more = len(calls[row["id"]]["armed_members"]) - 1
        text = f"{head['ticker']} +{more} CORE Starter entry · {row['name']} act within 4d"
        targets.append(
            _target(
                "queue",
                row["id"],
                text,
                text,
                links(row["id"], head["security_id"]),
                refs(row["id"]),
                96 + 30 * len(targets),
            )
        )
    for row in warming + armed:
        call = calls[row["id"]]
        text = (
            f"{row['name']} 12 names CORE {'Starter entry' if call['state'] == 'armed' else 'Not yet'}"
            " conviction key turned · confirmation key turned SY001 SY002 SY003 entry window "
            "through Nov 6 · act within 4d"
        )
        targets.append(
            _target(
                "card",
                row["id"],
                row["name"],
                text,
                links(row["id"], None),
                refs(row["id"]),
                1400 + 120 * len(targets),
            )
        )
    lines = [target["text"] for target in targets[:ARMED]]
    # The column heads as the real Board's innerText reads them (CSS upper-cases the labels).
    lines.insert(0, "DECISION QUEUE")
    lines.insert(
        ARMED + 1, f"INCUBATING\nquiet · do not act\n0\n—\nWARMING\nstirring\n{len(warming)}"
    )
    lines += [target["text"] for target in targets[ARMED : ARMED + len(warming)]]
    lines.append(f"ARMED\nact now\n{len(armed)}")
    lines += [target["text"] for target in targets[ARMED + len(warming) :]]
    page_text = "\n".join(lines) + "\n" + "Synthetic member line on a card. " * 1700
    # The read previews 50 annotations; theirs are sized so the observation preview lands near the
    # prod-derived 28K characters.
    annotations = [
        {
            "text": f"act within 4d · Starter entry · Synthetic card {k:02d} · "
            + "readiness: conviction key turned, confirmation key turned, entry window open",
            "title": "the entry window — this entry signal is valid through Nov 6",
            "label": None,
            "visible": True,
            "clipped": False,
            "in_viewport": True,
            "bounds": {"x": 612.25, "y": 96.5 + 30 * k, "width": 80.5, "height": 18.0},
        }
        for k in range(210)
    ]
    return {
        "status": "ready",
        "observation": {
            "title": "Board",
            **_bound(page_text),
            "tables": [],
            "text_semantics": "Browser innerText; CSS-clipped text may remain in the string.",
            "notes": [],
            "note_count": 0,
            "notes_truncated": False,
            "annotations": annotations,
            "annotation_count": len(annotations),
            "annotations_truncated": False,
            "charts": [],
            "chart_count": 0,
            "charts_truncated": False,
            "reference_links": [],
            "reference_link_count": 0,
            "reference_links_truncated": False,
            "controls": [],
            "target_count": len(targets),
            "targets_truncated": False,
            "fonts": {"status": "loaded", "family": "synthetic"},
        },
        "targets": targets,
    }


class ScaleCapture:
    def __init__(self):
        self.data = envelope()

    async def capture(self, descriptor, settings, asof, **kwargs):
        assert descriptor["slug"] == "board" and asof in {None, ASOF}
        return copy.deepcopy(self.data)


class ScaleRenderer:
    async def preflight(self):
        """No UI or browser behind this stand-in."""

    async def render(self, data, snapshot_id, settings, target_id=None):
        rendered = view(data)
        rendered.update(snapshot_id=snapshot_id, settings=copy.deepcopy(settings))
        return rendered, b"\x89PNG synthetic", {"asset_digest": "synthetic", "cropped": False}


def scale_service() -> DashboardService:
    return DashboardService(capture=ScaleCapture(), renderer=ScaleRenderer())


def sizes(data: dict) -> dict:
    """Characters per captured response kind, compact JSON (what the measurements quote)."""
    measured = {"call": []}
    for item in data["responses"]:
        chars = len(json.dumps(item["body"], separators=(",", ":")))
        if item["path"].endswith("/call"):
            measured["call"].append(chars)
        else:
            measured[item["path"]] = chars
    return measured
