"""The steward read tools' declared contracts: ``get_splits`` (Admin > Splits), ``get_admin_status`` (the Admin
status), ``get_shared_facts`` (Admin > Shared facts), ``get_insider_holds`` (Admin > Held insider rows) and
``get_admin_onboarding`` (Admin > Onboarding). Each view module names its sections and what each one needs; this
module turns them into the ``Contract`` the shared check (``tool_args.check``) and the generic contract tests read.
Pure: no I/O.
"""

from __future__ import annotations

from ...tool_args import Contract, Setting, contract
from . import insider_holds, onboarding, shared_facts, splits, status

__all__ = ["CONTRACTS"]


def _reason_rule(value: object) -> str | None:
    """The rule text when ``reason`` is not one of the three review reasons."""
    return None if value in splits.REASONS else splits.REASON_RULE


# the needs-review rows narrowed to one reason: applied by the view, never sent to the API
REASON = Setting(
    "reason",
    "string",
    splits.REASONS,
    None,
    "narrows section=needs_review",
    _reason_rule,
)


def _pen_rule(value: object) -> str | None:
    """The rule text when ``pen`` is not one of the three pen settings."""
    return None if value in shared_facts.PENS else shared_facts.PEN_RULE


# where the pen-only companies sit in the queue: passed to the API under the same name
PEN = Setting(
    "pen",
    "string",
    shared_facts.PENS,
    "after",
    "sets where the queue lists the companies only on a Discovered pen",
    _pen_rule,
)

CONTRACTS: dict[str, Contract] = {
    c.name: c
    for c in (
        contract(
            "get_splits",
            False,
            splits.SECTIONS,
            ("t",),
            settings=(REASON,),
            needs_text={"t": "t (a ticker on the split list)"},
        ),
        contract("get_admin_status", False, status.SECTIONS),
        contract(
            "get_shared_facts",
            False,
            shared_facts.SECTIONS,
            ("t", "fact"),
            settings=(PEN,),
            needs_text={"t": "t (a company's ticker)"},
        ),
        contract(
            "get_insider_holds",
            False,
            insider_holds.SECTIONS,
            ("t",),
            needs_text={"t": "t (a ticker with held insider rows)"},
        ),
        contract("get_admin_onboarding", False, onboarding.SECTIONS),
    )
}
