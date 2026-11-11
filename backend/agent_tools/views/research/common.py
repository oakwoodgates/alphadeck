"""Shared pieces of the research-stage views (``get_triage``, ``get_discovery``, ``get_extract``,
``get_onboarding``): the request and the reply header.

Pure. Every value is copied from the API's JSON; a view only counts entries, filters rows on a copied field and cuts
strings (with their full length beside them). It never sorts, scores or derives: the API's row order IS the UI's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..dashboards.common import ViewError, pick, put_cut

BUDGET = 8_000

__all__ = ["BUDGET", "ResearchRequest", "ViewError", "header", "pick", "put_cut"]


@dataclass(frozen=True)
class ResearchRequest:
    """What the caller asked for, already validated: the resolved card (``{id, name}``, None for a plain extract),
    the as-of (None on an undated tool), the section, the cursor and the narrowings the tool takes.
    """

    thesis: dict[str, Any] | None = None
    asof: str | None = None
    section: str | None = None
    cursor: int = 0
    segment: str | None = None
    t: str | None = None
    status: str | None = None
    run: str | None = None
    fact: str | None = None
    reason: str | None = None


def header(req: ResearchRequest) -> dict[str, Any]:
    """The reply's first fields: the card, the as-of (null when undated) and the source (always ``live``)."""
    out: dict[str, Any] = {}
    if req.thesis is not None:
        out["thesis"] = {"id": req.thesis.get("id"), "name": req.thesis.get("name")}
    out["asof"] = req.asof
    out["source"] = "live"
    return out
