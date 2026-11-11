"""Shared pieces of the steward views: one paging rule over several named lists.

Pure. Every value is copied from the API's JSON; a view only counts, cuts strings (with their full length beside
them) and pages. It never sorts, scores or derives: the API's row order IS the Admin panel's.
"""

from __future__ import annotations

from typing import Any

from ...budget import TrimRule, fit
from ..research.common import BUDGET, ResearchRequest, ViewError, header

__all__ = ["BUDGET", "ResearchRequest", "ViewError", "head", "header", "paged"]


def head(out: dict[str, Any], key: str, rows: list[Any] | None, shown: int) -> None:
    """``out[key]`` = the first ``shown`` rows; ``<key>_total`` (the full count) rides beside it."""
    rows = rows or []
    out[key] = rows[:shown]
    out[f"{key}_total"] = len(rows)


def paged(
    reply: dict[str, Any],
    lists: list[tuple[str, list[Any]]],
    cursor: int,
    section: str,
) -> dict[str, Any]:
    """``reply`` plus the named lists paged TOGETHER by one cursor (the first list's rows, then the next's), in
    the API's order, each list under its own name and its full count under ``totals``. One flat index space, so
    ``next_cursor`` is a single absolute index whichever list the cut lands in."""
    flat = [(name, row) for name, rows in lists for row in rows][cursor:]
    working = {
        **reply,
        "totals": {name: len(rows) for name, rows in lists},
        "_rows": flat,
    }

    def render(r: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in r.items() if k != "_rows"}
        for name, _ in lists:
            out[name] = []
        for name, row in r["_rows"]:
            out[name].append(row)
        return out

    done = fit(
        working, BUDGET, [TrimRule(("_rows",), min_keep=1, start=cursor, hint="cursor")], render
    )
    if done.get("next_cursor_for") == "_rows":
        done["next_cursor_for"] = section
    return done
