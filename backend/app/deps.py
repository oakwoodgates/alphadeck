from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException

from db.session import connect, current_tenant_id
from domain.settings import get_settings
from domain.thesis import Thesis
from ingest.edgar.client import EdgarClient
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


def get_research_client() -> LLMClient:
    """The live LLM client for the narrative→chain RESEARCH pass (Slice 1) — on its OWN dials
    (``llm_research_*``, default Opus) so the Sonnet decompose + Haiku flag seams are undisturbed. Overridden in
    tests with a fake; fail-open by contract (no ``ANTHROPIC_API_KEY`` -> the research pass is skipped and the
    draft degrades to the recall-only decompose, never an error)."""
    _s = get_settings()
    return LLMClient(
        allow_live=True,
        model=_s.llm_research_model,
        max_tokens=_s.llm_research_max_tokens,
        timeout_s=_s.llm_research_timeout_s,
        max_retries=0,  # an expensive web-search one-shot must NEVER auto-repeat at the SDK layer
    )


def get_edgar_client() -> EdgarClient:
    """The EDGAR client for the EDGAR-first discovery (Slice 4b). Live by default (cache-first + polite +
    declared User-Agent, like the extract seam's inline client), overridden in tests with a fake EFTS client.
    Discovery is fail-open: any EFTS trouble degrades to an empty universe inside ``run_discovery`` — the draft
    then falls back to the recall-only decompose, never a 5xx."""
    return EdgarClient(allow_live=True)


def get_keyword_client() -> LLMClient:
    """The live LLM client for the thesis→keyword generator (discovery Slice 2a) — on its OWN cheap dials
    (``llm_keyword_*``, default Haiku, no web search) so the other seams are undisturbed. Overridden in tests
    with a fake; fail-open by contract (no ``ANTHROPIC_API_KEY`` -> no EFTS keywords -> the caller degrades to
    the tail-sweep / hand-authoring)."""
    _s = get_settings()
    return LLMClient(
        allow_live=True,
        model=_s.llm_keyword_model,
        max_tokens=_s.llm_keyword_max_tokens,
        timeout_s=_s.llm_keyword_timeout_s,
    )
