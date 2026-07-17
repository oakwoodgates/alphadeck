from __future__ import annotations

from signals.display.base import DisplayMember

_REGISTERED: dict[str, DisplayMember] = {}


def register_display_member(member: DisplayMember) -> DisplayMember:
    """Register one display member, rejecting duplicate names instead of silently replacing one."""
    if member.name in _REGISTERED:
        raise ValueError(f"display member already registered: {member.name}")
    _REGISTERED[member.name] = member
    return member


def registered_display_members() -> tuple[DisplayMember, ...]:
    """The display members in deterministic registration order (= the panel's render order)."""
    return tuple(_REGISTERED.values())
