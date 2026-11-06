"""The argument rules of the ``compare-rotation`` prompt. Pure: no I/O, no clock; a date is checked by FORMAT only
(the API judges whether a night exists). Raises ``ValueError`` whose message is the rule, so the caller can show it.
"""

from __future__ import annotations

import re
from datetime import date

# the guided reads, one prompt file each under ``prompts/``; the server registers a handler per name and
# ``skill_sync`` embeds each file in the Claude Code skill (this module is pure, so both can read the list)
PROMPT_NAMES = (
    "board-review",
    "explain-call",
    "morning-brief",
    "ticker-check",
    "compare-rotation",
)
GROUP_BYS = ("business_type", "sector", "card", "link")
DEFAULT_GROUP_BY = "business_type"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day(name: str, value: str) -> date:
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValueError(f"{name} must be a date as YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be a real date as YYYY-MM-DD.") from None


def check_compare(start: str, end: str, group_by: str, group: str | None) -> None:
    """Validate the arguments of ``compare-rotation``; raise ``ValueError(rule)`` on the first broken rule."""
    first = _day("start", start)
    last = _day("end", end)
    if not first < last:
        raise ValueError("start must be before end.")
    if group_by not in GROUP_BYS:
        raise ValueError(f"group_by must be one of: {', '.join(GROUP_BYS)}.")
    if group is not None and not group.strip():
        raise ValueError("group must be a group key from a default read, or omitted.")
    if group_by == "link" and group is None:
        raise ValueError(
            "group_by=link needs group (a key from a default read): a full link comparison is "
            "dozens of pages."
        )
