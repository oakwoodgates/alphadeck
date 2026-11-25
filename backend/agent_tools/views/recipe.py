"""``get_recipe``: the guided reads as text (no I/O, no API call, no clock).

A recipe is a prompt file the package ships (``prompts/<name>.md``), rendered by ``server.render_prompt``, the same
function ``prompts/get`` uses. This view only shapes the reply: the index of the recipes the caller holds (the server
passes only those, so a steward recipe is never named to a plain caller), or one recipe's text.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..budget import TrimRule, fit
from ..prompt_args import PromptSpec

# the largest reply is a 2,000-character recipe plus at most four 200-character arguments written into it
# (``test_recipe.py`` pins the largest); the index of twelve recipes is about 3,500
BUDGET = 8_000


def _arguments(spec: PromptSpec) -> list[dict[str, Any]]:
    return [
        {"name": name, "required": required, "default": default}
        for name, required, default in spec.arguments
    ]


def view_index(specs: Sequence[PromptSpec], *, cursor: int = 0) -> dict[str, Any]:
    """The recipes in ``specs`` (the caller's, in table order), from ``cursor``, cut by the size rule."""
    entries = [
        {"name": s.name, "description": s.description, "arguments": _arguments(s)} for s in specs
    ]
    reply: dict[str, Any] = {"asof": None, "source": "live", "recipes": entries[cursor:]}
    return fit(reply, BUDGET, [TrimRule(("recipes",), min_keep=1, start=cursor, hint="cursor")])


def view_recipe(spec: PromptSpec, text: str) -> dict[str, Any]:
    """One recipe: its name, description, arguments and the rendered ``text`` (what ``prompts/get`` returns)."""
    reply: dict[str, Any] = {
        "asof": None,
        "source": "live",
        "name": spec.name,
        "description": spec.description,
        "arguments": _arguments(spec),
        "text": text,
    }
    return fit(reply, BUDGET, [])
