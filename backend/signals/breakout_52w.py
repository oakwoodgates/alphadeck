"""§2.3 — the 52-week / long-base breakout: the STRUCTURAL confirmation behind the 5–10x (R9).

A Key-2 CONFIRMATION (it ARMS a co-located conviction), fixed CORE grade. Fires when the close makes a
fresh **52-week closing high** AND RVOL >= ``breakout_52w_volume_mult`` x the ~50-day average volume — the
year-high-on-volume structural breakout, distinct from the 8-day momentum tool in ``volume_breakout`` (which
stays intact). Both emit ``kind=Kind.TECHNICAL_BREAKOUT`` (a VARIANT — NO new Kind, so no OpenAPI change),
so a name can fire BOTH; the assembler's confirmation grade is the STRONGEST of the co-firing confirmations
(``call_grade`` = max), so a co-firing flip 8-day breakout never drags a core 52-week breakout down to flip.

Rule (R9, ratified):
  * fresh 52-week CLOSING high: ``close_i > max(close over the ~252 trading bars before i)``
  * volume gate:              ``RVOL = vol_i / mean(vol over the ~50 bars before i) >= breakout_52w_volume_mult``
  * role=entry_trigger · kind=TECHNICAL_BREAKOUT (variant) · grade=CORE (structural) · liveness 45d.

Honesty (#9/#6): the min-base-bars gate DECLINES (returns None) rather than assert a "52-week high" on less
than ~a year of tape; a bar with no volume (or a zero base) declines the volume gate rather than fabricate a
volume-backed breakout. Reads EOD bars via the point-in-time view, so it runs identically live and in replay
(no new fact table — the same ``fact_price_eod`` ``volume_breakout`` and ``range_52w`` already read).

Fire-date-anchored at the breakout bar (``asof`` = that bar's date, not the query asof), mirroring
``volume_breakout``: the firing is sticky across a consolidation until it decays out of its liveness window.
"""

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

DETECTOR_NAME = "breakout_52w"


def _score(price_over_high: float, rvol: float, cfg: CallConfig) -> float:
    """A confirmed structural 52-week breakout is inherently strong (a high base), lifted by how decisively
    price cleared the year-high and how heavy the volume ran beyond the gate. Bounded to a high sub-unity
    ceiling. Grade is fixed CORE — this score feeds the confidence bar, never the grade."""
    price_leg = min(max(price_over_high - 1.0, 0.0) * 10.0, 1.0)  # ~10% above the 52w high -> full
    vol_leg = min(rvol / (cfg.breakout_52w_volume_mult * 2.0), 1.0)  # ~2x the gate (3.0x) -> full
    return round(min(0.6 + 0.25 * price_leg + 0.15 * vol_leg, 0.95), 4)


def _rvol(bars: list[dict[str, Any]], i: int, cfg: CallConfig) -> float | None:
    """RVOL = bar ``i``'s volume / the mean volume over the ~50 bars immediately before it. None (declines
    the volume gate) when the bar has no volume, there is no base volume, or the base sums to zero — never a
    fabricated ratio (#6/#9). Volumeless base bars are dropped from the mean (mirroring ``volume_breakout``).
    """
    base = [
        float(b["volume"])
        for b in bars[max(0, i - cfg.breakout_52w_vol_base_bars) : i]
        if b.get("volume")
    ]
    bar_vol = bars[i].get("volume")
    if not base or not bar_vol:
        return None
    avg = fmean(base)
    if avg <= 0.0:
        return None
    return float(bar_vol) / avg


def score(
    bars: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure CORE 52-week breakout over ascending EOD bars (last bar = the asof bar), or None.

    Reports the MOST-RECENT bar still inside its alpha-liveness window whose close makes a fresh 52-week
    closing high AND clears the volume gate — stamped with THAT bar's own date. So the firing is sticky
    across a consolidation and re-anchors when a fresher 52-week breakout prints; the freshness floor
    mirrors the assembler's liveness, so a reported breakout is always live and a long-decayed one is
    never resurrected (the ``volume_breakout`` idiom, with a 52-week base and a 50-day volume reference).
    """
    bars = [b for b in bars if b.get("close") is not None]
    earliest = cfg.breakout_52w_min_base_bars
    if len(bars) <= earliest:  # not enough prior bars to honestly claim a 52-week high anywhere
        return None
    closes = [float(b["close"]) for b in bars]

    idx: int | None = None
    hit_rvol = 0.0
    for i in range(len(bars) - 1, earliest - 1, -1):
        if not entry_signal_is_live(bars[i]["d"], cfg.breakout_52w_alpha_liveness_days, asof):
            break  # bars are ascending; everything earlier is past the freshness window too
        base_high = max(closes[max(0, i - cfg.breakout_52w_base_bars) : i])
        if closes[i] <= base_high:
            continue
        rvol = _rvol(bars, i, cfg)
        if rvol is None or rvol < cfg.breakout_52w_volume_mult:
            continue
        idx, hit_rvol = i, rvol
        break
    if idx is None:
        return None

    bar = bars[idx]
    event_date = bar["d"]
    last_close = closes[idx]
    base_high = max(closes[max(0, idx - cfg.breakout_52w_base_bars) : idx])
    base_bars = min(idx, cfg.breakout_52w_base_bars)
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.TECHNICAL_BREAKOUT,
        grade=Grade.CORE,
        score=_score(last_close / base_high, hit_rvol, cfg),
        label=(
            f"52-week breakout: close {last_close:.2f} cleared the 52-week high "
            f"{base_high:.2f} on {hit_rvol:.1f}x avg volume"
        ),
        alpha_liveness_days=cfg.breakout_52w_alpha_liveness_days,
        provenance=[
            source_provenance(
                "price",
                f"price:{security_id}:{event_date.isoformat()}",
                detail={
                    "close": last_close,
                    "high_52w": round(base_high, 4),
                    "rvol": round(hit_rvol, 2),
                    "base_bars": base_bars,
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
    """Key 2 — the structural 52-week breakout confirmation (arms), fixed CORE. Reads EOD bars via the
    point-in-time view; arming still needs a co-located conviction (a fresh insider/catalyst/revenue key).
    """
    bars = pit.price_history(security_id, lookback_days=cfg.breakout_52w_lookback_days)
    return score(bars, security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
