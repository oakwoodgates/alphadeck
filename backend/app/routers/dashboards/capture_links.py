"""Capture-owned navigation recipes and exact JSON provenance pointers; never URL fetching.

These are application relationships. MCP only follows their declared destinations and resolves
their pointers inside an already captured response; it never reconstructs domain joins.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException

from app.routers.dashboards.capture_contract import (
    CaptureContext,
    CaptureDestination,
    CapturedResponse,
    CaptureNavigation,
    CaptureReference,
)

MAX_RELATIONSHIPS = 2048
# The temporal guard hashes EVERY CaptureContext field except these, so a new field is guarded
# by default. Limitations are per-surface notes (a Cockpit adds its own to the Board's), not time.
# Mirrored in agent_access/references.CONTEXT_HASH_EXCLUDED; a parity test pins both.
CONTEXT_HASH_EXCLUDED = ("limitations",)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def context_hash(asof: str, context: CaptureContext) -> str:
    """Hash exact serialized wire values, including Pydantic's UTC-Z datetime spelling."""
    wire = context.model_dump(mode="json", exclude=set(CONTEXT_HASH_EXCLUDED))
    return canonical_hash({**wire, "asof": asof})  # asof last: no field can stand in for it


def navigation(dashboard: str, **settings: Any) -> CaptureNavigation:
    destination = CaptureDestination(dashboard=dashboard, settings=settings)
    return CaptureNavigation(
        id="nav_" + canonical_hash(destination.model_dump(mode="json"))[:24],
        destination=destination,
    )


def reference(
    responses: list[CapturedResponse],
    index: int,
    pointer: str,
    *,
    category: str,
    label: str | None = None,
) -> CaptureReference:
    source = responses[index]
    identity = {"path": source.path, "query": source.query, "pointer": pointer}
    return CaptureReference(
        id="ref_" + canonical_hash(identity)[:24],
        response_index=index,
        pointer=pointer,
        category=category,
        label=label[:256] if label else None,
    )


def resolve_pointer(body: Any, pointer: str) -> Any:
    if pointer == "":
        return body
    if not pointer.startswith("/") or len(pointer.encode()) > 4096:
        raise ValueError("invalid capture pointer")
    segments = pointer[1:].split("/")
    if len(segments) > 64:
        raise ValueError("capture pointer too deep")
    for raw in segments:
        if re.search(r"~(?![01])", raw):
            raise ValueError("invalid pointer escape")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(body, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError("invalid array index")
            body = body[int(key)]
        elif isinstance(body, dict):
            body = body[key]
        else:
            raise ValueError("capture pointer traverses a scalar")
    return body


def validate_relationships(
    responses: list[CapturedResponse],
    links: list[CaptureNavigation],
    references: list[CaptureReference],
) -> None:
    for items in (links, references):
        if len(items) > MAX_RELATIONSHIPS:
            raise HTTPException(
                413,
                detail="capture exceeds 2048 navigation or evidence entries; nothing was truncated",
            )
        if len({item.id for item in items}) != len(items):
            raise HTTPException(500, detail="capture adapter returned duplicate relationship IDs")
    try:
        for item in references:
            resolve_pointer(responses[item.response_index].body, item.pointer)
    except (IndexError, KeyError, TypeError, ValueError):
        raise HTTPException(
            500, detail="capture adapter returned an invalid evidence pointer"
        ) from None
