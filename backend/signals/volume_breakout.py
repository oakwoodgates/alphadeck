from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from signals.base import PointInTimeData


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

    Fires on a price breakout — the asof close makes a new ``breakout_base_window``-day CLOSING high
    AND is up at least ``breakout_min_return`` over ``breakout_return_days`` sessions (a momentum
    thrust). **Volume grades the confirmation:** volume-backed (vol >= ``breakout_volume_mult`` x base
    average) is CORE-quality; a momentum thrust on weak volume still arms but is FLIP-grade (the
    assembler reads that as reduced confidence + a volume-gap counter-case). A clearly-minimal
    placeholder for richer breakout logic, kept labeled as such.
    """
    bars = [b for b in bars if b.get("close") is not None]
    need = max(cfg.breakout_base_window, cfg.breakout_return_days, cfg.breakout_min_base_bars) + 1
    if len(bars) < need:
        return None
    closes = [float(b["close"]) for b in bars]
    last_close = closes[-1]
    base_closes = closes[-(cfg.breakout_base_window + 1) : -1]
    base_high = max(base_closes)
    ret = last_close / closes[-(cfg.breakout_return_days + 1)] - 1.0
    if not (last_close > base_high and ret >= cfg.breakout_min_return):
        return None

    vols = [
        float(b["volume"]) for b in bars[-(cfg.breakout_base_window + 1) : -1] if b.get("volume")
    ]
    base_vol_avg = fmean(vols) if vols else 0.0
    last_vol = bars[-1].get("volume")
    vol_ratio = (float(last_vol) / base_vol_avg) if (last_vol and base_vol_avg) else 0.0
    volume_backed = vol_ratio >= cfg.breakout_volume_mult
    quality = "Volume-backed" if volume_backed else "Momentum-only"
    return SignalEvent(
        detector="volume_breakout",
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE if volume_backed else Grade.FLIP,
        score=_score(last_close / base_high, ret, vol_ratio, volume_backed, cfg),
        fired=True,
        label=(
            f"{quality} breakout: close {last_close:.2f} cleared the {cfg.breakout_base_window}-day "
            f"high {base_high:.2f}, +{ret * 100:.0f}% over {cfg.breakout_return_days}d on "
            f"{vol_ratio:.1f}x avg volume"
        ),
        alpha_half_life_days=cfg.breakout_alpha_half_life_days,
        provenance=[
            Provenance(
                source="price",
                ref=f"price:{security_id}:{asof.isoformat()}",
                detail={
                    "close": last_close,
                    "base_high": base_high,
                    "ret": round(ret, 4),
                    "vol_ratio": round(vol_ratio, 2),
                    "volume_backed": volume_backed,
                },
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
    """Key 2 — breakout confirmation (arms), graded by volume. Reads EOD bars via the point-in-time view."""
    bars = pit.price_history(security_id, lookback_days=cfg.breakout_lookback_days)
    return score(bars, security_id, asof, cfg)
