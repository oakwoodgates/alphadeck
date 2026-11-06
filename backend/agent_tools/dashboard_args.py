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
    parts += [n for n in ("thesis", "group") if n in dash.narrows]
    if dash.dated:
        parts.insert(0, "asof")
    parts.append("section")
    return f"`{dash.slug}` takes only: {', '.join(parts)}."


def check(
    dash: Dashboard,
    *,
    asof: str | None,
    section: str | None,
    thesis: str | None,
    group: str | None,
    given: dict[str, Any],
) -> dict[str, Any]:
    """Validate the arguments against the dashboard; return the effective settings (defaults filled)."""
    if asof is not None and not dash.dated:
        raise ToolFailure(
            "invalid_arguments",
            f"`{dash.slug}` is not dated, so it takes no asof. " + accepted(dash),
        )
    for name, value in given.items():
        if value is not None and name not in dash.setting_names():
            raise ToolFailure(
                "invalid_arguments", f"`{name}` is not a setting of it. " + accepted(dash)
            )
    if thesis is not None and "thesis" not in dash.narrows:
        raise ToolFailure("invalid_arguments", "`thesis` does not narrow it. " + accepted(dash))
    if group is not None and "group" not in dash.narrows:
        raise ToolFailure("invalid_arguments", "`group` does not narrow it. " + accepted(dash))
    if section is not None and section not in dash.sections:
        raise ToolFailure(
            "invalid_arguments",
            f"Unknown section {section!r}; `{dash.slug}` has: {', '.join(dash.sections)}.",
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
                "invalid_arguments", "since must be last_night, 7d or a real date as YYYY-MM-DD."
            )
    return {
        s.name: given.get(s.name) if given.get(s.name) is not None else s.default
        for s in dash.settings
    }
