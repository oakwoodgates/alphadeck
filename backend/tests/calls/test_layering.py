"""The call path never imports the pipeline — ``pipeline`` depends on ``calls``, never the reverse.

``pipeline/core.py`` and ``pipeline/call_for_thesis.py`` import ``calls.assembler``; the replay harness and
the backtest reuse the assembler through them. An edge back from ``calls/`` into ``pipeline/`` would make the
pure call brain depend on the orchestration layer (and invite an import cycle the day a pipeline module that
imports ``calls`` is pulled in). Q14 briefly added one (``pipeline.schedule.is_scheduled_day``); the
predicate now lives in ``domain/trading_days.py``. AST, not a regex, so prose that merely NAMES a pipeline
module in a docstring or comment is not a violation — the same discipline as ``test_market_time``'s scans.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_FORBIDDEN = "pipeline"


def _imported_top_levels(path: Path) -> list[tuple[int, str]]:
    """Every module imported anywhere in the file (top level, inside functions, under TYPE_CHECKING),
    as ``(line, dotted name)``. A relative import has no top-level package to name and is skipped.
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append((node.lineno, node.module))
    return out


def _violations(paths: list[Path]) -> list[str]:
    return [
        f"{p.relative_to(_BACKEND).as_posix()}:{line} imports {mod}"
        for p in paths
        for line, mod in _imported_top_levels(p)
        if mod.split(".")[0] == _FORBIDDEN
    ]


def _calls_sources() -> list[Path]:
    return sorted((_BACKEND / "calls").rglob("*.py"))


def test_calls_never_imports_pipeline():
    found = _violations(_calls_sources())
    assert not found, (
        "calls/ must not import pipeline/ (pipeline depends on calls, never the reverse):\n  "
        + "\n  ".join(found)
        + "\n  Put a shared pure helper in domain/ instead (see domain/trading_days.py)."
    )


def test_the_scan_walks_the_call_path():
    names = {p.name for p in _calls_sources()}
    assert {"assembler.py", "grading.py", "confidence.py", "counter_case.py"} <= names


def test_the_guard_actually_detects_a_violation(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        '"""Mentions pipeline.schedule in prose only — fine."""\n'
        "from pipeline.schedule import is_scheduled_day\n"
        "def f():\n"
        "    import pipeline.core\n"
        "from pipelines import x  # a different package — fine\n",
        encoding="utf-8",
    )
    # the file sits outside the backend tree, so apply the same filter `_violations` uses directly
    hits = [mod for _line, mod in _imported_top_levels(bad) if mod.split(".")[0] == _FORBIDDEN]
    assert hits == ["pipeline.schedule", "pipeline.core"]
