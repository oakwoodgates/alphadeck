from __future__ import annotations

from datetime import date, timedelta
from typing import Any, NamedTuple
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "insider_conviction"


def _is_senior(role: str | None, keywords: frozenset[str]) -> bool:
    if not role:
        return False
    r = role.lower()
    return any(k in r for k in keywords)


def _score(n_distinct: int, total_usd: float, senior: bool, cfg: CallConfig) -> float:
    # Conservative, bounded: scales with cluster breadth + seniority + size, capped below "certain".
    s = 0.4
    s += min(n_distinct, 3) * 0.1
    s += 0.2 if senior else 0.0
    s += min(total_usd / (cfg.insider_core_min_usd * 4.0), 1.0) * 0.2
    return round(min(s, 0.95), 4)


def _is_open_market_buy(txn: dict[str, Any], day_lows: dict[date, float], cfg: CallConfig) -> bool:
    """Does this code-P purchase belong in the OPEN-MARKET conviction total?

    SEC transaction code 'P' is "open market OR PRIVATE purchase" — not a synonym for open-market. A
    primary-market subscription (IPO allocation / PIPE / private placement) files as code P at the OFFER
    price, which sits below the security's public trading range that day; counting it as open-market
    conviction inflated the call (PBLS: RA Capital's $394M IPO subscription at $20 vs a $29.65-$34.47 tape).
    We separate the two the only way the structured data allows — the buy's price vs the security's own EOD
    low for the day (see ``CallConfig.insider_offmarket_below_low_frac``). Two exclusions:

    - **below the day's low** → a primary-market/offer-price subscription, not an open-market purchase.
    - **above the absolute $ ceiling** (``insider_max_plausible_txn_usd``) → a physically-impossible row
      (bad source data: a $100,000/share price → a $2T buy) the price check misses.

    Recall-safe (#9): no day low → KEEP (never silently drop); a genuine open-market print is within
    [low, high], so the below-low rule cannot exclude one save a name that REVERSE-split between the buy and
    asof (documented limitation). Excluded rows STAY in ``fact_insider_txn`` + the display tape — only the
    CALL skips them.
    """
    if float(txn.get("usd") or 0.0) > cfg.insider_max_plausible_txn_usd:
        return False
    price = txn.get("price")
    low = day_lows.get(txn.get("valid_from"))
    if price is not None and low is not None:
        if float(price) < low * (1.0 - cfg.insider_offmarket_below_low_frac):
            return False
    return True


def _norm_entity(name: str | None) -> str:
    """Minimal normalization for a self-filing name match: casefold + collapse whitespace + drop a trailing
    period ('Roivant Sciences Ltd.' -> 'roivant sciences ltd'). Deliberately does NOT strip corporate
    suffixes (CORP/INC/LP): a self-filing has the SAME name on both sides, so no stripping is needed to match
    — and stripping would only widen the match and risk excluding a real, non-self buy (recall-safe, #9).
    """
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def _is_issuer_self(txn: dict[str, Any], issuer_name: str | None) -> bool:
    """Did the ISSUER file this Form 4 on ITSELF (reporting owner == issuer)?

    SEC code 'P' is filed by any acquirer, not just the officers/directors the open-market-purchase
    literature (Lakonishok-Lee) is about. The cleanest false positive is the company filing on its own stock
    — KYOCERA-on-KYOCERA ($690M @ $21.75), Roivant-on-Roivant ($350M @ $21): a buyback / treasury / ADR
    mechanic priced AT the market (so the price screen keeps it), never personal insider conviction (#3). We
    recognise it two ways, most-robust first:

    - **CIK equality** — ``rpt_owner_cik == issuer_cik`` (both captured from the filing; migration 0024). The
      canonical match; present on rows ingested after the capture, and it flows into replay via ``SELECT *``.
    - **name equality** — the filer name equals the issuer name (the row's captured ``issuer_name`` or, for a
      row ingested before the capture, the security's ``security_master`` name passed as ``issuer_name``).
      This is what makes the screen effective on ALREADY-ingested rows with no backfill.

    Recall-safe (#9): a self-filing is the ONLY time the filer name equals the issuer name, and the failure
    mode is one-directional — a missing CIK / name-format mismatch simply KEEPS the row (never drops a real
    buy). Excluded rows STAY in ``fact_insider_txn`` + the display tape; only the open-market conviction total
    skips them.
    """
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


