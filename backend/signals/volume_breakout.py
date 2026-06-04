from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from signals.base import PointInTimeData


def _score(price_ratio: float, vol_ratio: float, cfg: CallConfig) -> float:
    price_leg = min(max(price_ratio - 1.0, 0.0) * 5.0, 1.0)  # ~+20% over the base -> full leg
    vol_leg = min(vol_ratio / (cfg.breakout_volume_mult * 2.0), 1.0)
    return round(min(0.5 * price_leg + 0.5 * vol_leg, 0.95), 4)


def score(
    bars: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the deliberately-minimal Key-2 breakout over ascending EOD bars (last bar = the asof bar).

    Fires when the asof close clears the base high on expanding volume. This is a clearly-minimal
    placeholder for real breakout logic, kept labeled as such so it isn't mistaken for the finished
    detector when we widen later.
    """
    if len(bars) < cfg.breakout_min_base_bars + 1:
        return None
    last = bars[-1]
    base = bars[:-1][-cfg.breakout_base_window :]
    if len(base) < cfg.breakout_min_base_bars or last.get("close") is None:
        return None
    highs = [float(b["high"]) for b in base if b.get("high") is not None]
    vols = [float(b["volume"]) for b in base if b.get("volume") is not None]
    if not highs or not vols:
        return None
    base_high = max(highs)
    base_vol_avg = fmean(vols)
    close = float(last["close"])
    vol_ratio = (
        (float(last["volume"]) / base_vol_avg) if (last.get("volume") and base_vol_avg) else 0.0
    )
    if not (close > base_high and vol_ratio >= cfg.breakout_volume_mult):
        return None
    return SignalEvent(
        detector="volume_breakout",
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE,
        score=_score(close / base_high, vol_ratio, cfg),
        fired=True,
        label=f"Close {close:.2f} broke the base high {base_high:.2f} on {vol_ratio:.1f}x avg volume",
        alpha_half_life_days=cfg.breakout_alpha_half_life_days,
        provenance=[
            Provenance(
                source="price",
                ref=f"price:{security_id}:{asof.isoformat()}",
                detail={"close": close, "base_high": base_high, "vol_ratio": round(vol_ratio, 2)},
            )
        ],
        asof=asof,
    )


def detect(
    pit: PointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 2 — volume breakout (arms). Reads EOD bars via the point-in-time view."""
    bars = pit.price_history(security_id, lookback_days=cfg.breakout_lookback_days)
    return score(bars, security_id, asof, cfg)
