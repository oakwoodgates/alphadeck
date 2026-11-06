"""``get_onboarding``: the staged cold-ingest queue scoped to a card's members (``GET /workbench/theses/{id}/onboarding``,
undated).

The default reply keeps the counts, the window the job yields to, the stalled / paused notes, the last run whole, the
first pending names and the latest finished ones; ``section="pending"`` and ``"recent_done"`` page the full lists in the
API's order. A name still onboarding is why a card is ``ingest_fresh: false``. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, ResearchRequest, ViewError, header, pick, put_cut

SECTIONS = (("pending", None), ("recent_done", None))

BODY_KEYS = (
    "counts",
    "pending",
    "recent_done",
    "last_run",
    "window",
    "stalled",
    "paused",
    "history_legs",
)
NAME_KEYS = (
    "security_id",
    "ticker",
    "name",
    "status",
    "legs_done",
    "attempts",
    "last_error",
    "source",
    "in_live_basket",
    "enqueued_at",
    "last_attempt_at",
    "finished_at",
)
RUN_KEYS = ("started_at", "finished_at", "status", "run_kind", "stopped", "detail", "counts")

PENDING_SHOWN = 10
RECENT_SHOWN = 5
ERROR_CUT_BRIEF = 120
ERROR_CUT_FULL = 300


def _pending_brief(n: dict[str, Any]) -> dict[str, Any]:
    out = pick(n, ("ticker", "status", "legs_done", "attempts"))
    put_cut(out, "last_error", n.get("last_error"), ERROR_CUT_BRIEF)
    out["enqueued_at"] = n.get("enqueued_at")
    out["in_live_basket"] = n.get("in_live_basket")
    return out


def _recent_brief(n: dict[str, Any]) -> dict[str, Any]:
    return pick(n, ("ticker", "finished_at", "legs_done"))


def _full(n: dict[str, Any]) -> dict[str, Any]:
    out = pick(n, tuple(k for k in NAME_KEYS if k != "last_error"))
    put_cut(out, "last_error", n.get("last_error"), ERROR_CUT_FULL)
    return out


def view(payload: dict[str, Any], req: ResearchRequest) -> dict[str, Any]:
    pending = payload.get("pending") or []
    recent = payload.get("recent_done") or []
    reply = header(req)
    if req.section is None:
        reply.update(
            {
                "counts": payload.get("counts"),
                "window": payload.get("window"),
                "stalled": payload.get("stalled"),
                "paused": payload.get("paused"),
                "history_legs": payload.get("history_legs"),
                "last_run": payload.get("last_run"),
                "pending_total": len(pending),
                "pending": [_pending_brief(n) for n in pending[:PENDING_SHOWN]],
                "recent_done_total": len(recent),
                "recent_done": [_recent_brief(n) for n in recent[:RECENT_SHOWN]],
                "sections": dict(SECTIONS),
            }
        )
        return fit(reply, BUDGET, [])
    if req.section not in ("pending", "recent_done"):
        raise ViewError(f"Unknown section {req.section!r}.")
    rows = pending if req.section == "pending" else recent
    reply["section"] = req.section
    reply[f"{req.section}_total"] = len(rows)
    reply[req.section] = [_full(n) for n in rows[req.cursor :]]
    reply["sections"] = dict(SECTIONS)
    return fit(
        reply, BUDGET, [TrimRule((req.section,), min_keep=1, start=req.cursor, hint="cursor")]
    )
