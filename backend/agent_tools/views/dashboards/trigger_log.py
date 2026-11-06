"""``trigger-log``: every trigger the recorded calls show in the window, newest recorded night first
(``GET /dashboards/trigger-log``, dated, settings ``days`` and ``role``; display only).

The API's response is about 5.6 MB (5,002 rows), so the view builds a row's reply shape only for the rows it can show
(the page from the cursor), never a copy of the whole list; the counts by kind and by week come from one pass. The
default reply keeps the window, the counts and the leading rows (the ONE paged list; ``thesis`` narrows it);
``section="row"`` with ``thesis`` returns that card's rows in full (label changes, sources, the return since).
Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick, put_cut

SLUG = "trigger-log"
SECTIONS = ("row",)
# rows built per page before the size rule (about 200 characters each, so about 35 fit)
SHOWN_CAP = 120

BODY_KEYS = ("sessions", "window_start", "window_days", "role")
ROW_KEYS = (
    "ticker",
    "thesis_name",
    "role",
    "kind",
    "grade",
    "turns",
    "event_date",
    "recorded_on",
    "week",
)
FULL_KEYS = (
    "label_changes",
    "recorded_days_after",
    "reconstructed",
    "from_state",
    "to_state",
    "new_card",
    "live",
    "newest",
    "sessions_since",
    "since",
    "sources",
)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, ROW_KEYS)
    put_cut(out, "label", r.get("label"), 100)
    return out


def _row_full(r: dict[str, Any]) -> dict[str, Any]:
    out = pick(r, ROW_KEYS)
    out["label"] = r.get("label")
    out.update(pick(r, FULL_KEYS))
    return out


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    rows = payload.get("rows") or []
    if req.thesis is not None:
        thesis_id = req.thesis.get("id")
        rows = [r for r in rows if r.get("thesis_id") == thesis_id]  # references, not copies
    reply = header(
        SLUG, payload, req, sections=SECTIONS, cov=coverage(payload), narrowed=narrowed(req)
    )
    if req.section == "row":
        if req.thesis is None:
            raise ViewError("section=row needs thesis (a card's rows in full).")
        reply["rows_total"] = len(rows)
        reply["rows"] = [_row_full(r) for r in rows[req.cursor : req.cursor + SHOWN_CAP]]
        return fit(
            reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    if req.section is not None:
        raise ViewError(f"Unknown section {req.section!r}.")
    by_kind: dict[str, int] = {}
    by_week: dict[str, int] = {}
    for r in rows:  # one pass for both counts
        by_kind[r.get("kind")] = by_kind.get(r.get("kind"), 0) + 1
        by_week[r.get("week")] = by_week.get(r.get("week"), 0) + 1
    reply.update({k: payload.get(k) for k in BODY_KEYS})
    reply["rows_total"] = len(rows)
    reply["by_kind"] = by_kind
    reply["by_week"] = by_week
    reply["rows"] = [_row(r) for r in rows[req.cursor : req.cursor + SHOWN_CAP]]
    return fit(reply, BUDGET, [TrimRule(("rows",), min_keep=1, start=req.cursor, hint="cursor")])
