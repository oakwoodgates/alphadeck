from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import Archetype, CatalystType, Grade, Kind, State, TermTier, Verdict
from domain.settings import get_settings
from domain.signal import Provenance
from domain.thesis import (
    BasketMember,
    Catalyst,
    Evidence,
    ExcludedName,
    KillCriterion,
    Position,
    Segment,
    TermSetEntry,
    Thesis,
)
from domain.workbench import ScoredFigure, ScoredMember
from scoreboard.schema import EpisodeOperator, OperatorSpan, ScoredEpisode, ThesisRecord
from workbench.chain_draft import ResolvedPlacement, ResolvedSegment

# API response contracts — the WIRE shape, kept distinct from domain/ so the frontend's generated TS
# types follow the API, not the domain schema. The one real transform vs. the domain CallCard: each
# provenance ref resolves to a clickable EDGAR URL (a presentation concern, not a domain one).

_FILING_SOURCES = frozenset({"form4", "8-k"})  # provenance sources that map to an EDGAR filing


def _is_accession(ref: str) -> bool:
    parts = ref.split("-")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def edgar_url(source: str, ref: str, cik: str | None) -> str | None:
    """Resolve a filing provenance ref to its EDGAR filing-index page, built from the ISSUER ``cik``
    (off security_master) — NOT the accession's prefix, which is the filing AGENT's CIK and only
    coincides with the issuer for some filers. Non-filing refs (e.g. price) or an unknown issuer
    CIK -> None.
    """
    if source in _FILING_SOURCES and cik and _is_accession(ref):
        nodash = ref.replace("-", "")
        return f"{get_settings().sec_archives_base}/{int(cik)}/{nodash}/{ref}-index.htm"
    return None


class ProvenanceOut(BaseModel):
    source: str
    ref: str
    url: str | None = None  # resolved clickable link (None when not resolvable)
    detail: dict[str, Any] = {}


class TriggerRefOut(BaseModel):
    label: str
    kind: Kind
    grade: Grade | None = None
    # the name this trigger fired on — attributes it in a multi-name basket
    ticker: str | None = None
    sources: list[ProvenanceOut] = []


def _provenance_out(p: Provenance, cik: str | None) -> ProvenanceOut:
    """One provenance ref -> its wire form, resolving the clickable EDGAR URL from the issuer ``cik``. The
    single place ProvenanceOut is built (reused by the trigger/risk-signal + scored-figure mappers).
    """
    return ProvenanceOut(
        source=p.source,
        ref=p.ref,
        url=edgar_url(p.source, p.ref, cik),
        detail=p.detail,
    )


def _trigger_out(
    t: TriggerRef, ciks: Mapping[UUID, str | None], tickers: Mapping[UUID, str | None]
) -> TriggerRefOut:
    return TriggerRefOut(
        label=t.label,
        kind=t.kind,
        grade=t.grade,
        ticker=tickers.get(t.security_id),
        sources=[_provenance_out(p, ciks.get(t.security_id)) for p in t.sources],
    )


class MemberCallOut(BaseModel):
    """One basket member's call in the per-member ranked menu (M5 Part A). `armed_members` is ranked
    (the headline is [0]); `watch_members` are confirmation-only ("moving, no conviction yet")."""

    security_id: UUID
    ticker: str | None = None
    verdict: Verdict | None = None
    conviction_grade: Grade | None = None
    confirmation_grade: Grade | None = None
    entry_grade: Grade | None = None
    confidence: float | None = None
    exit_by: date | None = None  # the liveness/hold horizon = the "runway" the ranking uses
    arm_until: date | None = None
    lapsing: bool = False  # runway below the dial; the UI flags it (ranks below fresh)
    theme_armed: bool = (
        False  # armed via the THEME-conviction fallback (M5b), not its own conviction
    )
    triggers: list[TriggerRefOut] = []

    @classmethod
    def from_member(
        cls, m: MemberCall, ciks: Mapping[UUID, str | None], tickers: Mapping[UUID, str | None]
    ) -> "MemberCallOut":
        return cls(
            security_id=m.security_id,
            ticker=tickers.get(m.security_id),
            verdict=m.verdict,
            conviction_grade=m.conviction_grade,
            confirmation_grade=m.confirmation_grade,
            entry_grade=m.entry_grade,
            confidence=m.confidence,
            exit_by=m.exit_by,
            arm_until=m.arm_until,
            lapsing=m.lapsing,
            theme_armed=m.theme_armed,
            triggers=[_trigger_out(t, ciks, tickers) for t in m.triggers],
        )


