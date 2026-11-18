"""V2 W6a: GUARD the one writer of the counting-catalyst store (``fact_thesis_catalyst``, migration 0212).

A counting entry turns a user's own statement into a call input (W6c), so WHO may write it is part of the rule:
only ``repositories/thesis_catalyst_repo.py`` (``append_version``), where the entry time is the database's
``now()`` and the version rules live. This guard reads source code only (AST), no database, in the style of
``tests/ingest/test_platform_value_writers.py``. It fails when:

(1) any other product module holds a write statement against the table (``INSERT INTO`` / ``UPDATE`` /
    ``DELETE FROM`` / ``TRUNCATE`` / ``COPY``, in any string the module builds);
(2) any module calls ``append_fact`` (the generic fact append) with the table's name;
(3) any product module that names the table also builds a write statement against a COMPUTED table (``INSERT
    INTO {...}``), unless it is listed in ``_GENERIC_WRITERS`` with the reason it cannot reach this table;
(4) the model's packages (``llm/``, ``agent_tools/``) import the writer (invariant 3: the model never sets a
    counting entry).

A STALE allowlist entry fails too (a listed module that no longer matches is removed, never left to cover a future
one). W6c adds the seed's converted-row writer and migration 0213 here, by name.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_TABLE = "fact_thesis_catalyst"
_WRITER = "repositories/thesis_catalyst_repo.py"
_WRITE = re.compile(
    rf"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(\s+TABLE)?|COPY)\s+{_TABLE}\b", re.I
)
_COMPUTED_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+\{", re.I)

# file -> why it builds a write against a computed table while naming this one, and why that cannot reach it
_GENERIC_WRITERS: dict[str, str] = {
    # ``append_fact`` (the definition): the table name appears only in the as-of whitelist ``_FACT_IDENTITY``;
    # rule (2) forbids every call of it on this table
    "db/bitemporal.py": "the generic append_fact (the definition)",
    # the cutover reset: ``DELETE FROM {}`` runs over ``RESET_ORDER`` only (the calls and editions record); it
    # names this table in ``_CHILD_TABLES``, which it COUNTS for the recall baseline and never deletes from
    "pipeline/v2_reset.py": "the reset deletes only RESET_ORDER; this table is a counted child (keep)",
}


@lru_cache(maxsize=1)
def _product_files() -> tuple[tuple[str, ast.Module], ...]:
    return tuple(_walk())


def _walk() -> Iterator[tuple[str, ast.Module]]:
    roots = [
        p
        for p in sorted(_BACKEND.iterdir())
        if not p.name.startswith(".") and p.name not in ("tests", "build", "node_modules")
    ]
    for root in roots:
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            if path.suffix != ".py":
                continue
            rel = path.relative_to(_BACKEND).as_posix()
            yield rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)


def _strings(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(
            node, ast.JoinedStr
        ):  # an f-string: its literal parts, joined with a placeholder
            out.append(
                "".join(v.value if isinstance(v, ast.Constant) else "{x}" for v in node.values)
            )
    return out


def _appends_this_table(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "append_fact" and any(
                isinstance(a, ast.Constant) and a.value == _TABLE for a in node.args
            ):
                return True
    return False


def _imports_writer(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "repositories.thesis_catalyst_repo":
                return True
            if node.module == "repositories" and any(
                a.name == "thesis_catalyst_repo" for a in node.names
            ):
                return True
        if isinstance(node, ast.Import) and any(
            a.name == "repositories.thesis_catalyst_repo" for a in node.names
        ):
            return True
    return False


def test_only_the_repository_writes_the_table():
    writers = sorted(
        rel for rel, tree in _product_files() if any(_WRITE.search(s) for s in _strings(tree))
    )
    assert writers == [_WRITER], f"only {_WRITER} may write {_TABLE}; found {writers}"


def test_no_module_appends_the_table_through_append_fact():
    found = sorted(rel for rel, tree in _product_files() if _appends_this_table(tree))
    assert found == [], f"{_TABLE} is written only by {_WRITER}, never append_fact: {found}"


def test_a_generic_writer_naming_the_table_is_listed():
    found = set()
    for rel, tree in _product_files():
        strings = _strings(tree)
        if rel != _WRITER and any(_TABLE in s for s in strings):
            if any(_COMPUTED_WRITE.search(s) for s in strings):
                found.add(rel)
    assert found - set(_GENERIC_WRITERS) == set(), "a new generic writer names the table: list it"
    assert set(_GENERIC_WRITERS) - found == set(), "a stale _GENERIC_WRITERS entry: remove it"


def test_the_model_never_reaches_the_writer():
    found = sorted(
        rel
        for rel, tree in _product_files()
        if rel.startswith(("llm/", "agent_tools/")) and _imports_writer(tree)
    )
    assert found == []


def test_the_guard_sees_the_writer():
    """The teeth: the scan really finds the one writer's INSERT (so rule (1) cannot pass on an empty scan)."""
    tree = dict(_product_files())[_WRITER]
    assert any(_WRITE.search(s) for s in _strings(tree))
