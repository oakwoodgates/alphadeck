"""The research-stage tools' declared contracts: ``get_triage`` (TRIAGE), ``get_discovery`` and ``get_extract`` (SURFACE)
and ``get_onboarding`` (the staged cold-ingest queue). Each view module names its sections and what each
one needs; this module turns them into the ``Contract`` the shared check (``tool_args.check``) and the generic contract
tests read. Pure: no I/O.
"""

from __future__ import annotations

from ...tool_args import Contract, Setting
from ...tool_args import contract as _contract
from . import discovery, extract, onboarding, triage

__all__ = ["CONTRACTS"]


def _status_rule(value: object) -> str | None:
    """The rule text when ``status`` is not one of the four placement statuses."""
    if value in discovery.STATUSES:
        return None
    return f"status must be one of: {', '.join(discovery.STATUSES)}."


# the placements narrowed to one status: applied by the view, never sent to the API
STATUS = Setting(
    "status",
    "string",
    discovery.STATUSES,
    None,
    "narrows the default reply's placements",
    _status_rule,
)

CONTRACTS: dict[str, Contract] = {
    c.name: c
    for c in (
        _contract("get_triage", True, triage.SECTIONS, ("segment", "t")),
        _contract(
            "get_discovery",
            False,
            discovery.SECTIONS,
            ("run", "segment", "t"),
            (STATUS,),
        ),
        _contract("get_extract", False, extract.SECTIONS, ("thesis", "fact")),
        _contract("get_onboarding", False, onboarding.SECTIONS),
    )
}
