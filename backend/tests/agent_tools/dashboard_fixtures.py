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


# --- hot-board ----------------------------------------------------------------------------------------------------


def _cell(i: int, note: str | None = None) -> dict[str, Any]:
    return {"value": round(1.0 + i % 9 / 3, 2), "note": note}


def _hot_trigger(i: int, k: int) -> dict[str, Any]:
    return {
        "label": text(95, i + k),
        "kind": ("insider", "technical_breakout", "laggard", "squeeze")[k % 4],
        "grade": "core" if k % 2 else "flip",
        "event_date": fx._day(-k),
        "sources": [
            {
                "source": "form4",
                "ref": f"0000000000-26-{i * 10 + k:06d}",
                "url": f"https://www.sec.gov/{i}/{k}",
            }
        ],
    }


def _hot_row(i: int) -> dict[str, Any]:
    return {
        "key": f"hot-{i}",
        "kind": ("armed", "warming", "watch", "managing")[i % 4],
        "thesis_id": tid(i % CARDS),
        "thesis_name": thesis_name(i % CARDS),
        "security_id": uid("sec", "hot", i),
        "ticker": ticker(i % 90),
        "name": f"Company {i + 1:02d} Synthetic Holdings Incorporated",
        "also_armed": [{"thesis_id": tid(c), "thesis_name": thesis_name(c)} for c in range(2)],
        "verdict": "starter_entry",
        "entry_grade": "flip",
        "confidence": 0.62,
        "keys": ["conviction", "confirmation"],
        "triggers": [_hot_trigger(i, k) for k in range(4)],
        "risk_kinds": ["breakdown", "dilution_risk"],
        "clocks": {
            "arm_night": fx._day(-(i % 15)),
            "arm_reconstructed": False,
            "sessions_since": i % 15,
            "arm_until": fx._day(9),
            "sessions_left": 9 - i % 9,
            "closed_ago": None,
            "exit_by": fx._day(40),
            "days_to_exit": 40,
        },
        "entry_window_closing": i % 7 == 0,
        "lapsing": False,
        "since_arm": {
            "entry_date": fx._day(-(i % 15)),
            "entry_close": 10.0,
            "last_date": ASOF,
            "last_close": 11.5,
            "ret_pct": 15.0,
            "iwm_pct": 2.0,
            "gap": None,
        },
        "tape": {
            "flag": None,
            "rvol20": _cell(i),
            "vs_50d": _cell(i),
            "off_high": _cell(i),
            "vs_iwm": _cell(i),
            "rs_leading": True,
            "rs_leading_vs": ["IWM", "SPY"],
            "ret_7d": _cell(i),
            "ret_30d": _cell(i),
            "buys_30d": 2,
            "buyers_30d": 1,
            "adv_usd_20d": _cell(i),
            "spark": [round(10 + k / 5, 2) for k in range(30)],
        },
        "cap": {
            "value": 450_000_000.0,
            "tier": "small",
            "gap": None,
            "note": None,
            "close": 11.5,
            "close_date": ASOF,
            "shares": 39_000_000.0,
            "shares_date": fx._day(-30),
            "ads_ratio": None,
            "split_factor": None,
        },
    }


@cache
def hot_board() -> dict[str, Any]:
    """MEASURED 194K: 69 rows of about 2.6K (4 triggers with sources, tape, cap)."""
    return {
        "asof": ASOF,
        "clock": "record",
        "benchmark": "IWM",
        "live_cards": CARDS,
        "cards_counted": CARDS,
        "cards_not_yet_created": 0,
        "roster_fallback_cards": 1,
        "cards_armed": 37,
        "cards_warming": 26,
        "cards_managing": 0,
        "cards_incubating": 1,
        "cards_no_call": 2,
        "calendar_source": "benchmarks",
        "calendar_sessions": 250,
        "calendar_first": "2025-11-05",
        "calendar_last": ASOF,
        "price_window_start": "2025-11-05",
        "no_ticker_rows": 0,
        "rows_loaded": "armed",
        "candidate_counts": {"armed": 69, "warming": 120, "watch": 30, "managing": 0},
        "rows": [_hot_row(i) for i in range(69)],
        "flagged": _flagged(3),
        "cache": "hit",
        "compute_ms": 12,
    }


