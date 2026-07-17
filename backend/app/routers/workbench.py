from __future__ import annotations

import logging
import math
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
    get_purity_client,
    get_research_client,
    get_thesis_or_404,
    get_tier_rec_client,
)
from app.schemas_api import (
    AutoConfirmOut,
    AutoConfirmRequest,
    ChainDraftOut,
    DraftCoverageOut,
    DraftJobRef,
    DraftJobStatus,
    DraftReportOut,
    EditTermsRequest,
    FlagExplanationOut,
    PriceIngestOut,
    ProduceTermsRequest,
    PromoteThesisRequest,
    RatifiedFactOut,
    RatifyFactRequest,
    SavedRunSummary,
    ScoredMemberOut,
    SecurityMatchOut,
    ThesisDetail,
    TierRecommendation,
    TriageSessionEnvelope,
    TriageSessionGet,
    TriageSessionPut,
    WorkbenchScored,
)
from db.session import connect
from domain.enums import Authorship, TermTier
from domain.extraction import ExtractedFact, Tier
from domain.settings import get_settings
from domain.thesis import Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import extract_for_security
from ingest.edgar.fulltext import DiscoveryUnavailable
from ingest.prices.eod_loader import latest_bar_date
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from llm.chain_decomposition import (
    TailSweepStatus,
    decompose_narrative,
    narrate_placements,
    research_tail_sweep,
)
from llm.client import LLMClient
from llm.flag_explanation import explain_flag
from llm.purity_estimate import propose_purity
from llm.tier_recommendation import recommend_tiers
from repositories import thesis_repo
from securities import coherence, master
from signals.base import PointInTimeData
from workbench import run_loader, triage_store
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
from workbench.draft_jobs import DraftError, DraftInFlight, DraftJob, get_job, start_draft_job
from workbench.draft_run_log import write_draft_run_log
from workbench.enrichment import enrich_for_ciks
from workbench.research_runner import run_research
from workbench.scoring import score_thesis
from workbench.term_set import produce_term_set, stamp_edited_term_set

_log = logging.getLogger("alphadeck.workbench")

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
    ident_for = master.identity_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    return WorkbenchScored(
        thesis_id=thesis.id,
        asof=asof,
        segments=list(thesis.segments),
        members=[ScoredMemberOut.from_scored(m, cik_for, ticker_for, ident_for) for m in scored],
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
    thesis_id: UUID | None = Query(
        None,
        description="if given, propose a GROUNDED purity % for this thesis (SURFACE 1b); purity-only",
    ),
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
    purity_llm: LLMClient = Depends(get_purity_client),
) -> list[ExtractedFact]:
    """Auto-EXTRACT candidate scoring facts for a security from its latest SEC 10-Q/10-K (Slice hybrid-1) —
    the three-tier hybrid: AUTO pre-fills the clean facts, FLAG carries the raw value + a detected risk + the
    located passage (the operator ratifies the composition), HUMAN (purity) is LOCATED only and never
    auto-valued. An EXPLICIT operator action (cache-first, live SEC), never fired on a render. The extractor
    never DECIDES — the operator confirms (hybrid-2). Requires ``ALPHADECK_USER_AGENT`` (SEC etiquette).

    PURITY ESTIMATE (SURFACE 1b): with ``thesis_id``, the grounded purity seam proposes an UNVERIFIED
    on-thesis % for the revenue_mix candidate — read ONLY from its located segment passage, with the thesis
    narrative selecting the segment. It attaches ``value`` + ``estimate_source="llm_proposed"`` (the passage
    stays on the candidate), never a fact until the operator ratifies. PURITY-ONLY: ``thesis_id`` touches no
    other candidate, and its absence (or a fail-open decline) leaves purity as today's HUMAN (located, no value).
    """
    cik = master.ciks_for(conn, {security_id}, tenant_id=tenant_id).get(security_id)
    if not cik:
        raise HTTPException(status_code=404, detail="no CIK for this security — resolve it first")
    try:
        cands = extract_for_security(EdgarClient(allow_live=True), cik)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — SEC unreachable / no UA / parse hiccup -> a clear 502, not a 500
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc

    # Thesis-aware purity ESTIMATE — the ONLY thesis-scoped branch (purity's on-thesis segment depends on the
    # narrative; shares/cash are thesis-independent). Fail-open: no thesis / no key / ungrounded -> purity stays
    # today's HUMAN. The proposal is UNVERIFIED and carries its passage; it becomes a fact only on the operator's
    # ratify (never here — this endpoint writes nothing).
    thesis = thesis_repo.get(conn, thesis_id) if thesis_id is not None else None
    if thesis is not None:
        for c in cands:
            if c.fact_type != "revenue_mix":
                continue
            prop = propose_purity(purity_llm, thesis.narrative, c)
            if prop is not None:
                c.value = prop.pct
                c.estimate_source = "llm_proposed"
                c.note = (
                    f"LLM-PROPOSED purity (UNVERIFIED — confirm or override): {prop.reason} "
                    f"[on-thesis segment: {prop.segment}]. Grounded in the located segment passage."
                )
    return cands


