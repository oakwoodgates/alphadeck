from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, Request

from app.auth import CurrentUser, require_user
from app.db_conn import get_conn  # re-exported: the one connection dependency (app/db_conn.py)
from domain.settings import get_settings
from domain.thesis import Thesis
from ingest.edgar.client import EdgarClient
from llm.client import LLMClient
from repositories import thesis_repo

__all__ = ["get_conn"]  # re-exported from app.db_conn (moved there so app.auth can depend on it)


WORKSPACE_HEADER = "X-Alphadeck-Workspace"


def get_workspace(request: Request, user: CurrentUser = Depends(require_user)) -> UUID:
    """The workspace a request reads and writes: the signed-in caller's own (``user.workspace_id``).

    The SYSTEM principal (the cron's dashboard warm-up, ``app.auth.SYSTEM_USER``) has no workspace of its own: it
    names one per request in the ``X-Alphadeck-Workspace: <uuid>`` header, honored ONLY for that principal (every
    other caller's header is ignored, so a user can never read another workspace by sending it). A system request
    on a workspace-scoped route without a well-formed header answers 400 naming the header."""
    if user.principal != "system":
        return user.workspace_id
    raw = request.headers.get(WORKSPACE_HEADER)
    if not raw:
        raise HTTPException(
            status_code=400,
            detail=f"the system principal must name a workspace in the {WORKSPACE_HEADER} header",
        )
    try:
        return UUID(raw.strip())
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{WORKSPACE_HEADER} must be a workspace id (a UUID)"
        ) from None


def get_thesis_or_404(
    thesis_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> Thesis:
    """Load the thesis for a ``/{thesis_id}`` route from the CALLER'S workspace, or raise 404 — the one ownership
    choke point (a thesis in another workspace is "not found", never "forbidden": its existence is not revealed).
    ``get_conn`` is request-cached, so this shares the route's connection (and the fixture conn under the test
    override)."""
    thesis = thesis_repo.get(conn, thesis_id, workspace=workspace)
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


def get_tier_rec_client() -> LLMClient:
    """The live LLM client for the tier RECOMMENDER (INVARIANT #10) — on its OWN cheap Haiku dials
    (``llm_tier_rec_*``, no web search) so the keyword / flag / decompose seams are undisturbed. Overridden in
    tests with a fake; fail-open by contract (no ``ANTHROPIC_API_KEY`` -> ``recommend_tiers`` returns ``[]`` ->
    the chips render with no recommendation, exactly as today)."""
    _s = get_settings()
    return LLMClient(
        allow_live=True,
        model=_s.llm_tier_rec_model,
        max_tokens=_s.llm_tier_rec_max_tokens,
        timeout_s=_s.llm_tier_rec_timeout_s,
    )


def get_purity_client() -> LLMClient:
    """The live LLM client for the PURITY-ESTIMATE drafter (SURFACE 1b) — on its OWN cheap Haiku dials
    (``llm_purity_*``, no web search) so the other seams are undisturbed. Overridden in tests with a fake;
    fail-open by contract (no ``ANTHROPIC_API_KEY`` -> ``propose_purity`` returns ``None`` -> the purity
    candidate stays today's HUMAN: located, no value, the operator authors it)."""
    _s = get_settings()
    return LLMClient(
        allow_live=True,
        model=_s.llm_purity_model,
        max_tokens=_s.llm_purity_max_tokens,
        timeout_s=_s.llm_purity_timeout_s,
    )
