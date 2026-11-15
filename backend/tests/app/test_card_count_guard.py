"""V2 W4a: the GREP GUARD on the cross-workspace card count (``platform_values.cards.card_counts``).

The card count is the one W4 read that looks into every workspace's baskets. It answers two numbers per company,
and it may run only behind the steward: its one product caller is the steward-only router
``app/routers/admin_platform_values.py`` (mounted with ``require_super_admin``). A new caller (W3's backfill, for
its order) is added to ``ALLOWED`` in the same change, with the reason it is steward-side.
"""

from __future__ import annotations

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_DEFINED_IN = "platform_values/cards.py"
ALLOWED: dict[str, str] = {
    "app/routers/admin_platform_values.py": "the steward's queue (require_super_admin)",
}
_NAME = re.compile(r"\bcard_counts\b")


def _product_files() -> list[Path]:
    out = []
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv/", "build/")) or "/site-packages/" in rel:
            continue
        out.append(path)
    return out


def test_the_card_count_is_defined_once_and_called_only_by_the_steward_router():
    users = {
        path.relative_to(_BACKEND).as_posix()
        for path in _product_files()
        if _NAME.search(path.read_text(encoding="utf-8"))
    }
    assert _DEFINED_IN in users, "the card count moved: update this guard"
    assert users - {_DEFINED_IN} == set(ALLOWED), (
        "card_counts reads every workspace's baskets; only a steward-only caller may use it: "
        f"{sorted(users - {_DEFINED_IN} - set(ALLOWED))}"
    )


def test_the_card_count_module_logs_nothing():
    """Nothing it reads may reach a log: the module imports no logger and prints nothing."""
    text = (_BACKEND / _DEFINED_IN).read_text(encoding="utf-8")
    assert "logging" not in text and "print(" not in text and "_log" not in text
