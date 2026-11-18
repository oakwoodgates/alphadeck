"""V2 W5b: the GREP GUARD on the flag table (plan risk R9).

A user's flag is seen only by that user and the steward. Every query of ``value_flag`` therefore lives in ONE
repository (``repositories/value_flag_repo.py``), where each user-side read filters by the user's id in SQL; a new
product file that queries the table directly is a privacy regression until it is reviewed and listed here with its
reason.
"""

from __future__ import annotations

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_REPO = "repositories/value_flag_repo.py"
ALLOWED: dict[str, str] = {
    _REPO: "the one repository of flags (user reads filter by user_id in SQL)",
    # V2 W5a (the user's read, plan fix 1): the caller's own flag beside each value, queried with the caller's id
    # in SQL, never cached by company alone. Listed ahead of its merge so the two Wave-1 PRs merge in any order.
    "platform_values/user_view.py": "W5a's my_flag read (the caller's own flag, user_id in SQL)",
}
_SQL = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+value_flag\b", re.IGNORECASE)


def _product_files() -> list[Path]:
    out = []
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel.startswith(("tests/", ".venv/", "build/")) or "/site-packages/" in rel:
            continue
        out.append(path)
    return out


def test_only_the_listed_files_query_the_flag_table():
    users = {
        path.relative_to(_BACKEND).as_posix()
        for path in _product_files()
        if _SQL.search(path.read_text(encoding="utf-8"))
    }
    assert _REPO in users, "the flag repository moved: update this guard"
    assert users <= set(ALLOWED), (
        "value_flag holds users' private flags; only a reviewed file may query it: "
        f"{sorted(users - set(ALLOWED))}"
    )


def test_the_user_reads_filter_by_the_user_in_sql():
    """Each user-side read in the repository names ``user_id = %s`` in its WHERE clause."""
    text = (_BACKEND / _REPO).read_text(encoding="utf-8")
    for fn in ("def open_flag(", "def for_user(", "def mine(", "def count_mine(", "def withdraw("):
        start = text.index(fn)
        body = text[start : text.find("\ndef ", start + 1)]
        assert "user_id = %s" in body, fn
