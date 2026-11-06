"""The argument rules of the ``compare-rotation`` prompt. Pure: no I/O, no clock; a date is checked by FORMAT only
(the API judges whether a night exists). Raises ``ValueError`` whose message is the rule, so the caller can show it.
"""

from __future__ import annotations

import re
from datetime import date

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
        raise ValueError(f"start {start} must be before end {end}.")
    if group_by not in GROUP_BYS:
        raise ValueError(f"group_by must be one of: {', '.join(GROUP_BYS)}.")
    if group is not None and not group.strip():
        raise ValueError("group must be a group key from a default read, or omitted.")
    if group_by == "link" and group is None:
        raise ValueError(
            "group_by=link needs group (a key from a default read): a full link comparison is "
            "dozens of pages."
        )


class PromptSpec:
    """One guided read: its name (= the file under ``prompts/``), what it says in ``prompts/list``, its arguments
    as ``(name, required, default)`` in the order the client sees them, an optional pre-render ``check`` (called with
    the arguments by name; raises ``ValueError(rule)``) and ``derived`` placeholders (name -> function of the
    argument values). Imports nothing beyond this module's own (the purity test pins them), so no dataclass.
    """

    __slots__ = ("name", "description", "arguments", "check", "derived")

    def __init__(
        self,
        name: str,
        description: str,
        arguments: tuple,
        check: object = None,
        derived: dict | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.arguments = arguments
        self.check = check
        self.derived = derived or {}

    def __repr__(self) -> str:
        return f"PromptSpec({self.name!r})"


# the guided reads, one prompt file each under ``prompts/``; the server registers every entry in one loop and
# ``skill_sync`` embeds each file in the Claude Code skill (this module is pure, so both can read the table)
PROMPTS: tuple[PromptSpec, ...] = (
    PromptSpec(
        "board-review",
        "Review the Decision Queue: the first queue cards' calls, with flags only where due.",
        (("asof", False, None), ("max_items", False, 5)),
    ),
    PromptSpec(
        "explain-call",
        "Explain one card's call: its evidence, missing conditions and spine.",
        (("thesis", True, None), ("asof", False, None)),
    ),
    PromptSpec(
        "morning-brief",
        "The morning read from the dashboards: what changed, what is due, what is hot or forming.",
        (("asof", False, None),),
    ),
    PromptSpec(
        "ticker-check",
        "What one company is on: its cards, its triggers of record, its tape, the next catalysts.",
        (("t", True, None), ("asof", False, None)),
    ),
    PromptSpec(
        "compare-rotation",
        "The Rotation Map at two dates, matched by group key; start before end; link needs a group.",
        (
            ("start", True, None),
            ("end", True, None),
            ("group_by", False, DEFAULT_GROUP_BY),
            ("group", False, None),
        ),
        check=check_compare,
        derived={"group_clause": lambda v: f", group={v['group']}" if v["group"] else ""},
    ),
    PromptSpec(
        "ratify-queue",
        "What is still the operator's to ratify on a card: the unconfirmed estimates, the names not signed off, "
        "the model drafts and the ambiguous picks.",
        (("thesis", True, None), ("asof", False, None)),
    ),
    PromptSpec(
        "discovery-review",
        "What a card's newest saved discovery run found: the report, the capped and empty terms, the counts by "
        "status and the names waiting for a pick.",
        (("thesis", True, None),),
    ),
)
PROMPT_NAMES = tuple(p.name for p in PROMPTS)
