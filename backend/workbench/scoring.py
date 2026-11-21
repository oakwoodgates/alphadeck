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
from uuid import UUID

from domain.burn_pace import stored_pace
from domain.cash_currency import currency_symbol
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade
from domain.platform_values import pick_current, ratifier_kind
from domain.signal import Provenance
from domain.splits import applicable, listed_from_row, scale_after
from domain.thesis import BasketMember, Thesis
from domain.workbench import ScoredFigure, ScoredMember
from platform_values import words
from signals import catalyst_conviction, dilution_clock
from signals.base import PointInTimeData
from signals.common import entry_signal_is_live
from signals.horizons import derive_bounds

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


def _prov(fact: dict[str, Any], *, own: bool = False, **figures: Any) -> Provenance:
    """Provenance from a scoring fact (revenue_mix / shares / cash_burn): the ``source`` (the BASIS — e.g.
    ``10-k-segment`` vs ``10-k-business-description``), the ``source_ref`` (the filing), and the ``note``.
    The extra ``figures`` are the fact's OWN ratified values (shares / cash_usd / mix_pct / …), threaded
    into ``detail`` so the ratify panel can show WHAT is on file instead of re-offering the stale extract
    candidate — the DB-free extract endpoint can't know a ratify happened; the scored read is the only
    DB-backed surface the panel sees. Display-only (#11): these ride provenance, they never make a number.

    WHO set the fact rides ``detail`` as ``set_by``, a KIND and never the stored ``ratified_by`` (V2 W5d: the
    steward's email must not reach a user or an agent). A shared value (shares, cash/burn): ``machine`` /
    ``steward`` / ``carried`` (``domain.platform_values.ratifier_kind``, the one vocabulary). The reader's own
    purity (``own``, their workspace's row): ``you`` when a person ratified it (nothing when the machine or a feed
    stored it). PROVENANCE, never a scoring input — the as-of read never branches on it (display-only).
    """
    detail: dict[str, Any] = {k: v for k, v in figures.items() if v is not None}
    if fact.get("note"):
        detail["note"] = fact["note"]
    by = fact.get("ratified_by")
    if not own:
        detail["set_by"] = ratifier_kind(by)
    elif ratifier_kind(by) != "machine":
        detail["set_by"] = "you"
    return Provenance(source=fact["source"], ref=fact["source_ref"], detail=detail)


# The READ-HORIZON declaration (``signals/horizons.py``) for the scored PIT: every table the meters read
# is UNBOUNDED (``None``). ``fact_price_eod`` in particular must stay unbounded — ``_market_cap`` prices
# off the LATEST bar as-of, however old it is (a stale/delisted name still shows "price on file"), so an
# event-time floor would flip that reading, not just trim it.
HORIZONS: dict[str, int | None] = {
    "fact_revenue_mix": None,
    "fact_cash_burn": None,
    "fact_catalyst": None,
    "fact_dilution": None,
    "fact_shares_outstanding": None,
    "fact_price_eod": None,
}


def scored_bounds() -> dict[str, int | None]:
    """The SCORED PIT's read bounds (``/workbench/theses/{id}/scored``) — derived from ``HORIZONS``, so
    every bounded table reads ``None`` (unbounded) and the basket prefetch is the whole gain."""
    return derive_bounds([HORIZONS])


