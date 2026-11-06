"""The dashboard registry: the eight MONITOR dashboards ``read_dashboard`` can read, as plain data.

Each entry names the dashboard, what it answers, whether it is dated, the settings the API's route takes (the allowed
values mirror the route's own enums, read from the router), the sections its reply can be narrowed to and which
narrowing parameters (``thesis``, ``group``) it takes. ``view_module`` is the module that shapes the API's JSON
(``view(payload, req)``, pure). Nothing here does I/O; ``list_dashboards`` serves this table as it stands.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

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
class Setting:
    """A query parameter the dashboard's route takes: passed through to the API under the same name."""

    name: str
    type: str
    allowed: tuple[Any, ...] | str  # the route's enum, or a pattern description
    default: Any
    applies: str


@dataclass(frozen=True)
class Dashboard:
    slug: str
    purpose: str
    dated: bool
    sections: tuple[str, ...]
    narrows: tuple[str, ...] = ()
    settings: tuple[Setting, ...] = ()
    view_module: str = ""
    # a section that reads one card or one group says so: section -> "thesis" | "group" (the others need nothing)
    needs: tuple[tuple[str, str], ...] = ()

    def needs_of(self, section: str) -> str | None:
        return dict(self.needs).get(section)

    def setting_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.settings)

    def view(self):
        """The dashboard's pure view function (imported on first use)."""
        return import_module(f"{__name__}.{self.view_module or self.slug.replace('-', '_')}").view


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
)

REGISTRY: dict[str, Dashboard] = {
    d.slug: d
    for d in (
        Dashboard(
            "daily-digest",
            "What the last pass changed, what is due, insider buys, the tape on armed names, what is forming.",
            True,
            ("last_night", "tape", "insider", "due"),
            ("thesis",),
        ),
        Dashboard(
            "what-changed",
            "Every change between two recorded nights: states, keys, names, triggers, clocks, new cards.",
            False,
            ("column_moves", "keys", "names", "triggers", "clocks", "new_cards"),
            ("thesis", "change"),
            (SINCE,),
        ),
        Dashboard(
            "hot-board",
            "The armed names (or all candidates) by heat: triggers, clocks, return since arm, tape, cap.",
            True,
            ("row",),
            ("thesis",),
            (ROWS,),
            needs=(("row", "thesis"),),
        ),
        Dashboard(
            "entry-windows",
            "Each armed card's entry window and hold clock, with its members and catalysts.",
            True,
            ("members", "catalysts"),
            ("thesis",),
        ),
        Dashboard(
            "forming-quietly",
            "Warming and incubating cards: keys, names with triggers, missing conditions, risks, quiet tape.",
            True,
            ("names", "risks", "missing", "watch", "quiet", "adoption"),
            ("thesis",),
            needs=tuple(
                (s, "thesis") for s in ("names", "risks", "missing", "watch", "quiet", "adoption")
            ),
        ),
        Dashboard(
            "catalyst-calendar",
            "Catalysts, entry windows and exit-by dates by bucket across all cards, plus passed and undated.",
            False,
            ("bucket", "passed", "undated"),
            ("thesis", "group"),
            needs=(("bucket", "group"),),
        ),
        Dashboard(
            "rotation-map",
            "Where price is moving by link, business type, sector or card: returns, SMA, RVOL, crosses.",
            True,
            ("members", "flagged"),
            ("group",),
            (GROUP_BY, INCLUDE_SILENT),
            needs=(("members", "group"),),
        ),
        Dashboard(
            "trigger-log",
            "Every trigger the recorded calls show in the window, newest night first.",
            True,
            ("row",),
            ("thesis",),
            (DAYS, ROLE),
            needs=(("row", "thesis"),),
        ),
    )
}