class CallCardResponse(BaseModel):
    """The CallCard as served — the domain card plus resolved provenance URLs."""

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None
    confirmation_grade: Grade | None = None  # core = volume-backed, flip = momentum-only
    entry_grade: Grade | None = None
    armed_security_id: UUID | None = None
    expression: str
    exit_by: date | None = None
    arm_until: date | None = None
    catalyst_surface: list[Catalyst] = []
    confidence: float | None = None  # the Armed card's bar; None for a not-yet card (§7)
    key_conviction: KeyState
    key_confirmation: KeyState
    triggers_fired: list[TriggerRefOut] = []
    risk_signals: list[TriggerRefOut] = []
    missing: list[str] = []
    counter_case: str = ""
    safe_sleeve: str | None = None
    armed_members: list[MemberCallOut] = []  # ranked; the headline is [0]
    watch_members: list[MemberCallOut] = []  # confirmation-only ("moving, no conviction yet")

    @classmethod
    def from_card(
        cls,
        card: CallCard,
        cik_for: Mapping[UUID, str | None] | None = None,
        ticker_for: Mapping[UUID, str | None] | None = None,
    ) -> "CallCardResponse":
        ciks = cik_for or {}
        tickers = ticker_for or {}
        return cls(
            thesis_id=card.thesis_id,
            asof=card.asof,
            state=card.state,
            verdict=card.verdict,
            conviction_grade=card.conviction_grade,
            confirmation_grade=card.confirmation_grade,
            entry_grade=card.entry_grade,
            armed_security_id=card.armed_security_id,
            expression=card.expression,
            exit_by=card.exit_by,
            arm_until=card.arm_until,
            catalyst_surface=list(card.catalyst_surface),
            confidence=card.confidence,
            key_conviction=card.key_conviction,
            key_confirmation=card.key_confirmation,
            triggers_fired=[_trigger_out(t, ciks, tickers) for t in card.triggers_fired],
            risk_signals=[_trigger_out(r, ciks, tickers) for r in card.risk_signals],
            missing=list(card.missing),
            counter_case=card.counter_case,
            safe_sleeve=card.safe_sleeve,
            armed_members=[MemberCallOut.from_member(m, ciks, tickers) for m in card.armed_members],
            watch_members=[MemberCallOut.from_member(m, ciks, tickers) for m in card.watch_members],
        )


class ThesisSummary(BaseModel):
    """Lightweight list item for the Board (the full thesis comes from GET /theses/{id})."""

    id: UUID
    name: str
    ticker: str | None = None  # None for a multi-name theme thesis; the Board shows a basket marker
    basket_size: int = 0
    narrative: str
    archived: bool = False  # archived = out of the default list + the cron's walk; restorable

    @classmethod
    def from_thesis(cls, thesis: Thesis) -> "ThesisSummary":
        return cls(
            id=thesis.id,
            name=thesis.name,
            ticker=thesis.ticker,
            basket_size=len(thesis.basket),
            narrative=thesis.narrative,
            archived=thesis.archived_at is not None,
        )


class ThesisDetail(BaseModel):
    """The full thesis for the Cockpit — a wire model (no tenant_id) so generated FE types never bind
    to the domain Thesis. Sub-objects reuse the domain value types (no transform needed, like the
    catalyst surface on CallCardResponse)."""

    id: UUID
    parent_id: UUID | None = None
    name: str
    narrative: str
    ticker: str | None = None
    basket: list[BasketMember] = []
    segments: list[Segment] = []
    term_set: list[TermSetEntry] = []  # the persisted SIGNAL/BROAD discovery terms (read-only here)
    evidence: list[Evidence] = []
    catalysts: list[Catalyst] = []
    kill_criteria: list[KillCriterion] = []
    position: Position | None = None
    # the durable exclusion set (#7) — the editor seeds its greyed state from this; never a filter
    exclusions: list[ExcludedName] = []

    @classmethod
    def from_thesis(cls, t: Thesis) -> "ThesisDetail":
        return cls(
            id=t.id,
            parent_id=t.parent_id,
            name=t.name,
            narrative=t.narrative,
            ticker=t.ticker,
            basket=list(t.basket),
            segments=list(t.segments),
            term_set=list(t.term_set),
            evidence=list(t.evidence),
            catalysts=list(t.catalysts),
            kill_criteria=list(t.kill_criteria),
            position=t.position,
            exclusions=list(t.exclusions),
        )


# --- Workbench (Slice 3) — the scored read + the promote payload ---


class ScoredFigureOut(BaseModel):
    """One meter/figure on the wire: the 0-4 pip (null = "—"/no data), the raw value, and the provenance
    chips ("behind the scores"). market_cap carries `value` only (pips null — a figure, not a meter).
    """

    pips: int | None = None
    value: float | None = None
    provenance: list[ProvenanceOut] = []