class _Candidate(NamedTuple):
    """One CANDIDATE anchor's fully-evaluated cluster (see ``score``'s anchor walk).

    Only QUALIFYING candidates are built (total >= ``insider_min_usd`` and live for their own
    grade), so the selection below is a pure ranking — ``is_core`` first, then ``anchor`` — never a
    second round of floors.
    """

    is_core: bool
    anchor: date
    buys: list[dict[str, Any]]
    total_usd: float
    n_distinct: int
    senior: bool
    liveness: int


def _cluster_at(
    p_buys: list[dict[str, Any]], anchor: date, asof: date, w: float, cfg: CallConfig
) -> _Candidate | None:
    """Evaluate the cohesion cluster anchored on ``anchor`` — or None if it does not qualify.

    The window is bounded on BOTH sides, ``[anchor - insider_cluster_window_days, anchor]``: the
    upper bound is what keeps a LATER buy out of an older anchor's total (an unbounded gather would
    let the walk double-count the very buy that shadowed this anchor). Floors + grade + graded
    liveness are exactly the single-anchor rules, unchanged — only WHICH anchors get asked is new.
    """
    floor = anchor - timedelta(days=cfg.insider_cluster_window_days)
    buys = [t for t in p_buys if floor <= t["valid_from"] <= anchor]
    total_usd = float(
        sum(float(t.get("usd") or 0) * (w if t.get("aff_10b5_1") is True else 1.0) for t in buys)
    )
    if total_usd < cfg.insider_min_usd:
        return None
    distinct = {t.get("insider_name") for t in buys if t.get("insider_name")}
    senior = any(_is_senior(t.get("insider_role"), cfg.insider_senior_role_keywords) for t in buys)
    # core via a multi-insider cluster, OR a single strong senior buy above the high floor (calibration)
    is_core = senior and (
        (len(distinct) >= cfg.insider_core_min_distinct and total_usd >= cfg.insider_core_min_usd)
        or total_usd >= cfg.insider_strong_single_usd
    )
    liveness = (
        cfg.insider_core_alpha_liveness_days if is_core else cfg.insider_flip_alpha_liveness_days
    )
    # Freshness floor at the GRADED horizon (mirrors volume_breakout): drop the cluster once its edge
    # has decayed for its grade, so re-derivation/replay stays honest and a flip can't linger for months.
    if not entry_signal_is_live(anchor, liveness, asof):
        return None
    return _Candidate(is_core, anchor, buys, total_usd, len(distinct), senior, liveness)


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    day_lows: dict[date, float] | None = None,
    issuer_name: str | None = None,
) -> SignalEvent | None:
    """Pure: score an open-market insider cluster into a Key-1 SignalEvent (or None).

    Reads only open-market purchases (code 'P'); never fires on sales. Two screens keep a code-P row OUT of
    the open-market conviction total (both supplied by ``detect`` from the point-in-time view; both
    recall-safe, #9):

    - ``day_lows`` maps a trade date to the security's EOD low that day → screens out primary-market
      subscriptions (IPO/PIPE offer-price buys that file as code P but transact below the public tape) and
      physically-impossible rows (see ``_is_open_market_buy``).
    - ``issuer_name`` is the security's name → screens out a SELF-FILING (the issuer filing a Form 4 on its
      own stock — a buyback/treasury/ADR mechanic, priced AT market so the price screen keeps it), see
      ``_is_issuer_self``.

    Absent/``None`` ``day_lows``/``issuer_name`` only disables that one screen (nothing over-excluded).

    **Anchor selection (the walk).** A cluster is one EPISODE of buying: an anchor date plus the kept
    buys inside ``[anchor - insider_cluster_window_days, anchor]``. Every distinct kept-buy date is a
    CANDIDATE anchor; each is evaluated independently (total, distinct, seniority, grade, and the
    GRADED liveness — a flip decays in weeks, a CORE cluster over months) and QUALIFIES iff its total
    clears ``insider_min_usd`` and it is still live for its own grade. Among the qualifying candidates
    the strongest fires: **prefer CORE, then the most recent anchor** — ``catalyst_conviction``'s
    selection precedent. None qualify → None.

    Anchoring UNCONDITIONALLY on the single most-recent buy (the shape before this) let one small,
    late, non-senior buy SHADOW an older still-live CORE: the new anchor's 30-day window excluded the
    big buy, the grade fell to flip, and the flip's 18-day liveness then expired the whole conviction —
    so *more* insider buying read *less* bullish, contradicting this docstring's own "the lookback
    never drops a still-live conviction". The walk is what makes that sentence true.

    Fire-date semantics are unchanged: the event's ``asof`` is the CHOSEN cluster's anchor and the
    provenance is that cluster's accessions (a cluster spanning Jan 30 → Feb 25 reads Feb 25).
    Grade rule (§3, config-driven): core if a senior officer + >= N distinct insiders + >= $ threshold.
    """
    lows = day_lows or {}
    # DORMANT 10b5-1 planned-buy weight (Band 03 S2c, `insider_10b5_1_buy_weight`): scales a KEPT
    # planned buy's $ contribution; a planned buy is PRESENT (distinct / senior / anchor) iff w > 0
    # (w == 0.0 is a FULL screen — the buy leaves the survivor set here, so the anchor moves). At the
    # 1.0 default this hook is byte-identity (`x * 1.0` is IEEE-exact; the fold order is unchanged) —
    # flipping the dial is a measured config decision, never a code change. Tri-state: only an
    # explicit `aff_10b5_1 is True` weighs; None/False weigh 1.0 (#9). See docs/CALL_LOGIC.md §3.
    w = cfg.insider_10b5_1_buy_weight
    p_buys = [
        t
        for t in txns
        if t.get("txn_code") == "P"
        and t.get("valid_from") is not None
        and t["valid_from"] <= asof
        and _is_open_market_buy(t, lows, cfg)
        and not _is_issuer_self(t, issuer_name)
        and not (w == 0.0 and t.get("aff_10b5_1") is True)
    ]
    if not p_buys:
        return None
    # THE ANCHOR WALK (see the docstring): every distinct kept-buy date is a candidate FIRE date, newest
    # -> oldest, each evaluated on its own window so unrelated buys months apart aren't fused into one
    # cluster. Stopping short of the newest anchor is the whole point — a small late buy must not shadow
    # an older, still-live CORE. Stamping the event at the CHOSEN anchor (not the query asof) anchors
    # exit_by/liveness to when that conviction actually formed; that anchor is ALSO the date shown on the
    # call-card trigger row (event_date). See docs/CALL_LOGIC.md §6.
    # The walk stops once an anchor is older than the LONGEST configured horizon: no older candidate can
    # be live under any grade's window, so nothing is missed (and history stays cheap to walk).
    earliest_live_anchor = asof - timedelta(
        days=max(cfg.insider_core_alpha_liveness_days, cfg.insider_flip_alpha_liveness_days)
    )
    candidates: list[_Candidate] = []
    for candidate_anchor in sorted({t["valid_from"] for t in p_buys}, reverse=True):
        if candidate_anchor < earliest_live_anchor:
            break
        found = _cluster_at(p_buys, candidate_anchor, asof, w, cfg)
        if found is not None:
            candidates.append(found)
    if not candidates:
        return None
    # the strongest LIVE conviction: prefer a CORE cluster, then the most recent anchor — the same
    # selection key catalyst_conviction uses to pick among live catalysts.
    best = max(candidates, key=lambda c: (c.is_core, c.anchor))
    by_accession = {t["accession"]: t for t in best.buys if t.get("accession")}
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.INSIDER,
        grade=Grade.CORE if best.is_core else Grade.FLIP,
        score=_score(best.n_distinct, best.total_usd, best.senior, cfg),
        label=(
            f"{best.n_distinct} insider{'s' if best.n_distinct != 1 else ''}"
            f"{' incl. senior officer' if best.senior else ''} bought "
            f"${best.total_usd:,.0f} open-market (code P) across {len(best.buys)} txns"
        ),
        alpha_liveness_days=best.liveness,
        provenance=[source_provenance("form4", acc) for acc in sorted(by_accession)],
        asof=best.anchor,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — insider conviction (warms). Reads open-market purchases via the point-in-time view.

    From the SAME as-of view (no lookahead) it also builds the two screens for the open-market total: the
    per-day low map (``price_history``) that drops primary-market/off-market code-P rows
    (``_is_open_market_buy``), and the security's name (identity, not a bitemporal fact) that drops a
    self-filing — the issuer filing a Form 4 on its own stock (``_is_issuer_self``; rows carrying the
    issuer/owner CIKs match canonically without the name). A buy older than the earliest bar has no low → kept (#9).
    """
    day_lows = {
        b["d"]: float(b["low"]) for b in pit.price_history(security_id) if b.get("low") is not None
    }
    return score(
        pit.insider_txns(security_id),
        security_id,
        asof,
        cfg,
        day_lows=day_lows,
        issuer_name=pit.security_name(security_id),
    )


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
