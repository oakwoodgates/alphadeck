"""Shared pieces of the dashboard views: the request, the common reply header and the coverage block.

Pure. Every value is copied from the API's JSON; this module only counts entries and cuts strings (with their full
length beside them). It never sorts: the API's row order IS the UI's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...budget import cut_text

# ``cut_text`` is for a reply's few long strings (a reason); rows use ``put_cut`` (cheaper per row)
BUDGET = 8_000
REASONS_SHOWN = 5

# counts and caveats a dashboard's response carries at the top (copied whole when present)
COVERAGE_KEYS = (
    "counts",
    "live_cards",
    "cards_counted",
    "cards_armed",
    "cards_warming",
    "cards_managing",
    "cards_incubating",
    "cards_incubating_lit",
    "cards_no_call",
    "cards_with_term_months",
    "cards_without_term_months",
    "cards_not_yet_created",
    "not_yet_created",
    "roster_fallback",
    "roster_fallback_cards",
    "cards_with_change",
    "cards_without_calls",
    "cards_without_catalysts",
    "no_ticker_rows",
    "names_read",
    "names_flagged",
    "basket_rows",
    "companies",
    "priced_companies",
    "silent_rows_left_out",
    "unresolved_rows",
    "unpriced",
    "min_priced",
    "cross_days",
    "calendar_source",
    "calendar_sessions",
    "calendar_first",
    "calendar_last",
    "session_source",
    "price_window_start",
)


class ViewError(Exception):
    """A request the payload cannot answer (an unknown group key): the server reports it as invalid_arguments."""


@dataclass(frozen=True)
class Request:
    """What the caller asked for, already validated: the as-of (None for an undated dashboard), the section, the
    cursor, the resolved thesis (``{id, name}``), the group key, the effective settings and the API's read time.
    """

    asof: str | None = None
    section: str | None = None
    cursor: int = 0
    thesis: dict[str, Any] | None = None
    group: str | None = None
    change: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


def _name_list(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """``count`` names, the first ``REASONS_SHOWN`` reasons and ``reasons_shown`` (so a cut list says it was cut)."""
    reasons = [
        {"ticker": r.get("ticker"), "reason": cut_text(r.get("reason"), 60)["text"]}
        for r in rows[:REASONS_SHOWN]
    ]
    return {"count": len(rows), "reasons_shown": len(reasons), "reasons": reasons}


def coverage(
    payload: dict[str, Any], extra: dict[str, Any] | None = None, skip: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The dashboard's own counts and caveats: every count field it carries, ``flagged`` as a count with the first
    five reasons, and any list-valued caveat (``unpriced``) as a count. Small by construction."""
    out: dict[str, Any] = {}
    for key in COVERAGE_KEYS:
        if key not in payload or key in skip:
            continue
        value = payload[key]
        out[key] = {"count": len(value)} if isinstance(value, list) else value
    flagged = payload.get("flagged")
    if isinstance(flagged, list):
        out["flagged"] = _name_list(flagged)
    if extra:
        out.update(extra)
    return out


def header(
    slug: str,
    payload: dict[str, Any],
    req: Request,
    *,
    sections: tuple[str, ...],
    cov: dict[str, Any],
    narrowed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The reply's first fields, the same on every dashboard (``asof`` is null for an undated one)."""
    out: dict[str, Any] = {
        "dashboard": slug,
        "asof": req.asof,
        "source": "live",
    }
    if "clock" in payload:
        out["clock"] = payload["clock"]
    if "cache" in payload:
        out["cache"] = payload["cache"]
    if "compute_ms" in payload:
        # on a cache hit the API still reports the cold time: it rides as what a cold read costs, never as this
        # read's time (a 173 ms hit that says compute_ms 41,669 reads like a slow call)
        key = "compute_ms" if payload.get("cache", "computed") == "computed" else "cold_compute_ms"
        out[key] = payload["compute_ms"]
    out["settings"] = dict(req.settings)
    out["coverage"] = cov
    out["sections"] = list(sections)
    if req.section is not None:
        out["section"] = req.section
    if narrowed:
        out["narrowed"] = narrowed
    return out


def narrowed(req: Request) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    if req.thesis is not None:
        out["thesis"] = {"id": req.thesis.get("id"), "name": req.thesis.get("name")}
    if req.group is not None:
        out["group"] = req.group
    if req.change is not None:
        out["change"] = req.change
    return out or None


def pick(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """The named fields of a row, copied (a missing key reads as null)."""
    return {k: row.get(k) for k in keys}


def put_cut(out: dict[str, Any], key: str, text: str | None, limit: int) -> None:
    """``out[key]`` = the text cut at ``limit``; ``<key>_total_chars`` rides beside it only when it was cut."""
    if text is None:
        out[key] = None
        return
    out[key] = text[:limit]
    if len(text) > limit:
        out[f"{key}_total_chars"] = len(text)
