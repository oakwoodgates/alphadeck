"""``get_data_flags``: the data flags on a card's names (``GET /theses/{id}/data-flags``) or on every card's names
(``GET /data-flags``, the cross-card read: slow, so the reply states ``compute_s``).

Per member: ``ticker``, ``marked``, each flag's kind / label / level / reason / what clears it / since when / the
fields it affects / ``call_affected``,
the counts of notes and splits, and (cross-card) the cards holding it. The API lists only the names that carry a flag, a
note or a split: ``names_total`` is its own count of the card's names, ``listed`` the rows it returned (never a
count of clean names). Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ..budget import TrimRule, fit

BUDGET = 8_000
CARD_NAME_LIMIT = 40


def _member(m: dict[str, Any], *, cross_card: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ticker": m.get("ticker"),
        "marked": m.get("marked"),
        "flags": [
            {
                "kind": f.get("kind"),
                "label": f.get("label"),
                "level": f.get("level"),
                "reason": f.get("reason"),
                "clears": f.get("clears"),
                "since": f.get("since"),
                "affects": f.get("affects"),
                "call_affected": f.get("call_affected"),
            }
            for f in m.get("flags") or []
        ],
        "notes": len(m.get("notes") or []),
        "splits": len(m.get("splits") or []),
    }
    if cross_card:
        out["cards"] = [
            {"id": t.get("id"), "name": (t.get("name") or "")[:CARD_NAME_LIMIT]}
            for t in m.get("theses") or []
        ]
    return out


def view_data_flags(
    payload: dict[str, Any],
    *,
    asof: str,
    thesis: dict[str, Any] | None,
    compute_s: float | None = None,
    cursor: int = 0,
) -> dict[str, Any]:
    cross_card = thesis is None
    members = payload.get("members") or []
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "live",
        "thesis": thesis,
        "scope": "all_cards" if cross_card else "thesis",
        "names_total": payload.get("names"),
        "listed": len(members),
        "members_flagged": sum(1 for m in members if m.get("flags")),
        "call_affected": sum(
            1 for m in members if any(f.get("call_affected") for f in m.get("flags") or [])
        ),
        "members": [_member(m, cross_card=cross_card) for m in members[cursor:]],
    }
    if compute_s is not None:
        reply["compute_s"] = compute_s
    return fit(reply, BUDGET, [TrimRule(("members",), min_keep=1, start=cursor)])
