from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException

from db.session import connect, current_tenant_id
from domain.settings import get_settings
from domain.thesis import Thesis
from llm.client import LLMClient
from repositories import thesis_repo


def get_conn() -> Iterator[psycopg.Connection]:
    """Request-scoped DB connection. Overridden in tests to share the fixture's connection."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_tenant() -> UUID:
    """The current deployment tenant (env-config, NOT auth). Overridable in tests, like ``get_conn``."""
    return current_tenant_id()


def get_thesis_or_404(thesis_id: UUID, conn: psycopg.Connection = Depends(get_conn)) -> Thesis:
    """Load the thesis for a ``/{thesis_id}`` route, or raise 404 — the shared load-or-404 the call, scored,
    detail, and draft-chain routes all need (the thesis carries its own tenant; auth deferred). ``get_conn``
    is request-cached, so this shares the route's connection (and the fixture conn under the test override).
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    return thesis


def get_llm_client() -> LLMClient:
    """The live LLM client for the FLAG-explanation drafter. Overridden in tests with a fake (no network,
    no key). Live by default, but fail-open: with no ``ANTHROPIC_API_KEY`` set, every call degrades to
    no-explanation rather than erroring (the absence of a key is the feature's off switch)."""
    return LLMClient(allow_live=True)


def get_decompose_client() -> LLMClient:
    """The live LLM client for the narrative→chain DECOMPOSE seam (Slice 5b) — the Sonnet sibling to
    ``get_llm_client``, on its OWN dials (``llm_decompose_*``) so the Haiku flag drafter is undisturbed.
    Overridden in tests with a fake; fail-open by contract (no ``ANTHROPIC_API_KEY`` -> the draft endpoint
    returns an empty draft and hand-authoring is untouched)."""
    _s = get_settings()
    return LLMClient(
        allow_live=True,
        model=_s.llm_decompose_model,
        max_tokens=_s.llm_decompose_max_tokens,
        timeout_s=_s.llm_decompose_timeout_s,
    )
