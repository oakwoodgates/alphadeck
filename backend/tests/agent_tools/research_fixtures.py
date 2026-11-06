"""Synthetic API payloads for the research-stage tools at the MEASURED production sizes (2026-11-06, mcp-phase5 spec
section 7): the scored view of a 79-member card, the saved draft runs, the extract of one name and the onboarding queue.

Every name is synthetic (``Thesis 01``, ``TK01``, ``Segment 01``, ``term-01``); no card, company, segment or ticker from a
live card. Each payload is built as a dict, VALIDATED through the API's own wire model (so a fixture cannot drift from the
contract) and dumped to JSON exactly as FastAPI serves it.
"""

from __future__ import annotations

import random
from typing import Any

from app.schemas_api import (
    ChainDraftOut,
    OnboardingReadoutOut,
    SavedRunSummary,
    ThesisDetail,
    WorkbenchScored,
)
from domain.extraction import ExtractionResult
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


# --- GET /workbench/theses/{id}/runs and /runs/{run_id} -------------------------------------------------------------------

RUN_LATEST_ID = "2026-11-04T20-00-00_job-01"
RUN_SMALL_ID = "2026-10-28T19-00-00_job-02"
TERM_COUNT = 31
CAPPED_TERMS = ["term-02", "term-05", "term-09", "term-14"]
EMPTY_TERMS = ["term-27", "term-28"]
FAILED_TERMS = ["term-31"]
# the terms a placement can carry: every term except the ones that returned nothing or whose pages failed
HIT_POOL = [
    f"term-{k:02d}"
    for k in range(1, TERM_COUNT + 1)
    if f"term-{k:02d}" not in EMPTY_TERMS + FAILED_TERMS
]
DRAFT_SEGMENTS = [f"Segment {k:02d}" for k in range(1, 7)] + ["Discovered"]


def runs() -> list[dict[str, Any]]:
    """MEASURED: 393 B; the newest first."""
    return [
        _dump(
            SavedRunSummary,
            {
                "run_id": RUN_LATEST_ID,
                "written_at": "2026-11-04T20:00:00+00:00",
                "job_id": "job-01",
                "placement_count": 1_735,
                "segment_count": 7,
            },
        ),
        _dump(
            SavedRunSummary,
            {
                "run_id": RUN_SMALL_ID,
                "written_at": "2026-10-28T19:00:00+00:00",
                "job_id": "job-02",
                "placement_count": 203,
                "segment_count": 4,
            },
        ),
    ]


def _placements(
    n: int, labels: list[str], counts: dict[str, int], off_thesis: int, off_universe: int
) -> list[dict[str, Any]]:
    """``n`` placements with exactly ``counts`` statuses, ``off_thesis`` flagged and ``off_universe`` sourced."""
    assert sum(counts.values()) == n
    rng = random.Random(n)  # a fixed seed: the same scramble on every run, with every count exact
    statuses = [s for s, c in counts.items() for _ in range(c)]
    rng.shuffle(statuses)
    flagged = set(rng.sample(range(n), off_thesis))
    out = []
    placed_seen = 0
    for j in range(n):
        status = statuses[j]
        k = j + 1
        terms = [HIT_POOL[(j * 3 + m) % len(HIT_POOL)] for m in range(1 + (j % 10 == 0) * 2)]
        if j % 97 == 0:
            terms = [HIT_POOL[(j + m) % len(HIT_POOL)] for m in range(20)]
        row: dict[str, Any] = {
            "name": f"Company {k:04d} synthetic holdings",
            "ticker": None if status in ("ambiguous", "absent") and j % 2 else f"TK{k:04d}",
            "prose": text(140, j),
            "segment": labels[j % len(labels)],
            "status": status,
            "security_id": uid("sec", "run", j) if status in ("placed", "verify") else None,
            "candidates": [],
            "matched_terms": terms if status != "absent" else [],
            "discovery_source": "edgar",
            "sector": "Services",
            "exchange": "Nasdaq",
            "listing_status": "active",
            "category": "Large Filer",
            "origin": None,
            "business_type": "software_it",
            "royalty": False,
            "off_thesis": j in flagged,
        }
        if status == "ambiguous":
            row["candidates"] = [
                {
                    "security_id": uid("sec", "cand", j, c),
                    "ticker": f"CD{k:04d}{'AB'[c]}",
                    "name": f"Candidate {k:04d}{'AB'[c]} synthetic",
                    "cik": f"{1_000_000 + j * 2 + c:010d}",
                }
                for c in range(2)
            ]
        if status == "placed":
            placed_seen += 1
            if placed_seen in (50, 150, 250)[:off_universe]:
                row["discovery_source"] = "off_universe"
                row["matched_terms"] = []
        out.append(row)
    return out


