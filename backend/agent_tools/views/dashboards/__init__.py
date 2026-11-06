"""The dashboard registry: the eight MONITOR dashboards ``read_dashboard`` can read, as plain data.

Each entry names the dashboard, what it answers, whether it is dated, the settings the API's route takes (the allowed
values mirror the route's own enums, read from the router), the sections its reply can be narrowed to and which
narrowing parameters (``thesis``, ``group``) it takes. ``view_module`` is the module that shapes the API's JSON
(``view(payload, req)``, pure). Nothing here does I/O; ``list_dashboards`` serves this table as it stands.

A ``Dashboard`` is a ``tool_args.Contract`` (its ``name`` is the slug) with a purpose and a view on top; the
``Setting`` type lives in ``tool_args`` and is re-exported here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from typing import Any

from ...tool_args import Contract, Setting, register

__all__ = ["Dashboard", "REGISTRY", "SLUGS", "Setting"]

SLUGS = (
    "daily-digest",
    "what-changed",
    "hot-board",
    "entry-windows",
    "forming-quietly",
    "catalyst-calendar",
    "rotation-map",
    "trigger-log",
)


@dataclass(frozen=True)
class Dashboard(Contract):
    purpose: str = ""
    view_module: str = ""

    @property
    def slug(self) -> str:
        return self.name

    def view(self):
        """The dashboard's pure view function (imported on first use)."""
        return import_module(f"{__name__}.{self.view_module or self.slug.replace('-', '_')}").view


_DATE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SINCE = re.compile(r"^(last_night|7d|\d{4}-\d{2}-\d{2})$")


def _since_rule(value: Any) -> str | None:
    """The rule text when ``since`` is not last_night, 7d or a real YYYY-MM-DD date."""
    ok = bool(_SINCE.match(value))
    if ok and _DATE_SHAPE.match(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            ok = False
    return None if ok else "since takes last_night, 7d or a real date as YYYY-MM-DD."


ROWS = Setting("rows", "string", ("armed", "all"), "armed", "default reply and the row section")
DAYS = Setting("days", "integer", (30, 90, 180), 90, "the window of every reply")
ROLE = Setting("role", "string", ("entry", "risk", "both"), "entry", "which triggers load")
GROUP_BY = Setting(
    "group_by", "string", ("link", "business_type", "sector", "card"), "link", "the rows grouped"
)
INCLUDE_SILENT = Setting(
    "include_silent", "boolean", (True, False), False, "adds Context and Bench memberships"
)
SINCE = Setting(
    "since",
    "string",
    "last_night | 7d | YYYY-MM-DD",
    "last_night",
    "the baseline night items are measured from",
    _since_rule,
)

REGISTRY: dict[str, Dashboard] = {
    d.slug: register(d)
    for d in (
        Dashboard(
            "daily-digest",
            True,
            ("last_night", "tape", "insider", "due"),
            ("thesis",),
            purpose="What the last pass changed, what is due, insider buys, the tape on armed names, what is forming.",
        ),
        Dashboard(
            "what-changed",
            False,
            ("column_moves", "keys", "names", "triggers", "clocks", "new_cards"),
            ("thesis", "change"),
            (SINCE,),
            purpose="Every change between two recorded nights: states, keys, names, triggers, clocks, new cards.",
        ),
        Dashboard(
            "hot-board",
            True,
            ("row",),
            ("thesis",),
            (ROWS,),
            (("row", "thesis"),),
            purpose="The armed names (or all candidates) by heat: triggers, clocks, return since arm, tape, cap.",
        ),
        Dashboard(
            "entry-windows",
            True,
            ("members", "catalysts"),
            ("thesis",),
            purpose="Each armed card's entry window and hold clock, with its members and catalysts.",
        ),
        Dashboard(
            "forming-quietly",
            True,
            ("names", "risks", "missing", "watch", "quiet", "adoption"),
            ("thesis",),
            needs=tuple(
                (s, "thesis") for s in ("names", "risks", "missing", "watch", "quiet", "adoption")
            ),
            purpose="Warming and incubating cards: keys, names with triggers, missing conditions, risks, quiet tape.",
        ),
        Dashboard(
            "catalyst-calendar",
            False,
            ("bucket", "passed", "undated"),
            ("thesis", "group"),
            needs=(("bucket", "group"),),
            purpose="Catalysts, entry windows and exit-by dates by bucket across all cards, plus passed and undated.",
        ),
        Dashboard(
            "rotation-map",
            True,
            ("members", "flagged"),
            ("group",),
            (GROUP_BY, INCLUDE_SILENT),
            needs=(("members", "group"),),
            purpose="Where price is moving by link, business type, sector or card: returns, SMA, RVOL, crosses.",
        ),
        Dashboard(
            "trigger-log",
            True,
            ("row",),
            ("thesis",),
            (DAYS, ROLE),
            (("row", "thesis"),),
            purpose="Every trigger the recorded calls show in the window, newest night first.",
        ),
    )
}
