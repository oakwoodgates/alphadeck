from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "volume_breakout"


def _score(
    price_ratio: float, ret: float, vol_ratio: float, volume_backed: bool, cfg: CallConfig
) -> float:
    price_leg = min(max(price_ratio - 1.0, 0.0) * 10.0, 1.0)  # how far above the base high
    mom_leg = min(ret / (cfg.breakout_min_return * 2.0), 1.0) if cfg.breakout_min_return else 0.0
    base = 0.5 * price_leg + 0.5 * mom_leg
    if volume_backed:
        vol_leg = min(vol_ratio / cfg.breakout_volume_mult, 1.0) if vol_ratio else 0.0
        return round(min(0.6 * base + 0.4 * vol_leg, 0.95), 4)
    return round(
        min(0.55 * base, 0.5), 4
    )  # momentum-only: real but kept below a volume-backed score


def score(
    bars: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure Key-2 breakout over ascending EOD bars (last bar = the asof bar). Deliberately minimal.

    Reports the MOST-RECENT breakout bar still inside its alpha-liveness window — a bar whose close makes a
    new ``breakout_base_window``-day CLOSING high AND is up at least ``breakout_min_return`` over
    ``breakout_return_days`` sessions — stamped with **that bar's own date**, not the query ``asof``.
    So the firing is sticky across a consolidation (it keeps reporting the breakout until it decays)
    and re-anchors when a fresher breakout prints; the assembler decides whether it is still live.
    The freshness floor mirrors the assembler's liveness, so a reported breakout is always live and a
    long-decayed one is never resurrected. **Volume grades the confirmation:** volume-backed
    (vol >= ``breakout_volume_mult`` x base average) is CORE-quality; a momentum thrust on weak volume
    still arms but is FLIP-grade. A clearly-minimal placeholder for richer breakout logic.
    """
    bars = [b for b in bars if b.get("close") is not None]
    need = max(cfg.breakout_base_window, cfg.breakout_return_days, cfg.breakout_min_base_bars) + 1
    if len(bars) < need:
        return None
    closes = [float(b["close"]) for b in bars]
    earliest = max(cfg.breakout_base_window, cfg.breakout_return_days)

    idx = None
    for i in range(len(bars) - 1, earliest - 1, -1):
        if not entry_signal_is_live(bars[i]["d"], cfg.breakout_alpha_liveness_days, asof):
            break  # bars are ascending; everything earlier is past the freshness window too
        base_high = max(closes[i - cfg.breakout_base_window : i])
        ret = closes[i] / closes[i - cfg.breakout_return_days] - 1.0
        if closes[i] > base_high and ret >= cfg.breakout_min_return:
            idx = i
            break
    if idx is None:
        return None

    bar = bars[idx]
    event_date = bar["d"]
    last_close = closes[idx]
    base_high = max(closes[idx - cfg.breakout_base_window : idx])
    ret = last_close / closes[idx - cfg.breakout_return_days] - 1.0
    vols = [
        float(b["volume"]) for b in bars[idx - cfg.breakout_base_window : idx] if b.get("volume")
    ]
    base_vol_avg = fmean(vols) if vols else 0.0
    bar_vol = bar.get("volume")
    vol_ratio = (float(bar_vol) / base_vol_avg) if (bar_vol and base_vol_avg) else 0.0
    volume_backed = vol_ratio >= cfg.breakout_volume_mult
    quality = "Volume-backed" if volume_backed else "Momentum-only"
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE if volume_backed else Grade.FLIP,
        score=_score(last_close / base_high, ret, vol_ratio, volume_backed, cfg),
        label=(
            f"{quality} breakout: close {last_close:.2f} cleared the {cfg.breakout_base_window}-day "
            f"high {base_high:.2f}, +{ret * 100:.0f}% over {cfg.breakout_return_days}d on "
            f"{vol_ratio:.1f}x avg volume"
        ),
        alpha_liveness_days=cfg.breakout_alpha_liveness_days,
        provenance=[
            source_provenance(
                "price",
                f"price:{security_id}:{event_date.isoformat()}",
                detail={
                    "close": last_close,
                    "base_high": base_high,
                    "ret": round(ret, 4),
                    "vol_ratio": round(vol_ratio, 2),
                    "volume_backed": volume_backed,
                },
            )
        ],
        asof=event_date,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 2 — breakout confirmation (arms), graded by volume. Reads EOD bars via the point-in-time view."""
    bars = pit.price_history(security_id, lookback_days=cfg.breakout_lookback_days)
    return score(bars, security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
