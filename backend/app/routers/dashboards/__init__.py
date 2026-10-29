"""The Dashboards API seam (``docs/DASHBOARDS.md``): one ``/dashboards`` router that mounts each
dashboard's own router. Display only: every route here READS; ``tests/dashboards/test_isolation.py`` keeps
it free of SQL writes, the LLM and notifications.

Self-registering (rule 8): every module in THIS package that defines a module-level ``router`` (an
``APIRouter`` with ``prefix="/<slug>"``, its compute in ``backend/dashboards/<slug>.py``) is mounted, in
sorted module-name order, so adding a dashboard edits no shared line here and cutting one is deleting its
module (and a contract regen). A module without a ``router`` (a shared helper) is skipped. Each dashboard's
wire models live in its own module, NOT ``app/schemas_api.py``.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from types import ModuleType

from fastapi import APIRouter


def discover_routers(package: ModuleType) -> list[tuple[str, APIRouter]]:
    """``(module name, router)`` for every module directly in ``package`` that defines a module-level
    ``router`` (an ``APIRouter``), sorted by module name. A module that fails to import raises: a broken
    dashboard fails the app's start loud, it never quietly drops out of the API."""
    found: list[tuple[str, APIRouter]] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        candidate = getattr(module, "router", None)
        if isinstance(candidate, APIRouter):
            found.append((info.name, candidate))
    return found


router = APIRouter(prefix="/dashboards", tags=["dashboards"])

_found = discover_routers(sys.modules[__name__])
for _name, _dashboard_router in _found:
    router.include_router(_dashboard_router)

# the dashboard router modules mounted above, in mount order
MOUNTED: tuple[str, ...] = tuple(name for name, _ in _found)
