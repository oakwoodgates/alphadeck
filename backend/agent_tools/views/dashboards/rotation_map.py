"""``rotation-map``: where price is moving, by link, business type, sector or card
(``GET /dashboards/rotation-map``, dated, settings ``group_by`` and ``include_silent``; display only).

The default reply keeps the SPY reference, the counts (``coverage``) and the leading groups in the API's order (the ONE
paged list; the rest continues by cursor), each with its window returns, SMA, RVOL, cross and RS counts. A group's
members ride in ``section="members"`` with ``group`` = the group ``key`` from the default reply; ``section="flagged"``
lists the names whose data flags kept them out of every aggregate. Every value is copied from the API's JSON; entries
are only counted.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from .common import BUDGET, Request, ViewError, coverage, header, narrowed, pick

SLUG = "rotation-map"
SECTIONS = ("members", "flagged")

GROUP_KEYS = (
    "key",
    "names",
    "flagged",
    "above_50d",
    "above_50d_n",
    "rvol20_median",
    "rvol20_n",
    "golden",
    "death",
    "rs_leaders",
    "rs_n",
)
LABEL_KEYS = ("link", "thesis_name", "business_type", "supersector")
WINDOW_KEYS = ("median", "n", "up", "above_spy")
MEMBER_KEYS = (
    "ticker",
    "returns",
    "above_50d",
    "rvol20",
    "cross",
    "cross_date",
    "rs_leading",
    "flag",
)


def _label(g: dict[str, Any]) -> Any:
    """The group's own label: whichever of link, card, business type or supersector the API set."""
    for key in LABEL_KEYS:
        if g.get(key) is not None:
            return g[key]
    return g.get("key")


def _group(g: dict[str, Any]) -> dict[str, Any]:
    out = {"key": g.get("key"), "label": _label(g)}
    out.update(pick(g, GROUP_KEYS[1:]))
    out["returns"] = {w.get("key"): pick(w, WINDOW_KEYS) for w in g.get("windows") or []}
    return out


def _member(m: dict[str, Any]) -> dict[str, Any]:
    out = pick(m, MEMBER_KEYS)
    out["cards"] = len(m.get("cards") or [])
    return out


def view(payload: dict[str, Any], req: Request) -> dict[str, Any]:
    groups = payload.get("groups") or []
    reply = header(
        SLUG, payload, req, sections=SECTIONS, cov=coverage(payload), narrowed=narrowed(req)
    )
    keys = [g.get("key") for g in groups]
    if req.group is not None and req.group not in keys:
        raise ViewError("Unknown group key; pass a key from the default reply's groups.")
    if req.section == "members":
        if req.group is None:
            raise ViewError("section=members needs group (a key from the default reply).")
        members = next(g for g in groups if g.get("key") == req.group).get("members") or []
        reply["members_total"] = len(members)
        reply["members"] = [_member(m) for m in members[req.cursor :]]
        return fit(
            reply, BUDGET, [TrimRule(("members",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    if req.section == "flagged":
        flagged = payload.get("flagged") or []
        reply["flagged_total"] = len(flagged)
        reply["flagged"] = [pick(f, ("ticker", "reason")) for f in flagged[req.cursor :]]
        return fit(
            reply, BUDGET, [TrimRule(("flagged",), min_keep=1, start=req.cursor, hint="cursor")]
        )
    if req.section is not None:
        raise ViewError(f"Unknown section {req.section!r}.")
    if req.group is not None:
        groups = [g for g in groups if g.get("key") == req.group]
    reply["spy"] = payload.get("spy")
    reply["groups_total"] = len(groups)
    reply["groups"] = [_group(g) for g in groups[req.cursor :]]
    return fit(reply, BUDGET, [TrimRule(("groups",), min_keep=1, start=req.cursor, hint="cursor")])