class ScoredMemberOut(BaseModel):
    """A basket member scored for the Workbench — the four meters + the market-cap figure + the fit label."""

    security_id: UUID
    ticker: str | None = None
    # Display identity, joined from the master on read (never promoted onto a BasketMember, #2): the
    # company NAME rides the scored row (a ticker-only list made the finalize pass a memory quiz), and
    # the enrichment strings give the rail its who-is-this context.
    name: str | None = None
    sector: str | None = None
    exchange: str | None = None
    category: str | None = None
    # ``None`` = not yet characterized (item F: placement never stamps a default; the archetype is decided
    # ONCE, on the finalize screen — the hint below recommends, the operator applies/overrides).
    archetype: Archetype | None = None
    # A DERIVED-DEFAULT archetype recommendation (Slice 4, INVARIANT #10): deterministic, from market cap +
    # purity. Display-only — the operator confirms/overrides; NEVER auto-applied to ``archetype``, never
    # promoted. ``None`` = abstain (no facts yet, or a relational role — shovel/fund — the rule won't guess).
    archetype_hint: Archetype | None = None
    segment: str | None = None
    purity: ScoredFigureOut
    runway: ScoredFigureOut
    catalysts: ScoredFigureOut
    dilution: ScoredFigureOut
    market_cap: ScoredFigureOut
    fit: str
    # HONEST CONFIDENCE (SURFACE Slice 1a): how many fact-backed meters (purity/runway/market cap) have no
    # operator-confirmed value yet. A "rests on N unconfirmed" readiness signal; never a scoring input.
    unconfirmed_estimates: int = 0

    @classmethod
    def from_scored(
        cls,
        m: ScoredMember,
        ciks: Mapping[UUID, str | None],
        tickers: Mapping[UUID, str | None],
        identity: Mapping[UUID, Mapping[str, str | None]] | None = None,
    ) -> "ScoredMemberOut":
        cik = ciks.get(m.security_id)
        ident = (identity or {}).get(m.security_id, {})

        def fig(f: ScoredFigure) -> ScoredFigureOut:
            return ScoredFigureOut(
                pips=f.pips,
                value=f.value,
                provenance=[_provenance_out(p, cik) for p in f.provenance],
            )

        return cls(
            security_id=m.security_id,
            ticker=tickers.get(m.security_id),
            name=ident.get("name"),
            sector=ident.get("sector"),
            exchange=ident.get("exchange"),
            category=ident.get("category"),
            archetype=m.archetype,
            archetype_hint=m.archetype_hint,
            segment=m.segment,
            purity=fig(m.purity),
            runway=fig(m.runway),
            catalysts=fig(m.catalysts),
            dilution=fig(m.dilution),
            market_cap=fig(m.market_cap),
            fit=m.fit,
            unconfirmed_estimates=m.unconfirmed_estimates,
        )


class WorkbenchScored(BaseModel):
    """The Workbench scored read for a thesis: its value-chain segments + the scored members (the UI groups
    by `member.segment`). Re-derived on read — never persisted."""

    thesis_id: UUID
    asof: date
    segments: list[Segment] = []
    members: list[ScoredMemberOut] = []


class PriceIngestOut(BaseModel):
    """The per-security price pull's receipt (the finalize screen's decoupled price leg): how many EOD
    bars appended (0 = already current — the ingest is incremental), how many overlap bars were
    RE-VERSIONED (a source restatement, e.g. a split re-base — the exceptional path; source-strategy
    Option A), and the latest bar now on file.
    """

    security_id: UUID
    ticker: str
    bars_reversioned: int = 0
    bars_appended: int
    latest_bar: date | None = None  # None = the source returned nothing (e.g. an unquoted line)


class SecurityMatchOut(BaseModel):
    """A security-master match for the Workbench's add-a-name typeahead (Slice 4b). The operator picks the
    exact row; its ``security_id`` is then placed into the basket. A discovery net over the EXISTING
    per-tenant master (INVARIANT #2) — every match is a real member, nothing is ingested or guessed.
    """

    security_id: UUID
    ticker: str
    name: str | None = None
    cik: str | None = None