@router.post("/securities/{security_id}/ingest-prices", response_model=PriceIngestOut)
def ingest_security_prices(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> PriceIngestOut:
    """Pull EOD price bars for ONE security — the price leg DECOUPLED from the back-half ingest, so the
    finalize screen can complete a name (real market cap, live archetype hint) BEFORE the operator
    promotes. This endpoint WRITES (``fact_price_eod``) — deliberately, per explicit click: price bars are
    FEED data (the same class as Form 4s), never operator-ratified facts; what they feed (the cap, the
    hint) stays display/recommendation until the operator acts.

    Bounded + polite by construction: one name per call (the tightest bound — the section button fans out
    client-side over a section's members, never the draft); INCREMENTAL (only bars newer than the latest
    stored one append — a re-click adds zero rows); CACHE-FIRST (``force_refresh=False``: a first pull on
    a fresh name is a cache miss and fetches live; a same-day re-click serves from the cache — the daily
    cron, not this endpoint, owns force-refresh). No-lookahead: ``recorded_at`` = now, never backdated.
    Shares ONE implementation with ``pipeline.ingest_thesis`` (``ingest_bars_for_security``)."""
    sec = master.get(conn, security_id, tenant_id=tenant_id)
    if sec is None:
        raise HTTPException(status_code=404, detail="unknown security for this tenant")
    if not sec.ticker:
        raise HTTPException(
            status_code=422, detail="no listed ticker — there is no price line to pull"
        )
    try:
        bars = ingest_bars_for_security(conn, sec, tenant_id=tenant_id)
        conn.commit()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — source unreachable -> a clear 502, never a silent 500
        conn.rollback()
        raise HTTPException(status_code=502, detail=f"price pull failed: {exc}") from exc
    return PriceIngestOut(
        security_id=security_id,
        ticker=sec.ticker,
        bars_appended=bars.appended,
        bars_reversioned=bars.reversioned,
        latest_bar=latest_bar_date(conn, security_id, tenant_id=tenant_id),
    )


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

    Write-side guards (INVARIANT #2): ``authored_by`` is HONORED from the body — the human path sends
    ``operator_set``; the S5 draft/ratify path sends ``system_drafted`` (a kept draft) or ``operator_edited``
    (an edited one) — not coerced, so a drafted placement stays drafted until the operator ratifies it. Every
    placed ``security_id`` must be an EXACT member of this tenant's master (fail-closed — a caller-supplied
    id is never trusted), the single point where bound #2 is enforced now that the S5 drafter returns a draft
    and writes nothing itself. And the id is CANONICALIZED: a multi-sibling CIK's non-primary row (a foreign
    ordinary / warrant / dual-class sibling the draft happened to surface) is re-pointed to the CIK's primary
    instrument before the spine write (``master.canonicalize_ids`` — the same pick as ``ids_for_ciks``), so
    the basket stores the instrument the operator actually trades; the response carries the canonical
    ticker."""
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
    # The guard's second half — CANONICALIZE (the operator-ratified coerce-all rule): the spine stores each
    # CIK's PRIMARY instrument regardless of which sibling the draft surfaced. ``exists`` proves the id is *a*
    # master row; this makes it the RIGHT one (ASML never ASMLF, KTTA never KTTAW) — everything downstream
    # (shown ticker, the price cache, the position MONITOR tracks) anchors to security_id, so a non-primary
    # sibling here would be a stably-wrong instrument a real trade rides on. Same pick as ``ids_for_ciks``,
    # so draft-time and promote-time resolution can never disagree. Visible, never silent: each coercion is
    # logged and the response (the FE re-snapshot) carries the canonical ticker. Accepted consequence
    # (gate-1): a DELIBERATE non-primary line (e.g. intentionally trading the foreign ordinary) is
    # unrepresentable in v1.
    canon = master.canonicalize_ids(
        conn, [m.security_id for m in thesis.basket if m.security_id], tenant_id=tenant_id
    )
    if canon:
        for i, m in enumerate(thesis.basket):
            hit = canon.get(m.security_id) if m.security_id else None
            if hit is None:
                continue
            primary_id, primary_ticker = hit
            _log.info(
                "promote: canonicalized %s -> %s for thesis %s (non-primary sibling re-pointed)",
                m.ticker,
                primary_ticker,
                thesis.id,
            )
            thesis.basket[i] = m.model_copy(
                update={"security_id": primary_id, "ticker": primary_ticker}
            )
    # The IDENTITY-COHERENCE guard (the misbind class, fail-closed): after canonicalize, a member whose
    # shown ticker STILL disagrees with its bound master row is either another company's label riding the id
    # (cross-company — how KLAC's label rode LRCX's security_id) or a label no current row carries
    # (label-drift). Persisting that pair silently would corrupt every downstream read (facts pull by the
    # bound id; the operator reads the label), and choosing a side is not promote's judgment to make (#2) —
    # so it 422s NAMING BOTH IDENTITIES, unless the operator explicitly listed the member's security_id in
    # ``identity_overrides`` (per-member, logged — the gate idiom: friction + a record, never a silent
    # pass). A SIBLING disagreement (same CIK, another line's label) is ALIGNED instead, mirroring the
    # canonicalize coerce-all rule the operator already ratified — same company, the spine's label follows
    # the bound instrument.
    findings = coherence.classify_members(
        conn, [(m.ticker, m.security_id) for m in thesis.basket], tenant_id=tenant_id
    )
    overrides = set(req.identity_overrides)
    blocked: list[str] = []
    for i, (m, f) in enumerate(zip(thesis.basket, findings)):
        if f.kind is coherence.CoherenceKind.SIBLING:
            _log.info(
                "promote: sibling label %s aligned to bound %s for thesis %s",
                m.ticker,
                f.bound_ticker,
                thesis.id,
            )
            thesis.basket[i] = m.model_copy(update={"ticker": f.bound_ticker})
        elif f.kind in (
            coherence.CoherenceKind.CROSS_COMPANY,
            coherence.CoherenceKind.LABEL_DRIFT,
        ):
            if m.security_id in overrides:
                _log.warning(
                    "promote: identity override ACCEPTED for thesis %s: shown %r stays bound to "
                    "%s (%s, CIK %s) — %s",
                    thesis.id,
                    m.ticker,
                    f.bound_ticker,
                    f.bound_name,
                    f.bound_cik,
                    f.kind,
                )
            else:
                blocked.append(
                    f"{m.ticker!r} is bound to {f.bound_ticker} ({f.bound_name}, CIK {f.bound_cik})"
                    f" — {f.detail}"
                )
    if blocked:
        raise HTTPException(
            status_code=422,
            detail=(
                "identity mismatch: "
                + "; ".join(blocked)
                + ". Re-pick the security for each member, or resend listing the member's "
                "security_id in identity_overrides to bind it deliberately (the override is logged)."
            ),
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


@router.post("/theses/{thesis_id}/recommend-tiers", response_model=list[TierRecommendation])
def recommend_tiers_endpoint(
    tier_rec_llm: LLMClient = Depends(get_tier_rec_client),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> list[TierRecommendation]:
    """Recommend a tier (signal/broad) + a one-line reason per term in the thesis's term set — the LLM
    RECOMMENDS, the operator DECIDES (INVARIANT #10). DISPLAY-ONLY + RESPONSE-ONLY: no writer is called
    (``set_term_set`` / ``upsert`` never appear), so a recommendation can NEVER become a persisted tier — it
    rides on its OWN wire type (``list[TierRecommendation]``), never on ``ThesisDetail.term_set``, and the
    operator confirms it via the EXISTING tier toggle (``PUT .../terms/edit``), where ``stamp_edited_term_set``
    stamps ``operator_edited`` (never an LLM-authored SIGNAL). The model judges each term INDEPENDENTLY of its
    current tier; the FE does the agree/disagree compare. OFF ``produce_term_set``'s determinism path (advisory
    metadata, never a tier the producer applies — recall stays sacred, #9). Fail-open: no key / model trouble ->
    ``[]`` (the chips render with no recommendation). Sources NO number (#3) — a tier label + a reason string.
    """
    recs = recommend_tiers(tier_rec_llm, thesis.narrative, [e.term for e in thesis.term_set])
    by_key = {r["term"].strip().lower(): r for r in recs}
    out: list[TierRecommendation] = []
    for (
        e
    ) in (
        thesis.term_set
    ):  # align to the stored set, preserving its order; only terms the model returned
        r = by_key.get(e.term.strip().lower())
        if r is not None:
            out.append(
                TierRecommendation(
                    term=e.term, recommended_tier=TermTier(r["tier"]), reason=r["reason"]
                )
            )
    return out


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

    RESPONSE-ONLY for the THESIS SPINE: it returns a draft and persists no basket member / fact / number — the
    operator's promote is the only spine writer (INVARIANT #2/#3). It DOES enrich the discovered names' MASTER
    identity columns (sector / exchange / status, machine-parsed from submissions with an enrichment basis,
    Slice 2) — an operational universe write that touches no spine, no fact, and no number, and lets the chain
    reconciler's status-gate read a fresh listing status. DISCOVERY IS COMPLETENESS-OR-FAIL: every not-ready /
    can't-enumerate state
    RAISES (``DiscoveryNoTerms`` / the ``DiscoveryUnavailable`` base = Degraded/Empty) — the JOB RUNNER maps these
    to a VISIBLE failed job (never a silent recall draft, #9). The tail-sweep still fails-open to None (an
    additive corner), and a failed DECOMPOSE yields an EMPTY draft (a done-but-empty job, today's benign note).
    """
    universe = run_discovery(conn, edgar, thesis.term_set, tenant_id=thesis.tenant_id)
    # Lazy IDENTITY enrichment (Slice 2): fill sector / exchange / status onto the discovered names' master rows
    # from their submissions, BEFORE resolution — so the chain reconciler's status-gate reads a fresh listing
    # status. Per-CIK fail-visible (a miss leaves a row un-enriched → abstains); the network stays OUT of the
    # pure resolver. Writes only the master's identity columns (machine-parsed, an enrichment basis) — no thesis
    # spine, no fact, no number (#1/#3); the operator's promote remains the only spine writer.
    enrich_for_ciks(conn, edgar, {**universe.placed, **universe.verify}, tenant_id=thesis.tenant_id)
    # The sweep's OUTCOME rides the draft report (the tri-state — a lost foreign/ADR tail is no longer
    # indistinguishable from "no foreign names exist"). run_research's Callable contract stays str|None (its
    # cache-only-non-None property is load-bearing), so the status travels via a cell the thunk writes; a TTL
    # cache HIT never runs the thunk but IS a prior successful run -> "ran".
    sweep_status: list[TailSweepStatus] = ["skipped"]

    def _sweep() -> str | None:
        ts = research_tail_sweep(research_llm, thesis.narrative, discovered_names(universe))
        sweep_status[0] = ts.status
        return ts.synthesis

    sweep = (
        run_research(  # fail-open -> None; TTL cache; the job layer owns the in-flight 409 guard
            thesis.id,
            thesis.narrative,
            ttl_s=get_settings().llm_research_cache_ttl_s,
            run=_sweep,
        )
    )
    tail_status: TailSweepStatus = "ran" if sweep is not None else sweep_status[0]
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
    narration_filled = 0
    if needs:
        # narrate_placements returns {name: {"prose", "off_thesis"}} — the display prose AND the narrator's
        # on/off-thesis opinion (a display recommendation, #10; the flagged name stays placed). This merge is the
        # ONE seam narration lands on placements: off_thesis is set here (not at resolution) and defaults False
        # everywhere else. Fail-open: a narration miss leaves prose empty + off_thesis False (never flag on a miss).
        narrated = narrate_placements(decompose_llm, thesis.narrative, needs)
        if narrated:
            placements = [
                (
                    p.model_copy(
                        update={
                            "prose": narrated[p.name].get("prose", ""),
                            "off_thesis": bool(narrated[p.name].get("off_thesis")),
                        }
                    )
                    if (_needs_prose(p) and p.name in narrated)
                    else p
                )
                for p in chain.placements
            ]
        # The fill count (M of N) rides the report — a partial narration was a log line only (#9 rule 2).
        narration_filled = sum(
            1 for n in needs if (narrated.get(n["name"], {}).get("prose") or "").strip()
        )

    # The run's honesty report: every formerly-silent recall-loss mode, named (#9 rules 2/3). Display-only RUN
    # state riding the response — value-free (#3), never persisted (response-only stays intact).
    cov = universe.coverage
    report = DraftReportOut(
        coverage=DraftCoverageOut(
            pages_ok=cov.pages_ok if cov else 0,
            pages_attempted=cov.pages_attempted if cov else 0,
            failed_terms=list(cov.failed_terms) if cov else [],
        ),
        capped_terms=list(universe.capped_terms),
        tail_sweep=tail_status,
        narration_needed=len(needs),
        narration_filled=narration_filled,
    )
    return ChainDraftOut(
        thesis_id=thesis.id, segments=chain.segments, placements=placements, report=report
    )


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
    (the job writes only its in-memory result — no fact, no promote). A completed job additionally dumps a
    WRITE-ONLY run-of-record artifact (``data/draft_runs/`` — the DISCOVER stage's ``calls``-log analogue:
    the term set as used, the dials, the full draft); nothing in the app reads it, and a failed write is
    logged + swallowed, never a failed draft."""

    def _run() -> ChainDraftOut:
        own = connect()  # the job outlives the request; the request-scoped conn is already closed
        try:
            return execute_draft(own, research_llm, decompose_llm, edgar, thesis)
        except DiscoveryNoTerms as exc:  # SPECIFIC first (it subclasses DiscoveryUnavailable)
            raise DraftError(
                "term set is empty — produce or seed it first (POST .../terms or the edit UI)"
            ) from exc
        except DiscoveryUnavailable as exc:  # Degraded / Empty / enumerate fault
            # The exception's own message carries the COUNTS (DiscoveryDegraded: "N/M EFTS pages failed
            # (X%) after retries"; DiscoveryEmpty: the term counts) — the operator-facing error names the
            # numbers, never just "unavailable" (#9 rule 3: degradation is loud AND specific).
            raise DraftError(f"discovery unavailable — {exc}; please retry") from exc
        finally:
            own.close()

    def _record_run(job: DraftJob, result: ChainDraftOut) -> None:
        # The DISCOVER run-of-record: dump the completed draft + its inputs (write-only, fail-open — see
        # workbench/draft_run_log.py). The thesis captured here is the SAME object execute_draft read, so
        # the artifact's term set is the set the run actually used.
        write_draft_run_log(thesis, result, job_id=job.job_id)

    try:
        job_id = start_draft_job(thesis.id, _run, on_success=_record_run)
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


# --- Run loader: seed the editable workbench from a saved draft run (the DISCOVER-stage cost-saver) ---
# Two READ-ONLY, NON-SPINE endpoints, both GATED behind ``ALPHADECK_RUN_LOADER_ENABLED`` (the single flag → 404
# when off, so the FE picker self-hides). They read the write-only run-of-record (``data/draft_runs/``) back so
# a saved run can be LOADED into the editor instead of paying for a fresh Opus draft. Loading seeds FE state
# only — the operator's promote stays the ONLY spine writer (``test_draft_endpoint_writes_nothing`` intact);
# ``get_thesis_or_404`` enforces tenant ownership (the runs dir is keyed by thesis_id alone). See
# ``workbench/draft_run_log.py`` (writer) + ``workbench/run_loader.py`` (reader).


@router.get("/theses/{thesis_id}/runs", response_model=list[SavedRunSummary])
def list_saved_runs(thesis: Thesis = Depends(get_thesis_or_404)) -> list[SavedRunSummary]:
    """List this thesis's saved draft-run artifacts, newest-first — the run-loader picker's source. Pure file
    read (no compute, no EDGAR). **404** when the run loader is disabled (so the FE picker is absent).
    """
    if not get_settings().run_loader_enabled:
        raise HTTPException(status_code=404, detail="run loader is disabled")
    return [SavedRunSummary(**r) for r in run_loader.list_runs(thesis.id)]


@router.get("/theses/{thesis_id}/runs/{run_id}", response_model=ChainDraftOut)
def get_saved_run(run_id: str, thesis: Thesis = Depends(get_thesis_or_404)) -> ChainDraftOut:
    """Load one saved run's inner draft as a ``ChainDraftOut`` — the SAME shape the draft endpoint returns, so
    the FE hands it straight to the editor's ``applyDraft`` (no re-draft, no refetch). **404** when the loader
    is disabled, or for an unknown / traversal ``run_id``; **422** if a stale artifact no longer validates
    against the current schema."""
    if not get_settings().run_loader_enabled:
        raise HTTPException(status_code=404, detail="run loader is disabled")
    draft = run_loader.read_run(thesis.id, run_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="saved run not found")
    try:
        return ChainDraftOut.model_validate(draft)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"saved run payload is incompatible with the current schema: {exc}",
        ) from exc


# --- Triage session: the resumable prune (one MUTABLE opaque blob per thesis; NOT the spine) ---
# Three tenant-guarded endpoints over ``workbench/triage_store.py`` (a dumb blob store — no DB conn, no repo).
# The operator's prune work (a large drafted universe → a shortlist) is browser state today; a refresh wipes it
# or forces a fresh Opus re-draft. These let the FE autosave its whole working state and rehydrate on open.
# STRUCTURAL zero-spine-write (a session is not a fact): the store cannot write ``basket_member`` / ``fact_*``
# regardless of payload — the promote stays the ONLY writer (``test_session_put_writes_no_spine_rows``).
# ``get_thesis_or_404`` enforces tenant ownership (the sessions dir is keyed by thesis_id alone).


@router.get("/theses/{thesis_id}/triage-session", response_model=TriageSessionGet)
def get_triage_session(thesis: Thesis = Depends(get_thesis_or_404)) -> TriageSessionGet:
    """Restore this thesis's saved prune session. ``session`` is the stored envelope, or ``null`` for
    GENUINELY-ABSENT (no prune saved yet → the FE seeds fresh from the thesis). A read FAULT raises **500**, NOT
    a null session — so the FE never mistakes a transient error for "no session" and silently discards a real
    prune. Pure file read (no compute, no DB write)."""
    try:
        env = triage_store.read_session(thesis.id)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to read triage session: {exc}"
        ) from exc
    return TriageSessionGet(session=TriageSessionEnvelope(**env) if env is not None else None)


@router.put("/theses/{thesis_id}/triage-session", response_model=TriageSessionEnvelope)
def put_triage_session(
    body: TriageSessionPut, thesis: Thesis = Depends(get_thesis_or_404)
) -> TriageSessionEnvelope:
    """Autosave this thesis's prune session — overwrite the single ``latest.json`` with the FE's opaque working
    state. Returns the stored envelope (server-stamped ``updated_at``). FAIL-LOUD: a write fault raises **500**
    so the operator's "Not saved" indicator is honest (the deliberate contrast with the fail-open draft-run
    log). Writes ZERO spine rows regardless of payload — the blob is bytes to a file, never a fact.
    """
    try:
        env = triage_store.write_session(thesis.id, body.schema_version, body.state)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to write triage session: {exc}"
        ) from exc
    return TriageSessionEnvelope(**env)


@router.delete("/theses/{thesis_id}/triage-session", status_code=204)
def delete_triage_session(thesis: Thesis = Depends(get_thesis_or_404)) -> None:
    """Discard this thesis's saved prune session (the operator's explicit "start over" — the ONLY remove; a
    promote KEEPS the session so the operator can keep pruning the remainder). Idempotent: deleting an absent
    session is a no-op. Returns **204**."""
    try:
        triage_store.delete_session(thesis.id)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"failed to delete triage session: {exc}"
        ) from exc


def _vouched(estimate: float | None, value: float) -> str | None:
    """The confirm/override PROVENANCE for a ratify: did the operator accept the shown estimate as-is, or change
    it? ``'confirmed'`` (ratified value == the estimate), ``'overridden'`` (differs), or ``None`` (no estimate
    was shown — a manual/legacy ratify). PROVENANCE only — the scoring read never branches on it; a NULL-vouched
    fact scores identically to a fresh confirm."""
    if estimate is None:
        return None
    return (
        "confirmed" if math.isclose(estimate, value, rel_tol=1e-9, abs_tol=1e-9) else "overridden"
    )


def _has_shares_fact(conn: psycopg.Connection, security_id: UUID, tenant_id: UUID) -> bool:
    """Does ANY shares fact exist for this security? The auto-confirm's idempotency + no-clobber gate.

    Deliberately existence-of-ANY-row, not "the latest is auto": once the operator has OVERRIDDEN an
    auto-applied count, a later get-data must not re-apply the machine value over their decision. Cheap
    (append-only table, indexed by tenant+security); the read never needs the value.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM fact_shares_outstanding WHERE tenant_id=%s AND security_id=%s LIMIT 1",
            (tenant_id, security_id),
        )
        return cur.fetchone() is not None


