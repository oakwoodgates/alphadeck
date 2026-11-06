"""``what-changed``: every change between two recorded nights (``GET /dashboards/what-changed``, UNDATED).

The API anchors it on the newest recorded night (``latest``) against a baseline night (``since``), so the reply states
``asof: null`` and carries those two dates as ``anchor``. The default reply keeps the anchor and the counts, then the
leading items in the API's order (the ONE paged list; the rest continues by cursor); ``section`` filters the same
items to one of the API's six item sections, ``thesis`` to one card. ``by_section`` and ``by_change`` only count
items. Every value is copied from the API's JSON.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick, put_cut

SLUG = "what-changed"
SECTIONS = ("column_moves", "keys", "names", "triggers", "clocks", "new_cards")
ANCHOR_KEYS = ("since", "since_date", "baseline", "latest", "first_recorded")
ITEM_KEYS = (
    "section",
    "change",
    "thesis_name",
    "ticker",
    "from_state",
    "to_state",
    "signal_kind",
    "on",
    "previous_on",
    "days",
    "reconstructed",
)
UNRECORDED_SHOWN = 10


def _item(i: dict[str, Any]) -> dict[str, Any]:
    out = pick(i, ITEM_KEYS)
    put_cut(out, "label", i.get("label"), 100)
    return out


def _count(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i.get(key)] = out.get(i.get(key), 0) + 1
    return out


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    items = payload.get("items") or []
    unrecorded = payload.get("unrecorded") or []
    cov = coverage(
        payload,
        {
            "unrecorded": {
                "count": len(unrecorded),
                "cards": [u.get("thesis_name") for u in unrecorded[:UNRECORDED_SHOWN]],
            }
        },
    )
    reply = header(SLUG, payload, req, sections=SECTIONS, cov=cov, narrowed=narrowed(req))
    reply["anchor"] = pick(payload, ANCHOR_KEYS)
    if req.section is not None and req.section not in SECTIONS:
        raise ViewError(f"Unknown section {req.section!r}.")
    of_card = items
    if req.thesis is not None:
        of_card = [i for i in items if i.get("thesis_id") == req.thesis.get("id")]
    shown = of_card
    if req.section is not None:
        shown = [i for i in of_card if i.get("section") == req.section]
    reply["by_section"] = _count(of_card, "section")
    reply["by_change"] = _count(
        shown, "change"
    )  # before the change narrowing: the keys `change` takes
    if req.change is not None:
        shown = [i for i in shown if i.get("change") == req.change]
    reply["items_total"] = len(shown)
    if not shown:
        where = req.section or "any section"
        what = f"{req.change!r} in {where}" if req.change else where
        reply["note"] = f"nothing changed in {what} since {reply['anchor'].get('since')}" + (
            f"; the changes read are: {', '.join(reply['by_change'])}" if req.change else ""
        )
    reply["items"] = [_item(i) for i in shown[req.cursor :]]
    return fit(reply, BUDGET, [TrimRule(("items",), min_keep=1, start=req.cursor, hint="cursor")])