class PromoteThesisRequest(BaseModel):
    """The promote/update payload — a thesis-with-chain. The router builds a domain Thesis (the
    segment-consistency validator runs) under the CURRENT tenant (the resolver, not the body), then upserts
    it (create when `id` is null, update otherwise). Scores are NOT sent — they re-derive on read.
    `authored_by` is STAMPED server-side (the human path authors `operator_set`), not taken from the body.
    """

    id: UUID | None = None
    name: str
    narrative: str
    ticker: str | None = None
    basket: list[BasketMember] = []
    segments: list[Segment] = []
    # The identity-coherence override (fail-closed with an escape hatch — the gate idiom): promote REJECTS
    # (422) a member whose shown ticker disagrees with its bound master row (cross-company / label-drift,
    # the misbind class) UNLESS that member's security_id is listed here — an explicit, per-member, LOGGED
    # acceptance ("I know the label and the binding disagree; bind it anyway"). Never blanket, never
    # remembered across promotes.
    identity_overrides: list[UUID] = []


class ProduceTermsRequest(BaseModel):
    """Body for ``POST /theses/{id}/terms`` (optional). ``seeds`` are the operator-anchored canonical compounds
    (e.g. the known psychedelic compounds) — persisted as operator-authored SIGNAL, the recall guarantor against
    keyword-gen non-determinism. Omitted / empty seeds -> regenerate preserves the thesis's EXISTING operator
    seeds and just re-rolls the LLM-proposed terms."""

    seeds: list[str] = []


class TermEdit(BaseModel):
    """One operator-edited term in the manual save (``PUT .../terms/edit``). The operator owns ``term`` +
    ``tier``; ``authored_by`` is NOT in the body — the server stamps it by diffing against the stored set (a
    naive client must not be able to mark a term ``operator_edited`` and freeze it against regenerate).
    """

    term: str
    tier: TermTier


class EditTermsRequest(BaseModel):
    """Body for ``PUT /theses/{id}/terms/edit`` — the operator's full, edited term set, saved DIRECTLY (no LLM,
    mirroring LLM-out-of-promote). Authorship is re-stamped server-side: an untouched ``system_drafted`` BROAD
    term keeps its authorship so a later regenerate can re-roll it; only operator-touched entries become
    ``operator_set`` (added) / ``operator_edited`` (re-tiered). An empty list clears the set (a visible operator
    choice)."""

    terms: list[TermEdit] = []


class TierRecommendation(BaseModel):
    """An advisory tier recommendation for ONE term (INVARIANT #10 — the LLM recommends, the operator decides).
    DISPLAY-ONLY: it is the response of ``POST .../recommend-tiers``, never persisted, never mutating
    ``authored_by``. The operator confirms it via the EXISTING tier toggle (``PUT .../terms/edit``), where
    ``stamp_edited_term_set`` stamps ``operator_edited``. Deliberately a SEPARATE wire type — it never rides on
    ``ThesisDetail.term_set``, so a produce/edit round-trip can't persist it (the ``matched_terms`` precedent).
    Carries NO number (#3): a tier label + a one-line reason."""

    term: str
    recommended_tier: TermTier
    reason: str


# --- Ratify (hybrid-2a) — the first fact-WRITE: confirm an extracted candidate -> the existing ingest_* ---


class _RatifyBase(BaseModel):
    """Common provenance for a ratified scoring fact. ``source`` is the CANDIDATE's BASIS (e.g.
    ``10-k-segment`` vs ``10-k-business-description``) — preserved, NOT flattened to "ratified", so the
    DD-rail basis-provenance stays honest (it's read into the provenance chip, not the score). ``event_date``
    -> the fact's ``valid_from`` (no lookahead). ``ratified_by`` is stamped "operator" server-side.
    """

    security_id: UUID
    source: str
    source_ref: str
    event_date: date
    note: str | None = None
    # The system estimate the operator was shown (the fact-type's primary value: mix_pct / shares /
    # quarterly_burn). The server compares the ratified value to it -> stamps `vouched` confirmed/overridden
    # PROVENANCE. None = a manual ratify with no estimate shown (vouched stays NULL). Never a scoring input.
    estimate: float | None = None


class RatifyRevenueMix(_RatifyBase):
    fact_type: Literal["revenue_mix"]
    segment_label: str
    mix_pct: float


class RatifyShares(_RatifyBase):
    fact_type: Literal["shares_outstanding"]
    shares: float


class RatifyCashBurn(_RatifyBase):
    fact_type: Literal["cash_burn"]
    cash_usd: float
    quarterly_burn_usd: float


