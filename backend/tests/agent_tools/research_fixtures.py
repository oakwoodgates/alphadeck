"""Synthetic API payloads for the research-stage tools at the MEASURED production sizes (2026-11-06, mcp-phase5 spec
section 7): the scored view of a 79-member card, the saved draft runs, the extract of one name and the onboarding queue.

Every name is synthetic (``Thesis 01``, ``TK01``, ``Segment 01``, ``term-01``); no card, company, segment or ticker from a
live card. Each payload is built as a dict, VALIDATED through the API's own wire model (so a fixture cannot drift from the
contract) and dumped to JSON exactly as FastAPI serves it.
"""

from __future__ import annotations

from typing import Any

from app.schemas_api import OnboardingReadoutOut, WorkbenchScored
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, ETFS_LABEL, Segment, with_silent_links
from tests.agent_tools import fixtures as fx
from tests.agent_tools.fixtures import text, uid

ASOF = fx.ASOF
THESIS_ID = uid("thesis", 0)
SECOND_THESIS_ID = uid("thesis", 1)  # a card with no saved run
SECURITY_ID = uid("sec", "lookup")  # the id the fake ticker resolver answers for TK01


def _dump(model: Any, payload: Any) -> Any:
    return model.model_validate(payload).model_dump(mode="json")


# --- GET /workbench/theses/{id}/scored?asof= -----------------------------------------------------------------------------

REAL_LABELS = ["Segment 01", "Segment 02", "Segment 03", "Segment 04"]
# members per segment: the four real links, then Context, ETFs, Bench (the three silent ones, last, in that order)
MEMBER_COUNTS = (14, 5, 8, 22, 6, 0, 24)
CONFIRMED = {0, 10, 20, 30, 40, 48}  # six voting members with every estimate confirmed
BUSINESS_TYPES = (
    "software_it",
    "semiconductors",
    "biotech_pharma",
    "industrials_machinery",
    "miner",
)
SUPERSECTOR = {
    "software_it": "technology",
    "semiconductors": "technology",
    "biotech_pharma": "healthcare",
    "industrials_machinery": "industrials",
    "miner": "materials",
}


def _provenance(
    source: str, i: int, note_chars: int = 20, *, filing: bool = True
) -> dict[str, Any]:
    ref = f"0001{i:06d}-26-0000{i % 90 + 10}"
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{i + 1000}/{ref.replace('-', '')}/{ref}-index.htm"
    )
    return {
        "source": source,
        "ref": ref if filing else f"{source}-{i}",
        "url": url if filing else None,
        "detail": {"note": text(note_chars, i)} if filing else {},
    }


def _figure(
    pips: int | None, value: float | None, provenance: list[dict[str, Any]]
) -> dict[str, Any]:
    return {"pips": pips, "value": value, "provenance": provenance}


def _member(i: int, label: str) -> dict[str, Any]:
    n = i + 1
    kind = BUSINESS_TYPES[i % len(BUSINESS_TYPES)]
    confirmed = i in CONFIRMED
    purity_src = "ratified" if confirmed else "10-k-segment"
    cap_provenance = [_provenance("price", i, 20, filing=False)]
    if i % 3 == 0 and i < 72:  # 24 of the 79 carry the 10-Q cover share count
        cap_provenance.append(_provenance("10-q-cover", i, 20))
    elif i in (5, 31, 52, 77):
        cap_provenance.append(_provenance("computed", i, 30, filing=False))
    return {
        "security_id": uid("sec", "triage", i),
        "ticker": fx.ticker(n),
        "name": f"Company {n:02d} synthetic " + ("holdings" if i % 2 else "corporation"),
        "sector": "Services",
        "exchange": "Nasdaq",
        "category": "Large Filer",
        "origin": None,
        "foreign_filer_form": None,
        "price_symbol": None,
        "business_type": kind,
        "business_supersector": SUPERSECTOR[kind],
        "business_type_override": None,
        "royalty": False,
        "instrument_kind": "equity",
        "segment": label,
        "purity": _figure(i % 5, round(10.0 + i, 1), [_provenance(purity_src, i)]),
        "runway": _figure(
            None if i % 9 == 0 else (i + 1) % 5,
            None if i % 9 == 0 else round(4.0 + i % 11, 1),
            [] if i % 4 == 0 else [_provenance("10-q", i)],
        ),
        "catalysts": _figure(i % 4, float(i % 4), [_provenance("catalyst", i, 20, filing=False)]),
        "dilution": _figure(
            (i + 2) % 5,
            round(0.5 + i % 7, 1),
            [] if i % 3 == 0 else [_provenance("fact-shares", i, 20, filing=False)],
        ),
        "market_cap": _figure(None, 5.0e8 + i * 1.7e7, cap_provenance),
        "fit": ("strong", "partial", "weak")[i % 3],
        "unconfirmed_estimates": 0 if confirmed else 3,
        "thin_price_history": i == 33,
        "price_feed_limitation": text(60, 3) if i == 61 else None,
    }


