"""``get_decisions``: the operator's logged decisions on a card (``GET /theses/{id}/decisions``), newest first,
each entry as the API gives it (a voided row rides along flagged ``voided: true``)."""

from __future__ import annotations

from typing import Any

from ..budget import TrimRule, fit

BUDGET = 8_000


def view_decisions(
    payload: list[dict[str, Any]], *, thesis: dict[str, Any], cursor: int = 0
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "asof": None,
        "source": "live",
        "thesis": thesis,
        "decisions_total": len(payload),
        "decisions": list(payload[cursor:]),
    }
    return fit(reply, BUDGET, [TrimRule(("decisions",), min_keep=1, start=cursor)])