def _draft(labels: list[str], placements: list[dict[str, Any]], pages: int) -> dict[str, Any]:
    narrated = sum(1 for p in placements if p["status"] in ("placed", "verify"))
    return _dump(
        ChainDraftOut,
        {
            "thesis_id": THESIS_ID,
            "segments": [{"label": lbl, "descriptor": text(90, k)} for k, lbl in enumerate(labels)],
            "placements": placements,
            "report": {
                "coverage": {
                    "pages_ok": pages,
                    "pages_attempted": pages + 5,
                    "failed_terms": FAILED_TERMS,
                },
                "capped_terms": CAPPED_TERMS,
                "empty_terms": EMPTY_TERMS,
                "tail_sweep": "ran",
                "narration_needed": narrated,
                "narration_filled": narrated,
                "scope": "full",
            },
        },
    )


def run_latest() -> dict[str, Any]:
    """MEASURED: 1.18 MB; 7 segments, 1,735 placements (640 placed / 1,066 verify / 27 ambiguous / 2 absent), 1,145
    off-thesis, 3 off-universe, 4 capped terms, 1,296 pages."""
    counts = {"placed": 640, "verify": 1_066, "ambiguous": 27, "absent": 2}
    return _draft(DRAFT_SEGMENTS, _placements(1_735, DRAFT_SEGMENTS, counts, 1_145, 3), 1_296)


def run_small() -> dict[str, Any]:
    counts = {"placed": 90, "verify": 100, "ambiguous": 10, "absent": 3}
    return _draft(DRAFT_SEGMENTS[:4], _placements(203, DRAFT_SEGMENTS[:4], counts, 120, 0), 190)


def thesis_with_terms() -> dict[str, Any]:
    """``GET /theses/{id}`` with a 31-term term set (the first 8 SIGNAL): the rest of the card is the shared fixture."""
    payload = fx.thesis_detail()
    payload["term_set"] = [
        {
            "term": f"term-{k:02d}",
            "tier": "signal" if k <= 8 else "broad",
            "authored_by": "operator_set" if k <= 8 else "system_drafted",
            "source": "operator" if k <= 8 else "keyword_gen",
        }
        for k in range(1, TERM_COUNT + 1)
    ]
    return _dump(ThesisDetail, payload)


# --- GET /workbench/securities/{id}/extract[?thesis_id=] -------------------------------------------------------------------

FILING = "https://www.sec.gov/Archives/edgar/data/1000001/000100000126000012/synthetic-10q.htm"


def extract(thesis_scoped: bool = False) -> dict[str, Any]:
    """MEASURED: 12.8 KB (12.9 KB thesis-scoped): revenue_mix HUMAN with 3 passages (3,004 / 800 / 400 characters),
    shares_outstanding FLAG with 1 flag and 1 passage, cash_burn AUTO with 2 passages."""

    def passage(kind: str, chars: int, seed: int) -> dict[str, Any]:
        return {
            "kind": kind,
            "source_ref": FILING,
            "anchor": f"synthetic anchor {seed} " + text(70, seed),
            "excerpt": text(chars, seed),
            "offset": 52_000 + seed,
        }

    revenue: dict[str, Any] = {
        "fact_type": "revenue_mix",
        "tier": "human",
        "source": "10-k-segment",
        "source_ref": FILING,
        "event_date": "2026-09-30",
        "note": "Located only: " + text(260, 3),
        "flags": [],
        "located_passages": [
            passage("segment", 3_004, 1),
            passage("business-description", 800, 2),
            passage("segment", 400, 3),
        ],
    }
    if thesis_scoped:
        revenue["estimate_source"] = "llm_proposed"
    shares = {
        "fact_type": "shares_outstanding",
        "tier": "flag",
        "source": "10-q-cover",
        "source_ref": FILING,
        "event_date": "2026-10-28",
        "note": "Cover count; two share classes: " + text(120, 4),
        "value": 152_000_000.0,
        "flags": ["dual-class"],
        "located_passages": [passage("cover", 600, 4)],
    }
    burn = {
        "fact_type": "cash_burn",
        "tier": "auto",
        "source": "10-q",
        "source_ref": FILING,
        "event_date": "2026-09-30",
        "note": "Derived from the cash-flow statement: " + text(150, 5),
        "cash_usd": 410_000_000.0,
        "quarterly_burn_usd": 38_000_000.0,
        "flags": [],
        "located_passages": [passage("cash-flow", 900, 5), passage("balance-sheet", 700, 6)],
    }
    return _dump(ExtractionResult, {"facts": [revenue, shares, burn]})
