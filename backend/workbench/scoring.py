"""The Workbench scoring engine (Slice 3) — pure, data-derived, re-derived on read (Option B).

``score_member(pit, member, cfg) -> ScoredMember``: each figure comes from a tenant-filtered as-of read off
the ``PointInTimeData`` (no-lookahead for free); nothing persists. Every 0-4 pip cutoff comes from
``CallConfig`` — there are NO hardcoded thresholds here (the magic-number discipline; guarded by a behavioral
test + a lexical float-literal scan). The dilution meter reuses the back half's dilution clock (the shared
``dilution_clock.overhang_pct`` — the raw overhang, never backed out of the clamped risk severity); the
catalyst meter reuses the back half's ``catalyst_conviction.liveness`` window. Scores never touch the
operational thesis columns.
"""

from __future__ import annotations

from typing import Any

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Archetype, Grade
from domain.signal import Provenance
from domain.thesis import BasketMember, Thesis
from domain.workbench import ScoredFigure, ScoredMember
from signals import catalyst_conviction, dilution_clock
from signals.base import PointInTimeData

_MONTHS_PER_QUARTER = 3  # quarterly burn -> monthly (a calendar fact, not a calibration dial)

# fit label: purity drives the base read; runway/dilution append a risk flag. Pip-level (not float) rules.
_FIT_BY_PURITY = {
    4: "pure-play",
    3: "core exposure",
    2: "partial exposure",
    1: "peripheral",
    0: "off-thesis",
}
_FIT_FUNDING_RISK_PIP = 1  # runway pips <= this -> "funding risk"
_FIT_DILUTION_RISK_PIP = 3  # dilution pips >= this -> "dilution risk"


def _bucket(value: float, cutoffs: tuple[float, ...]) -> int:
    """0..len(cutoffs) pips: how many ascending lower-bound cutoffs ``value`` meets or exceeds."""
    return sum(1 for c in cutoffs if value >= c)


def _prov(fact: dict[str, Any]) -> Provenance:
    """Provenance from a scoring fact (revenue_mix / shares / cash_burn): the ``source`` (the BASIS — e.g.
    ``10-k-segment`` vs ``10-k-business-description``), the ``source_ref`` (the filing), and the ``note``.
    """
    detail = {"note": fact["note"]} if fact.get("note") else {}
    return Provenance(source=fact["source"], ref=fact["source_ref"], detail=detail)


