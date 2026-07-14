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


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: score an open-market insider cluster into a Key-1 SignalEvent (or None).

    Reads only open-market purchases (code 'P'); never fires on sales. The cluster is anchored on the
    most-recent buy (its FIRE date) and gathers the buys within the cohesion window before it — one
    episode of buying. It stays in the re-derived stream until its GRADED alpha horizon decays (a flip
    in weeks, a CORE cluster over months), so the lookback never drops a still-live conviction.
    Grade rule (§3, config-driven): core if a senior officer + >= N distinct insiders + >= $ threshold.
    """
    p_buys = [
        t
        for t in txns
        if t.get("txn_code") == "P" and t.get("valid_from") is not None and t["valid_from"] <= asof
    ]
    if not p_buys:
        return None
    # FIRE date = the most-recent open-market buy; the cluster = the buys within the cohesion window
    # before it (so unrelated buys months apart aren't fused into one cluster). Stamping the event at
    # the anchor (not the query asof) anchors exit_by/liveness to when conviction actually formed.
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
    """Key 1 — insider conviction (warms). Reads open-market purchases via the point-in-time view."""
    return score(pit.insider_txns(security_id), security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