class RatifyCatalyst(_RatifyBase):
    """A hand-authored catalyst-CONVICTION fact (the Key-1 arming path — ``fact_catalyst`` via
    ``ingest_catalyst``, ``source='ratified'``). Unlike the extractor-fed types there is no candidate:
    the operator authors the event and MUST cite it (``source_ref`` — the press release / 8-K / IR
    page; provenance is the point, #6). ``event_date`` = when the catalyst became known (valid time,
    no lookahead); ``horizon_end`` optionally pins its relevance horizon (else the liveness default).
    Distinct from the thesis-level catalyst SURFACE (display objects, ``PUT /theses/{id}/catalysts``).
    """

    fact_type: Literal["catalyst"]
    catalyst_type: CatalystType
    grade: Grade
    label: str
    horizon_end: date | None = None


# the discriminated body — Pydantic validates the per-type required fields for free (a missing field -> 422)
RatifyFactRequest = Annotated[
    RatifyRevenueMix | RatifyShares | RatifyCashBurn | RatifyCatalyst,
    Field(discriminator="fact_type"),
]


class RatifiedFactOut(BaseModel):
    fact_id: UUID
    fact_type: str


# --- FLAG-explanation drafter (M4b — the LLM seam) — a DISPLAY aid, NOT a fact ---


class FlagExplanationOut(BaseModel):
    """The model-drafted, plain-English explanation of a FLAG candidate, shown ALONGSIDE the raw passage.

    Deliberately carries NO value field: it is display-only and never rides the ratify rail (the ratified
    number comes solely from the operator's typed field on ``RatifyFactRequest``). ``grounded=False`` (with an
    empty ``explanation``) is the honest no-explanation / fail-open signal — the UI shows the raw passage and
    manual ratify exactly as today. (INVARIANT #3.)"""

    explanation: str
    grounded: bool


# --- S5: the narrative→chain DECOMPOSE drafter (the SECOND LLM seam) — a DISPLAY draft, never a fact ---


class DraftCoverageOut(BaseModel):
    """How much of the universe the draft's EFTS enumeration actually covered (the #9 rule-2/3 instrument on
    the wire): a sub-threshold gap used to pass looking complete (logged only); now the pages fetched vs
    attempted — and the TERMS whose pages are still missing — ride every draft to the operator. RUN state,
    display-only, never persisted."""

    pages_ok: int
    pages_attempted: int
    failed_terms: list[str] = []


class DraftReportOut(BaseModel):
    """The draft run's honesty report — every formerly-silent recall-loss mode, named per run (#9 rules 2/3):
    EFTS coverage, the hit-capped terms (enumeration truncated at the cap — deep hits not searched), the
    tail-sweep outcome (``ran`` / ``failed`` / ``skipped`` — a failed sweep is no longer indistinguishable from
    "no foreign names exist"), and the narration fill (M of N placed/verify names carrying thesis-fit prose).
    Value-free (#3) and RESPONSE-ONLY — display run state, never a fact, never persisted; the Workbench strip
    renders it quiet at 100% healthy, loud on any gap (inverse loudness)."""

    coverage: DraftCoverageOut
    capped_terms: list[str] = []
    tail_sweep: Literal["ran", "failed", "skipped"]
    narration_needed: int
    narration_filled: int


class ChainDraftOut(BaseModel):
    """The narrative→chain draft (Slice 5b): the value-chain SEGMENTS the model proposed + each proposed name
    resolved against the master to PLACED / AMBIGUOUS / ABSENT (exact membership decides — INVARIANT #2).

    RESPONSE-ONLY and value-free: it carries NO score/number field, and the endpoint persists NOTHING — a
    placed name is UNSCORED until the operator extract→ratifies it, and the operator's promote is the only
    writer. ``segments`` / ``placements`` reuse the resolver's domain result types directly (the wire is the
    resolver's output). ``report`` is the run's honesty report (coverage / capped terms / tail-sweep /
    narration — ``DraftReportOut``): ALWAYS set by ``execute_draft``, optional on the wire only so a reader
    handles its absence."""

    thesis_id: UUID
    segments: list[ResolvedSegment] = []
    placements: list[ResolvedPlacement] = []
    report: DraftReportOut | None = None


# --- Async draft delivery (kick-off → poll): the draft is a JOB, not a held-open request ---


class DraftJobRef(BaseModel):
    """The 202 kick-off body — the draft started as a background JOB (it takes minutes; held open it 504'd at the
    proxy). The FE polls ``GET .../draft-chain/jobs/{job_id}`` for the result. Only the DELIVERY changed; the
    draft logic is unchanged."""

    job_id: str
    status: Literal["running", "done", "failed"]


class DraftJobStatus(BaseModel):
    """The poll body. ``done`` carries the ``result`` (the ChainDraftOut); ``failed`` carries an operator-facing
    ``error`` (discovery-not-ready, a timeout, or an unexpected fault — VISIBLE, never a silent empty draft, #9).
    A benign fail-open (no key / the model declined) is ``done`` with an EMPTY draft, not a failure.
    """

    job_id: str
    status: Literal["running", "done", "failed"]
    result: ChainDraftOut | None = None
    error: str | None = None


