"""``read_dashboard`` argument checks: a setting or narrowing the dashboard does not take is ``invalid_arguments``
naming what it does take. Runs BEFORE any API request, so the API's own 422 is only a backstop. A thin caller into
``tool_args`` (the one check every tool contract shares)."""

from __future__ import annotations

from typing import Any

from . import tool_args
from .views.dashboards import Dashboard

SETTING_NAMES = ("since", "group_by", "include_silent", "rows", "days", "role")


def accepted(dash: Dashboard) -> str:
    """The sentence that names what the dashboard takes."""
    return tool_args.accepted(dash)


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
    narrows = {"thesis": thesis, "group": group, "change": change}
    return tool_args.check(dash, asof=asof, section=section, narrows=narrows, given=given)
