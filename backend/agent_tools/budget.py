"""The ONE size rule: ``fit`` shortens named lists until the compact reply fits its budget.

Every view builds its whole reply (one page of one list, from the caller's ``cursor``), then calls ``fit``. ``fit``
measures the compact encoding and, rule by rule, keeps the longest prefix of the named list that fits (never fewer
than the rule's minimum). It sets ``next_cursor`` to the first dropped index of the FIRST list it cut (absolute in
the full list: the rule's ``start`` plus what it kept) and names that list in ``next_cursor_for``; the tool's
``cursor`` parameter restarts there. A reply that still does not fit with every list at its minimum raises
``OutputTooLarge`` naming the parameter that would shrink it. Pure: no I/O, no clock.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def size(payload: Any) -> int:
    """Characters in the compact encoding (``separators=(",", ":")``, ``ensure_ascii=True``)."""
    return len(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


@dataclass(frozen=True)
class TrimRule:
    """A list in the reply ``path`` (keys from the top), the fewest items to keep, where the list starts in the
    full sequence (the cursor it was cut from) and the parameter to name when even the minimum is too large.
    """

    path: tuple[str, ...]
    min_keep: int = 0
    start: int = 0
    hint: str = "cursor"


class OutputTooLarge(Exception):
    """The reply exceeds its budget with every list at its minimum."""

    def __init__(self, hint: str, chars: int, budget: int) -> None:
        self.hint = hint
        self.chars = chars
        self.budget = budget
        super().__init__(hint)


def cut_text(text: str | None, limit: int) -> dict:
    """A string cut by a view, with its full length beside it."""
    value = text or ""
    return {"text": value[:limit], "total_chars": len(value)}


def _get_list(reply: dict, path: tuple[str, ...]) -> list:
    node: Any = reply
    for key in path:
        node = node[key]
    if not isinstance(node, list):
        raise TypeError(f"trim path {'/'.join(path)} is not a list")
    return node


def _set_list(reply: dict, path: tuple[str, ...], items: list) -> None:
    node: Any = reply
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = items


def fit(
    reply: dict,
    budget: int,
    trim: list[TrimRule],
    render: Callable[[dict], dict] | None = None,
) -> dict:
    """Return ``reply`` within ``budget`` characters, or raise ``OutputTooLarge``.

    ``render`` (optional) turns the working reply into its final shape (``get_thesis`` groups its flat member list
    under the links); the size is measured on, and the result is, the rendered reply. The trim rules name lists
    of the working reply."""
    finish = render or (lambda r: r)
    reply.setdefault("next_cursor", None)
    reply.setdefault("next_cursor_for", None)

    def rendered_size(working: dict) -> int:
        return size(finish(working))

    if rendered_size(reply) <= budget:
        return finish(reply)
    first_cut: tuple[TrimRule, int] | None = None
    for rule in trim:
        full = list(_get_list(reply, rule.path))
        if len(full) <= rule.min_keep:
            continue

        def attempt(keep: int, rule: TrimRule = rule, full: list = full) -> int:
            _set_list(reply, rule.path, full[:keep])
            if first_cut is None:
                reply["next_cursor"] = rule.start + keep if keep < len(full) else None
                reply["next_cursor_for"] = "/".join(rule.path) if keep < len(full) else None
            return rendered_size(reply)

        lo, hi = rule.min_keep, len(full)
        best = None
        while lo <= hi:  # the longest prefix that fits
            mid = (lo + hi) // 2
            if attempt(mid) <= budget:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        keep = best if best is not None else rule.min_keep
        attempt(keep)
        if keep < len(full) and first_cut is None:
            first_cut = (rule, keep)
        if best is not None:
            return finish(reply)
    chars = rendered_size(reply)
    if chars <= budget:
        return finish(reply)
    hint = first_cut[0].hint if first_cut else (trim[0].hint if trim else "detail")
    raise OutputTooLarge(hint, chars, budget)
