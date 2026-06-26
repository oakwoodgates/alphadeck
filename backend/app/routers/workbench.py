from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.deps import (
    get_conn,
    get_current_tenant,
    get_decompose_client,
    get_edgar_client,
    get_keyword_client,
    get_llm_client,
    get_research_client,
    get_thesis_or_404,
)
from app.schemas_api import (
    ChainDraftOut,
    DraftJobRef,
    DraftJobStatus,
    EditTermsRequest,
    FlagExplanationOut,
    ProduceTermsRequest,
    PromoteThesisRequest,
    RatifiedFactOut,
    RatifyFactRequest,
    ScoredMemberOut,
    SecurityMatchOut,
    ThesisDetail,
    WorkbenchScored,
)
from db.session import connect
from domain.enums import Authorship
from domain.extraction import ExtractedFact
from domain.settings import get_settings
from domain.thesis import Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import extract_for_security
from ingest.edgar.fulltext import DiscoveryUnavailable
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from llm.chain_decomposition import decompose_narrative, narrate_placements, research_tail_sweep
from llm.client import LLMClient
from llm.flag_explanation import explain_flag
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from workbench.chain_draft import (
    PlacementStatus,
    proposed_from_decomposition,
    resolve_discovered_chain,
)
from workbench.discovery import (
    DiscoveryNoTerms,
    discovered_names,
    discovery_context,
    run_discovery,
)
from workbench.draft_jobs import DraftError, DraftInFlight, get_job, start_draft_job
from workbench.research_runner import run_research
from workbench.scoring import score_thesis
from workbench.term_set import produce_term_set, stamp_edited_term_set

router = APIRouter(prefix="/workbench", tags=["workbench"])


