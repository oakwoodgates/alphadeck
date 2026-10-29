"""The Dashboards API seam (``docs/DASHBOARDS.md``): one ``/dashboards`` router that mounts each
dashboard's own router. Display only: every route here READS; ``tests/dashboards/test_isolation.py`` keeps
it free of SQL writes, the LLM and notifications."""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.dashboards import (
    cross_radar,
    idea_timeline,
    rotation_map,
    shared_names,
    what_changed,
)

# Each dashboard adds its own ``<slug>.py`` router module in this package (an ``APIRouter`` with
# ``prefix="/<slug>"``, its compute in ``backend/dashboards/<slug>.py``) and ONE line to the tuple below.
# Its wire models live in its own module — NOT ``app/schemas_api.py`` — so cutting a dashboard deletes
# cleanly: the module, its tuple line, and a contract regen.
_DASHBOARD_ROUTERS: tuple[APIRouter, ...] = (
    shared_names.router,
    idea_timeline.router,
    rotation_map.router,
    what_changed.router,
    cross_radar.router,
)

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
for _r in _DASHBOARD_ROUTERS:
    router.include_router(_r)
