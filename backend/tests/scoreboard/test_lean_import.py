from __future__ import annotations

import ast
from pathlib import Path

# The lean-image guard: the live Scoreboard imports the replay scorer in the prod API image, where
# duckdb (the optional .[replay] extra) is NOT installed. ``replay.scoring``'s duckdb import must
# therefore stay annotation-only (under ``if TYPE_CHECKING:``) — this test pins it structurally, so
# it holds in ANY environment (a dev venv that happens to have duckdb can't mask a regression).


def _runtime_imports(path: Path) -> set[str]:
    """Top-level modules imported at RUNTIME (i.e. not under an ``if TYPE_CHECKING:`` block)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()

    def visit(nodes, type_checking: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.If):
                test = node.test
                is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                visit(node.body, type_checking or is_tc)
                visit(node.orelse, type_checking)
            elif isinstance(node, ast.Import):
                if not type_checking:
                    out.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if not type_checking and node.module:
                    out.add(node.module.split(".")[0])
            elif hasattr(node, "body"):
                visit(node.body, type_checking)

    visit(tree.body, False)
    return out


def test_replay_scoring_has_no_runtime_duckdb_import():
    path = Path(__file__).resolve().parents[2] / "replay" / "scoring.py"
    assert "duckdb" not in _runtime_imports(path)


def test_scoreboard_package_imports_without_replay_extra_modules():
    """The whole scoreboard package (and the replay pieces it reuses) import cleanly — none of them
    may pull duckdb/pyarrow at import time."""
    for module in (
        "replay.schema",
        "replay.episodes",
        "replay.scoring",
        "replay.metrics",
        "scoreboard.prices",
        "scoreboard.record",
        "scoreboard.schema",
        "scoreboard.run",
        "scoreboard.artifact",
        "scoreboard.replay_snapshot",  # the CLI imports replay's duckdb bits inside main() only
    ):
        path = Path(__file__).resolve().parents[2] / Path(*module.split(".")).with_suffix(".py")
        runtime = _runtime_imports(path)
        assert "duckdb" not in runtime and "pyarrow" not in runtime, module
        __import__(module)  # and they genuinely import in this env
