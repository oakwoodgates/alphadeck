"""The steward read tools' declared contracts: ``get_splits`` (Admin > Splits) and ``get_admin_status`` (the Admin
status). Each view module names its sections and what each one needs; this module turns them into the ``Contract`` the
shared check (``tool_args.check``) and the generic contract tests read. Pure: no I/O.
"""

from __future__ import annotations

from ...tool_args import Contract, Setting, contract
from . import splits, status

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
    )
}
