"""The call path never imports ``editions``. The Scoreboard editions re-run history beside the product; nothing
that computes, serves or records a call may depend on them (a test, not a convention)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
    return out


def test_no_module_outside_editions_imports_it():
    offenders = []
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND)
        if rel.parts[0] in ("editions", "tests", ".venv") or "site-packages" in rel.parts:
            continue
        if any(m == "editions" or m.startswith("editions.") for m in _imports(p)):
            offenders.append(rel.as_posix())
    assert offenders == []


def test_loading_the_call_path_loads_no_editions_module():
    """The app, the nightly, the backfill and the CLI funnel, imported in a fresh interpreter."""
    code = (
        "import sys\n"
        "import app.main, pipeline.daily, pipeline.backfill, pipeline.run, pipeline.call_for_thesis\n"
        "import pipeline.core, calls.assembler, signals\n"
        "bad = sorted(m for m in sys.modules if m == 'editions' or m.startswith('editions.'))\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout + r.stderr