def _purity(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Exposure concentration — the largest revenue-line % for the name (the basis rides ``source``)."""
    facts = pit.revenue_mix_facts(sid)
    if not facts:
        return ScoredFigure()  # "—": no purity data
    best = max(facts, key=lambda f: float(f["mix_pct"]))
    pct = float(best["mix_pct"])
    return ScoredFigure(pips=_bucket(pct, cfg.purity_pip_pct), value=pct, provenance=[_prov(best)])


def _runway(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Months of funding at the current burn (cash / monthly burn); cash-generative reads max (top pip)."""
    facts = pit.cash_burn_facts(sid)
    if not facts:
        return ScoredFigure()  # "—": no cash/burn data
    best = max(facts, key=lambda f: f["valid_from"])
    cash, burn = float(best["cash_usd"]), float(best["quarterly_burn_usd"])
    if burn <= 0:  # cash-generative: no burn-funding risk -> the top pip, no finite months figure
        return ScoredFigure(pips=len(cfg.runway_pip_months), value=None, provenance=[_prov(best)])
    months = cash / (burn / _MONTHS_PER_QUARTER)
    return ScoredFigure(
        pips=_bucket(months, cfg.runway_pip_months),
        value=round(months, 1),
        provenance=[_prov(best)],
    )


def _catalysts(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Density of LIVE catalysts (the back half's liveness window). Count + the core-vs-flip grade: 0 live
    -> 0; 1 live core -> 2, 1 live flip -> 1; >= multi -> 3; >= dense -> 4. (0 is a real reading, not "—".)
    """
    facts = pit.catalyst_facts(sid)
    live = [
        f
        for f in facts
        if (pit.asof - f["valid_from"]).days <= catalyst_conviction.liveness(f, cfg)
    ]
    n = len(live)
    if n == 0:
        pips = 0
    elif n >= cfg.catalyst_pip_dense_count:
        pips = 4
    elif n >= cfg.catalyst_pip_multi_count:
        pips = 3
    else:  # exactly one live catalyst — graded
        pips = 2 if any(Grade(f["grade"]) is Grade.CORE for f in live) else 1
    prov = [Provenance(source=f["source"], ref=f["source_ref"]) for f in live]
    return ScoredFigure(pips=pips, value=float(n), provenance=prov)


def _dilution(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Convert-overhang PRESSURE — reuses the back half's dilution clock. The raw ``overhang_pct`` (one
    source of overhang); the 4-pip bar reuses ``dilution_overhang_severe_pct``. No convert fact -> "—".
    """
    facts = pit.dilution_facts(sid)
    pct = dilution_clock.overhang_pct(facts, sid, pit.asof)
    if pct is None:
        return ScoredFigure()  # "—": no convert data (no fake zero)
    cutoffs = (*cfg.dilution_pip_pct, cfg.dilution_overhang_severe_pct)
    sig = dilution_clock.score(facts, sid, pit.asof, cfg)  # reuse for the rich convert provenance
    prov = list(sig.provenance) if sig is not None else []
    return ScoredFigure(pips=_bucket(pct, cutoffs), value=round(pct, 1), provenance=prov)


def _market_cap(pit: PointInTimeData, sid) -> ScoredFigure:
    """A FIGURE, not a meter: latest close x latest shares (total-economic). One input alone can't
    compute a cap — but a RATIFIED input must stay VISIBLE (value None + its provenance + a note naming
    the missing half). A bare "—" here made a confirm on a price-less fresh name read as a silent no-op
    (the gate-3 finding: the operator ratified MU's shares three times looking for ANY change — the
    fact was on file all along; the other input, price, comes from the per-thesis back-half ingest).
    """
    shares_facts = pit.shares_outstanding_facts(sid)
    prices = pit.price_history(sid)
    if not shares_facts and not prices:
        return ScoredFigure()  # "—": nothing on file
    if not prices:
        sh = max(shares_facts, key=lambda f: f["valid_from"])
        awaiting = Provenance(
            source="computed",
            ref="market-cap:awaiting-price",
            detail={
                "note": "No price bars for this name yet — market cap = shares × price; the "
                "per-thesis ingest (the back half) pulls prices. The ratified shares fact IS on file."
            },
        )
        return ScoredFigure(pips=None, value=None, provenance=[_prov(sh), awaiting])
    if not shares_facts:
        latest = prices[-1]
        awaiting = Provenance(
            source="price",
            ref=f"price:{latest['d']}",
            detail={
                "close": float(latest["close"]),
                "note": "Price on file but NO ratified shares yet — extract → ratify the cover count "
                "to compute the cap.",
            },
        )
        return ScoredFigure(pips=None, value=None, provenance=[awaiting])
    sh = max(shares_facts, key=lambda f: f["valid_from"])
    latest = prices[-1]  # price_history is sorted ascending by date
    cap = float(sh["shares"]) * float(latest["close"])
    prov = [
        _prov(sh),
        Provenance(
            source="price", ref=f"price:{latest['d']}", detail={"close": float(latest["close"])}
        ),
    ]
    return ScoredFigure(pips=None, value=round(cap), provenance=prov)


def _fit(purity_pips: int | None, runway_pips: int | None, dilution_pips: int | None) -> str:
    """A deterministic label from the pips (NOT prose — the fit paragraph is the LLM's job, Slice 5):
    purity drives the base read, runway/dilution append a risk flag."""
    base = "unrated" if purity_pips is None else _FIT_BY_PURITY[purity_pips]
    mods = []
    if runway_pips is not None and runway_pips <= _FIT_FUNDING_RISK_PIP:
        mods.append("funding risk")
    if dilution_pips is not None and dilution_pips >= _FIT_DILUTION_RISK_PIP:
        mods.append("dilution risk")
    return base + (" · " + " · ".join(mods) if mods else "")


def _archetype_hint(
    market_cap: ScoredFigure, purity: ScoredFigure, cfg: CallConfig
) -> Archetype | None:
    """A DERIVED-DEFAULT archetype recommendation (Slice 4, INVARIANT #10) — display-only, the operator
    overrides; changes nothing until confirmed. Deterministic, from the figures the scorer already computed
    (no model, no new number — #1/#3): a SIZE + PURITY read. ABSTAINS (``None``) when there's nothing to stand
    on — no market cap yet (the honest fallback; the operator's default stands) — and it NEVER guesses the
    RELATIONAL archetypes (``shovel`` = a value-chain role, ``fund`` = an ETF — those stay the operator's).
    It RECOMMENDS, never decides."""
    cap = market_cap.value
    if cap is None:
        return None  # no facts yet -> abstain (no recommendation; the operator's default stands)
    if purity.pips is not None and purity.pips <= cfg.archetype_adjacent_max_purity_pip:
        return Archetype.ADJACENT  # off-thesis / peripheral exposure (the _fit "off-thesis" read)
    if cap >= cfg.archetype_leader_min_cap_usd:
        return (
            Archetype.LEADER
        )  # large-cap bellwether (ABSOLUTE, not relative-to-basket — see CallConfig)
    if cap >= cfg.archetype_high_beta_min_cap_usd:
        return Archetype.HIGH_BETA  # small / mid amplifier
    return Archetype.LOTTO  # micro / nano — the small-size, binary lotto


def score_member(
    pit: PointInTimeData, member: BasketMember, cfg: CallConfig = DEFAULT_CONFIG
) -> ScoredMember:
    """Score one basket member from the point-in-time facts. The member MUST have a ``security_id`` (no
    facts to read otherwise — ``score_thesis`` filters)."""
    sid = member.security_id
    purity = _purity(pit, sid, cfg)
    runway = _runway(pit, sid, cfg)
    catalysts = _catalysts(pit, sid, cfg)
    dilution = _dilution(pit, sid, cfg)
    market_cap = _market_cap(pit, sid)
    # The fact-backed meters (purity / runway / market cap) each rest on an extract->ratify scoring fact; a BLANK
    # one (no pip AND no value) has no operator-confirmed fact yet — an unconfirmed estimate the operator can
    # confirm. Count the blanks so the surface flags "rests on N unconfirmed" honestly. Confidence signal only;
    # the score is unchanged (still confirmed-only). (catalysts/dilution ride other feeds, not this loop.)
    unconfirmed = sum(1 for f in (purity, runway, market_cap) if f.pips is None and f.value is None)
    return ScoredMember(
        security_id=sid,
        archetype=member.archetype,
        archetype_hint=_archetype_hint(market_cap, purity, cfg),
        segment=member.segment,
        purity=purity,
        runway=runway,
        catalysts=catalysts,
        dilution=dilution,
        market_cap=market_cap,
        fit=_fit(purity.pips, runway.pips, dilution.pips),
        unconfirmed_estimates=unconfirmed,
    )


def score_thesis(
    pit: PointInTimeData, thesis: Thesis, cfg: CallConfig = DEFAULT_CONFIG
) -> list[ScoredMember]:
    """Score every basket member that has a resolved security (re-derived on read; nothing persisted)."""
    return [score_member(pit, m, cfg) for m in thesis.basket if m.security_id is not None]
