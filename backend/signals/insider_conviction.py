from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from signals.base import PointInTimeData


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


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: score an open-market insider cluster into a Key-1 SignalEvent (or None).

    Reads only open-market purchases (code 'P') inside the lookback window — never fires on sales.
    Grade rule (§3, config-driven): core if a senior officer + >= N distinct insiders + >= $ threshold.
    """
    cutoff = asof - timedelta(days=cfg.insider_lookback_days)
    buys = [
        t
        for t in txns
        if t.get("txn_code") == "P"
        and t.get("valid_from") is not None
        and t["valid_from"] >= cutoff
    ]
    total_usd = float(sum(float(t.get("usd") or 0) for t in buys))
    if not buys or total_usd < cfg.insider_min_usd:
        return None

    distinct = {t.get("insider_name") for t in buys if t.get("insider_name")}
    senior = any(_is_senior(t.get("insider_role"), cfg.insider_senior_role_keywords) for t in buys)
    # core via a multi-insider cluster, OR a single strong senior buy above the high floor (calibration)
    is_core = senior and (
        (len(distinct) >= cfg.insider_core_min_distinct and total_usd >= cfg.insider_core_min_usd)
        or total_usd >= cfg.insider_strong_single_usd
    )
    by_accession = {t["accession"]: t for t in buys if t.get("accession")}
    # Stamp the cluster's FIRE date = the most recent open-market buy (the event date), not the query
    # asof — so exit_by/liveness anchor to when conviction actually formed (re-derived from facts).
    event_date = max(t["valid_from"] for t in buys)
    return SignalEvent(
        detector="insider_conviction",
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.INSIDER,
        grade=Grade.CORE if is_core else Grade.FLIP,
        score=_score(len(distinct), total_usd, senior, cfg),
        fired=True,
        label=(
            f"{len(distinct)} insider{'s' if len(distinct) != 1 else ''}"
            f"{' incl. senior officer' if senior else ''} bought "
            f"${total_usd:,.0f} open-market (code P) across {len(buys)} txns"
        ),
        alpha_half_life_days=cfg.insider_alpha_half_life_days,
        provenance=[Provenance(source="form4", ref=acc) for acc in by_accession],
        asof=event_date,
    )


def detect(
    pit: PointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — insider conviction (warms). Reads open-market purchases via the point-in-time view."""
    return score(pit.insider_txns(security_id), security_id, asof, cfg)
