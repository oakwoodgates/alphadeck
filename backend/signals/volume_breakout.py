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
    price_ratio: float,
    ret: float,
    vol_ratio: float,
    volume_backed: bool,
    follow_factor: float,
    cfg: CallConfig,
) -> float:
    price_leg = min(max(price_ratio - 1.0, 0.0) * 10.0, 1.0)  # how far above the base high
    mom_leg = min(ret / (cfg.breakout_min_return * 2.0), 1.0) if cfg.breakout_min_return else 0.0
    base = 0.5 * price_leg + 0.5 * mom_leg
    if volume_backed:
        vol_leg = min(vol_ratio / cfg.breakout_volume_mult, 1.0) if vol_ratio else 0.0
        raw = min(0.6 * base + 0.4 * vol_leg, 0.95)
    else:
        raw = min(0.55 * base, 0.5)  # momentum-only: real but kept below a volume-backed score
    # §3.1 follow-through (R13): a weak close and/or a failed next-day hold multiply the score DOWN — the
    # false-breakout tell. follow_factor == 1.0 (a clean or still-fresh breakout) leaves the score exactly
    # as before, so a clean breakout is numerically unchanged.
    return round(min(raw * follow_factor, 0.95), 4)


def _close_strength(bar: dict[str, Any]) -> float | None:
    """(close - low) / (high - low): where the bar closed within its own day range, 0 (at the low) .. 1
    (at the high). None when high/low are absent (a missing field must never read as a weak close, #9) or
    the bar has zero range (degenerate — treated as neutral, not weak)."""
    hi, lo, close = bar.get("high"), bar.get("low"), bar.get("close")
    if hi is None or lo is None or close is None:
        return None
    rng = float(hi) - float(lo)
    if rng <= 0.0:
        return None
    return (float(close) - float(lo)) / rng


def _next_bar_held(bars: list[dict[str, Any]], idx: int, level: float) -> bool | None:
    """Did the bar AFTER the breakout hold above the breakout level (the base high it cleared)? True/False
    when a next bar exists, None when the breakout is the last bar (no next bar YET — unknown, NOT a
    failed hold, so a fresh breakout is never penalized for the missing bar)."""
    if idx + 1 >= len(bars):
        return None
    nxt = bars[idx + 1].get("close")
    if nxt is None:
        return None
    return float(nxt) > level


def _follow_factor(strength: float | None, held: bool | None, cfg: CallConfig) -> float:
    """The §3.1 score multiplier: 1.0 for a clean (or still-fresh) breakout, cut for a weak close and/or a
    confirmed failed hold. The two cuts stack; floored at 0."""
    factor = 1.0
    if strength is not None and strength < cfg.breakout_close_strength_min:
        factor -= cfg.breakout_weak_close_penalty
    if held is False:
        factor -= cfg.breakout_failed_hold_penalty
    return max(factor, 0.0)


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
    still arms but is FLIP-grade. **§3.1 follow-through (R13) SHARPENS the SCORE:** the breakout still
    fires and its grade stays volume-based, but a weak close (outside the top
    ``breakout_close_strength_min`` of the day range) and/or a failed next-day hold multiply the score
    DOWN — that lower score IS the rejected false breakout. A still-fresh breakout with no next bar yet is
    never penalized (unknown, not failed). Grading a weak-CLOSE breakout DOWN to flip is a gated opt-in
    (``breakout_weak_close_grade_down``, default OFF). A clearly-minimal placeholder for richer breakout logic.
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

    # §3.1 follow-through / hold quality (R13): a weak close and/or a failed next-day hold multiply the SCORE
    # DOWN — the "rejected" false breakout; a clean or still-fresh breakout keeps its full score. GRADE:
    # volume-backed => CORE, momentum-only => FLIP. The GRADE-DOWN (a weak CLOSE also caps a volume-backed
    # breakout at FLIP) is a GATED opt-in via cfg.breakout_weak_close_grade_down (default OFF => grade stays
    # volume-only, byte-unchanged). ON: a weak-close breakout is a quick-trade, not a structural hold — it
    # re-verdicts the UNH flagship (CORE_ENTRY -> starter) + theme-arm eligibility + member ranking.
    strength = _close_strength(bar)
    held = _next_bar_held(bars, idx, base_high)
    follow_factor = _follow_factor(strength, held, cfg)
    weak_close = strength is not None and strength < cfg.breakout_close_strength_min
    core_grade = volume_backed and not (cfg.breakout_weak_close_grade_down and weak_close)
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE if core_grade else Grade.FLIP,
        score=_score(last_close / base_high, ret, vol_ratio, volume_backed, follow_factor, cfg),
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
                    "close_strength": round(strength, 3) if strength is not None else None,
                    "next_bar_held": held,
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