# --- Run loader (the saved-draft-run picker) — RUN metadata, never a fact ---


class SavedRunSummary(BaseModel):
    """One saved draft-run artifact's summary for the run-loader picker (the cheap label fields — never the
    draft itself; the detail endpoint returns the inner ``ChainDraftOut``). RUN metadata only: no score, no
    number (#3). ``run_id`` is the artifact's filename stem (the detail-endpoint path segment)."""

    run_id: str
    written_at: str | None = None
    job_id: str | None = None
    placement_count: int
    segment_count: int


# --- Triage session (the resumable prune) — one MUTABLE opaque blob per thesis, NOT the spine ---


class TriageSessionPut(BaseModel):
    """The autosave body: the FE's ENTIRE editor working state serialized to one opaque JSON blob. ``state`` is
    ``dict`` — the backend NEVER interprets it (the FE owns and shapes it); ``schema_version`` is the FE's, so a
    future breaking shape change is decidable on restore. A session is NOT a fact: this write persists zero spine
    rows (``test_session_put_writes_no_spine_rows``)."""

    schema_version: int
    state: dict[str, Any]


class TriageSessionEnvelope(BaseModel):
    """A stored session: the opaque ``state`` plus the thin envelope the store types (thesis + version +
    server-stamped ``updated_at``). Returned by PUT and nested in GET when a session exists."""

    thesis_id: UUID
    schema_version: int
    updated_at: str
    state: dict[str, Any]


class TriageSessionGet(BaseModel):
    """The restore body. ``session`` is the envelope when one exists, or ``null`` for GENUINELY-ABSENT (no prune
    saved yet → the FE seeds fresh). A load FAILURE is a non-2xx, never ``session: null`` — so the FE never
    mistakes a transient error for "no session" and silently discards a real prune."""

    session: TriageSessionEnvelope | None = None


# --- Thesis-list authoring: the catalyst SURFACE + kill criteria (spine children, operator-owned) ---


class CatalystIn(BaseModel):
    """One catalyst-surface entry (a narrative binary event the card's surface renders between entry
    and exit-by) — display objects, distinct from the conviction FACTS (``RatifyCatalyst``). Server
    generates the id; the list is replaced whole (the operator edits it as a list)."""

    label: str
    kind: str | None = None  # display kind e.g. "earnings", "regulatory"
    when_date: date | None = None  # dated -> enters the catalyst_surface filter; None = fuzzy
    when_label: str | None = None  # display string e.g. "~3wk", "Q3"


class KillCriterionIn(BaseModel):
    """One kill criterion — the operator's documented "what would kill this thesis"; feeds the
    deterministic counter-case (the card stops reading "no documented counter-case")."""

    text: str


class ExclusionIn(BaseModel):
    """One durably-excluded name (#7): the operator's NO with the optional why. Full-list replaced
    via the sole writer; discovery never filters on it (#9) — the editor greys, visibly."""

    security_id: UUID
    ticker: str | None = None
    reason: str | None = None


# --- Decision capture (the operator-decisions log) — an EVENT log, never a scoring fact ---


class DecisionIn(BaseModel):
    """One operator decision to APPEND (gate-1 ratified 2026-07-10). Advisory only (#5): this LOGS a
    fill/pass the operator made elsewhere — nothing routes, nothing blocks. ``take`` opens the thesis's
    (single, v1) position; ``close`` closes it; ``pass`` records a no-act (any state, reason optional);
    ``void`` points ``voids`` at a mistaken row — the reversibility inverse, never a delete."""

    action: Literal["take", "pass", "close", "void"]
    decision_date: date  # VALID time — the day the fill/decision actually happened
    security_id: UUID | None = None  # the name acted on (defaults to thesis-level for a pass)
    shares: float | None = None
    price: float | None = None
    reason: str | None = None
    voids: UUID | None = None  # required iff action == "void"


class DecisionOut(BaseModel):
    """One logged decision. ``call_state``/``call_verdict`` are the platform's stance when it was
    logged (display denormalization — attribution re-derives from the calls-log join); ``voided``
    marks a row a later ``void`` points at (the strip greys it — visible, never hidden)."""

    id: UUID
    action: Literal["take", "pass", "close", "void"]
    decision_date: date
    security_id: UUID | None = None
    shares: float | None = None
    price: float | None = None
    reason: str | None = None
    voids: UUID | None = None
    call_state: str | None = None
    call_verdict: str | None = None
    recorded_at: str
    voided: bool = False