def _purity(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Exposure concentration — the largest revenue-line % for the name (the basis rides ``source``)."""
    facts = pit.revenue_mix_facts(sid)
    if not facts:
        return ScoredFigure()  # "—": no purity data
    best = max(facts, key=lambda f: float(f["mix_pct"]))
    pct = float(best["mix_pct"])
    prov = _prov(best, own=True, mix_pct=pct, segment_label=best["segment_label"])
    return ScoredFigure(pips=_bucket(pct, cfg.purity_pip_pct), value=pct, provenance=[prov])


def _runway(pit: PointInTimeData, sid, cfg: CallConfig, *, applies: bool = True) -> ScoredFigure:
    """Months of funding at LAST QUARTER'S burn (cash / monthly burn); cash-generative reads max (top pip).

    The year-to-date pace (V2 extractor correctness PR 3, ``domain.burn_pace``) rides the provenance DETAIL only,
    read from the stored pace columns (else, before 0209, the note): ``ytd_quarterly_burn_usd``, ``ytd_burn_days``, ``ytd_runway_months`` (when that pace
    burns) and ``pace_caveat`` (only when the quarter is far off that pace). The pips and the value never read it.

    ``applies=False`` (V2 C1: ``securities.business_type.runway_applies`` said a runway means nothing for this
    company, a bank, insurer, broker, exchange, asset manager, lender or fund trust): no pips and no months, the
    stored cash and burn kept on the fact's provenance (numbers with their source) WITHOUT the pace (its months and
    caveat are runway readings), and a ``runway:not-applicable`` entry carrying the sentence (``_runway_not_applicable``).
    """
    facts = pit.cash_burn_facts(sid)
    if not facts:
        if not applies:
            return _runway_not_applicable([])
        return ScoredFigure()  # "—": no cash/burn data
    best = pick_current(facts)  # THE one pick (domain.platform_values, D1 a)
    cash, burn = float(best["cash_usd"]), float(best["quarterly_burn_usd"])
    # the figures' own currency (D4): the caveat sentence speaks it; the detail carries the stored code and the
    # investment split so every screen labels the cash (``domain.cash_currency``; the front end's twin)
    pace = (
        stored_pace(best, symbol=currency_symbol(best.get("currency"), source=best.get("source")))
        if applies
        else None
    )
    part = best.get("investments_usd")
    prov = _prov(
        best,
        cash_usd=cash,
        quarterly_burn_usd=burn,
        currency=best.get("currency"),  # None = not recorded: _prov drops it
        investments_usd=None if part is None else float(part),
        ytd_quarterly_burn_usd=None if pace is None else pace.ytd_quarterly_burn_usd,
        ytd_burn_days=None if pace is None else pace.ytd_days,
        ytd_runway_months=None if pace is None else pace.ytd_runway_months,
        pace_caveat=None if pace is None else pace.caveat,
    )
    if not applies:
        return _runway_not_applicable([prov])
    if burn <= 0:  # cash-generative: no burn-funding risk -> the top pip, no finite months figure
        return ScoredFigure(pips=len(cfg.runway_pip_months), value=None, provenance=[prov])
    months = cash / (burn / _MONTHS_PER_QUARTER)
    return ScoredFigure(
        pips=_bucket(months, cfg.runway_pip_months),
        value=round(months, 1),
        provenance=[prov],
    )


def _runway_not_applicable(facts: list[Provenance]) -> ScoredFigure:
    """The runway of a company it means nothing for: no pips, no value (so ``_fit`` adds no "funding risk"), the
    cash fact's provenance when one is on file, then the ``runway:not-applicable`` entry with the sentence (the
    ``market-cap:awaiting-price`` shape)."""
    na = Provenance(
        source="computed",
        ref="runway:not-applicable",
        detail={"note": words.RUNWAY_NOT_APPLICABLE},
    )
    return ScoredFigure(pips=None, value=None, provenance=[*facts, na])


def _catalysts(pit: PointInTimeData, sid, cfg: CallConfig) -> ScoredFigure:
    """Density of LIVE catalysts (the back half's liveness window). Count + the core-vs-flip grade: 0 live
    -> 0; 1 live core -> 2, 1 live flip -> 1; >= multi -> 3; >= dense -> 4. (0 is a real reading, not "—".)
    """
    facts = pit.catalyst_facts(sid)
    live = [
        f
        for f in facts
        if entry_signal_is_live(f["valid_from"], catalyst_conviction.liveness(f, cfg), pit.asof)
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
    pct = dilution_clock.overhang_pct(facts, pit.asof)
    if pct is None:
        return ScoredFigure()  # "—": no convert data (no fake zero)
    cutoffs = (*cfg.dilution_pip_pct, cfg.dilution_overhang_severe_pct)
    sig = dilution_clock.score(facts, sid, pit.asof, cfg)  # reuse for the rich convert provenance
    prov = list(sig.provenance) if sig is not None else []
    return ScoredFigure(pips=_bucket(pct, cutoffs), value=round(pct, 1), provenance=prov)


def _shares_prov(sh: dict[str, Any]) -> Provenance:
    """The shares fact's provenance entry, ADS-ratio metadata riding along WHEN PRESENT (spec §10):
    ``_prov`` drops None figures, so a legacy / domestic NULL-NULL row renders byte-identically to the
    pre-ratio shape — the regression guarantee the NULL encoding exists for."""
    return _prov(
        sh,
        shares=float(sh["shares"]),
        shares_asof=sh["valid_from"].isoformat(),
        ads_ratio=sh.get("ads_ratio"),
        ads_ratio_status=sh.get("ads_ratio_status"),
    )


def _market_cap(pit: PointInTimeData, sid) -> ScoredFigure:
    """A FIGURE, not a meter: latest close x latest shares (total-economic). One input alone can't
    compute a cap — but a share count on file must stay VISIBLE (value None + its provenance + a note naming
    the missing half). A bare "—" here made a confirm on a price-less fresh name read as a silent no-op
    (the gate-3 finding: the operator ratified MU's shares three times looking for ANY change — the
    fact was on file all along; the other input, price, comes from the per-thesis back-half ingest).

    THE ADS RATIO (spec §10): an annual foreign filer's fact is the ORDINARY count while the price feed
    carries the ADS price — they only multiply at 1:1. ``ads_ratio_status`` on the fact modulates the
    DERIVATION (never the fact): ``"known"`` divides the count by the ratio; ``"unread"`` (ADR evidence,
    no defensible ratio) WITHHOLDS the cap in the same visible shape as awaiting-price — a defaulted 1:1
    would be a multiplicative, silent, permanent error, and better detection later must move a name from
    suppressed→correct, never wrong→right. ``NULL/NULL`` is NOT APPLICABLE -> 1:1: every legacy row and
    every domestic 10-Q name has NULL, so a NULL-means-suppress encoding would blank every cap in the app
    (the storage trap the spec's §10 storage section names).
    """
    shares_facts = pit.shares_outstanding_facts(sid)
    prices = pit.price_history(sid)
    if not shares_facts and not prices:
        return ScoredFigure()  # "—": nothing on file
    if not prices:
        sh = pick_current(shares_facts)  # THE one pick (domain.platform_values, D1 a)
        awaiting = Provenance(
            source="computed",
            ref="market-cap:awaiting-price",
            detail={"note": words.CAP_NO_PRICE},
        )
        return ScoredFigure(pips=None, value=None, provenance=[_shares_prov(sh), awaiting])
    if not shares_facts:
        latest = prices[-1]
        awaiting = Provenance(
            source="price",
            ref=f"price:{latest['d']}",
            detail={
                "close": float(latest["close"]),
                "note": words.CAP_NO_SHARES,
            },
        )
        return ScoredFigure(pips=None, value=None, provenance=[awaiting])
    sh = pick_current(shares_facts)  # THE one pick (domain.platform_values, D1 a)
    latest = prices[-1]  # price_history is sorted ascending by date
    price_prov = Provenance(
        source="price", ref=f"price:{latest['d']}", detail={"close": float(latest["close"])}
    )
    status = sh.get("ads_ratio_status")
    ratio = sh.get("ads_ratio")
    if status == "unread" or (status == "known" and not (ratio and float(ratio) >= 1)):
        # ("known" with a missing/absurd ratio is a corrupt pairing the writer never produces —
        # treated as unread, because fail-closed is the only safe read of an inconsistent row.)
        withheld = Provenance(
            source="computed",
            ref="market-cap:ads-ratio-unread",
            detail={"note": words.CAP_ADS_UNREAD},
        )
        return ScoredFigure(
            pips=None, value=None, provenance=[_shares_prov(sh), price_prov, withheld]
        )
    divisor = float(ratio) if status == "known" else 1  # NULL/NULL -> not applicable -> 1:1
    provenance = [_shares_prov(sh), price_prov]
    # S2 — a split between the share count and the close: the count is as of the cover date, the close is
    # on the post-split scale, so the count is put on the close's scale (a 1-for-16 split: shares / 16). Only
    # splits in force with a first session AFTER the count's date and on or before the price's — a count
    # dated on or after the split is already post-split. Recorded by the view's ``known_at`` (#1).
    listed = [listed_from_row(r) for r in pit.split_events(sid)]
    k = scale_after(listed, sh["valid_from"], through=latest["d"])  # exact (a Fraction)
    if k != 1:
        splits = [
            s
            for s in applicable(listed, on=latest["d"])
            if sh["valid_from"] < s.effective_date <= latest["d"]
        ]
        provenance.append(
            Provenance(
                source="computed",
                ref="market-cap:split-adjusted-shares",
                detail={
                    "share_factor": float(1 / k),
                    "note": f"The share count is from {sh['valid_from'].isoformat()}, before the "
                    + "; ".join(
                        f"{s.label} (first session {s.effective_date.isoformat()})" for s in splits
                    )
                    + f" — counted x{float(1 / k):.6g} to match the post-split close.",
                },
            )
        )
    cap = float(sh["shares"]) / divisor / float(k) * float(latest["close"])
    return ScoredFigure(pips=None, value=round(cap), provenance=provenance)


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


def score_member(
    pit: PointInTimeData,
    member: BasketMember,
    cfg: CallConfig = DEFAULT_CONFIG,
    *,
    runway_applies: bool = True,
) -> ScoredMember:
    """Score one basket member from the point-in-time facts. The member MUST have a ``security_id`` (no
    facts to read otherwise — ``score_thesis`` filters). ``runway_applies`` is the runway rule's answer for it
    (``securities.business_type.runway_applies``, asked by the caller from the master; default: it applies).
    """
    sid = member.security_id
    purity = _purity(pit, sid, cfg)
    runway = _runway(pit, sid, cfg, applies=runway_applies)
    catalysts = _catalysts(pit, sid, cfg)
    dilution = _dilution(pit, sid, cfg)
    market_cap = _market_cap(pit, sid)
    # (V2 W5d, the plan's Q8) no "unconfirmed estimates" count: a blank runway or cap is a SHARED value's state
    # (awaiting the steward, no value yet), read with its words from ``GET /platform-values/...``, never a task
    # the user confirms; a blank purity is the user's own to set.
    return ScoredMember(
        security_id=sid,
        segment=member.segment,
        purity=purity,
        runway=runway,
        catalysts=catalysts,
        dilution=dilution,
        market_cap=market_cap,
        fit=_fit(purity.pips, runway.pips, dilution.pips),
    )


def score_thesis(
    pit: PointInTimeData,
    thesis: Thesis,
    cfg: CallConfig = DEFAULT_CONFIG,
    *,
    runway_na: frozenset[UUID] = frozenset(),
) -> list[ScoredMember]:
    """Score every basket member that has a resolved security (re-derived on read; nothing persisted).
    ``runway_na`` = the ids the runway rule says a runway means nothing for (V2 C1; the caller reads the master).
    """
    return [
        score_member(pit, m, cfg, runway_applies=m.security_id not in runway_na)
        for m in thesis.basket
        if m.security_id is not None
    ]
