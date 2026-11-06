"""Synthetic dashboard payloads at the MEASURED production sizes (2026-11-06, mcp-phase2 spec section 7).

Built in code, never stored as JSON, with synthetic names only (``Thesis 01 synthetic theme card``, ``TK01``). Each
builder carries the wire fields the API's response models declare (the views' key tuples are checked against those
models in ``test_dashboards``), at row sizes close to what the API serves. Built once per module and shared.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from tests.agent_tools import fixtures as fx
from tests.agent_tools.fixtures import ASOF, text, ticker, uid

CARDS = 66


def thesis_name(i: int) -> str:
    return f"Thesis {i + 1:02d} synthetic theme card"


def tid(i: int) -> str:
    return uid("thesis", i)


def _source(i: int) -> dict[str, Any]:
    return {"source": "form4", "ref": f"0000000000-26-{i:06d}", "url": f"https://www.sec.gov/{i}"}


def _flagged(n: int) -> list[dict[str, Any]]:
    return [
        {"security_id": uid("sec", "flag", i), "ticker": ticker(i), "reason": text(120, i)}
        for i in range(n)
    ]


# --- daily-digest -------------------------------------------------------------------------------------------------

DIGEST_CARDS = 60
CHANGES = ("state", "armed", "trigger_new", "risk_new", "entry_window", "trigger_redated")


def _digest_line(card: int, k: int) -> dict[str, Any]:
    return {
        "section": "triggers",
        "change": CHANGES[(card + k) % len(CHANGES)],
        "security_id": uid("sec", card, k),
        "ticker": ticker((card + k) % 90),
        "from_state": "warming",
        "to_state": "armed",
        "label": text(60, card + k),
        "signal_kind": "insider",
        "on": fx._day(-k),
        "previous_on": None,
        "days": None,
        "tone": "accent",
        "source": _source(card * 10 + k),
    }


def _last_session(i: int) -> dict[str, Any]:
    return {
        "status": "priced",
        "reason": None,
        "session": ASOF,
        "previous_session": fx._day(-1),
        "ret_pct": round(1.5 + i % 7, 2),
        "bench_pct": 0.4,
    }


def _tape_row(i: int) -> dict[str, Any]:
    return {
        "key": f"tape-{i}",
        "thesis_id": tid(i % CARDS),
        "thesis_name": thesis_name(i % CARDS),
        "security_id": uid("sec", "tape", i),
        "ticker": ticker(i % 90),
        "arm_night": fx._day(-(i % 20)),
        "arm_reconstructed": False,
        "sessions_since": i % 20,
        "arm_until": fx._day(9),
        "sessions_left": 9 - i % 9,
        "closed_ago": None,
        "last_session": _last_session(i),
        "since_arm": {
            "entry_date": fx._day(-(i % 20)),
            "entry_close": 10.0 + i,
            "last_date": ASOF,
            "last_close": 11.0 + i,
            "ret_pct": round(3.2 + i % 11, 2),
            "iwm_pct": 1.1,
            "gap": None,
        },
        "cut": "rise" if i % 3 else "fall",
    }


def _insider_row(i: int) -> dict[str, Any]:
    return {
        "key": f"buy-{i}",
        "security_id": uid("sec", "buy", i),
        "ticker": ticker(i),
        "accession": f"0000000000-26-{i:06d}",
        "form": "4",
        "url": f"https://www.sec.gov/buy/{i}",
        "filed": fx._day(-1),
        "trade_date": fx._day(-2),
        "filed_days_after": 1,
        "cards": [
            {"thesis_id": tid(c), "thesis_name": thesis_name(c), "ticker": ticker(i)}
            for c in range(3)
        ],
        "filer": f"Filer {i} Synthetic",
        "role": "Chief Executive Officer",
        "role_classes": ["officer"],
        "co_filers": 0,
        "shares": 10_000.0,
        "price": 5.25,
        "usd": 52_500.0,
        "tags": [],
        "headline": text(160, i),
        "cluster": None,
        "triggers": [
            {"thesis_id": tid(c), "thesis_name": thesis_name(c), "recorded_on": ASOF, "live": True}
            for c in range(2)
        ],
    }


def _due_item(i: int) -> dict[str, Any]:
    return {
        "item": ("catalyst", "entry_window", "exit_by")[i % 3],
        "on": fx._day(i % 10),
        "days": i % 10,
        "today": i == 0,
        "label": text(110, i),
        "kind": "earnings",
        "when_label": None,
        "thesis_id": tid(i),
        "thesis_name": thesis_name(i),
        "state": "armed",
    }


@cache
def daily_digest() -> dict[str, Any]:
    """MEASURED 339K: 60 changed cards (headline + lines), tape 69 rows, insider 9 rows, due 14 items."""
    cards = []
    for c in range(DIGEST_CARDS):
        lines = [_digest_line(c, k) for k in range(2)]
        cards.append(
            {
                "thesis_id": tid(c),
                "thesis_name": thesis_name(c),
                "tier": "armed" if c % 2 else "warming",
                "state": "armed" if c % 2 else "warming",
                "previous_state": "warming",
                "reconstructed": False,
                "ingest_fresh": True,
                "headline": lines[0],
                "lines": lines,
            }
        )
    return {
        "asof": ASOF,
        "clock": "record",
        "nights": {
            "asof": ASOF,
            "latest": ASOF,
            "previous": fx._day(-1),
            "sessions_between": 1,
            "days_after_night": 0,
        },
        "last_night": {
            "no_baseline": False,
            "cards_with_change": DIGEST_CARDS,
            "lines": 2 * DIGEST_CARDS,
            "cards": cards,
        },
        "positions": [
            {
                "thesis_id": tid(0),
                "thesis_name": thesis_name(0),
                "has_position": True,
                "security_id": uid("sec", "pos"),
                "ticker": ticker(1),
                "opened_on": fx._day(-12),
                "logged_price": 5.5,
                "shares": 100.0,
                "fill_usd": 550.0,
                "since_fill": {"status": "priced", "ret_pct": 4.1, "bench_pct": 1.0},
                "last_session": _last_session(0),
                "hold": {"exit_by": fx._day(40), "sessions_left": 28, "passed_sessions": 12},
                "risk_signals": 2,
                "risk_new": 0,
                "kill_criteria": [text(80, 1)],
                "reconstructed": False,
                "ingest_fresh": True,
            }
        ],
        "due": {
            "start": ASOF,
            "end": fx._day(14),
            "items": [_due_item(i) for i in range(14)],
            "catalysts": 9,
            "deadlines": 5,
            "passed_since": 3,
            "passed_total": 134,
            "cards_without_calls": 2,
        },
        "insider": {
            "no_baseline": False,
            "span_start": fx._day(-3),
            "span_end": ASOF,
            "rows": [_insider_row(i) for i in range(9)],
            "counts": {
                "names_read": 700,
                "filings_read": 40,
                "purchases": 9,
                "routine_folded": 4,
                "amendments_no_purchase": 1,
                "names_with_purchases": 9,
                "filer_unresolved": 0,
                "cluster_rows": 0,
                "trigger_rows": 18,
                "nights_read": 3,
                "nights_unreadable": 0,
                "silent_rows_left_out": 12,
                "unresolved_rows": 0,
            },
        },
        "tape": {
            "session": ASOF,
            "previous_session": fx._day(-1),
            "iwm_pct": 0.42,
            "calendar_source": "benchmarks",
            "calendar_sessions": 250,
            "rows": [_tape_row(i) for i in range(69)],
            "counts": {
                "rows": 69,
                "names": 69,
                "names_priced": 66,
                "names_unpriced": 2,
                "names_flagged": 1,
                "since_arm_priced": 60,
                "since_arm_blank": 9,
                "rises_shown": 30,
                "falls_shown": 30,
            },
        },
        "forming": {
            "warming": 26,
            "incubating": 1,
            "key_turned": [
                {"thesis_id": tid(c), "thesis_name": thesis_name(c), "labels": ["Conviction"]}
                for c in range(5)
            ],
        },
        "counts": {
            "cards_counted": CARDS,
            "live_cards": CARDS,
            "not_yet_created": 0,
            "roster_fallback": 1,
            "cards_recorded_on_night": 64,
            "cards_carried": 2,
            "cards_no_call": 2,
            "cards_armed": 37,
            "cards_warming": 26,
            "cards_managing": 0,
            "cards_incubating": 1,
            "cards_ingest_not_fresh": 0,
            "cards_reconstructed": 1,
        },
        "flagged": _flagged(8),
        "cache": "computed",
        "compute_ms": 41_700,
    }


# --- what-changed -------------------------------------------------------------------------------------------------

MIX = (
    ("risk_new", "names", 423),
    ("trigger_redated", "triggers", 34),
    ("trigger_new", "triggers", 26),
    ("no_longer_armed", "column_moves", 23),
    ("trigger_gone", "triggers", 15),
    ("entry_window", "clocks", 11),
    ("armed", "column_moves", 9),
    ("state", "column_moves", 8),
    ("key_on", "keys", 6),
    ("new_card", "new_cards", 4),
)


@cache
def what_changed() -> dict[str, Any]:
    """MEASURED 273K for 559 items over the five measured sections, with the measured change mix."""
    items: list[dict[str, Any]] = []
    n = 0
    for change, section, count in MIX:
        for _ in range(count):
            c = n % CARDS
            items.append(
                {
                    "section": section,
                    "change": change,
                    "thesis_id": tid(c),
                    "thesis_name": thesis_name(c),
                    "reconstructed": False,
                    "security_id": uid("sec", n),
                    "ticker": ticker(n % 90),
                    "from_state": "warming",
                    "to_state": "armed",
                    "label": text(150, n),
                    "signal_kind": "breakdown",
                    "on": fx._day(-(n % 5)),
                    "previous_on": None,
                    "days": None,
                }
            )
            n += 1
    return {
        "since": "last_night",
        "since_date": fx._day(-1),
        "first_recorded": "2026-09-08",
        "latest": ASOF,
        "baseline": fx._day(-1),
        "live_cards": CARDS,
        "cards_with_change": 60,
        "unrecorded": [{"thesis_id": tid(c), "thesis_name": thesis_name(c)} for c in (10, 40)],
        "items": items,
    }


PAYLOADS = {
    "daily-digest": daily_digest,
    "what-changed": what_changed,
}