@router.get("/theses/{thesis_id}/scored", response_model=WorkbenchScored)
def get_scored(
    asof: date = Query(..., description="as-of date; the scores use no data knowable after it"),
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> WorkbenchScored:
    """Re-derive the per-name Workbench scores live at ``asof`` — a READ-ONLY path (Option B; nothing
    persists). Mirrors the call endpoint: load the thesis (404 + its tenant), thread ``thesis.tenant_id``
    into every scoring fact read so a production thesis scores off production's facts."""
    pit = PointInTimeData(conn, asof=asof, tenant_id=thesis.tenant_id)
    scored = score_thesis(pit, thesis)
    sec_ids = {m.security_id for m in scored}
    cik_for = master.ciks_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    ticker_for = master.tickers_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    return WorkbenchScored(
        thesis_id=thesis.id,
        asof=asof,
        segments=list(thesis.segments),
        members=[ScoredMemberOut.from_scored(m, cik_for, ticker_for) for m in scored],
    )


@router.get("/securities", response_model=list[SecurityMatchOut])
def search_securities(
    q: str = Query("", description="ticker or name fragment; a discovery net over the master"),
    limit: int = Query(10, ge=1, le=50),
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> list[SecurityMatchOut]:
    """Search the current tenant's security master for names to PLACE into a basket (the authoring
    typeahead, Slice 4b). A DISCOVERY NET (INVARIANT #2): exact master rows for the operator to pick from
    — never a fuzzy decision, never an ingest (no ``allow_live``). No match -> ``[]``; the operator's pick
    carries the exact ``security_id``. Tenant from the deployment resolver (which universe to author in).
    """
    matches = master.search(conn, q, tenant_id=tenant_id, limit=limit)
    return [
        SecurityMatchOut(security_id=m.id, ticker=m.ticker, name=m.name, cik=m.cik) for m in matches
    ]


@router.get("/securities/{security_id}/extract", response_model=list[ExtractedFact])
def extract_scoring_facts(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> list[ExtractedFact]:
    """Auto-EXTRACT candidate scoring facts for a security from its latest SEC 10-Q/10-K (Slice hybrid-1) —
    the three-tier hybrid: AUTO pre-fills the clean facts, FLAG carries the raw value + a detected risk + the
    located passage (the operator ratifies the composition), HUMAN (purity) is LOCATED only and never
    auto-valued. An EXPLICIT operator action (cache-first, live SEC), never fired on a render. The extractor
    never DECIDES — the operator confirms (hybrid-2). Requires ``ALPHADECK_USER_AGENT`` (SEC etiquette).
    """
    cik = master.ciks_for(conn, {security_id}, tenant_id=tenant_id).get(security_id)
    if not cik:
        raise HTTPException(status_code=404, detail="no CIK for this security — resolve it first")
    try:
        return extract_for_security(EdgarClient(allow_live=True), cik)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — SEC unreachable / no UA / parse hiccup -> a clear 502, not a 500
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc


@router.post("/facts/explain", response_model=FlagExplanationOut)
def explain_flag_candidate(
    candidate: ExtractedFact,
    llm: LLMClient = Depends(get_llm_client),
) -> FlagExplanationOut:
    """Draft a plain-English explanation of a FLAG candidate, grounded in its located passage — the one LLM
    seam (M4b). A DISPLAY aid shown ALONGSIDE the raw passage; it NEVER becomes a fact.

    Note what is absent: no ``get_conn``, no tenant, no write. The explanation rides a separate rail that
    dead-ends at the screen — the ratified number can only ever come from the operator's typed field on
    ``/facts`` (INVARIANT #3). The prompt asks the model not to state the final value; this missing
    connection is what guarantees it can't become one.

    Fail-open by contract: any LLM trouble (no ``ANTHROPIC_API_KEY``, timeout, SDK error, or the model
    declining to ground it) returns 200 with ``{explanation: "", grounded: false}`` — NEVER a 5xx. The facts
    panel renders identically to today. (FLAG-only: a non-FLAG candidate returns the same empty signal.)
    """
    explanation, grounded = explain_flag(llm, candidate)
    return FlagExplanationOut(explanation=explanation, grounded=grounded)


@router.post("/theses", response_model=ThesisDetail)
def promote(
    req: PromoteThesisRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> ThesisDetail:
    """Promote a structured thesis to the Board (Incubating) — the app's FIRST mutation. Create (``id``
    null) or update (``id`` set); the value-chain structure (segments + placements + authorship) persists
    via ``thesis_repo.upsert`` (the existing operational save path). The tenant comes from the deployment
    resolver, NOT the body. Scores are never sent and never persist — they re-derive on read.

    Two write-side guards (INVARIANT #2): ``authored_by`` is HONORED from the body — the human path sends
    ``operator_set``; the S5 draft/ratify path sends ``system_drafted`` (a kept draft) or ``operator_edited``
    (an edited one) — not coerced, so a drafted placement stays drafted until the operator ratifies it. And
    every placed ``security_id`` must be an EXACT member of this tenant's master (fail-closed — a
    caller-supplied id is never trusted), the single point where bound #2 is enforced now that the S5 drafter
    returns a draft and writes nothing itself."""
    try:
        thesis = Thesis(  # the Slice-1 segment-consistency validator runs here
            id=req.id or uuid4(),
            tenant_id=tenant_id,
            name=req.name,
            narrative=req.narrative,
            ticker=req.ticker,
            # `authored_by` is honored, not coerced: Pydantic has already validated each value against the
            # `Authorship` enum (an out-of-enum value is a 422 at parse time), so the field IS the authorship
            # seam. INVARIANT #1 is held by the membership check below + the LLM never writing — never by
            # flattening authorship here.
            basket=list(req.basket),
            segments=req.segments,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Bound #2, fail-closed: a placed security must be an EXACT member of this tenant's master (mirrors the
    # ratify write-side check). The id is caller-supplied — the operator's pick or an S5 draft's resolved
    # placement — so a foreign / hallucinated id must NEVER reach the spine. A null id (unplaced name) is OK.
    for m in thesis.basket:
        if m.security_id is not None and not master.exists(
            conn, m.security_id, tenant_id=tenant_id
        ):
            raise HTTPException(
                status_code=404,
                detail=f"basket member {m.ticker!r} references a security not in this tenant's master",
            )
    thesis_repo.upsert(conn, thesis)
    conn.commit()
    return ThesisDetail.from_thesis(thesis)


@router.post("/theses/{thesis_id}/terms", response_model=ThesisDetail)
def produce_terms(
    req: ProduceTermsRequest | None = None,
    conn: psycopg.Connection = Depends(get_conn),
    keyword_llm: LLMClient = Depends(get_keyword_client),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Produce + PERSIST the thesis's tiered discovery term set — the SIGNAL/BROAD keywords the EDGAR precision
    filter reads. Two sources, two authorities: the operator's ``seeds`` (canonical compounds) are anchored as
    ``operator_set`` SIGNAL — the RECALL guarantor against keyword-gen's non-determinism — and keyword-gen
    PROPOSES the rest, which a DETERMINISTIC guard tiers (``system_drafted``; the "is this discriminating?"
    decision is OFF the LLM, ``workbench.term_set``). This is the WRITER seam: the LLM lives HERE, never in
    ``promote`` (the pure structured writer).

    REGENERABLE + CONVERGENT: a re-POST PRESERVES every operator-authored entry — ``operator_set`` seeds AND
    ``operator_edited`` promotions/demotions (from the edit-UI), each VERBATIM (term + tier + authorship) — while
    RE-ROLLING only the ``system_drafted`` LLM-proposed terms. So the inspect-and-tune loop re-rolls the
    augmentation without ever dropping an operator decision (a demoted SIGNAL→BROAD term comes back BROAD, not
    re-promoted). New ``req.seeds`` are added as fresh SIGNAL. Returns the thesis so the operator can INSPECT the
    stored SIGNAL/BROAD split. Fail-open: no key / blank narrative + no seeds → an empty set is stored (the draft
    then 503s "term set is empty" — surfaced, never a silent recall fallback). It sources NO number (#3) — terms
    only — and writes ONLY ``term_set`` (the narrow ``set_term_set``)."""
    operator_terms = [
        e for e in thesis.term_set if e.authored_by is not Authorship.SYSTEM_DRAFTED
    ]  # operator_set ∪ operator_edited — preserved verbatim; only system_drafted re-rolls
    entries = produce_term_set(
        keyword_llm,
        thesis.narrative,
        seeds=(req.seeds if req else []),
        operator_terms=operator_terms,
    )
    thesis_repo.set_term_set(conn, thesis.id, entries)
    conn.commit()
    return ThesisDetail.from_thesis(thesis.model_copy(update={"term_set": entries}))


@router.put("/theses/{thesis_id}/terms/edit", response_model=ThesisDetail)
def edit_terms(
    req: EditTermsRequest,
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """SAVE the operator's manually-edited term set DIRECTLY — NO LLM (the LLM lives only in ``POST .../terms``;
    this mirrors LLM-out-of-promote, a structural boundary, not a convention). The operator adds a seed
    (→ ``operator_set`` SIGNAL), removes a term, promotes BROAD→SIGNAL or demotes SIGNAL→BROAD
    (→ ``operator_edited``); an UNTOUCHED ``system_drafted`` BROAD term keeps its authorship so a later
    regenerate can re-roll it. Authorship is STAMPED server-side by diffing the stored set
    (``stamp_edited_term_set``), never trusted from the body. A full-set replace via the narrow
    ``set_term_set`` (the SOLE writer; ``upsert`` never names ``term_set`` — the structural wipe-guard stays
    intact). Sources NO number (#3) — structure/config only. 422 on an empty term or a case-insensitive
    duplicate. An empty ``terms`` clears the set (a visible operator choice; the draft then 503s "term set is
    empty")."""
    try:
        entries = stamp_edited_term_set(thesis.term_set, [(t.term, t.tier) for t in req.terms])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thesis_repo.set_term_set(conn, thesis.id, entries)
    conn.commit()
    return ThesisDetail.from_thesis(thesis.model_copy(update={"term_set": entries}))


def execute_draft(
    conn: psycopg.Connection,
    research_llm: LLMClient,
    decompose_llm: LLMClient,
    edgar: EdgarClient,
    thesis: Thesis,
) -> ChainDraftOut:
    """The narrative→chain draft PIPELINE — the SECOND LLM seam (S5), EDGAR-FIRST. Unchanged by the async-job
    move; it runs inside a background job now (``start_draft_chain`` → ``workbench.draft_jobs``), not held open.

    Discovery is OFF the model: (1) the thesis's PERSISTED term set (SIGNAL seeds + BROAD terms, produced
    out-of-band by ``POST .../terms``) is read — no keyword-gen on the draft path; (2) the deterministic EDGAR
    full-text enumerator finds the US-listed universe by CIK and ``classify`` splits PLACED (>=1 SIGNAL seed) vs
    the lower-confidence VERIFY tier (``run_discovery``); (3) a directed web-search TAIL-SWEEP
    (``research_tail_sweep``, Opus) adds only the foreign / brand-new names EFTS structurally can't see, given
    the already-found list. Their combined synthesis is threaded as CONTEXT into the DECOMPOSE call (Sonnet
    ORGANIZES the stable name set into segments + thesis-fit prose — it never enumerates). Then
    ``resolve_discovered_chain`` reconciles the organizer's layout against the discovered universe PER CIK: a
    matched name is PLACED / VERIFY by its CIK's exact membership (the cleanest INVARIANT #2), an off-universe
    name falls to the master resolver, and every discovered CIK the organizer dropped is appended to a
    'Discovered' bucket — completeness is the deterministic layer's, never the organizer's to lose. A final
    fail-open narration step then writes thesis-fit prose for the reconciler-appended names the organizer never
    narrated (so EVERY placed/verify name carries reasoning); each name also carries its matched discovery
    term(s) as provenance. Both are display strings — no number (#3), nothing persisted.

    Only the expensive Opus TAIL-SWEEP runs behind the cost-safety wrapper (``workbench.research_runner``): its
    TTL cache keyed by thesis + narrative-hash (``llm_research_cache_ttl_s``; 0 = always fresh) makes a re-draft
    free. Its amplifiers stay closed: the SDK research client runs ``max_retries=0``. EFTS is free + deterministic
    (no guard needed). (The in-flight 409 guard now lives at the JOB layer — one running draft per thesis —
    ``start_draft_chain``; ``run_research``'s own guard is harmless defense-in-depth and owns the TTL cache.)

    RESPONSE-ONLY: it returns a draft and persists NOTHING (the operator's promote is the only writer; it sources
    NO number — INVARIANT #2/#3). DISCOVERY IS COMPLETENESS-OR-FAIL: every not-ready / can't-enumerate state
    RAISES (``DiscoveryNoTerms`` / the ``DiscoveryUnavailable`` base = Degraded/Empty) — the JOB RUNNER maps these
    to a VISIBLE failed job (never a silent recall draft, #9). The tail-sweep still fails-open to None (an
    additive corner), and a failed DECOMPOSE yields an EMPTY draft (a done-but-empty job, today's benign note).
    """
    universe = run_discovery(conn, edgar, thesis.term_set, tenant_id=thesis.tenant_id)
    sweep = (
        run_research(  # fail-open -> None; TTL cache; the job layer owns the in-flight 409 guard
            thesis.id,
            thesis.narrative,
            ttl_s=get_settings().llm_research_cache_ttl_s,
            run=lambda: research_tail_sweep(
                research_llm, thesis.narrative, discovered_names(universe)
            ),
        )
    )
    context = discovery_context(universe, sweep)
    segments = proposed_from_decomposition(
        decompose_narrative(decompose_llm, thesis.narrative, research_context=context)
    )
    chain = resolve_discovered_chain(conn, segments, universe, tenant_id=thesis.tenant_id)

    # Fill thesis-fit prose for the PLACED + VERIFY names the ORGANIZER didn't narrate — the deterministic
    # reconciler appends discovered CIKs with prose="" (it owns completeness, not prose). Both tiers are
    # PROMOTABLE (a verify name the operator adds becomes a basket member, and promote — the structured writer,
    # no LLM — carries whatever prose it had at draft time), so both must carry reasoning or the gap just moves
    # one tier down onto the names acted on by hand. AMBIGUOUS/ABSENT are left alone. narrate_placements BATCHES
    # (a large universe would truncate one call to nothing) and logs any batch failure (#9 — visible, never a
    # silent empty). FAIL-OPEN: a narration miss leaves prose empty, never drops a name; a DISPLAY string only —
    # no number (#3), nothing persisted (response-only).
    def _needs_prose(p) -> bool:
        return (
            p.status in (PlacementStatus.PLACED, PlacementStatus.VERIFY)
            and bool(p.name)
            and not p.prose.strip()
        )

    needs = [
        {"name": p.name, "ticker": p.ticker, "segment": p.segment}
        for p in chain.placements
        if _needs_prose(p)
    ]
    placements = chain.placements
    if needs:
        narrated = narrate_placements(decompose_llm, thesis.narrative, needs)
        if narrated:
            placements = [
                (
                    p.model_copy(update={"prose": narrated[p.name]})
                    if (_needs_prose(p) and p.name in narrated)
                    else p
                )
                for p in chain.placements
            ]
    return ChainDraftOut(thesis_id=thesis.id, segments=chain.segments, placements=placements)


@router.post("/theses/{thesis_id}/draft-chain", status_code=202, response_model=DraftJobRef)
def start_draft_chain(
    research_llm: LLMClient = Depends(get_research_client),
    decompose_llm: LLMClient = Depends(get_decompose_client),
    edgar: EdgarClient = Depends(get_edgar_client),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> DraftJobRef:
    """KICK OFF the narrative→chain draft as a background JOB and return immediately (**202** + ``job_id``); the
    FE polls ``GET .../draft-chain/jobs/{job_id}`` for the result. The draft takes minutes (EDGAR discovery + the
    Opus tail-sweep + decompose + narrate); held open as one request it blew past nginx's 300s proxy timeout —
    the browser 504'd while the backend kept billing. Only the DELIVERY changed; ``execute_draft`` is unchanged.

    The IN-FLIGHT 409 guard lives HERE now (one running draft per thesis — ``DraftInFlight`` → HTTP 409, so a
    double-click / stray retry can never launch a parallel Opus pass). The job runs in a daemon thread that opens
    its OWN DB connection (it OUTLIVES this request — the ``get_thesis_or_404`` conn is closed once the 202 is
    sent). Discovery-not-ready / unexpected faults become a VISIBLE *failed* job on the poll (never a silent empty
    draft, #9); a benign fail-open (no key / the model declined) is a *done* job with an empty draft. RESPONSE-ONLY
    (the job writes only its in-memory result — no fact, no promote)."""

    def _run() -> ChainDraftOut:
        own = connect()  # the job outlives the request; the request-scoped conn is already closed
        try:
            return execute_draft(own, research_llm, decompose_llm, edgar, thesis)
        except DiscoveryNoTerms as exc:  # SPECIFIC first (it subclasses DiscoveryUnavailable)
            raise DraftError(
                "term set is empty — produce or seed it first (POST .../terms or the edit UI)"
            ) from exc
        except DiscoveryUnavailable as exc:  # Degraded / Empty / enumerate fault
            raise DraftError(
                "discovery unavailable — couldn't enumerate the universe; please retry"
            ) from exc
        finally:
            own.close()

    try:
        job_id = start_draft_job(thesis.id, _run)
    except DraftInFlight as exc:
        raise HTTPException(
            status_code=409, detail="a draft is already running for this thesis"
        ) from exc
    return DraftJobRef(job_id=job_id, status="running")


@router.get("/theses/{thesis_id}/draft-chain/jobs/{job_id}", response_model=DraftJobStatus)
def get_draft_chain_job(thesis_id: UUID, job_id: str) -> DraftJobStatus:
    """POLL a kicked-off draft job. ``done`` → the ``result`` (a ChainDraftOut); ``failed`` → an operator-facing
    ``error`` (discovery-not-ready, a timeout, or an unexpected fault — VISIBLE, #9). **404** if the job is
    unknown / expired, or the registry was wiped by a restart — the FE shows a visible "draft was lost" (never an
    infinite spinner). The job_id must belong to this thesis."""
    job = get_job(job_id)
    if job is None or job.thesis_id != str(thesis_id):
        raise HTTPException(
            status_code=404,
            detail="draft job not found (it may have expired or the server restarted)",
        )
    return DraftJobStatus(job_id=job.job_id, status=job.status, result=job.result, error=job.error)


@router.post("/facts", response_model=RatifiedFactOut)
def ratify_fact(
    req: RatifyFactRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> RatifiedFactOut:
    """Ratify an extracted candidate -> write the scoring fact (hybrid-2a) — the app's first fact-WRITE. The
    operator confirms/edits a candidate (AUTO as-is, FLAG the composition, HUMAN purity the value); this
    persists it via the existing ``ingest_*`` so the meter re-derives on the next scored read.

    WRITE-SIDE TENANT DISCIPLINE: the security must be in the CURRENT tenant's master (fail-closed) — the
    tenant is the deployment resolver's, but the ``security_id`` is caller-supplied, so a foreign/unknown id
    must not write a junk fact. ``source`` is preserved (the candidate's basis, e.g. ``10-k-segment``);
    ``ratified_by`` is stamped "operator"; the fact is append-only (a re-ratify is a new row, latest-wins).
    """
    if not master.exists(conn, req.security_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="security not in this tenant's master")
    common = dict(
        source=req.source,
        source_ref=req.source_ref,
        event_date=req.event_date,
        note=req.note,
        ratified_by="operator",  # the human ratify path — stamped, not taken from the body
        tenant_id=tenant_id,
    )
    if req.fact_type == "revenue_mix":
        fid = ingest_revenue_mix(
            conn, req.security_id, segment_label=req.segment_label, mix_pct=req.mix_pct, **common
        )
    elif req.fact_type == "shares_outstanding":
        fid = ingest_shares_outstanding(conn, req.security_id, shares=req.shares, **common)
    else:  # cash_burn
        fid = ingest_cash_burn(
            conn,
            req.security_id,
            cash_usd=req.cash_usd,
            quarterly_burn_usd=req.quarterly_burn_usd,
            **common,
        )
    conn.commit()
    return RatifiedFactOut(fact_id=fid, fact_type=req.fact_type)
