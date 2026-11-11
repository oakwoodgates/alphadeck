"""The steward read tools' declared contracts: ``get_splits`` (Admin > Splits) and ``get_admin_status`` (the Admin
status). Each view module names its sections and what each one needs; this module turns them into the ``Contract`` the
shared check (``tool_args.check``) and the generic contract tests read. Pure: no I/O.
"""

from __future__ import annotations

from ...tool_args import Contract, contract
from . import splits, status

__all__ = ["CONTRACTS"]

CONTRACTS: dict[str, Contract] = {
    c.name: c
    for c in (
        contract("get_splits", False, splits.SECTIONS, ("t",)),
        contract("get_admin_status", False, status.SECTIONS),
    )
}
