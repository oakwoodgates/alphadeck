"""Synthetic API payloads at the MEASURED production sizes (2026-11-06, mcp-phase1 spec section 7).

Every name is synthetic (``Thesis 01``, ``TK01``, ``Company 01``); no card, company or ticker from a live card.
Each payload is built as a dict, VALIDATED through the API's own wire model (so a fixture cannot drift from the
contract) and dumped to JSON exactly as FastAPI serves it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from app.routers.dashboards.ticker_lookup import TickerLookupOut
from app.routers.data_flags import DataFlagsResponse
from app.schemas_api import (
    CallCardResponse,
    DecisionOut,
    DisplaySignalsResponse,
    ThesisDetail,
    ThesisSummary,
)

ASOF = "2026-11-05"
CLOCK = {
    "current_night": ASOF,
    "action_day": "2026-11-06",
    "market_today": "2026-11-06",
    "market_tz": "America/New_York",
}
_NS = uuid.UUID("00000000-0000-4000-8000-000000000000")
_WORDS = (
    "synthetic filler text standing in for operator prose about a value chain link and the "
    "public evidence a deterministic reader located in a filing for the record"
).split()


def uid(*parts: object) -> str:
    return str(uuid.uuid5(_NS, "/".join(str(p) for p in parts)))


def text(n: int, seed: int = 0) -> str:
    """ASCII filler of exactly ``n`` characters."""
    out: list[str] = []
    i = seed
    length = 0
    while length < n:
        w = _WORDS[i % len(_WORDS)]
        out.append(w)
        length += len(w) + 1
        i += 1
    return " ".join(out)[:n]


def ticker(i: int) -> str:
    return f"TK{i:02d}"


def _day(offset: int) -> str:
    return (date.fromisoformat(ASOF) + timedelta(days=offset)).isoformat()


def _dump(model: Any, payload: Any) -> Any:
    return model.model_validate(payload).model_dump(mode="json")


# --- GET /theses?include_archived=true&with_call=true&asof= -------------------------------------------------------

THESES_COUNT = 68
ARCHIVED = {66, 67}
NO_RECORD = {10, 40}
STALE = {
    5: "armed",
    50: "warming",
}  # record_asof before ASOF (an armed one must never reach the queue)


def _state_for(i: int) -> str:
    # 64 current records: 37 armed, 26 warming, 1 incubating (prod, 2026-11-06); + 2 stale, 2 with none
    if i in STALE:
        return STALE[i]
    with_record = [j for j in range(THESES_COUNT) if j not in NO_RECORD and j not in STALE]
    k = with_record.index(i)
    if k < 37:
        return "armed"
    if k < 63:
        return "warming"
    return "incubating"


def theses_list(with_call: bool = True) -> list[dict[str, Any]]:
    out = []
    for i in range(THESES_COUNT):
        entry: dict[str, Any] = {
            "id": uid("thesis", i),
            "name": f"Thesis {i + 1:02d} synthetic theme card",
            "ticker": None,
            "basket_size": 20 + i % 15,
            "narrative": text(1_000, i),
            "archived": i in ARCHIVED,
        }
        if with_call:
            if i in NO_RECORD:
                entry["call"] = None
            else:
                state = _state_for(i)
                armed = state == "armed"
                entry["call"] = {
                    "record_asof": _day(-3) if i in STALE else ASOF,
                    "seq": 1000 + i,
                    "reconstructed": i == 20,
                    "recorded_at": f"{ASOF}T22:41:00Z",
                    "state": state,
                    "verdict": "starter_entry" if armed else "not_yet",
                    "conviction_grade": "core",
                    "confirmation_grade": "flip" if armed else None,
                    "entry_grade": "flip" if armed else None,
                    "confidence": 0.62 if armed else None,
                    "exit_by": _day(40),
                    "arm_until": _day(9) if armed else None,
                    "armed_security_id": uid("sec", i, 0) if armed else None,
                    "armed_ticker": ticker(i % 90) if armed else None,
                    "armed_count": 3 if armed else 0,
                    "missing_count": 0 if armed else 2,
                    "triggers": [
                        {
                            "ticker": ticker((i + k) % 90),
                            "kind": "insider" if k % 2 else "technical_breakout",
                            "label": text(110, i + k),
                            "grade": "core" if k % 2 else "flip",
                            "event_date": _day(-k),
                        }
                        for k in range(8)
                    ],
                }
        out.append(ThesisSummary.model_validate(entry).model_dump(mode="json", exclude_unset=True))
    return out


def queue_first_order() -> list[str]:
    """``cards`` order: the queue, then the other live cards, then the archived ones (list order)."""
    queue = queue_order()
    rest = [uid("thesis", i) for i in range(THESES_COUNT) if uid("thesis", i) not in queue]
    live = [t for t in rest if int(_index(t)) not in ARCHIVED]
    return queue + live + [t for t in rest if int(_index(t)) in ARCHIVED]


def _index(thesis_id: str) -> int:
    return next(i for i in range(THESES_COUNT) if uid("thesis", i) == thesis_id)


def queue_order() -> list[str]:
    """The expected Decision Queue: live, armed, not stale, in list order."""
    return [
        uid("thesis", i)
        for i in range(THESES_COUNT)
        if i not in ARCHIVED and i not in NO_RECORD and i not in STALE and _state_for(i) == "armed"
    ]


# --- GET /theses/{id}/call --------------------------------------------------------------------------------------


def _source(seed: int, detail_chars: int) -> dict[str, Any]:
    return {
        "source": "form4",
        "ref": f"0000000000-26-{seed:06d}",
        "url": f"https://www.sec.gov/Archives/edgar/data/1/{seed:018d}/index.html",
        "detail": {"computation": text(detail_chars, seed), "window_days": 20, "n": seed},
    }


def _trigger(i: int, kind: str, *, sources: int, detail_chars: int, label_chars: int = 110):
    return {
        "label": text(label_chars, i),
        "kind": kind,
        "grade": "core" if kind in ("insider", "technical_breakout") else None,
        "event_date": _day(-(i % 30)),
        "ticker": ticker(i % 28),
        "sources": [_source(i * 10 + s, detail_chars) for s in range(sources)],
    }


RISK_KINDS = (
    ["breakdown"] * 12 + ["corporate_risk"] * 5 + ["dilution_risk"] * 5 + ["insider_sell"] * 3
)


def call_payload() -> dict[str, Any]:
    """MEASURED: 58K total; risk_signals 45.7K for 25; counter_case 5.5K; triggers_fired 3.8K for 8."""
    triggers = [
        _trigger(i, "insider" if i % 2 else "technical_breakout", sources=1, detail_chars=95)
        for i in range(8)
    ]
    risks = [
        _trigger(100 + i, kind, sources=3, detail_chars=365, label_chars=85)
        for i, kind in enumerate(RISK_KINDS)
    ]
    members = [
        {
            "security_id": uid("sec", "call", m),
            "ticker": ticker(m),
            "verdict": "starter_entry",
            "conviction_grade": "core",
            "confirmation_grade": "flip",
            "entry_grade": "flip",
            "confidence": 0.6,
            "exit_by": _day(40),
            "arm_until": _day(9),
            "lapsing": False,
            "entry_window_closing": m == 6,
            "theme_armed": True,
            "triggers": [_trigger(200 + m, "insider", sources=1, detail_chars=60)],
        }
        for m in range(7)
    ]
    watch = [
        {
            "security_id": uid("sec", "call", "watch"),
            "ticker": ticker(27),
            "confirmation_grade": "flip",
            "arm_until": _day(9),
            "triggers": [_trigger(300, "technical_breakout", sources=1, detail_chars=60)],
        }
    ]
    payload = {
        "thesis_id": uid("thesis", 0),
        "asof": ASOF,
        "state": "armed",
        "verdict": "starter_entry",
        "conviction_grade": "core",
        "confirmation_grade": "flip",
        "entry_grade": "flip",
        "armed_security_id": members[0]["security_id"],
        "expression": text(180, 7),
        "exit_by": _day(40),
        "arm_until": _day(9),
        "catalyst_surface": [
            {"id": uid("cat", c), "label": text(45, c), "kind": "earnings", "when_date": _day(c)}
            for c in range(14)
        ],
        "confidence": 0.62,
        "key_conviction": {"turned": True, "label": "Conviction", "detail": text(64, 1)},
        "key_confirmation": {"turned": True, "label": "Confirmation", "detail": text(54, 2)},
        "triggers_fired": triggers,
        "risk_signals": risks,
        "missing": [text(170, 3), text(180, 4)],
        "counter_case": text(5_000, 5),
        "safe_sleeve": None,
        "armed_members": members,
        "watch_members": watch,
    }
    return _dump(CallCardResponse, payload)


# --- GET /theses/{id} -------------------------------------------------------------------------------------------

REAL_LINKS = [f"Link {k + 1} synthetic segment" for k in range(5)]
SILENT = ["Context", "ETFs", "Bench"]


def thesis_detail() -> dict[str, Any]:
    """MEASURED: 19K for 28 members (basket 12.7K), 14 catalysts, 5 kill criteria, 5 segments, 10 terms."""
    labels = REAL_LINKS + ["Discovered"] + SILENT
    basket = []
    for m in range(28):
        seg = labels[m % len(labels)] if m < 24 else REAL_LINKS[m % 5]
        basket.append(
            {
                "ticker": ticker(m),
                "role": text(20, m),
                "security_id": uid("sec", "detail", m),
                "detail": None,
                "segment": seg,
                "thesis_fit": text(190, m),
                "conviction": (m % 5) + 1 if m % 3 == 0 else None,
                "surfaced_terms": [],
                "authored_by": "operator_edited" if m % 4 == 0 else "system_drafted",
                "signed_off": m % 2 == 0,
                "context_role": "payer" if seg == "Context" else None,
            }
        )
    payload = {
        "id": uid("thesis", 0),
        "parent_id": None,
        "name": "Thesis 01 synthetic theme card",
        "narrative": text(1_200, 9),
        "ticker": None,
        "basket": basket,
        "segments": [{"label": lbl, "descriptor": text(126, k)} for k, lbl in enumerate(REAL_LINKS)]
        + [{"label": lbl, "descriptor": text(110, 20 + k)} for k, lbl in enumerate(SILENT)],
        "term_set": [
            {
                "term": f"synthetic term {k:02d}",
                "tier": "signal" if k < 6 else "broad",
                "authored_by": "operator_set",
                "source": "operator",
            }
            for k in range(10)
        ],
        "evidence": [],
        "catalysts": [
            {
                "id": uid("cat", c),
                "label": text(45, c),
                "kind": "regulatory",
                "when_date": _day(c * 5),
                "when_label": None,
            }
            for c in range(14)
        ],
        "kill_criteria": [{"id": uid("kill", k), "text": text(101, k)} for k in range(5)],
        "position": None,
        "exclusions": [],
    }
    return _dump(ThesisDetail, payload)


# --- GET /theses/{id}/display-signals ------------------------------------------------------------------------------

SIGNAL_KINDS = [
    "sma_position",
    "trailing_returns",
    "rvol",
    "insider_flow",
    "range_position",
    "drawdown",
    "volume_trend",
    "relative_strength",
    "gap_tracker",
]


def _signal(kind: str, chars: int, seed: int) -> dict[str, Any]:
    metrics = [
        {"key": f"m{k}", "label": f"Metric {k}", "value": 1.5 * k, "unit": "pct", "note": None}
        for k in range(4)
    ]
    sig = {
        "kind": kind,
        "label": kind.replace("_", " ").title(),
        "headline": {"key": "above", "label": "Above 50d", "glyph": "up", "detail": text(40, seed)},
        "metrics": metrics,
        "events": [],
        "series": [{"key": "close", "label": "Close", "unit": "price", "values": []}],
        "basis": {"source": "fact_price_eod", "params": {"window": 50}, "bars_used": 60},
    }
    base = len(str(sig))
    sig["series"][0]["values"] = [100.0 + (k % 7) for k in range(max(0, (chars - base) // 7))]
    return sig


def display_signals() -> dict[str, Any]:
    """MEASURED: 222K for 28 members x 9 signals of 500 to 1,900 chars."""
    members = [
        {
            "security_id": uid("sec", "ds", m),
            "ticker": ticker(m),
            "signals": [
                _signal(kind, 500 + ((m * 9 + k) * 97) % 800, m * 9 + k)
                for k, kind in enumerate(SIGNAL_KINDS)
            ],
        }
        for m in range(28)
    ]
    payload = {
        "thesis_id": uid("thesis", 0),
        "asof": ASOF,
        "breadth": _signal("theme_breadth", 950, 1),
        "sector_rs": _signal("sector_rs", 1_350, 2),
        "members": members,
    }
    return _dump(DisplaySignalsResponse, payload)


# --- GET /data-flags and GET /theses/{id}/data-flags ----------------------------------------------------------------


def _flag(seed: int) -> dict[str, Any]:
    return {
        "kind": "split_unconfirmed",
        "label": "Split not confirmed",
        "level": "in_doubt",
        "reason": text(220, seed),
        "clears": text(80, seed + 1),
        "since": _day(-10),
        "affects": ["price", "returns"],
        "columns": ["close"],
        "dates": [_day(-10)],
        "evidence": [{"label": "8-K", "url": "https://www.sec.gov/x"}],
        "call_affected": seed % 3 == 0,
    }


def _flag_member(m: int, *, flagged: bool, cards: int) -> dict[str, Any]:
    return {
        "security_id": uid("sec", "flags", m),
        "ticker": ticker(m % 100),
        "marked": flagged,
        "flags": [_flag(m)] if flagged else [],
        "splits": [],
        "notes": [],
        "theses": [
            {"id": uid("thesis", (m + c) % 68), "name": f"Thesis {(m + c) % 68 + 1:02d}"}
            for c in range(cards)
        ],
    }


def data_flags_all() -> dict[str, Any]:
    """MEASURED: 159K cross-card."""
    members = [_flag_member(m, flagged=True, cards=2) for m in range(200)]
    return _dump(
        DataFlagsResponse, {"thesis_id": None, "asof": ASOF, "names": 200, "members": members}
    )


def data_flags_one() -> dict[str, Any]:
    """MEASURED: 5.9K per thesis."""
    members = [_flag_member(m, flagged=m in (3, 11, 19), cards=0) for m in range(28)]
    return _dump(
        DataFlagsResponse,
        {"thesis_id": uid("thesis", 0), "asof": ASOF, "names": 2, "members": members},
    )


# --- GET /theses/{id}/decisions -----------------------------------------------------------------------------------


def decisions(n: int = 40) -> list[dict[str, Any]]:
    return [
        _dump(
            DecisionOut,
            {
                "id": uid("decision", d),
                "action": ("take", "pass", "close")[d % 3],
                "decision_date": _day(-d),
                "security_id": uid("sec", "call", d % 7),
                "shares": 100.0,
                "price": 12.5,
                "reason": text(120, d),
                "voids": None,
                "call_state": "armed",
                "call_verdict": "starter_entry",
                "recorded_at": f"{_day(-d)}T15:00:00Z",
                "voided": False,
            },
        )
        for d in range(n)
    ]


# --- GET /dashboards/ticker-lookup/{security_id} --------------------------------------------------------------------


def ticker_resolve() -> dict[str, Any]:
    return {
        "security_id": uid("sec", "lookup"),
        "ticker": "TK01",
        "name": "Company 01",
        "matched": "ticker",
    }


def _filing_row(r: int) -> dict[str, Any]:
    return {
        "kind": "filing",
        "form": ("8-K", "10-Q", "4", "424B5")[r % 4],
        "date": _day(-(r // 2)),
        "headline": text(90, r),
        "detail": [text(400, r + k) for k in range(3)],
        "url": f"https://www.sec.gov/Archives/edgar/data/1/{r:018d}/index.html",
        "accession": f"0000000000-26-{r:06d}",
        "tags": ["material"],
        "routine": r % 4 == 2,
        "fold": None,
        "usd": None,
        "ratifier": None,
        "ratified_on": None,
    }


def ticker_lookup() -> dict[str, Any]:
    """MEASURED: 164K, ``filings`` 150K, the rest about 14K."""
    rows = [_filing_row(r) for r in range(94)]
    days: dict[str, list] = {}
    for row in rows:
        days.setdefault(row["date"], []).append(row)
    sid = uid("sec", "lookup")
    payload = {
        "asof": ASOF,
        "clock": "record",
        "identity": {
            "security_id": sid,
            "ticker": "TK01",
            "name": "Company 01",
            "cik": "0000000001",
            "edgar_url": "https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000000001",
            "exchange": "NASDAQ",
            "business_type": "biotech_pharma",
            "supersector": "health_care",
            "royalty": False,
            "origin": "US",
            "instrument_kind": "common",
            "price_symbol": None,
            "former_tickers": [],
            "cap": {
                "value": 1.2e9,
                "tier": "small",
                "gap": None,
                "note": None,
                "close": 12.5,
                "close_date": ASOF,
                "shares": 9.6e7,
                "shares_date": _day(-30),
                "ads_ratio": None,
                "split_factor": None,
            },
            "adv_usd_20d": 4.1e7,
            "adv_note": None,
            "flag": None,
        },
        "cards": {
            "live_cards": 66,
            "not_yet_created": 0,
            "roster_fallback": 0,
            "counted": 66,
            "rows": [
                {
                    "thesis_id": uid("thesis", c),
                    "thesis_name": f"Thesis {c + 1:02d}",
                    "ticker": "TK01",
                    "links": [
                        {
                            "link": REAL_LINKS[c],
                            "thesis_fit": text(300, c),
                            "authored_by": "system_drafted",
                            "signed_off": True,
                            "weight": None,
                        }
                    ],
                    "silent": None,
                    "context_role": None,
                    "signed_off": True,
                    "state": "armed",
                    "verdict": "starter_entry",
                    "recorded_on": ASOF,
                    "reconstructed": False,
                    "place": {
                        "kind": "armed",
                        "verdict": "starter_entry",
                        "grade": "flip",
                        "strength": 0.6,
                        "live_triggers": 2,
                        "turns": ["confirmation"],
                        "live_risks": 1,
                    },
                }
                for c in range(2)
            ],
            "archived": [],
            "excluded": [],
        },
        "triggers": {
            "window_start": _day(-365),
            "events": [
                {
                    "thesis_id": uid("thesis", e % 2),
                    "thesis_name": f"Thesis {e % 2 + 1:02d}",
                    "ticker": "TK01",
                    "recorded_on": _day(-e),
                    "reconstructed": False,
                    "event_date": _day(-e - 1),
                    "recorded_after_days": 1,
                    "role": "entry_trigger" if e % 3 else "risk_signal",
                    "kind": "insider",
                    "grade": "core",
                    "label": text(110, e),
                    "label_changes": 0,
                    "turns": "conviction",
                    "sources": [
                        {
                            "source": "form4",
                            "ref": f"0000000000-26-{e:06d}",
                            "url": "https://www.sec.gov/x",
                            "detail": [text(200, e)],
                        }
                    ],
                    "live": e < 3,
                }
                for e in range(9)
            ],
            "cards_read": 2,
            "nights_read": 300,
            "reconstructed_nights": 0,
            "before_window_live": 0,
        },
        "tape": {
            "figures": [
                {
                    "member": "trailing_returns",
                    "key": f"f{f}",
                    "group": "returns",
                    "label": f"Figure {f}",
                    "value": 1.25 * f,
                    "unit": "pct",
                    "note": None,
                    "tone": "pos" if f % 2 else None,
                    "level": None,
                    "on": None,
                }
                for f in range(21)
            ],
            "leading": [],
            "coiling": False,
            "coiling_detail": None,
            "headline_notes": [],
            "gaps": [],
            "members_read": 9,
            "members_without": 0,
            "figures_blank": 0,
            "sparkline": {
                "dates": [_day(-k) for k in range(60)],
                "stock": [100.0 + k for k in range(60)],
                "iwm": [100.0 - k / 10 for k in range(60)],
                "bars": 60,
                "iwm_bars": 60,
                "note": None,
            },
            "window_end": ASOF,
        },
        "filings": {
            "read_days": 365,
            "days": [{"date": d, "rows": r} for d, r in days.items()],
            "rows": len(rows),
            "by_kind": [{"kind": "filing", "rows": len(rows)}],
            "routine": 27,
            "not_listed": [],
        },
        "catalyst_facts": [],
        "card_catalysts": [
            {
                "thesis_id": uid("thesis", c % 2),
                "thesis_name": f"Thesis {c % 2 + 1:02d}",
                "label": text(45, c),
                "kind": "regulatory",
                "when_date": _day(c * 3 - 6),
                "when_label": None,
                "passed": c < 2,
            }
            for c in range(14)
        ],
        "decisions": [],
        "decisions_voided": 0,
        "cache": "computed",
        "compute_ms": 2400,
    }
    return _dump(TickerLookupOut, payload)
