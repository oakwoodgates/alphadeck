"""``read_dashboard`` argument checks: a setting or narrowing the dashboard does not take is ``invalid_arguments``
naming what it does take. Runs BEFORE any API request, so the API's own 422 is only a backstop."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .errors import ToolFailure
from .views.dashboards import Dashboard

SETTING_NAMES = ("since", "group_by", "include_silent", "rows", "days", "role")
_SINCE = re.compile(r"^(last_night|7d|\d{4}-\d{2}-\d{2})$")


def accepted(dash: Dashboard) -> str:
    """The sentence that names what the dashboard takes."""
    parts = list(dash.setting_names())
    parts += [n for n in ("thesis", "group", "change") if n in dash.narrows]
    if dash.dated:
        parts.insert(0, "asof")
    parts += ["section", "cursor"]
    return "it takes " + ", ".join(parts) + "."


def check(
    dash: Dashboard,
    *,
    asof: str | None,
    section: str | None,
    thesis: str | None,
    group: str | None,
    given: dict[str, Any],
    change: str | None = None,
) -> dict[str, Any]:
    """Validate the arguments against the dashboard; return the effective settings (defaults filled)."""
    if asof is not None and not dash.dated:
        raise ToolFailure(
            "invalid_arguments",
            f"`{dash.slug}` is not dated, so it takes no asof; " + accepted(dash),
        )
    for name, value in given.items():
        if value is not None and name not in dash.setting_names():
            raise ToolFailure(
                "invalid_arguments", f"`{dash.slug}` does not take {name}; " + accepted(dash)
            )
    if thesis is not None and "thesis" not in dash.narrows:
        raise ToolFailure(
            "invalid_arguments", f"`{dash.slug}` does not take thesis; " + accepted(dash)
        )
    if group is not None and "group" not in dash.narrows:
        raise ToolFailure(
            "invalid_arguments", f"`{dash.slug}` does not take group; " + accepted(dash)
        )
    if change is not None and "change" not in dash.narrows:
        raise ToolFailure(
            "invalid_arguments", f"`{dash.slug}` does not take change; " + accepted(dash)
        )
    if section is not None and section not in dash.sections:
        raise ToolFailure(
            "invalid_arguments",
            f"Unknown section {section!r}; `{dash.slug}` has: {', '.join(dash.sections)}.",
        )
    need = dash.needs_of(section) if section is not None else None
    if need == "thesis" and thesis is None:
        raise ToolFailure(
            "invalid_arguments",
            f"section={section} of `{dash.slug}` needs thesis (a card's id or name).",
        )
    if need == "group" and group is None:
        raise ToolFailure(
            "invalid_arguments",
            f"section={section} of `{dash.slug}` needs group (a key from the default reply).",
        )
    since = given.get("since")
    if since is not None:
        ok = bool(_SINCE.match(since))
        if ok and len(since) == 10:
            try:
                date.fromisoformat(since)
            except ValueError:
                ok = False
        if not ok:
            raise ToolFailure(
                "invalid_arguments",
                "since takes last_night, 7d or a real date as YYYY-MM-DD.",
            )
    return {
        s.name: given.get(s.name) if given.get(s.name) is not None else s.default
        for s in dash.settings
    }