def scored() -> dict[str, Any]:
    """MEASURED: 85.5 KB, 79 members of about 1 KB, 7 segments (three of them silent)."""
    segments = with_silent_links(
        [Segment(label=lbl, descriptor=text(110, k)) for k, lbl in enumerate(REAL_LABELS)]
    )
    labels = [s.label for s in segments]
    assert labels[-3:] == [CONTEXT_LABEL, ETFS_LABEL, BENCH_LABEL]
    members, i = [], 0
    for label, count in zip(labels, MEMBER_COUNTS, strict=True):
        for _ in range(count):
            members.append(_member(i, label))
            i += 1
    return _dump(
        WorkbenchScored,
        {
            "thesis_id": THESIS_ID,
            "asof": ASOF,
            "segments": [s.model_dump() for s in segments],
            "members": members,
        },
    )


# --- GET /workbench/theses/{id}/onboarding -----------------------------------------------------------------------------

LEGS = ["price", "form4", "filings"]


def _onboard_name(i: int, status: str, **kw: Any) -> dict[str, Any]:
    return {
        "security_id": uid("sec", "onboard", i),
        "ticker": fx.ticker(i + 1),
        "name": f"Company {i + 1:02d} synthetic onboarding name",
        "status": status,
        "legs_done": kw.pop("legs_done", LEGS[:2]),
        "attempts": kw.pop("attempts", 1),
        "last_error": kw.pop("last_error", None),
        "source": "promote",
        "in_live_basket": True,
        "enqueued_at": f"{ASOF}T21:00:00Z",
        "last_attempt_at": kw.pop("last_attempt_at", f"{ASOF}T21:30:00Z"),
        "finished_at": kw.pop("finished_at", None),
    }


def onboarding() -> dict[str, Any]:
    """MEASURED: 8.4 KB, 3 pending, 20 recent_done of 431 chars each."""
    pending = [
        _onboard_name(0, "queued", legs_done=[], attempts=0, last_attempt_at=None),
        _onboard_name(1, "in_progress", legs_done=LEGS[:1]),
        _onboard_name(2, "failed", attempts=3, last_error="Synthetic: " + text(210, 4)),
    ]
    recent = [
        _onboard_name(
            10 + k,
            "done",
            legs_done=LEGS,
            finished_at=f"{ASOF}T20:{k:02d}:00Z",
            last_error=text(120, k) if k == 3 else None,
        )
        for k in range(20)
    ]
    return _dump(
        OnboardingReadoutOut,
        {
            "counts": {"queued": 1, "in_progress": 1, "failed": 1, "done": 431, "parked": 0},
            "pending": pending,
            "recent_done": recent,
            "last_run": {
                "started_at": f"{ASOF}T20:00:00Z",
                "finished_at": f"{ASOF}T20:20:00Z",
                "status": "ok",
                "run_kind": "onboarding",
                "stopped": "done",
                "detail": None,
                "counts": {"done": 20, "failed": 1, "requeued": 1},
            },
            "window": "refuses 21:30 to 23:30 ET",
            "stalled": None,
            "paused": None,
            "history_legs": ["form4", "filings", "price"],
        },
    )
