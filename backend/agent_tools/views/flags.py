"""``get_data_flags``: the data flags on a card's names (``GET /theses/{id}/data-flags``) or on every card's names
(``GET /data-flags``, the cross-card read: slow, so the reply states ``compute_s``).

Per member: ``ticker``, ``marked``, each flag's kind / label / level / reason / what clears it / since when / the
fields it affects / ``call_affected``,
the counts of notes and splits, and (cross-card) the cards holding it. A member with no flag, note or split and not
marked is counted in ``members_clean`` and not listed. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ..budget import TrimRule, fit

BUDGET = 8_000
CARD_NAME_LIMIT = 40


def _has_anything(m: dict[str, Any]) -> bool:
    return bool(m.get("marked") or m.get("flags") or m.get("notes") or m.get("splits"))


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
    listed = [m for m in members if _has_anything(m)]
    reply: dict[str, Any] = {
        "asof": asof,
        "source": "live",
        "thesis": thesis,
        "scope": "all_cards" if cross_card else "thesis",
        "members_total": len(members),
        "members_clean": len(members) - len(listed),
        "members_flagged": sum(1 for m in members if m.get("flags")),
        "call_affected": sum(
            1 for m in members if any(f.get("call_affected") for f in m.get("flags") or [])
        ),
        "members": [_member(m, cross_card=cross_card) for m in listed[cursor:]],
    }
    if compute_s is not None:
        reply["compute_s"] = compute_s
    return fit(reply, BUDGET, [TrimRule(("members",), min_keep=1, start=cursor)])
