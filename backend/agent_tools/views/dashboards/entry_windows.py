"""``entry-windows``: each armed (or managing) card's entry window and hold clock (``GET /dashboards/entry-windows``,
dated; display only).

The default reply keeps the counts and the leading card rows in the API's order (the ONE paged list; the rest continues
by cursor). ``section="members"`` returns, per card, its member clocks and members (paged by card; ``thesis`` narrows
to one); ``section="catalysts"`` the cards' catalysts. Every value is copied from the API's JSON; ``member_count``,
``catalyst_count`` and ``trigger_count`` only count entries.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick

SLUG = "entry-windows"
SECTIONS = ("members", "catalysts")

ROW_KEYS = (
    "thesis_name",
    "state",
    "arm_night",
    "in_effect",
    "arm_until",
    "exit_by",
    "sessions_into",
    "sessions_left",
    "hold_days",
    "entry_window_closing",
    "lapsing",
    "reconstructed",
)
SINCE_KEYS = ("names", "priced", "median", "iwm", "spy")
CLOCK_KEYS = (
    "ticker",
    "headline",
    "entry_window_closing",
    "lapsing",
    "arm_until",
    "sessions_left",
    "closed_ago",
    "last_session",
)
MEMBER_KEYS = ("ticker", "arm_night", "entry_on", "since", "above", "flag")
CATALYST_KEYS = ("label", "kind", "when_date", "past")
BODY_KEYS = ("axis_start", "axis_end")


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, ROW_KEYS)
    out["setup_strength"] = r.get("confidence")  # the wire field is confidence
    out["member_count"] = len(r.get("members") or [])
    out["since"] = pick(r.get("since") or {}, SINCE_KEYS)
    out["catalyst_count"] = len(r.get("catalysts") or [])
    return out


def _member(m: dict[str, Any]) -> dict[str, Any]:
    out = pick(m, MEMBER_KEYS)
    out["trigger_count"] = len(m.get("triggers") or [])
    return out


def _members_card(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "thesis_name": r.get("thesis_name"),
        "member_clocks": [pick(c, CLOCK_KEYS) for c in r.get("member_clocks") or []],
        "members": [_member(m) for m in r.get("members") or []],
    }


def _catalysts_card(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "thesis_name": r.get("thesis_name"),
        "catalysts": [pick(c, CATALYST_KEYS) for c in r.get("catalysts") or []],
    }


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if req.thesis is not None:
        rows = [r for r in rows if r.get("thesis_id") == req.thesis.get("id")]
    reply = header(
        SLUG, payload, req, sections=SECTIONS, cov=coverage(payload), narrowed=narrowed(req)
    )
    if req.section in ("members", "catalysts"):
        build = _members_card if req.section == "members" else _catalysts_card
        reply["cards_total"] = len(rows)
        reply["cards"] = [build(r) for r in rows[req.cursor :]]
        return fit(
            reply, BUDGET, [TrimRule(("cards",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    if req.section is not None:
        raise ViewError(f"Unknown section {req.section!r}.")
    reply.update({k: payload.get(k) for k in BODY_KEYS})
    reply["rows_total"] = len(rows)
    reply["rows"] = [_row(r) for r in rows[req.cursor :]]
    return fit(reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")])
