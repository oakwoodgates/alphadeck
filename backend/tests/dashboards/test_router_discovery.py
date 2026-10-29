"""The self-registering dashboard routers (``app/routers/dashboards/__init__.py``, ``docs/DASHBOARDS.md`` rule 8):
every module in the package that defines a module-level ``router`` is mounted under ``/dashboards`` with no
shared line to edit, and a module without one is skipped. Pure: no DB."""

from __future__ import annotations

import ast
import importlib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

import app.routers.dashboards as dashboards_pkg
from app.main import app
from app.routers.dashboards import MOUNTED, discover_routers

ROUTERS_DIR = Path(dashboards_pkg.__file__).resolve().parent


def _defines_router(path: Path) -> bool:
    """Read off the SOURCE, independently of the import: a module-level ``router = ...`` (or ``router: T =``)."""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "router" for t in targets):
            return True
    return False


def _routes(routes) -> set[tuple[str, frozenset[str]]]:
    return {(r.path, frozenset(r.methods)) for r in routes if isinstance(r, APIRoute)}


def test_every_module_with_a_router_is_mounted_in_sorted_module_order():
    expected = sorted(
        p.stem for p in ROUTERS_DIR.glob("*.py") if p.stem != "__init__" and _defines_router(p)
    )
    assert expected, "no dashboard router module found: the scan points at the wrong place"
    assert list(MOUNTED) == expected

    served = _routes(app.routes)
    for name in expected:
        module = importlib.import_module(f"app.routers.dashboards.{name}")
        assert isinstance(module.router, APIRouter)
        mine = {("/dashboards" + path, methods) for path, methods in _routes(module.router.routes)}
        assert mine, f"{name}: a router with no route"
        assert mine <= served, f"{name}: not mounted under /dashboards"


def test_every_dashboard_route_the_app_serves_comes_from_a_mounted_module():
    """Nothing reaches /dashboards except through discovery: the served routes are exactly the union of the
    mounted modules' routes."""
    mounted = set()
    for name in MOUNTED:
        module = importlib.import_module(f"app.routers.dashboards.{name}")
        mounted |= {("/dashboards" + p, m) for p, m in _routes(module.router.routes)}
    served = {(p, m) for p, m in _routes(app.routes) if p.startswith("/dashboards")}
    assert served == mounted


@pytest.fixture
def fake_package(tmp_path, monkeypatch):
    """A throwaway package on ``sys.path``: two dashboard modules with a ``router`` (written out of order),
    a helper module with none, a module whose ``router`` is not an ``APIRouter``, and a subpackage with one.
    """
    name = f"fake_dashboards_{uuid.uuid4().hex}"
    pkg = tmp_path / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "zeta.py").write_text(
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/zeta")\n'
        '@router.get("")\ndef zeta():\n    return {}\n',
        encoding="utf-8",
    )
    (pkg / "alpha.py").write_text(
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/alpha")\n'
        '@router.get("")\ndef alpha():\n    return {}\n',
        encoding="utf-8",
    )
    (pkg / "helpers.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (pkg / "not_a_router.py").write_text(
        'router = "a string, not an APIRouter"\n', encoding="utf-8"
    )
    sub = pkg / "mid"
    sub.mkdir()
    (sub / "__init__.py").write_text(
        'from fastapi import APIRouter\nrouter = APIRouter(prefix="/mid")\n', encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield importlib.import_module(name)
    for mod in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[mod]


def test_a_module_without_a_router_is_ignored(fake_package):
    found = discover_routers(fake_package)
    assert [name for name, _ in found] == [
        "alpha",
        "mid",
        "zeta",
    ]  # sorted; helpers + not_a_router skipped
    assert all(isinstance(r, APIRouter) for _, r in found)
    assert [r.prefix for _, r in found] == ["/alpha", "/mid", "/zeta"]


def test_a_module_that_fails_to_import_fails_loud(fake_package):
    """A broken dashboard module never quietly drops out of the API: discovery raises."""
    (Path(fake_package.__path__[0]) / "broken.py").write_text(
        "raise RuntimeError('broken dashboard')\n", encoding="utf-8"
    )
    importlib.invalidate_caches()
    with pytest.raises(RuntimeError, match="broken dashboard"):
        discover_routers(fake_package)
