"""The research-stage tools' declared contracts: ``get_triage`` (TRIAGE), ``get_onboarding`` (the staged cold-ingest
queue) and, with them, ``get_discovery`` and ``get_extract`` (SURFACE). Each view module names its sections and what each
one needs; this module turns them into the ``Contract`` the shared check (``tool_args.check``) and the generic contract
tests read. Pure: no I/O.
"""

from __future__ import annotations

from ...tool_args import Contract, register
from . import onboarding, triage

__all__ = ["CONTRACTS"]


def _contract(
    name: str,
    dated: bool,
    sections: tuple[tuple[str, str | None], ...],
    narrows: tuple[str, ...] = (),
    settings: tuple = (),
) -> Contract:
    return register(
        Contract(
            name,
            dated,
            tuple(s for s, _ in sections),
            narrows,
            settings,
            tuple((s, need) for s, need in sections if need),
        )
    )


CONTRACTS: dict[str, Contract] = {
    c.name: c
    for c in (
        _contract("get_triage", True, triage.SECTIONS, ("segment", "t")),
        _contract("get_onboarding", False, onboarding.SECTIONS),
    )
}