# --- entry-windows ------------------------------------------------------------------------------------------------


def _ew_member(i: int, m: int) -> dict[str, Any]:
    return {
        "security_id": uid("sec", "ew", i, m),
        "ticker": ticker((i + m) % 90),
        "arm_night": fx._day(-(i % 15)),
        "entry_on": fx._day(-(i % 15) + 1),
        "since": round(4.2 + m, 2),
        "above": True,
        "flag": None,
        "triggers": [
            {
                "label": text(90, m),
                "kind": "insider",
                "grade": "core",
                "event_date": fx._day(-m),
                "sources": [{"source": "form4", "ref": f"0000000000-26-{m:06d}", "url": None}],
            }
        ],
    }


def _ew_row(i: int) -> dict[str, Any]:
    return {
        "thesis_id": tid(i),
        "thesis_name": thesis_name(i),
        "state": "armed",
        "arm_night": fx._day(-(i % 15)),
        "in_effect": ASOF,
        "reconstructed": False,
        "reconstructed_nights": 0,
        "run_nights": 1 + i % 15,
        "arm_until": fx._day(9),
        "exit_by": fx._day(40),
        "confidence": 0.62,
        "entry_window_closing": i % 7 == 0,
        "lapsing": False,
        "sessions_into": i % 15,
        "sessions_left": 9 - i % 9,
        "closed_ago": None,
        "last_session": ASOF,
        "hold_days": 40,
        "member_clocks": [
            {
                "security_id": uid("sec", "ew", i, m),
                "ticker": ticker((i + m) % 90),
                "headline": m == 0,
                "entry_window_closing": False,
                "lapsing": False,
                "arm_until": fx._day(9),
                "sessions_left": 9,
                "closed_ago": None,
                "last_session": ASOF,
            }
            for m in range(3)
        ],
        "catalysts": [
            {"label": text(60, c), "kind": "earnings", "when_date": fx._day(c * 5), "past": False}
            for c in range(4)
        ],
        "members": [_ew_member(i, m) for m in range(3)],
        "since": {
            "names": 3,
            "priced": 3,
            "flagged": 0,
            "no_bar": 0,
            "above": 3,
            "median": 4.2,
            "iwm": 1.1,
            "spy": 0.9,
        },
        "spark": {
            "dates": [fx._day(-k) for k in range(20)],
            "line": [1.0 + k / 10 for k in range(20)],
            "iwm": None,
        },
    }


@cache
def entry_windows() -> dict[str, Any]:
    """MEASURED 139K: 37 rows of about 1.6K with 3 members."""
    return {
        "asof": ASOF,
        "clock": "record",
        "axis_start": "2025-11-05",
        "axis_end": ASOF,
        "session_source": "benchmarks",
        "calendar_sessions": 250,
        "calendar_last": ASOF,
        "counts": {
            "live_cards": CARDS,
            "cards_counted": CARDS,
            "cards_not_yet_created": 0,
            "armed": 37,
            "managing": 0,
            "managing_without_arm_night": 0,
            "warming": 26,
            "incubating": 1,
            "no_call": 2,
            "reconstructed_arm_nights": 1,
            "armed_names": 110,
            "priced_names": 108,
            "unpriced_names": 2,
        },
        "flagged": _flagged(2),
        "rows": [_ew_row(i) for i in range(37)],
        "cache": "computed",
        "compute_ms": 9_800,
    }


# --- forming-quietly ----------------------------------------------------------------------------------------------


def _fq_trigger(c: int, n: int, k: int) -> dict[str, Any]:
    return {
        "label": text(70, c + n + k),
        "kind": "insider",
        "grade": "core",
        "event_date": fx._day(-k),
        "sources": [
            {
                "source": "form4",
                "ref": f"0000000000-26-{c * 100 + n:06d}",
                "url": f"https://www.sec.gov/{c}/{n}",
            }
            for _ in range(2)
        ],
    }


