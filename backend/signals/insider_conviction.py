from __future__ import annotations

from datetime import date, timedelta
from typing import Any
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


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    day_lows: dict[date, float] | None = None,
) -> SignalEvent | None:
    """Pure: score an open-market insider cluster into a Key-1 SignalEvent (or None).

    Reads only open-market purchases (code 'P'); never fires on sales. ``day_lows`` maps a trade date to
    the security's EOD low that day (built by ``detect`` from the point-in-time price view); it screens
    out primary-market subscriptions (IPO/PIPE offer-price buys that file as code P but transact below the
    public tape) and physically-impossible rows — see ``_is_open_market_buy``. Empty/absent ``day_lows``
    means no price context, so nothing is screened out on price (recall-safe, #9). The cluster is anchored
    on the most-recent buy (its FIRE date) and gathers the buys within the cohesion window before it — one
    episode of buying. It stays in the re-derived stream until its GRADED alpha horizon decays (a flip
    in weeks, a CORE cluster over months), so the lookback never drops a still-live conviction.
    Grade rule (§3, config-driven): core if a senior officer + >= N distinct insiders + >= $ threshold.
    """
    lows = day_lows or {}
    p_buys = [
        t
        for t in txns
        if t.get("txn_code") == "P"
        and t.get("valid_from") is not None
        and t["valid_from"] <= asof
        and _is_open_market_buy(t, lows, cfg)
    ]
    if not p_buys:
        return None
    # FIRE date = the most-recent open-market buy; the cluster = the buys within the cohesion window
    # before it (so unrelated buys months apart aren't fused into one cluster). Stamping the event at
    # the anchor (not the query asof) anchors exit_by/liveness to when conviction actually formed. This
    # anchor is ALSO the date shown on the call-card trigger row (event_date) — a cluster spanning
    # Jan 30 -> Feb 25 reads Feb 25. To display the earliest (cluster start) or the largest buy's date
    # instead, change this one line; exit_by/liveness follow it. See docs/CALL_LOGIC.md §6.
    anchor = max(t["valid_from"] for t in p_buys)
    floor = anchor - timedelta(days=cfg.insider_cluster_window_days)
    buys = [t for t in p_buys if t["valid_from"] >= floor]
    total_usd = float(sum(float(t.get("usd") or 0) for t in buys))
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
    by_accession = {t["accession"]: t for t in buys if t.get("accession")}
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.INSIDER,
        grade=Grade.CORE if is_core else Grade.FLIP,
        score=_score(len(distinct), total_usd, senior, cfg),
        label=(
            f"{len(distinct)} insider{'s' if len(distinct) != 1 else ''}"
            f"{' incl. senior officer' if senior else ''} bought "
            f"${total_usd:,.0f} open-market (code P) across {len(buys)} txns"
        ),
        alpha_liveness_days=liveness,
        provenance=[source_provenance("form4", acc) for acc in sorted(by_accession)],
        asof=anchor,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — insider conviction (warms). Reads open-market purchases via the point-in-time view.

    Also reads the security's price history (same as-of view — no lookahead) to build the per-day low
    map that screens primary-market/off-market code-P rows out of the open-market total (see ``score`` /
    ``_is_open_market_buy``). A buy older than the earliest bar we hold simply has no low → kept (#9).
    """
    day_lows = {
        b["d"]: float(b["low"]) for b in pit.price_history(security_id) if b.get("low") is not None
    }
    return score(pit.insider_txns(security_id), security_id, asof, cfg, day_lows=day_lows)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
