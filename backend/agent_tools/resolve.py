"""Thesis and ticker resolution. Exact matches only: never a fuzzy pick.

A thesis argument is a UUID or a name. A UUID must be one of the theses the API lists (archived included); a name
must match exactly one listed thesis, case-insensitively, after trimming spaces. No match -> ``unknown_thesis``
with up to five candidates whose names contain the text; several exact matches -> ``ambiguous_thesis`` listing
them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

from .client import ApiClient, segment
from .errors import ToolFailure

MAX_CANDIDATES = 5
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_uuid(text: str) -> UUID | None:
    try:
        return UUID(text.strip())
    except (ValueError, AttributeError):
        return None


def match_thesis(theses: list[dict[str, Any]], text: str) -> dict[str, Any]:
    """Pick ``{"id", "name"}`` from the API's thesis list for ``text`` (pure; raises ``ToolFailure``)."""
    wanted = (text or "").strip()
    if not wanted:
        raise ToolFailure("invalid_arguments", "thesis must be a UUID or a thesis name.")
    as_uuid = parse_uuid(wanted)
    if as_uuid is not None:
        for t in theses:
            if str(t.get("id")) == str(as_uuid):
                return {"id": str(t["id"]), "name": t.get("name")}
        raise ToolFailure("unknown_thesis", "No thesis has this id.")
    folded = wanted.casefold()
    exact = [t for t in theses if (t.get("name") or "").strip().casefold() == folded]
    if len(exact) == 1:
        return {"id": str(exact[0]["id"]), "name": exact[0].get("name")}
    if len(exact) > 1:
        raise ToolFailure(
            "ambiguous_thesis",
            "More than one thesis has this exact name; pass its id.",
            extra={"candidates": [{"id": str(t["id"]), "name": t.get("name")} for t in exact]},
        )
    near = [t for t in theses if folded in (t.get("name") or "").casefold()][:MAX_CANDIDATES]
    raise ToolFailure(
        "unknown_thesis",
        "No thesis has this exact name (names match whole, case-insensitively).",
        extra={"candidates": [{"id": str(t["id"]), "name": t.get("name")} for t in near]},
    )


async def resolve_thesis(client: ApiClient, text: str) -> dict[str, Any]:
    """``{"id", "name"}`` for a UUID or an exact thesis name, over ``GET /theses?include_archived=true``."""
    theses, _ = await client.get("/theses", {"include_archived": True})
    return match_thesis(theses, text)


def check_asof(asof: str | None) -> str | None:
    """A ``YYYY-MM-DD`` as-of, passed through as given (the server never derives a date)."""
    if asof is None:
        return None
    if not _DATE.match(asof):
        raise ToolFailure("invalid_arguments", "asof must be a date as YYYY-MM-DD.")
    try:
        date.fromisoformat(asof)
    except ValueError:
        raise ToolFailure("invalid_arguments", "asof is not a real calendar date.") from None
    return asof


async def resolve_asof(client: ApiClient, asof: str | None) -> tuple[str, dict[str, Any] | None]:
    """The as-of to read: the caller's, else the API's ``current_night`` from ``GET /clock``."""
    checked = check_asof(asof)
    if checked is not None:
        return checked, None
    clock, _ = await client.get("/clock")
    night = clock.get("current_night") if isinstance(clock, dict) else None
    if not isinstance(night, str):
        raise ToolFailure("api_error", "The API clock did not name a current night.")
    return night, clock


async def resolve_ticker(client: ApiClient, ticker: str) -> dict[str, Any]:
    """``{security_id, ticker, name, matched}`` from the ticker lookup's resolver (a former ticker counts)."""
    wanted = (ticker or "").strip()
    if not 1 <= len(wanted) <= 24:
        raise ToolFailure("invalid_arguments", "t must be a ticker of 1 to 24 characters.")
    found, _ = await client.get(
        "/dashboards/ticker-lookup/resolve",
        {"t": wanted, "former": True},
        not_found=ToolFailure("unknown_ticker", "No company has this ticker."),
    )
    if not isinstance(found, dict) or parse_uuid(str(found.get("security_id"))) is None:
        raise ToolFailure("api_error", "The ticker resolver answered without a company id.")
    found["security_id"] = segment(str(parse_uuid(str(found["security_id"]))))
    return found