def _fq_row(c: int) -> dict[str, Any]:
    names = [
        {
            "security_id": uid("sec", "fq", c, n),
            "ticker": ticker((c + n) % 90),
            "triggers": [_fq_trigger(c, n, k) for k in range(3)],
        }
        for n in range(20)
    ]
    return {
        "key": f"fq-{c}",
        "thesis_id": tid(c),
        "thesis_name": thesis_name(c),
        "state": "warming" if c % 4 else "incubating",
        "recorded_on": ASOF,
        "run_start": fx._day(-(c % 12)),
        "run_nights": 1 + c % 12,
        "days_in_state": c % 12,
        "run_reconstructed": False,
        "key_conviction": {"turned": c % 2 == 0, "label": "Conviction", "detail": text(60, c)},
        "key_confirmation": {"turned": False, "label": "Confirmation", "detail": None},
        "names": names,
        "missing": [text(120, c + k) for k in range(3)],
        "watch": [
            {"security_id": uid("sec", "fqw", c, w), "ticker": ticker((c + w + 40) % 90)}
            for w in range(4)
        ],
        "risks": [
            {
                "label": text(200, c + r),
                "kind": ("breakdown", "dilution_risk", "corporate_risk", "insider_sell")[r % 4],
                "security_id": uid("sec", "fq", c, r % 20),
                "ticker": ticker((c + r) % 90),
            }
            for r in range(40)
        ],
        "quiet": {
            "names": 20,
            "read": 18,
            "unresolved": 0,
            "flagged": [
                {
                    "security_id": uid("sec", "fq", c, 1),
                    "ticker": ticker(c % 90),
                    "reason": text(90, c),
                }
            ],
            "insider": [
                {
                    "security_id": uid("sec", "fq", c, n),
                    "ticker": ticker((c + n) % 90),
                    "buys_30d": 2,
                    "buyers_30d": 1,
                    "cluster": False,
                }
                for n in range(6)
            ],
            "no_form4": 3,
            "coiling": [
                {
                    "security_id": uid("sec", "fq", c, n),
                    "ticker": ticker((c + n) % 90),
                    "contraction": 0.42,
                }
                for n in range(8)
            ],
            "coiling_no_reading": 1,
            "leading": [
                {
                    "security_id": uid("sec", "fq", c, n),
                    "ticker": ticker((c + n) % 90),
                    "vs": ["IWM", "SPY"],
                }
                for n in range(5)
            ],
            "leading_no_reading": 0,
            "breadth": {
                "above": 12,
                "counted": 18,
                "thin": 0,
                "pct": 66.7,
                "delta_pts": 5.0,
                "thrust": False,
            },
            "above_200": 12,
            "n_200": 18,
            "no_200": 2,
            "lit": True,
        },
        "adoption": {
            "counted": True,
            "percent": 42.5,
            "recent": 120,
            "prior": 84,
            "recent_from": fx._day(-30),
            "recent_to": ASOF,
            "prior_from": fx._day(-60),
            "prior_to": fx._day(-31),
            "terms_signal": 5,
            "terms_counted": 5,
            "terms_partial": 0,
            "terms_unstored": 0,
            "months_stored": 24,
            "latest_partial": False,
        },
    }


@cache
def forming_quietly() -> dict[str, Any]:
    """MEASURED 393K: 29 rows of about 38K (20 names with triggers, 40 risks, 3 missing strings)."""
    return {
        "asof": ASOF,
        "clock": "record",
        "live_cards": CARDS,
        "cards_counted": CARDS,
        "cards_not_yet_created": 0,
        "roster_fallback_cards": 1,
        "cards_armed": 37,
        "cards_warming": 26,
        "cards_managing": 0,
        "cards_incubating": 1,
        "cards_incubating_lit": 1,
        "cards_no_call": 2,
        "cards_with_term_months": 25,
        "cards_without_term_months": 1,
        "names_read": 560,
        "names_flagged": 4,
        "members": [ticker(i) for i in range(30)],
        "rows": [_fq_row(c) for c in range(29)],
        "flagged": _flagged(4),
        "cache": "computed",
        "compute_ms": 30_000,
    }


# --- catalyst-calendar --------------------------------------------------------------------------------------------

