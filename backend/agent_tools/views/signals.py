"""``get_display_signals``: the read-only tape context beside the call (``GET /theses/{id}/display-signals``).

Display signals are never an input to the call: they cannot fire, arm, veto or grade. The view keeps the theme
breadth and sector relative-strength headlines and, per member, each signal's headline (``{kind: {key, label}}``);
with ``tickers`` it also keeps those members' signal ``metrics``. ``series``, ``events`` and ``basis`` are always
dropped. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ..budget import TrimRule, fit

BUDGET = 8_000
MAX_TICKERS = 8


def _headline(signal: dict[str, Any] | None) -> dict[str, Any] | None:
    if not signal:
        return None
    h = signal.get("headline") or {}
    return {"kind": signal.get("kind"), "key": h.get("key"), "label": h.get("label")}


def view_display_signals(
    payload: dict[str, Any],
    *,
    asof: str,
    thesis: dict[str, Any],
    tickers: list[str] | None = None,
    cursor: int = 0,
) -> dict[str, Any]:
    wanted = [t.strip().upper() for t in tickers] if tickers else None
    members = payload.get("members") or []
    if wanted is not None:
        members = [m for m in members if (m.get("ticker") or "").upper() in wanted]
    found = {(m.get("ticker") or "").upper() for m in members}
    rows = []
    for m in members:
        signals: dict[str, Any] = {}
        for s in m.get("signals") or []:
            h = s.get("headline") or {}
            entry: dict[str, Any] = {"key": h.get("key"), "label": h.get("label")}
            if wanted is not None:
                entry["metrics"] = s.get("metrics")
            signals[s.get("kind")] = entry
        rows.append({"ticker": m.get("ticker"), "signals": signals})
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "live",
        "thesis": thesis,
        "note": "Display only: never an input to the call.",
        "breadth": _headline(payload.get("breadth")),
        "sector_rs": _headline(payload.get("sector_rs")),
        "tickers": wanted,
        "tickers_not_found": [t for t in wanted if t not in found] if wanted else [],
        "members_total": len(rows),
        "members": rows[cursor:],
    }
    return fit(reply, BUDGET, [TrimRule(("members",), min_keep=1, start=cursor)])
