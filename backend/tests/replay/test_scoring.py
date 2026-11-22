from __future__ import annotations

import ast
import inspect

import replay.harness
import replay.scoring


def _import_modules(module) -> set[str]:
    """The modules a module actually IMPORTS (via the AST — docstring/comment mentions don't count)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _imported_names(module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.ImportFrom):
            names |= {a.name for a in node.names}
    return names


def test_lookahead_boundary_is_structural():
    """The scorer must not import the as-of-capped replay pit, and the replay loop must not import the
    forward RealizedPrices reader — so neither pass can reach the other's data (the lookahead boundary,
    as a CI-checkable import-graph fact)."""
    assert "replay.pit" not in _import_modules(replay.scoring)
    assert "RealizedPrices" not in _imported_names(replay.harness)