BUCKETS = (
    ("this_week", "This week", 0, 6),
    ("next_week", "Next week", 7, 13),
    ("next_30_days", "Next 30 days", 14, 30),
    ("days_31_90", "31 to 90 days", 31, 90),
    ("later", "Later", 91, None),
)


def _cc_item(n: int, days: int) -> dict[str, Any]:
    c = n % CARDS
    return {
        "item": ("catalyst", "entry_window", "exit_by")[n % 3],
        "on": fx._day(days),
        "days": days,
        "label": text(70, n),
        "kind": "earnings",
        "when_label": None,
        "catalyst_id": uid("cat", n),
        "thesis_id": tid(c),
        "thesis_name": thesis_name(c),
        "state": "armed",
    }


@cache
def catalyst_calendar() -> dict[str, Any]:
    """MEASURED 242K: 5 buckets of about 40 items, 63 undated groups, 134 passed."""
    n = 0
    buckets = []
    for key, label, lo, hi in BUCKETS:
        items = []
        for k in range(40):
            items.append(_cc_item(n, lo + k % max(1, (hi or lo + 60) - lo)))
            n += 1
        buckets.append(
            {
                "key": key,
                "label": label,
                "start": fx._day(lo),
                "end": fx._day(hi) if hi is not None else None,
                "items": items,
            }
        )
    groups = [
        {
            "thesis_id": tid(g),
            "thesis_name": thesis_name(g),
            "state": "warming",
            "count": 3,
            "catalysts": [
                {
                    "catalyst_id": uid("catu", g, k),
                    "label": text(60, g + k),
                    "kind": "regulatory",
                    "when_label": "mid 2027",
                }
                for k in range(3)
            ],
        }
        for g in range(63)
    ]
    return {
        "today": ASOF,
        "live_cards": CARDS,
        "cards_without_calls": 2,
        "cards_without_catalysts": 1,
        "catalysts": 400,
        "dated": 337,
        "upcoming_catalysts": 203,
        "passed_catalysts": 134,
        "undated": 63,
        "deadlines": 80,
        "buckets": buckets,
        "passed": [_cc_item(1_000 + k, -(1 + k % 90)) for k in range(134)],
        "undated_groups": groups,
    }


# --- rotation-map -------------------------------------------------------------------------------------------------

WINDOWS = ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y")


def _rm_member(g: int, m: int) -> dict[str, Any]:
    return {
        "security_id": uid("sec", "rm", g, m),
        "ticker": ticker((g * 7 + m) % 90),
        "cards": [
            {
                "thesis_id": tid(c),
                "thesis_name": thesis_name(c),
                "ticker": ticker(m),
                "links": [f"Link {c}"],
            }
            for c in range(2)
        ],
        "returns": {w: round(1.5 * (k + 1), 2) for k, w in enumerate(WINDOWS)},
        "above_50d": True,
        "rvol20": 1.2,
        "cross": "golden" if m == 0 else None,
        "cross_date": fx._day(-5) if m == 0 else None,
        "rs_leading": m % 2 == 0,
        "flag": None,
    }


def _rm_group(g: int) -> dict[str, Any]:
    return {
        "key": f"link:{g}",
        "link": f"Link {g + 1} synthetic segment",
        "thesis_id": tid(g),
        "thesis_name": thesis_name(g),
        "business_type": None,
        "supersector": None,
        "names": 7,
        "flagged": 0,
        "windows": [
            {"key": w, "median": 2.5, "n": 7, "up": 5, "above_spy": 4, "tone": "pos"}
            for w in WINDOWS
        ],
        "above_50d": 5,
        "above_50d_n": 7,
        "rvol20_median": 1.1,
        "rvol20_n": 7,
        "golden": 1,
        "death": 0,
        "rs_leaders": 3,
        "rs_n": 7,
        "members": [_rm_member(g, m) for m in range(7)],
    }


