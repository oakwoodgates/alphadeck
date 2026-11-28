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
    argument values). ``scope`` is the scope a caller needs to list and get it (``"read"``, or ``"steward"``; a
    plain string, like the tool registry's). Imports nothing beyond this module's own (the purity test pins them),
    so no dataclass.
    """

    __slots__ = ("name", "description", "arguments", "check", "derived", "scope")

    def __init__(
        self,
        name: str,
        description: str,
        arguments: tuple,
        check: object = None,
        derived: dict | None = None,
        scope: str = "read",
    ) -> None:
        self.name = name
        self.description = description
        self.arguments = arguments
        self.check = check
        self.derived = derived or {}
        self.scope = scope

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
        "card-data-check",
        "What is still the operator's to set on a card (purity, sign-off, context roles) and which shared values "
        "are awaiting the steward or missing; the model drafts and the ambiguous picks.",
        (("thesis", True, None), ("asof", False, None)),
    ),
    PromptSpec(
        "discovery-review",
        "What a card's newest saved discovery run found: the report, the capped and empty terms, the counts by "
        "status and the names waiting for a pick.",
        (("thesis", True, None),),
    ),
    PromptSpec(
        "splits-review",
        "The split list for the steward to settle: each needs-review split's two sources side by side, the "
        "located passage, the matching confirm option; then the announced splits, the unlisted jumps and the "
        "split filings the list could not place, and every listed split a filing calls off. Steward scope.",
        (("t", False, None),),
        derived={"t_clause": lambda v: f", starting with {v['t']}" if v["t"] else ""},
        scope="steward",
    ),
    PromptSpec(
        "admin-triage",
        "Everything waiting on the Admin page, in the order to work it: the held insider rows, the shared facts "
        "queue, the price-feed repairs, the onboarding queue and the splits count, each with the review to run. "
        "The Form 4/A panel has no tool and is named as read by hand. Steward scope.",
        (),
        scope="steward",
    ),
    PromptSpec(
        "facts-review",
        "The share counts and cash-and-burn values waiting on Admin > Shared facts: each candidate's own figure "
        "beside its located filing passage. The model suggests Approve, Dismiss or the company page, never a "
        "figure. Steward scope.",
        (("t", False, None),),
        derived={"t_clause": lambda v: f", starting with {v['t']}" if v["t"] else ""},
        scope="steward",
    ),
    PromptSpec(
        "insider-review",
        "The held Form 4 rows owed on Admin > Held insider rows: the filing's dates as written, the suggested "
        "date and whether it agrees, and why the rule did not place the row. The Form 4/A panel has no tool. "
        "Steward scope.",
        (("t", False, None),),
        derived={"t_clause": lambda v: f", starting with {v['t']}" if v["t"] else ""},
        scope="steward",
    ),
    PromptSpec(
        "price-feed-review",
        "The price-feed repairs owed on Admin > Price feed repairs: renamed names with the splice probe's "
        "verdict, names never priced and members re-filed under a new CIK, each with its recommendation. "
        "Steward scope.",
        (),
        scope="steward",
    ),
)
PROMPT_NAMES = tuple(p.name for p in PROMPTS)
# the scope each prompt needs: a prompt the caller does not hold is neither listed nor gettable (``AlphaDeckServer``)
PROMPT_SCOPES = {p.name: p.scope for p in PROMPTS}


def bind_values(spec: PromptSpec, given: dict[str, str]) -> dict:
    """The values ``spec`` renders with, from a ``get_recipe`` call's string arguments: each argument the caller named,
    the spec's default for each it left out. An integer argument (a default of ``int``) is read as the ``prompts/get``
    path reads it, so both paths fill the same text. Raises ``ValueError(rule)`` for an argument the recipe does not
    take, a required one left out, or an integer argument that is not a whole number.
    """
    takes = [name for name, _, _ in spec.arguments]
    listed = ", ".join(takes) if takes else "no arguments"
    foreign = sorted(set(given) - set(takes))
    if foreign:
        raise ValueError(f"{spec.name} does not take {', '.join(foreign)}; it takes {listed}.")
    missing = [name for name, required, _ in spec.arguments if required and name not in given]
    if missing:
        raise ValueError(f"{spec.name} needs {', '.join(missing)}; it takes {listed}.")
    values: dict = {}
    for name, _, default in spec.arguments:
        if name not in given:
            values[name] = default
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                values[name] = int(given[name])
            except ValueError:
                raise ValueError(f"{name} must be a whole number.") from None
        else:
            values[name] = given[name]
    return values
