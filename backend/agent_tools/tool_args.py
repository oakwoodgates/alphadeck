"""The shared tool contract: what a tool takes, and the ONE check that turns a bad call into ``invalid_arguments``.

A tool declares a ``Contract`` (its sections, what each section needs, its narrowings and its settings); ``check``
rejects a call the contract does not allow, naming what it does take, BEFORE any API request (the API's own 422 is only
a backstop). Pure: no I/O, no clock. A new tool registers its contract with ``register`` so ``test_contract.py``
covers it with no new test code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import ToolFailure

# the order ``accepted()`` names the narrowings in (any other narrowing follows in the contract's own order)
NARROWING_ORDER = ("thesis", "group", "change", "run", "segment", "t", "fact")
_NEEDS_TEXT = {
    "thesis": "thesis (a card's id or name)",
    "group": "group (a key from the default reply)",
    "t": "t (a ticker, or a name for a placement without one)",
    "segment": "segment (a link label from the default reply)",
    "fact": "fact (revenue_mix, shares_outstanding or cash_burn)",
}


@dataclass(frozen=True)
class Setting:
    """A query parameter the tool's route takes: passed through to the API under the same name.

    ``validate`` (optional) returns the rule text when a given value is bad, else ``None``."""

    name: str
    type: str
    allowed: tuple[Any, ...] | str  # the route's enum, or a pattern description
    default: Any
    applies: str
    validate: Callable[[Any], str | None] | None = None


@dataclass(frozen=True)
class Contract:
    name: str
    dated: bool
    # ``sections`` are the names; ``needs`` pairs a section with the narrowing it cannot answer without ("thesis" |
    # "group" | "t" | "segment" | "fact"; a section absent from ``needs`` needs nothing)
    sections: tuple[str, ...]
    narrows: tuple[str, ...] = ()
    settings: tuple[Setting, ...] = ()
    needs: tuple[tuple[str, str], ...] = ()

    def needs_of(self, section: str) -> str | None:
        return dict(self.needs).get(section)

    def section_names(self) -> tuple[str, ...]:
        return self.sections

    def setting_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.settings)

    def accepted(self) -> str:
        """The sentence every rejection names: what the tool takes."""
        parts = list(self.setting_names())
        ordered = [n for n in NARROWING_ORDER if n in self.narrows]
        parts += ordered + [n for n in self.narrows if n not in NARROWING_ORDER]
        if self.dated:
            parts.insert(0, "asof")
        parts += ["section", "cursor"]
        return "it takes " + ", ".join(parts) + "."


CONTRACTS: list[Contract] = []


def register(contract: Contract) -> Contract:
    """Record a contract so the generic contract tests cover it; returns it unchanged."""
    CONTRACTS.append(contract)
    return contract


def contract(
    name: str,
    dated: bool,
    sections: tuple[tuple[str, str | None], ...],
    narrows: tuple[str, ...] = (),
    settings: tuple[Setting, ...] = (),
) -> Contract:
    """Build and register a tool's contract from its ``(section, need)`` pairs (``need`` None = the section needs
    nothing): the one definition every view package uses."""
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


def accepted(contract: Contract) -> str:
    return contract.accepted()


def check(
    contract: Contract,
    *,
    asof: str | None,
    section: str | None,
    narrows: dict[str, Any],
    given: dict[str, Any],
) -> dict[str, Any]:
    """Validate a call against the contract; return the effective settings (defaults filled)."""
    name = contract.name
    if asof is not None and not contract.dated:
        raise ToolFailure(
            "invalid_arguments",
            f"`{name}` is not dated, so it takes no asof; " + contract.accepted(),
        )
    for key, value in given.items():
        if value is not None and key not in contract.setting_names():
            raise ToolFailure(
                "invalid_arguments", f"`{name}` does not take {key}; " + contract.accepted()
            )
    for key, value in narrows.items():
        if value is not None and key not in contract.narrows:
            raise ToolFailure(
                "invalid_arguments", f"`{name}` does not take {key}; " + contract.accepted()
            )
    if section is not None and section not in contract.section_names():
        raise ToolFailure(
            "invalid_arguments",
            f"Unknown section {section!r}; `{name}` has: {', '.join(contract.section_names())}.",
        )
    need = contract.needs_of(section) if section is not None else None
    if need is not None and narrows.get(need) is None:
        raise ToolFailure(
            "invalid_arguments",
            f"section={section} of `{name}` needs {_NEEDS_TEXT.get(need, need)}.",
        )
    for s in contract.settings:
        value = given.get(s.name)
        if value is not None and s.validate is not None:
            rule = s.validate(value)
            if rule is not None:
                raise ToolFailure("invalid_arguments", rule)
    return {
        s.name: given.get(s.name) if given.get(s.name) is not None else s.default
        for s in contract.settings
    }
