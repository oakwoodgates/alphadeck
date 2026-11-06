"""``catalyst-calendar``: catalysts, entry-window ends and exit-by dates across every card, in time buckets
(``GET /dashboards/catalyst-calendar``, UNDATED; display only).

The API anchors it on today (``today``), so the reply states ``asof: null``. The default reply keeps the counts and a
small bucket index (key, label, dates, item count) and ONE flat list of the bucket items in the API's order, each item
carrying its ``bucket`` key; a cursor continues across buckets. Sections: ``bucket`` (with ``group`` = a bucket key:
all its items), ``passed`` (the passed catalysts) and ``undated`` (the cards' catalysts with no date: counts per card,
or with ``thesis`` that card's catalysts). Every value is copied from the API's JSON; entries are only counted and
strings cut (with their full length).
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick, put_cut

SLUG = "catalyst-calendar"
SECTIONS = ("bucket", "passed", "undated")

COUNT_KEYS = (
    "today",
    "catalysts",
    "dated",
    "upcoming_catalysts",
    "passed_catalysts",
    "undated",
    "deadlines",
)
BUCKET_KEYS = ("key", "label", "start", "end")
ITEM_KEYS = ("item", "on", "days", "kind", "thesis_name", "state")
GROUP_KEYS = ("thesis_name", "state", "count")
CATALYST_KEYS = ("label", "kind", "when_label")


def _item(i: dict[str, Any], bucket: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {} if bucket is None else {"bucket": bucket}
    out.update(pick(i, ITEM_KEYS))
    put_cut(out, "label", i.get("label"), 80)
    return out


def _flat(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(b.get("key"), i) for b in payload.get("buckets") or [] for i in b.get("items") or []]


def _paged(reply: dict[str, Any], path: tuple[str, ...], req: Request) -> dict[str, Any]:
    return fit(reply, BUDGET, [TrimRule(path, min_keep=1, start=req.cursor, hint="cursor")])


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    buckets = payload.get("buckets") or []
    keys = [b.get("key") for b in buckets]
    reply = header(
        SLUG,
        payload,
        req,
        sections=SECTIONS,
        cov=coverage(payload),
        narrowed=narrowed(req),
    )
    if req.group is not None and req.group not in keys:
        raise ViewError(f"Unknown bucket {req.group!r}; the buckets are: {', '.join(keys)}.")
    if req.section == "bucket":
        if req.group is None:
            raise ViewError(f"section=bucket needs group (one of: {', '.join(keys)}).")
        items = next(b for b in buckets if b.get("key") == req.group).get("items") or []
        if req.thesis is not None:
            items = [i for i in items if i.get("thesis_id") == req.thesis.get("id")]
        reply["items_total"] = len(items)
        reply["items"] = [_item(i, None) for i in items[req.cursor :]]
        return _paged(reply, ("items",), req)
    if req.section == "passed":
        items = payload.get("passed") or []
        if req.thesis is not None:
            items = [i for i in items if i.get("thesis_id") == req.thesis.get("id")]
        reply["items_total"] = len(items)
        reply["items"] = [_item(i, None) for i in items[req.cursor :]]
        return _paged(reply, ("items",), req)
    if req.section == "undated":
        groups = payload.get("undated_groups") or []
        if req.thesis is not None:
            groups = [g for g in groups if g.get("thesis_id") == req.thesis.get("id")]
            catalysts = [c for g in groups for c in g.get("catalysts") or []]
            reply["catalysts_total"] = len(catalysts)
            reply["catalysts"] = []
            for c in catalysts[req.cursor :]:
                entry = pick(c, ("kind", "when_label"))
                put_cut(entry, "label", c.get("label"), 200)
                reply["catalysts"].append(entry)
            return _paged(reply, ("catalysts",), req)
        reply["groups_total"] = len(groups)
        reply["groups"] = [pick(g, GROUP_KEYS) for g in groups[req.cursor :]]
        return _paged(reply, ("groups",), req)
    if req.section is not None:
        raise ViewError(f"Unknown section {req.section!r}.")
    flat = _flat(payload)
    if req.group is not None:
        flat = [(k, i) for k, i in flat if k == req.group]
    if req.thesis is not None:
        flat = [(k, i) for k, i in flat if i.get("thesis_id") == req.thesis.get("id")]
    reply["counts"] = {k: payload.get(k) for k in COUNT_KEYS}
    reply["buckets"] = [
        {**pick(b, BUCKET_KEYS), "item_count": len(b.get("items") or [])} for b in buckets
    ]
    reply["passed_count"] = len(payload.get("passed") or [])
    reply["undated_groups_count"] = len(payload.get("undated_groups") or [])
    reply["items_total"] = len(flat)
    reply["items"] = [_item(i, k) for k, i in flat[req.cursor :]]
    return _paged(reply, ("items",), req)