# ---------- The Scoreboard (SCORE) — the forward record, served ----------


class ScoreboardEpisodeOut(BaseModel):
    """One arm episode from the record, scored — a ledger row. Outcome fields keep replay's
    canonical names (``forward_return`` = arm→exit_by on realized closes ≤ the request asof).
    ``status``/``matured``/``censored_start`` are the record-honesty flags: open = a RUNNING
    return, not a verdict; metrics judge only matured + non-censored episodes."""

    thesis_id: UUID
    security_id: UUID
    ticker: str | None = None
    is_headline: bool = False
    theme_armed: bool = False
    arm_date: date
    dearm_date: date | None = None
    close_reason: str
    status: Literal["open", "closed"]
    matured: bool
    censored_start: bool
    verdict: Verdict | None = None
    entry_grade: Grade | None = None
    conviction_grade: Grade | None = None
    confidence: float | None = None
    exit_by: date | None = None
    arm_until: date | None = None
    warm_date: date | None = None
    triggers_at_arm: list[TriggerRefOut] = []  # the WHY behind the arm (invariant #6)
    entry_close: float | None = None
    exit_close: float | None = None
    exit_date: date | None = None
    forward_return: float | None = None
    arm_until_return: float | None = None
    warm_return: float | None = None
    peak_return: float | None = None
    peak_date: date | None = None
    exit_vs_peak_days: int | None = None
    truncated: bool = False  # the hold horizon ran past the available (asof-capped) bars
    insufficient_prices: bool = False  # e.g. a day-1 arm: no bar on/after the arm yet
    operator: "EpisodeOperatorOut | None" = None  # None = no decision logged (the capture gap)


def _scoreboard_episode_out(
    e: ScoredEpisode, ciks: Mapping[UUID, str | None], tickers: Mapping[UUID, str | None]
) -> ScoreboardEpisodeOut:
    ep, out = e.episode, e.outcome
    return ScoreboardEpisodeOut(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        ticker=tickers.get(ep.security_id),
        is_headline=ep.is_headline,
        theme_armed=ep.theme_armed,
        arm_date=ep.arm_date,
        dearm_date=ep.dearm_date,
        close_reason=ep.close_reason,
        status=e.status,
        matured=e.matured,
        censored_start=e.censored_start,
        verdict=ep.verdict,
        entry_grade=ep.entry_grade,
        conviction_grade=ep.conviction_grade,
        confidence=ep.confidence,
        exit_by=ep.exit_by,
        arm_until=ep.arm_until,
        warm_date=ep.warm_date,
        triggers_at_arm=[_trigger_out(t, ciks, tickers) for t in e.triggers_at_arm],
        entry_close=out.entry_close,
        exit_close=out.exit_close,
        exit_date=out.exit_date,
        forward_return=out.forward_return,
        arm_until_return=out.arm_until_return,
        warm_return=out.warm_return,
        peak_return=out.peak_return,
        peak_date=out.peak_date,
        exit_vs_peak_days=out.exit_vs_peak_days,
        truncated=out.truncated,
        insufficient_prices=out.insufficient_prices,
        operator=_operator_out(e.operator),
    )


class ScoreboardThesisOut(BaseModel):
    """One thesis's slice of the Scoreboard: record coverage + scored episodes. Present even at
    zero episodes — the record span and an accruing warming window ARE the honest launch state.
    ``record_error`` surfaces an unreadable historical card (fault isolation), never a 500."""

    thesis_id: UUID
    name: str
    ticker: str | None = None
    basket_size: int = 0
    archived: bool = False
    first_call_asof: date | None = None
    last_call_asof: date | None = None
    current_state: str | None = None
    current_verdict: str | None = None
    warming_since: date | None = None
    episodes: list[ScoreboardEpisodeOut] = []
    operator_spans: "list[OperatorSpanOut]" = []  # off-record spans: overrides live here
    decision_anomaly: str | None = None  # a log shape the API should prevent — shown, not fixed
    record_error: str | None = None


def _scoreboard_thesis_out(
    t: ThesisRecord, ciks: Mapping[UUID, str | None], tickers: Mapping[UUID, str | None]
) -> ScoreboardThesisOut:
    return ScoreboardThesisOut(
        thesis_id=t.thesis_id,
        name=t.name,
        ticker=t.ticker,
        basket_size=t.basket_size,
        archived=t.archived,
        first_call_asof=t.first_call_asof,
        last_call_asof=t.last_call_asof,
        current_state=t.current_state,
        current_verdict=t.current_verdict,
        warming_since=t.warming_since,
        episodes=[_scoreboard_episode_out(e, ciks, tickers) for e in t.episodes],
        operator_spans=[_operator_span_out(s, tickers) for s in t.operator_spans],
        decision_anomaly=t.decision_anomaly,
        record_error=t.error,
    )


