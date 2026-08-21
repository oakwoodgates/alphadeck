from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.enums import (
    BusinessSupersector,
    BusinessType,
    CatalystType,
    Grade,
    InstrumentKind,
    Kind,
    State,
    TermTier,
    Verdict,
)
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
from signals.display.base import DisplaySignal
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
    event_date: date | None = None  # the trigger's fire/event date (SignalEvent.asof)
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
        event_date=t.event_date,
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
    # WHERE the name is from — DERIVED ON READ by the master join (``identity_for`` -> ``resolve_origin``
    # over the stored 0028 locator ingredients; the raw ingredients stay OFF the wire). Display identity
    # like the block above: never promoted (#2), never a call input, never a number (#3). ``None`` =
    # un-enriched / unknown — the FE renders NOTHING (honest abstain, never a guessed origin).
    origin: str | None = None
    # WHETHER this is a §16-exempt foreign filer that files NO Form 4 — DERIVED ON READ the same way
    # (``identity_for`` -> ``foreign_filer_form`` over the stored 0031 ingredients; raw ingredients stay OFF
    # the wire). "20-F" FPI / "40-F" Canadian-MJDS — §16-exempt, files NO Form 4; the insider signal is
    # structurally unavailable, not quiet. Display identity like origin, never a call input (#3/#4). None =
    # not a foreign filer / unknown.
    foreign_filer_form: str | None = None
    # The RESOLVED vendor price symbol (migration 0032) — carried verbatim from the master (``identity_for``).
    # A non-null value is the EXCEPTION: the name is priced under a different symbol than its SEC ticker
    # (FDCT priced under "FDCTD"). Display identity like origin, never a call input (#1/#3). ``None`` = priced
    # under the canonical ticker (the common, healthy case) — the FE renders the note ONLY when set, and the
    # watchlist export emits ``price_symbol ?? ticker`` so TradingView resolves the right listing.
    price_symbol: str | None = None
    # WHAT the company DOES — the two-level business type (Business-Type M1), DERIVED ON READ by the master
    # join (``identity_for`` -> ``resolve_business_type`` over the stored ``sector``/name/ticker + the 0033
    # re-tag). MONITOR display identity like origin: never promoted (#2), never a call input (#3/#4).
    # ``business_type`` is the EFFECTIVE leaf (the stored re-tag already folded in); ``None`` = unclassified
    # (un-enriched — the FE renders its own visible group, never a guess).
    business_type: BusinessType | None = None
    # The super-sector rollup of the effective leaf ("are the utilities moving?"); ``None`` iff the leaf is.
    business_supersector: BusinessSupersector | None = None
    # The stored operator re-tag VERBATIM (0033; NULL = classified by the maps — the store-on-diff exception
    # marker). Non-null lets the FE mark "your tag" and offer the revert-to-derived (reversibility, WB #1).
    business_type_override: BusinessType | None = None
    # The royalty/streaming OVERLAY (a company-NAME tell; SIC-invisible class) — co-exists with the leaf,
    # derive-only. Honest loudness: the FE marks ONLY when True (measured live: 32 of 8,106 names).
    royalty: bool = False
    # What the instrument IS (``equity``/``etf``), carried verbatim from the master — an ETF's sleeve label
    # keys on THIS, not the SIC maps (a fund has no sector). ``None`` only for a pre-migration hand-built row.
    instrument_kind: InstrumentKind | None = None
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
    # DATA-HEALTH (#1 thin-history flag) — the name's stored EOD bar-dates in the trailing year fall below the
    # longest active price-history lookback (``THIN_HISTORY_BARS`` = 200), so the history-window reads may be
    # STARVED. A derive-on-read bar COUNT (from ``recent_distinct_bar_counts``), NEVER persisted and NEVER a
    # call input — structurally out of the call path (like origin). It COVERS the resolver's blind spots: a
    # genuinely-uncovered name (0 bars, no resolvable symbol) still flags True. Honest loudness: the FE
    # renders the flag ONLY when True (a healthy name shows nothing).
    thin_price_history: bool = False

    @classmethod
    def from_scored(
        cls,
        m: ScoredMember,
        ciks: Mapping[UUID, str | None],
        tickers: Mapping[UUID, str | None],
        identity: Mapping[UUID, Mapping[str, str | bool | None]] | None = None,
        thin_history: Mapping[UUID, bool] | None = None,
    ) -> "ScoredMemberOut":
        cik = ciks.get(m.security_id)
        ident = (identity or {}).get(m.security_id, {})

        def fig(f: ScoredFigure) -> ScoredFigureOut:
            return ScoredFigureOut(
                pips=f.pips,
                value=f.value,
                provenance=[_provenance_out(p, cik) for p in f.provenance],
            )

        def s(key: str) -> str | None:
            v = ident.get(key)
            return v if isinstance(v, str) else None

        return cls(
            security_id=m.security_id,
            ticker=tickers.get(m.security_id),
            name=s("name"),
            sector=s("sector"),
            exchange=s("exchange"),
            category=s("category"),
            origin=s("origin"),
            foreign_filer_form=s("foreign_filer_form"),
            price_symbol=s("price_symbol"),
            business_type=s("business_type"),
            business_supersector=s("business_supersector"),
            business_type_override=s("business_type_override"),
            royalty=bool(ident.get("royalty", False)),
            instrument_kind=s("instrument_kind"),
            segment=m.segment,
            purity=fig(m.purity),
            runway=fig(m.runway),
            catalysts=fig(m.catalysts),
            dilution=fig(m.dilution),
            market_cap=fig(m.market_cap),
            fit=m.fit,
            unconfirmed_estimates=m.unconfirmed_estimates,
            thin_price_history=bool((thin_history or {}).get(m.security_id, False)),
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


class SetBusinessTypeRequest(BaseModel):
    """Body for ``POST /workbench/securities/{security_id}/business-type`` — the operator's per-security
    business-type RE-TAG (Business-Type M1). ``business_type`` is the leaf to tag, or ``null`` to CLEAR the
    re-tag (the visible revert back to the maps-derived classification — reversibility, WB #1). Identity
    metadata, never a fact (#3); the server stores it ONLY when it differs from the derived leaf (0033
    store-on-diff) and stamps ``operator:retag`` as the basis."""

    business_type: BusinessType | None = None


class BusinessTypeOut(BaseModel):
    """The re-tag receipt: the security's business-type read AFTER the write, re-derived server-side —
    what every scored surface will now show (#6: the response shows the effective state, not an echo).
    """

    security_id: UUID
    # the EFFECTIVE leaf after the write (re-tag folded in); None = unclassified (no sector, no tag)
    business_type: BusinessType | None = None
    business_supersector: BusinessSupersector | None = None
    # the STORED re-tag after store-on-diff coercion: None = classified by the maps (an agreeing pick
    # coerces to None — the derived read already says it), non-null = the standing exception
    business_type_override: BusinessType | None = None
    royalty: bool = False


class SecurityMatchOut(BaseModel):
    """A security-master match for the Workbench's add-a-name typeahead (Slice 4b) AND the surface-ETF
    resolve receipt (ETF Sleeve, Slice 1). The operator picks the exact row; its ``security_id`` is then
    placed into the basket. A discovery net over the EXISTING per-tenant master (INVARIANT #2) — every match
    is a real member, nothing is ingested or guessed.

    ``instrument_kind`` is what the row IS — ``equity`` (the typeahead default) or ``etf`` (returned by the
    surface-ETF resolve so the FE adds it as a ``fund`` sleeve). Identity, never a call input (#4/#6).
    """

    security_id: UUID
    ticker: str
    name: str | None = None
    cik: str | None = None
    instrument_kind: InstrumentKind = InstrumentKind.EQUITY


class ResolveEtfRequest(BaseModel):
    """Body for ``POST /workbench/securities/resolve-etf`` (ETF Sleeve, Slice 1) — the operator-supplied ETF
    ticker to surface as a ``fund`` sleeve. Just the ticker: the server resolves it (lookup-or-create) and
    marks it ``instrument_kind='etf'`` — operator-DECLARED, never auto-detected (#3, no LLM)."""

    ticker: str


class EtfHoldingOut(BaseModel):
    """One holding on the wire (ETF Sleeve, Slice 2a). ``ticker`` is the holding's best-known ticker —
    the filing's own where it carried one, else (for a held/available match) its CUSIP resolved to a US
    ticker via OpenFIGI; ``None`` only when nothing matched (``unresolved``). ``cusip``/``isin`` are the
    FILING's own identifiers, carried alongside as provenance (coverage varies by filing agent — some
    stamp no ticker at all). Plus the weight and — for the held/available buckets — the matched master
    ``security_id``.
    """

    name: str | None = None
    ticker: str | None = None
    cusip: str | None = None
    isin: str | None = None
    pct_val: float | None = None
    val_usd: float | None = None
    security_id: UUID | None = None


class EtfHoldingsOut(BaseModel):
    """A ``fund`` sleeve's N-PORT holdings + basket overlap (ETF Sleeve, Slice 2a) — RESPONSE-ONLY
    discovery context, recomputed per click, never persisted (the operator's promote stays the only
    spine writer, #2).

    ``report_date`` is the holdings VINTAGE (N-PORT is quarter-end, ~60 days lagged — fine for a
    discovery seed); ``source_ref`` the EDGAR filing index URL the whole answer traces to (#6). The
    three buckets partition ALL ``holdings_count`` positions — ``held`` (in this thesis's basket) ·
    ``available`` (in the master, not the basket) · ``unresolved`` (no master match — SHOWN, never
    dropped, #9). Weight-sorted, heaviest first.

    The fund INTERNALS ride the SAME N-PORT (no extra fetch): ``net_assets`` = AUM (the fund's size),
    with the ``total_assets``/``total_liabs`` composition it nets down from (net = gross − liabilities).
    Display-only context on the sleeve dossier — never a signal (#4/#6); ``None`` if a filer omits them.
    """

    report_date: date | None = None
    source_ref: str
    holdings_count: int
    net_assets: float | None = None
    total_assets: float | None = None
    total_liabs: float | None = None
    held: list[EtfHoldingOut] = []
    available: list[EtfHoldingOut] = []
    unresolved: list[EtfHoldingOut] = []


class PromoteThesisRequest(BaseModel):
    """The promote/update payload — a thesis-with-chain. The router builds a domain Thesis (the
    segment-consistency validator runs) under the CURRENT tenant (the resolver, not the body), then upserts
    it (create when `id` is null, update otherwise). Scores are NOT sent — they re-derive on read.
    `authored_by` is honored from the body for the two live member values (`system_drafted` = the
    description is a model draft / `operator_edited` = the operator changed it); the RETIRED
    `operator_set` is legacy-translated to `signed_off=true` + `system_drafted`, never stored
    (Discovery cleanup S1). `signed_off` (the per-name endorsement) round-trips as a marker — it never
    gates promotion.
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
    # ADS-ratio derivation metadata, carried through from the annual-cover candidate (spec §10) —
    # None/None for every 10-Q name and any hand-authored fact (the scorer then computes at 1:1).
    # "known" divides the ordinary count by the ratio; "unread" SUPPRESSES the cap (never a 1:1 guess).
    ads_ratio: int | None = None
    ads_ratio_status: Literal["known", "unread"] | None = None


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


# --- Auto-confirm — the AUTO-tier shares fact, applied without a ceremonial confirm ---


class AutoConfirmRequest(BaseModel):
    """Ask the server to auto-apply a security's AUTO-tier shares count (the get-data path).

    NOTE WHAT IS **NOT** HERE: a value. The client cannot supply the number — it names a security and a
    fact type, and the SERVER re-extracts and writes its OWN deterministic parse. That is the structural
    bound (INVARIANT #3): an auto-applied fact can only ever be the extractor's reproduction of filed
    companyfacts, so no client bug or caller can inject a figure under the ``auto`` provenance.

    ``fact_type`` is deliberately a ONE-value Literal: only the AUTO shares count is eligible. Purity is the
    operator's edge (HUMAN, never auto-valued) and cash_burn stays a manual ratify — extending this is a
    decision, not a parameter.
    """

    security_id: UUID
    fact_type: Literal["shares_outstanding"]


class AutoConfirmOut(BaseModel):
    """The outcome of an auto-confirm — honest about WHY nothing was written.

    ``applied=False`` is a normal, expected result (a FLAGged name, a name already on file), never an error:
    the caller fires this optimistically after get-data and the server decides. ``reason`` names which gate
    stopped it so the UI (and a test) can tell "we declined to auto-apply" from "it failed".
    """

    applied: bool
    # applied        — the AUTO parse was written (ratified_by="auto")
    # already_on_file — a shares fact exists (auto OR an operator override); never re-stamped, never clobbered
    # not_auto       — the candidate FLAGged (dual-class / stale-cover / no-companyfacts) -> the operator ratifies
    # no_candidate   — no shares candidate (e.g. a foreign 20-F/6-K filer)
    # no_value       — AUTO with no parsed figure (defensive; a FLAG never anchors a wrong number)
    reason: Literal["applied", "already_on_file", "not_auto", "no_candidate", "no_value"]
    fact_id: UUID | None = None


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
    # terms that returned zero EDGAR hits — a seed here placed no names (compute-on-read, response-only)
    empty_terms: list[str] = []
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
    company_name: str | None = (
        None  # the security's registered master name — shown beside the ticker
    )
    is_headline: bool = False
    theme_armed: bool = False
    arm_date: date
    dearm_date: date | None = None
    close_reason: str
    status: Literal["open", "closed"]
    matured: bool
    censored_start: bool
    # record-provenance (2d) — did the ARM rest on trustworthy ingest? Honesty flags only, composed
    # AFTER scoring (a flagged episode scores identically; it just leaves the aggregate metrics):
    arm_ingest_fresh: bool | None = None  # the arm-date run's R2b stamp, raw (None = legacy)
    freeze_era: bool = False  # armed inside the 2026-07 EDGAR freeze window
    thaw_lag_days: int | None = None  # max ingest lag of the arm's cited form4 facts
    ingest_flagged: bool = False  # the rollup: the INGEST badge + metric exclusion
    ingest_note: str | None = None  # the composed human "why" — None when clean
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
    e: ScoredEpisode,
    ciks: Mapping[UUID, str | None],
    tickers: Mapping[UUID, str | None],
    names: Mapping[UUID, str | None],
) -> ScoreboardEpisodeOut:
    ep, out = e.episode, e.outcome
    return ScoreboardEpisodeOut(
        thesis_id=ep.thesis_id,
        security_id=ep.security_id,
        ticker=tickers.get(ep.security_id),
        company_name=names.get(ep.security_id),
        is_headline=ep.is_headline,
        theme_armed=ep.theme_armed,
        arm_date=ep.arm_date,
        dearm_date=ep.dearm_date,
        close_reason=ep.close_reason,
        status=e.status,
        matured=e.matured,
        censored_start=e.censored_start,
        arm_ingest_fresh=e.arm_ingest_fresh,
        freeze_era=e.freeze_era,
        thaw_lag_days=e.thaw_lag_days,
        ingest_flagged=e.ingest_flagged,
        ingest_note=e.ingest_note,
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
    t: ThesisRecord,
    ciks: Mapping[UUID, str | None],
    tickers: Mapping[UUID, str | None],
    names: Mapping[UUID, str | None],
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
        episodes=[_scoreboard_episode_out(e, ciks, tickers, names) for e in t.episodes],
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
    n_ingest_flagged: int = 0  # provenance rollup (2d): ledger-visible, out of the aggregates
    n_eligible: int
    n_takes: int = 0  # the operator track: non-voided decisions <= asof
    n_passes: int = 0
    n_overrides: int = 0
    n_voided: int = 0
    record_began: date | None = None
    banner: str
    min_n: int
    metrics: list[ScoreboardMetricOut] = []
    # 2e — the maturity horizon (asof-pure: the countdown is coherent on a scrubbed view too). The
    # projection counts only non-censored, non-flagged future maturities — over currently-recorded
    # episodes, never a promise (new arms or de-arms shift it).
    next_maturity: date | None = None  # min FUTURE exit_by, ledger-wide
    n_maturing_30d: int = 0
    projected_min_n_date: date | None = None  # None = already cleared, or not reachable
    # Record freshness (compute-on-read; the read still writes nothing) — is the call-of-record
    # current NOW, measured against the last EXPECTED Mon-Fri+RUN_AT run (never raw today - edge),
    # exactly like AdminRecordOut. Asof-INDEPENDENT (the record edge + the clock drive it, not the
    # scrubbed view); the FE shows it only on the live view (asof >= today). ``record_edge`` None =
    # the record has never begun — the QUIET state (``days_behind`` None, ``stale`` False).
    record_edge: date | None
    expected_asof: date
    days_behind: int | None
    stale: bool = False
    today: date


class ScoreboardResponse(BaseModel):
    """The Scoreboard: the call-of-record scored as-of ``asof`` — the record, never a recompute."""

    asof: date
    generated_at: str  # known_at honesty stamp (ISO): when this read of the record was taken
    summary: ScoreboardSummaryOut
    theses: list[ScoreboardThesisOut] = []


class PriceBar(BaseModel):
    """One realized daily OHLCV bar in an episode's price window (the sparkline draws ``close``; the
    full bar rides the wire so a later candlestick is a pure-frontend swap — no second contract change).
    ``close`` is non-null (null-close rows are skipped); ``open``/``high``/``low``/``volume`` are nullable
    per-column — a close-only free-EOD bar surfaces them as ``null``, never invented (invariant #6).
    ``sma50``/``sma200`` are the trailing simple moving averages of ``close``, server-computed over a
    warm-up read so the window's LEFT edge is honest; ``null`` where too little history precedes the bar
    (a young security / early history) — an honest gap the context line simply begins after, never padded.
    """

    d: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    volume: float | None = None
    sma50: float | None = None
    sma200: float | None = None


class InsiderBuyOut(BaseModel):
    """One code-P insider purchase inside an episode's window (Slice A, characters in Band 03 S2c) — an
    overlay chip / event-ledger row.

    ``d`` = the transaction date (``valid_from``, the chip's x-position). TWO honest clocks (the MRVL
    two-clock fix — invariant #6): ``disclosed`` = the real SEC acceptance date (``accepted::date``, when the
    filing became public), ``None`` when unresolved (pre-backfill / unresolvable — the FE renders the
    "ingested" line only, recall-safe #9); ``ingested`` = ``recorded_at::date`` (when OUR pipeline wrote the
    row), rendered as a SECOND line only when it differs from ``disclosed``. On the rebuilt demo a Form 4
    accepted ~2d after its txn but re-ingested in 2026 reads "disclosed 2d later · ingested 326d later" —
    honest, where the old single ``disclosed=recorded_at`` field lied "disclosed 326d". ``aff_10b5_1``
    (TRUE/FALSE/NULL) flags a Rule 10b5-1 pre-scheduled plan (a note rendered only on an explicit TRUE,
    never a different number). Every field traces to a real ``fact_insider_txn`` row; both time axes are
    asof-capped (no-lookahead, #1 — the transaction-time gate keys on ``recorded_at`` like every fact table;
    ``accepted`` is a display/metrics column, never a gate).

    ``character`` is the buy's server-side classification (deterministic field predicates — #3, the same
    predicates the NamePanel's open-market screen composes): ``open_market`` means "passed the available
    screens", NEVER "proven discretionary" — a buy with no day-low price context stays ``open_market``;
    ``self_filing`` = the issuer filing a Form 4 on its own stock (labeled, still counted in the panel's
    90d net-flow — that re-base is deferred); ``primary_market`` (an offer-price IPO/PIPE/placement
    subscription) and ``implausible`` (bad source data) are SET-ASIDE rows the panel's open-market figure
    excludes — surfaced greyed + labeled instead of hidden (WB #2), so the ledger shows why a buy did or
    didn't count (#6). The chart's non-set-aside dots reconcile with the panel's net-flow.
    """

    d: date
    insider_name: str | None = None
    insider_role: str | None = None
    shares: float | None = None
    usd: float | None = None
    aff_10b5_1: bool | None = None
    disclosed: date | None = (
        None  # SEC acceptance date; None = unresolved -> FE falls back to "ingested" (#9)
    )
    ingested: (
        date  # recorded_at::date — our ingest time; the second "ingested" line when it differs
    )
    character: Literal["open_market", "self_filing", "primary_market", "implausible"]


class InsiderSellOut(BaseModel):
    """One code-S insider sale inside an episode's window (Slice B — the sell mirror of
    ``InsiderBuyOut``): an overlay chip / event-ledger row.

    Same shape and the same two honest clocks as the buy (``d`` = transaction date; ``disclosed`` =
    ``accepted::date``, ``None`` when unresolved → the FE falls back to the "ingested" line, #9;
    ``ingested`` = ``recorded_at::date``), both time axes asof-capped (no-lookahead #1, the
    ``recorded_at`` gate like every fact table).

    ``character`` is the sale's server-side classification via the CALL side's sell screen
    (``signals.insider_sell._screen`` — deterministic field predicates, #3), wire-mapped
    (``self`` → ``self_filing``, ``foreign`` → ``foreign_ordinary``): ``kept`` = what the risk
    detector's cluster counts; ``planned`` (an explicit 10b5-1 True — near-noise), ``self_filing``
    (the issuer transacting its own stock), ``below_low`` (a discounted secondary — a different risk
    family), ``implausible`` (bad source data), and ``foreign_ordinary`` (a home-market ordinary line
    mis-filed on the ADR's tape) are SCREENED rows — surfaced greyed + labeled instead of hidden
    (WB #2), so the ledger shows why a sale did or didn't count (#6).

    DIAL-MIRROR CAVEAT: the labels are classified with ``DEFAULT_CONFIG`` pinned — a deployment
    running non-default insider dials could show characters that drift from the call's actual cluster
    screens. The display rail's accepted posture; labels only, never the detector's math.
    """

    d: date
    insider_name: str | None = None
    insider_role: str | None = None
    shares: float | None = None
    usd: float | None = None
    aff_10b5_1: bool | None = None
    disclosed: date | None = None  # SEC acceptance date; None -> FE falls back to "ingested" (#9)
    ingested: date  # recorded_at::date — our ingest time
    character: Literal[
        "kept", "planned", "self_filing", "below_low", "implausible", "foreign_ordinary"
    ]


class CorporateEventOut(BaseModel):
    """One stored 8-K filing inside an episode's window (Slice B) — an overlay chip / ledger row.

    ``d`` = ``valid_from`` = ``filed`` (an 8-K is knowable exactly when EDGAR disseminates it, so the
    one event clock plus ``ingested`` suffices — no separate ``disclosed``). ``items`` is the SEC's
    own item-code list; ``None`` = not-yet-resolved, rendered honestly as "items unresolved", never
    dropped (#9). ``url`` is the EDGAR filing-index URL (#6). EVERY stored 8-K in the window rides —
    there is NO server-side item cut (loudness is a display concern; a cut would be a silent filter).
    Both time axes are asof-capped (no-lookahead #1)."""

    d: date
    form: str
    items: list[str] | None = None  # None = not-yet-resolved (honest), never invented
    url: str
    ingested: date


class ActivistStakeOut(BaseModel):
    """One stored 13D/G-family filing about the episode's security (Slice B) — an overlay chip /
    ledger row.

    ``d`` = ``valid_from`` = ``filed`` (knowability — never the in-document event date). ``form`` is
    verbatim, BOTH naming eras (``SC 13D`` / ``SCHEDULE 13D`` / amendments / the 13G family).
    Unresolved identity ships null and the row is KEPT (#9): ``filer_name`` / ``filer_cik`` ``None``
    = identity unresolved; ``pct_owned`` ``None`` = pre-structured era / unparsed. 13G rows ride too —
    the fire policy (13D-family originals only) lives in the detector; the FE mirrors it as display
    weight (13G-family greyed-passive), never as an omission. ``url`` is the EDGAR filing-index URL
    (#6). Both time axes are asof-capped (no-lookahead #1)."""

    d: date
    form: str
    filer_name: str | None = None  # None = identity unresolved — kept, never dropped (#9)
    filer_cik: str | None = None
    pct_owned: float | None = None  # structured cover percentOfClass; None = unparsed
    url: str
    ingested: date


class ScoreboardPriceWindowOut(BaseModel):
    """One episode's realized daily OHLCV series over ``[start, end]``, CAPPED at ``asof`` server-side —
    the drawer sparkline's on-demand read (Slice 3, extended in Slice A, widened in Slice B). It is the
    SAME asof-capped window the scorer runs (``PgRealizedPrices`` — ``bars_between`` shares
    ``closes_between``'s cap/known_at), exposed on request rather than embedded in the ledger payload.
    Each bar also carries ``sma50``/``sma200`` context, and four dated event families ride beside the
    bars, ALL under the identical two-axis no-lookahead discipline (valid_from window + the
    ``recorded_at`` knowability gate): ``insider_buys`` (code-P purchases, each carrying its
    server-classified ``character``, set-aside rows riding greyed-and-labeled rather than hidden — Band
    03 S2c), ``insider_sells`` (the code-S mirror, Slice B), ``corporate_events`` (every stored 8-K —
    no server-side item cut), and ``activist_stakes`` (the 13D/G tape, unresolved identity shipped as
    null, never dropped). Invariant #1: no bar with ``d > asof`` and no event recorded after the as-of
    is ever returned, whatever ``end`` the client passes. ``source`` names the fact table the bars came
    from (invariant #6). ``start`` is the EFFECTIVE relevance floor the server computed
    (``max(thesis.created_at − 365d, first_bar)``), NOT the requested start — the loaded extent the
    FE numbers the overlay universe over (Slice A R1).
    """

    thesis_id: UUID
    security_id: UUID
    start: date  # the effective relevance floor (R1), not the requested start
    end: date
    asof: date
    source: str
    bars: list[PriceBar] = []
    insider_buys: list[InsiderBuyOut] = []
    insider_sells: list[InsiderSellOut] = []  # Slice B — additive, defaulted
    corporate_events: list[CorporateEventOut] = []  # Slice B — additive, defaulted
    activist_stakes: list[ActivistStakeOut] = []  # Slice B — additive, defaulted


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


class MemberDisplaySignalsOut(BaseModel):
    """One resolved basket member's read-only indicators. ``signals: []`` = nothing computable at
    this asof (e.g. no ingested bars yet) — an honest empty, the member still shows."""

    security_id: UUID
    ticker: str | None = None
    signals: list[DisplaySignal] = []


class DisplaySignalsResponse(BaseModel):
    """Read-only per-name display indicators, re-derived from the bitemporal facts at ``asof``.

    Display-only tape context beside the call — NEVER a SignalEvent, never an input to the call,
    and never recorded: a day-varying field on the recorded domain CallCard would break the daily
    cron's ``record_if_changed`` idempotency (one appended calls row per night), so indicators ride
    this compute-on-read endpoint instead. See ``docs/DISPLAY_SIGNALS.md``.
    """

    thesis_id: UUID
    asof: date
    # THESIS-LEVEL theme-breadth thrust (§1.1) — the share of resolved basket members at/above their 50d
    # SMA plus the 20-bar surge (the loud "thrust" state, #7). DISPLAY-only, never a SignalEvent / call
    # input (#4); ``None`` when the thesis has no resolved members. Rides the SAME ``DisplaySignal`` wire
    # the per-name members use (``headline.key`` = ``thrust`` | ``quiet`` | ``unknown``), so the FE renders
    # it with the generic display block — the FE chip that reads it is a later step.
    breadth: DisplaySignal | None = None
    # THESIS-LEVEL supersector RS rollup (§1.3) — "which theme is leading the market": the resolved
    # members grouped by BusinessSupersector, each with a count of how many LEAD (RS vs SPY at a fresh
    # 13-week high). DISPLAY-only (#4), same generic ``DisplaySignal`` wire (``kind`` = ``sector_rs``,
    # ``headline.key`` = ``leading`` | ``quiet`` | ``unknown``, one metric per supersector). ``None`` when
    # the basket is empty OR no benchmark tape is ingested yet (RS uncomputable — an honest absence).
    sector_rs: DisplaySignal | None = None
    members: list[MemberDisplaySignalsOut] = []


# --- Backups (Slice 4): the operator DB-snapshot button — create + list + retain (NEVER restore) ---
# Defined BEFORE the Admin ops block so BackupOut is in scope for AdminStatusOut.last_backup (below).


class BackupOut(BaseModel):
    """One snapshot as a value-free file listing — ``name`` (``alphadeck-<UTC>[-<label>].sql``),
    ``bytes`` (file size), ``created_at`` (parsed from the filename timestamp, UTC), and ``labeled``
    (a named, prune-EXEMPT dump). Computed on read from the backups directory; writes nothing."""

    name: str
    bytes: int
    created_at: datetime
    labeled: bool


class BackupsOut(BaseModel):
    """The snapshot list — newest first. A pure directory read; an unreadable/foreign entry is skipped
    fail-open (the run-log read discipline)."""

    backups: list[BackupOut] = []


class BackupCreateIn(BaseModel):
    """The optional create body. A ``label`` marks a NAMED snapshot that retention never auto-prunes (a
    deliberate recovery point like ``pre-migration``); omit it for a rolling nightly-style dump."""

    label: str | None = None


class BackupJobRef(BaseModel):
    """The 202 kick-off body — the snapshot started as a background JOB (``pg_dump`` runs ~30-90s). Poll
    ``GET /admin/backup/jobs/{job_id}`` for the result."""

    job_id: str
    status: Literal["running", "done", "failed"]


class BackupJobStatus(BaseModel):
    """The poll body. ``done`` carries the finished snapshot as ``result`` (the same ``BackupOut`` the
    list shows — the ``.sql`` on disk is the durable record regardless); ``failed`` carries an
    operator-facing ``error``. A 404 on the poll = unknown / expired / restart-wiped job — the FE shows a
    visible "lost from view", never an infinite spinner."""

    job_id: str
    status: Literal["running", "done", "failed"]
    result: BackupOut | None = None
    error: str | None = None


# --- Admin ops surface (Slice 1): freshness/health READS + the explicit "run now" trigger ---


class AdminRunOut(BaseModel):
    """One daily pass, as the run-of-record artifact recorded it (``pipeline/cron_run_log.py`` — the
    field names are the ARTIFACT's, not inventions): counts + outcomes only, value-free. ``healthy`` /
    ``problems`` are ``assess_health`` re-read from the same numbers, so the freeze detector
    (``edgar_fetches == 0`` on a live run), withheld calls, and thesis errors surface on every row — a
    bad run can never hide behind a green history. ``mode`` is ``"live" | "no-live"`` (the R2
    recording-gate signal); ``ran_at`` is the artifact's ``started_at`` (UTC ISO)."""

    ran_at: str
    finished_at: str
    duration_s: float
    asof: date
    mode: str
    theses: int
    appended: int
    unchanged: int
    withheld: int
    errored: int
    transitions: int
    edgar_fetches: int
    healthy: bool
    problems: list[str] = []


class AdminRecordOut(BaseModel):
    """The record's freshness vs the LAST EXPECTED scheduled run — never raw ``today - edge``: a
    Friday edge on a Monday morning is CURRENT (no run was due yet), the same edge Monday night is 1
    behind (stale). ``edge is None`` = the record has never begun — a QUIET state (``days_behind``
    None, ``stale`` False), never an alarm on a fresh install."""

    edge: date | None
    today: date
    expected_asof: date
    days_behind: int | None
    stale: bool
    reason: str


class AdminCronOut(BaseModel):
    """The one-word cron verdict + a plain-English detail. ``unhealthy`` (the LAST run froze / errored /
    withheld on total ingest failure) is deliberately its own LOUD state, peer to ``stale`` — a bad run
    must read as loud as a missing one, never hide behind green (the R1 freeze lesson). A benign
    ``--no-live`` dev run is NOT unhealthy. ``never_ran`` = no run artifact at all (quiet)."""

    status: Literal["healthy", "stale", "never_ran", "unhealthy"]
    detail: str


class AdminStatusOut(BaseModel):
    """The admin page's one-GET summary: record freshness + the newest run + the cron verdict + the
    newest DB snapshot. READ-ONLY — the endpoint owns no tables and writes nothing (test-proved; the
    ``last_backup`` join is a pure directory read). ``last_backup`` is ``None`` = the quiet "no
    snapshots yet" state."""

    record: AdminRecordOut
    last_run: AdminRunOut | None = None
    cron: AdminCronOut
    last_backup: BackupOut | None = None


class AdminRunsOut(BaseModel):
    """The run history — parsed run-of-record artifacts, newest first. An unreadable artifact is
    SKIPPED fail-open (a corrupt night never blanks the history)."""

    runs: list[AdminRunOut] = []


class AdminRunJobRef(BaseModel):
    """The 202 kick-off body — the daily pass started as a background JOB (a cold pass runs ~65 min;
    held open it would 504 at the proxy). Poll ``GET /admin/run-daily/jobs/{job_id}`` for the result.
    """

    job_id: str
    status: Literal["running", "done", "failed"]


class AdminRunJobStatus(BaseModel):
    """The poll body. ``done`` carries the finished pass as ``result`` (the same ``AdminRunOut`` shape
    the run history shows — the pass also wrote the durable artifact + calls rows itself); ``failed``
    carries an operator-facing ``error``. A 404 on the poll = unknown / expired / restart-wiped job —
    the FE shows a visible "lost from view" and points at the run history, never an infinite spinner.
    """

    job_id: str
    status: Literal["running", "done", "failed"]
    result: AdminRunOut | None = None
    error: str | None = None


class SpacMatchOut(BaseModel):
    """One thesis's term-set hits against one radar filing (SPAC Radar, slice 2) — a RECOMMENDATION
    (INVARIANTS #10): display-only provenance, it changes nothing until the operator clicks an
    action. The matched term STRINGS by tier (the ``matched_terms`` idiom); ``truncated`` = the
    document was capped before matching (no silent caps — a cap must never read as "no match")."""

    thesis_id: UUID
    thesis_name: str
    signal_terms: list[str]
    broad_terms: list[str]
    truncated: bool = False


class SpacEventOut(BaseModel):
    """One blank-check TRANSITION filing on the radar tape (SPAC Radar, slice 1) plus its read-time
    ``deal_state`` (derived from the CIK's FULL event history — searching → announced → terminated |
    completed; announcement and termination are a PAIR, a dead deal never reads live). ``items`` =
    8-K item codes when resolvable (None = unknown → the state derive ignores the filing). A DA is
    a LEAD, not a live name: the target is pre-liquidity and the deal can die — the state chip is
    the honest frame. ``in_basket_of`` = theses already holding this security (drives the
    reversible added-toggle)."""

    cik: str
    ticker: str | None = None
    company_name: str
    security_id: UUID | None = None
    form: str
    items: list[str] | None = None
    filed: date
    accession: str
    url: str
    deal_state: Literal["searching", "announced", "terminated", "completed"]
    in_basket_of: list[UUID] = Field(default_factory=list)
    matches: list[SpacMatchOut] = Field(default_factory=list)


class RadarSpacOut(BaseModel):
    """The SPAC Radar tape (``GET /radar/spac``): the latest version of each transition filing in
    the window, newest first, with per-CIK deal state and per-thesis term matches. Pull-only and
    quiet by design (#7 — a blank check is maximally early); every row links its filing (#6)."""

    events: list[SpacEventOut]
    window_days: int
    shells_known: int


class SpacAttachRequest(BaseModel):
    """Body for ``POST /radar/spac/attach`` / ``.../detach`` — one radar name, one thesis. The
    server resolves the CIK to the canonical master row and derives ``surfaced_terms`` from the
    stored matches (never client-supplied provenance)."""

    thesis_id: UUID
    cik: str


class SpacAttachOut(BaseModel):
    """The attach/detach receipt. ``added`` / ``removed`` say what actually happened; ``already``
    marks the idempotent no-op (the member was already in the basket). Reversible by design
    (workbench principle #1): attach ⇄ detach round-trips to the prior state."""

    thesis_id: UUID
    security_id: UUID | None = None
    ticker: str | None = None
    added: bool = False
    already: bool = False
    removed: bool = False