@router.post("/facts/auto-confirm", response_model=AutoConfirmOut)
def auto_confirm_fact(
    req: AutoConfirmRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> AutoConfirmOut:
    """Auto-apply a security's AUTO-tier shares count on get-data — REMOVING a ceremonial confirm, not a real one.

    The AUTO shares confirm was never a human verification: the operator has no independent knowledge of a
    share count and does not look one up, so clicking "Confirm" on a machine-reproduced cover figure rubber-
    stamped it while recording ``ratified_by="operator"`` — provenance claiming a check that never happened.
    This applies the value directly and stamps it ``ratified_by="auto"``, which is what actually occurred. The
    REAL human check is downstream and unchanged: the operator reads the resulting MARKET CAP, a figure they do
    have intuition for, and overrides the shares if it looks wrong.

    WHY THIS IS NOT A #1/#3 VIOLATION: the written number is the extractor's deterministic reproduction of
    filed companyfacts (the market-cap trust class) — never a model output, never a computed/``llm_proposed``
    estimate. Estimates remain computed-on-read and MUST NOT become ``fact_*`` rows (see WORKBENCH_EXTRACTION.md);
    nothing here touches that. The AUTO tier is exactly the unambiguous single-class, current-cover case —
    anything ambiguous trips a FLAG and is excluded below.

    THE STRUCTURAL BOUND: the request carries NO value. The server re-extracts (cache-first — get-data just
    warmed it) and writes its OWN parse, so no caller can inject a figure under the ``auto`` provenance.

    Four gates, each an honest ``applied=False`` rather than an error:
    - the security must be in this tenant's master (fail-closed, like ``/facts``);
    - ``already_on_file`` — ANY existing shares fact (auto or an operator override) -> no-op. This is the
      idempotency guarantee (a re-run appends ZERO rows) AND the no-clobber guarantee;
    - ``not_auto`` — a FLAGged candidate is the operator's to ratify; the machine never resolves a dual-class
      sum or a stale cover;
    - ``no_candidate`` / ``no_value`` — nothing to apply.
    """
    if not master.exists(conn, req.security_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="security not in this tenant's master")
    # the idempotency + no-clobber gate FIRST: cheapest, and it short-circuits before any SEC traffic
    if _has_shares_fact(conn, req.security_id, tenant_id):
        return AutoConfirmOut(applied=False, reason="already_on_file")
    cik = master.ciks_for(conn, {req.security_id}, tenant_id=tenant_id).get(req.security_id)
    if not cik:
        raise HTTPException(status_code=404, detail="no CIK for this security — resolve it first")
    try:
        cands = extract_for_security(EdgarClient(allow_live=True), cik)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — SEC unreachable / no UA / parse hiccup -> 502, not a 500
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc
    cand = next((c for c in cands if c.fact_type == "shares_outstanding"), None)
    if cand is None:
        return AutoConfirmOut(applied=False, reason="no_candidate")
    if cand.tier != Tier.AUTO:
        return AutoConfirmOut(applied=False, reason="not_auto")
    if cand.value is None:
        return AutoConfirmOut(applied=False, reason="no_value")
    fid = ingest_shares_outstanding(
        conn,
        req.security_id,
        shares=cand.value,  # the SERVER's parse — never a client-supplied figure
        source=cand.source,
        source_ref=cand.source_ref,
        event_date=cand.event_date,
        note=cand.note,
        ratified_by="auto",  # honest provenance: applied by the machine, NOT confirmed by a human
        vouched=None,  # no estimate was shown to anyone -> no confirm/override to record
        tenant_id=tenant_id,
    )
    conn.commit()
    return AutoConfirmOut(applied=True, reason="applied", fact_id=fid)


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

    ``vouched`` records whether the operator confirmed the shown ``estimate`` as-is or overrode it — PROVENANCE
    for the drift-cron + the agree/disagree signal, NEVER a scoring input (all vouched states score identically).
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
            conn,
            req.security_id,
            segment_label=req.segment_label,
            mix_pct=req.mix_pct,
            vouched=_vouched(req.estimate, req.mix_pct),
            **common,
        )
    elif req.fact_type == "shares_outstanding":
        fid = ingest_shares_outstanding(
            conn,
            req.security_id,
            shares=req.shares,
            vouched=_vouched(req.estimate, req.shares),
            **common,
        )
    elif req.fact_type == "catalyst":
        # a hand-authored conviction fact (the Key-1 arming path) — no extractor candidate exists,
        # so the CITATION is the provenance (#6): an empty source_ref would be a bare operator claim
        if not req.source_ref.strip():
            raise HTTPException(
                status_code=422,
                detail="a catalyst fact needs its citation (source_ref) — the press release / "
                "8-K / IR page it traces to",
            )
        fid = ingest_catalyst(
            conn,
            req.security_id,
            catalyst_type=req.catalyst_type,
            grade=req.grade,
            label=req.label,
            source=req.source,
            source_ref=req.source_ref,
            event_date=req.event_date,
            horizon_end=req.horizon_end,
            ratified_by="operator",
            tenant_id=tenant_id,
        )
    else:  # cash_burn
        fid = ingest_cash_burn(
            conn,
            req.security_id,
            cash_usd=req.cash_usd,
            quarterly_burn_usd=req.quarterly_burn_usd,
            vouched=_vouched(req.estimate, req.quarterly_burn_usd),
            **common,
        )
    conn.commit()
    return RatifiedFactOut(fact_id=fid, fact_type=req.fact_type)
