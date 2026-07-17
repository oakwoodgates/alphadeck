"""Insider net flow (trailing 90d, open-market only) — the ambient context beside the trigger.

The insider *detector* fires on cluster patterns; this member just states the tape of ingested
Form 4s: open-market buys vs sells (codes P/S only — awards, tax, and option codes are noise here),
counts, distinct buyers, dollar totals, and the dates of the last buy/sell. Zero rows in the window
is honestly zero *ingested* filings, never a proof of no filings — the basis says so.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "insider_flow_90d"
LABEL = "Insider flow (90d, open-market)"
WINDOW_DAYS = 90  # trailing window: valid_from in (asof-90, asof] — 90 days ending at asof

BUY, SELL = "P", "S"


def _usd_sum(rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Sum the priced rows; the count of rows WITHOUT a $ value rides back for the honest note."""
    total = sum(float(r["usd"]) for r in rows if r.get("usd") is not None)
    unpriced = sum(1 for r in rows if r.get("usd") is None)
    return total, unpriced


def _count(key: str, label: str, value: int) -> DisplayMetric:
    return DisplayMetric(key=key, label=label, value=float(value), unit="count")


def _usd(key: str, label: str, value: float, unpriced: int) -> DisplayMetric:
    note = f"{unpriced} txns without $ value" if unpriced else None
    return DisplayMetric(key=key, label=label, value=round(value, 2), unit="usd", note=note)


def compute(rows: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure trailing-window flow over ingested Form 4 rows (any order; filtered here)."""
    if not rows:
        return None  # nothing ingested for this name — nothing to say, honestly absent
    start = asof - timedelta(days=WINDOW_DAYS)
    windowed = [r for r in rows if start < r["valid_from"] <= asof]
    buys = [r for r in windowed if r.get("txn_code") == BUY]
    sells = [r for r in windowed if r.get("txn_code") == SELL]
    buy_usd, buys_unpriced = _usd_sum(buys)
    sell_usd, sells_unpriced = _usd_sum(sells)

    metrics = [
        _count("buy_count", "buys", len(buys)),
        _count("sell_count", "sells", len(sells)),
        _count("distinct_buyers", "buyers", len({r.get("insider_name") for r in buys})),
        _usd("buy_usd", "buy $", buy_usd, buys_unpriced),
        _usd("sell_usd", "sell $", sell_usd, sells_unpriced),
        _usd("net_usd", "net $", buy_usd - sell_usd, buys_unpriced + sells_unpriced),
    ]
    events: list[DisplayEvent] = []
    if buys:
        events.append(
            DisplayEvent(
                key="last_buy",
                label="last open-market buy",
                date=max(r["valid_from"] for r in buys),
                direction="up",
            )
        )
    if sells:
        events.append(
            DisplayEvent(
                key="last_sell",
                label="last open-market sell",
                date=max(r["valid_from"] for r in sells),
                direction="down",
            )
        )
    basis = DisplayBasis(
        source="fact_insider_txn",
        params={"window_days": WINDOW_DAYS, "codes": [BUY, SELL]},
        window_start=start + timedelta(days=1),
        window_end=asof,
        note="from ingested Form 4 only — zero is zero ingested, not proven-zero filings",
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=metrics, events=events, basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read ingested Form 4 rows via the point-in-time view; the pure ``compute`` windows them."""
    return compute(pit.insider_txns(security_id), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
