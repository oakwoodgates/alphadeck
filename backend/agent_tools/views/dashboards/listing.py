"""``list_dashboards``: the registry as a reply (no I/O, no API call)."""

from __future__ import annotations

from typing import Any

from ...budget import fit
from . import REGISTRY, Dashboard, Setting

BUDGET = 3_500

COMMON = {
    "asof": "YYYY-MM-DD; dated dashboards only (omitted = current night)",
    "section": "a key of the dashboard's sections (omitted = the default reply); thesis or group where it says so",
    "cursor": "continue the list the reply cut (next_cursor)",
    "thesis": "a thesis id or exact name, where the dashboard narrows by card",
    "group": "a group or bucket key from the default reply, where it narrows by group",
    "change": "what-changed only: ONE change value (a key of by_change) to narrow the items to",
}


def _setting(s: Setting) -> dict[str, Any]:
    allowed = list(s.allowed) if isinstance(s.allowed, tuple) else s.allowed
    return {
        "name": s.name,
        "type": s.type,
        "allowed": allowed,
        "default": s.default,
        "applies": s.applies,
    }


def _entry(d: Dashboard) -> dict[str, Any]:
    out: dict[str, Any] = {"slug": d.slug, "purpose": d.purpose, "dated": d.dated}
    if d.settings:
        out["settings"] = [_setting(s) for s in d.settings]
    out["sections"] = {s: d.needs_of(s) for s in d.sections}  # null = needs no thesis or group
    if d.narrows:
        out["narrows"] = list(d.narrows)
    return out


def view_list_dashboards() -> dict[str, Any]:
    reply: dict[str, Any] = {
        "asof": None,
        "source": "live",
        "dashboards": [_entry(d) for d in REGISTRY.values()],
        "parameters": COMMON,
    }
    return fit(reply, BUDGET, [])