class ScoreboardMetricOut(BaseModel):
    """One claim-tied metric (the replay set, computed over eligible live outcomes). ``claim``
    names which system claim it tests — never a generic hit-rate; below ``n``/``insufficient_n``
    the summary must not be read as a claim (the FE renders it quiet)."""

    name: str
    claim: str
    n: int
    insufficient_n: bool
    summary: dict[str, float | None] = {}
    detail: list[dict[str, Any]] = []
    note: str = ""


class ScoreboardSummaryOut(BaseModel):
    """The aggregate strip: counts + the honesty banner + the gated metric set."""

    n_theses: int
    n_with_record: int
    n_episodes: int
    n_open: int
    n_matured: int
    n_censored: int
    n_eligible: int
    n_takes: int = 0  # the operator track: non-voided decisions <= asof
    n_passes: int = 0
    n_overrides: int = 0
    n_voided: int = 0
    record_began: date | None = None
    banner: str
    min_n: int
    metrics: list[ScoreboardMetricOut] = []


class ScoreboardResponse(BaseModel):
    """The Scoreboard: the call-of-record scored as-of ``asof`` — the record, never a recompute."""

    asof: date
    generated_at: str  # known_at honesty stamp (ISO): when this read of the record was taken
    summary: ScoreboardSummaryOut
    theses: list[ScoreboardThesisOut] = []


class EpisodeOperatorOut(BaseModel):
    """The operator's answer to an arm episode: took (with the operator's own prices/return —
    ``inferred`` marks a close used where no fill price was logged) or passed (no prices; the
    episode's own outcome sits beside it). No delta fields — v2."""

    action: Literal["took", "passed"]
    decision_id: UUID
    decision_date: date
    reason: str | None = None
    thesis_level: bool = False
    entry_price: float | None = None
    entry_inferred: bool = False
    exit_price: float | None = None
    exit_inferred: bool = False
    exit_date: date | None = None
    running: bool = False
    operator_return: float | None = None


class OperatorSpanOut(BaseModel):
    """An off-record take→close span (answering no armed episode), with the stance FROZEN on the
    take row at logging time. ``override`` = entered while the platform said not-armed — the
    gate's logged override, now carrying its outcome."""

    take_id: UUID
    take_date: date
    security_id: UUID | None = None
    ticker: str | None = None
    thesis_level: bool = False
    call_state_at_take: str | None = None
    call_verdict_at_take: str | None = None
    override: bool = False
    close_id: UUID | None = None
    close_date: date | None = None
    running: bool = False
    entry_price: float | None = None
    entry_inferred: bool = False
    exit_price: float | None = None
    exit_inferred: bool = False
    exit_date: date | None = None
    operator_return: float | None = None
    reason: str | None = None


def _operator_out(op: EpisodeOperator | None) -> EpisodeOperatorOut | None:
    if op is None:
        return None
    return EpisodeOperatorOut(**op.model_dump())


def _operator_span_out(s: OperatorSpan, tickers: Mapping[UUID, str | None]) -> OperatorSpanOut:
    return OperatorSpanOut(
        ticker=tickers.get(s.security_id) if s.security_id else None, **s.model_dump()
    )


class ScoreboardReplayThesisOut(BaseModel):
    """One thesis's slice of the HISTORICAL (replayed) panel — platform track only (decision
    capture post-dates history, so the operator column is structurally absent, not empty)."""

    thesis_id: UUID
    name: str
    ticker: str | None = None
    basket_size: int = 0
    episodes: list[ScoreboardEpisodeOut] = []


class ScoreboardReplayResponse(BaseModel):
    """The replay panel: replayed history served from the operator-kicked artifact — a RECOMPUTE
    (today's code + dials over historical facts), never the record; separate endpoint, separate
    section, metrics never pooled with the live summary. ``available=false`` = no artifact yet
    (run ``python -m scoreboard.replay_snapshot`` from the dev venv)."""

    available: bool
    generated_at: str | None = None
    window_start: date | None = None
    window_end: date | None = None
    known_at_pin: str | None = None
    record_began: date | None = None
    window_overlaps_record: bool = False
    banner: str | None = None
    min_n: int = 0
    n_theses: int = 0
    n_episodes: int = 0
    n_censored: int = 0
    n_eligible: int = 0
    metrics: list[ScoreboardMetricOut] = []
    theses: list[ScoreboardReplayThesisOut] = []