@cache
def rotation_map() -> dict[str, Any]:
    """MEASURED 692K: 66 groups of about 3.6K with 7 members each over 5 windows."""
    return {
        "asof": ASOF,
        "group_by": "link",
        "include_silent": False,
        "live_cards": CARDS,
        "cards_counted": CARDS,
        "cards_not_yet_created": 0,
        "roster_fallback_cards": 1,
        "basket_rows": 462,
        "silent_rows_left_out": 300,
        "unresolved_rows": 0,
        "companies": 440,
        "priced_companies": 436,
        "spy": {"ret_1d": 0.3, "ret_7d": 1.1, "ret_30d": 2.4, "ret_90d": 5.0, "ret_1y": 14.0},
        "groups": [_rm_group(g) for g in range(66)],
        "flagged": _flagged(4),
        "min_priced": 3,
        "cross_days": 30,
        "cache": "computed",
        "compute_ms": 6_200,
    }


# --- trigger-log --------------------------------------------------------------------------------------------------

KINDS = ("insider", "technical_breakout", "laggard", "breakdown", "dilution_risk")


def _tl_row(n: int) -> dict[str, Any]:
    c = n % CARDS
    return {
        "key": f"tl-{n}",
        "thesis_id": tid(c),
        "thesis_name": thesis_name(c),
        "security_id": uid("sec", "tl", n),
        "ticker": ticker(n % 90),
        "role": "entry_trigger" if n % 3 else "risk_signal",
        "kind": KINDS[n % len(KINDS)],
        "grade": "core" if n % 2 else None,
        "turns": "conviction" if n % 5 == 0 else None,
        "label": text(95, n),
        "label_changes": n % 3,
        "event_date": fx._day(-(n % 90)),
        "recorded_on": fx._day(-(n % 90)),
        "week": fx._day(-(n % 90) - (n % 90) % 7)[:10],
        "recorded_days_after": 0,
        "reconstructed": False,
        "from_state": "warming",
        "to_state": "armed",
        "new_card": False,
        "live": True,
        "newest": n % 4 == 0,
        "sessions_since": n % 40,
        "since": {
            "status": "priced",
            "reason": None,
            "entry_date": fx._day(-(n % 90)),
            "end_date": ASOF,
            "ret": 3.1,
            "bench_ret": 1.0,
        },
        "sources": [
            {
                "source": "form4",
                "ref": f"0000000000-26-{n:06d}",
                "url": f"https://www.sec.gov/tl/{n}",
                "bar_date": None,
                "detail": [text(120, n), text(100, n + 1)],
            }
        ],
    }


@cache
def trigger_log() -> dict[str, Any]:
    """MEASURED 5.58 MB: 5,002 rows of about 1.1K. Built lazily, once per process."""
    return {
        "asof": ASOF,
        "window_days": 90,
        "window_start": fx._day(-90),
        "role": "entry",
        "clock": "record",
        "rows": [_tl_row(n) for n in range(5_002)],
        "counts": {
            "cards_counted": CARDS,
            "cards_with_nights": 64,
            "cards_quiet": 2,
            "cards_no_call": 2,
            "live_cards": CARDS,
            "not_yet_created": 0,
            "roster_fallback": 1,
            "nights_read": 5_800,
            "nights_reconstructed": 40,
            "nights_unreadable": 0,
            "entry_rows": 3_400,
            "risk_rows": 1_602,
            "other_role_rows": 16_000,
            "carried_live": 70,
            "names_unpriced": 3,
            "names_flagged": 2,
            "rows_no_ticker": 0,
        },
        "sessions": {"source": "benchmarks", "count": 63, "first": fx._day(-90), "last": ASOF},
        "unpriced": [
            {"security_id": uid("sec", "up", i), "ticker": ticker(i), "reason": text(60, i)}
            for i in range(3)
        ],
        "flagged": [
            {"security_id": uid("sec", "fl", i), "ticker": ticker(i), "reason": text(60, i)}
            for i in range(2)
        ],
        "cache": "hit",
        "compute_ms": 4,
    }


PAYLOADS = {
    "daily-digest": daily_digest,
    "what-changed": what_changed,
    "hot-board": hot_board,
    "entry-windows": entry_windows,
    "forming-quietly": forming_quietly,
    "catalyst-calendar": catalyst_calendar,
    "rotation-map": rotation_map,
    "trigger-log": trigger_log,
}
