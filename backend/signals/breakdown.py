"""§2.5 (core) + §3.3 (flip) — the grade-aware structural DE-ARM (R10/R11/R12).

The exit half ``CALL_LOGIC`` §2 spec'd but never had a detector: *"a genuine breakdown de-arms only via a
``breakdown`` risk-signal detector (M4a) — price-signal logic stays in detectors, never in the pure
assembler."* Two RISK detectors, one per grade of arm:

  * **core (R10)** — a close below the **200-day SMA** (the long base) de-arms a CORE hold. NEVER the first
    pullback: the 200d level is exactly what separates a shallow dip (a ~20% pullback that HOLDS the 200d)
    from a structural break, and a regime gate (the last price-vs-200d cross was DOWNWARD) keeps a chronic
    downtrend that was never above its 200d from firing (honest loudness / #7).
  * **flip (R11)** — a close back below the **8-day breakout base** the flip entry cleared de-arms a FLIP
    entry. The mirror of ``volume_breakout``: it reads the SAME 8-day base (and freshness window) the arm
    cleared, so it only fires while there is a live flip breakout to fall back from.

Each emits ``role=risk_signal, kind=Kind.BREAKDOWN`` carrying **which grade it de-arms** on the
``SignalEvent.dearm_grade`` PROPERTY (core-breakdown -> CORE, flip-breakdown -> FLIP). The assembler reads
that property to make its veto grade-aware (a flip-style fast breakdown can never shake a core hold; a core
structural break de-arms the core hold even inside its ``arm_until`` window) — no per-kind branch (the
through-line). The de-arm reads as a **signal-validity** event (advisory), never a sell instruction (#4/#5).

Pure bar math over the point-in-time price view, so it runs identically live and in replay (no new fact
table — the same ``fact_price_eod`` ``volume_breakout`` / ``breakout_52w`` / ``sma_position`` already read).
The break is a STATE (close below the level), not a one-day cross event, so a de-armed hold stays de-armed
while price is below the base rather than silently re-arming the moment a cross event decays.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_CORE_NAME = "breakdown_core"
DETECTOR_FLIP_NAME = "breakdown_flip"


def _sma_series(closes: list[float], window: int) -> list[float | None]:
    """Rolling mean: out[i] = mean(closes[i-window+1 .. i]) once ``window`` bars exist, else None (the
    ``sma_position`` idiom)."""
    out: list[float | None] = [None] * len(closes)
    total = 0.0
    for i, close in enumerate(closes):
        total += close
        if i >= window:
            total -= closes[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def _last_downcross_date(
    dates: list[date], closes: list[float], sma: list[float | None]
) -> date | None:
    """The DATE of the most recent price-vs-SMA cross, IF that cross was DOWNWARD (above -> below) and has
    not since been reclaimed — else None. This is the structural BREAK's own event date (like a breakout's
    bar date), so the de-arm can be anchored to WHEN the base broke, not to the query ``asof``.

    None-SMA bars and exact touches (close ON the line) are skipped. Returns None when price never crossed
    (a chronic downtrend never above its 200d — #7 honest loudness) or when the last cross was UPWARD (price
    reclaimed the line — no live break). The returned date is what lets the assembler enforce that a break
    POST-DATES the arm (a give-back after the entry), never a break already true at the arming bar.
    """
    prev = 0
    last: tuple[date, int] | None = None  # (cross date, sign after the cross)
    for d, close, level in zip(dates, closes, sma):
        if level is None:
            continue
        diff = close - level
        if diff == 0.0:
            continue
        sign = 1 if diff > 0.0 else -1
        if prev and sign != prev:
            last = (d, sign)
        prev = sign
    if last is None or last[1] > 0:  # never crossed, or the last cross reclaimed the line
        return None
    return last[0]


def core_score(
    bars: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure CORE breakdown over ascending EOD bars (last bar = the asof bar), or None.

    Fires when the latest close is below the 200-day SMA (the structural break) AND the name reached that
    state by crossing DOWN from above (a genuine break, not a chronic downtrend). The event is fire-dated at
    the DOWNWARD-CROSS bar (when the base actually broke), NOT the query ``asof`` — so the assembler can
    enforce that a break POST-DATES the arm (a give-back after the entry, never a condition already true at
    the arming bar). Declines (returns None, #9) rather than fabricate a 200d SMA on less than ~a year of tape.
    """
    bars = [b for b in bars if b.get("close") is not None]
    if len(bars) < cfg.breakdown_core_min_bars:  # can't honestly compute a 200d SMA (#9)
        return None
    closes = [float(b["close"]) for b in bars]
    dates = [b["d"] for b in bars]
    sma = _sma_series(closes, cfg.breakdown_core_sma_window)
    last_sma = sma[-1]
    if last_sma is None:
        return None
    last_close = closes[-1]
    if last_close >= last_sma:  # holding the 200d — NOT a break (never the first pullback, R10)
        return None
    event_date = _last_downcross_date(dates, closes, sma)  # the break's own date (post-date anchor)
    if (
        event_date is None
    ):  # chronic downtrend / no live downward cross — not a structural break (#7)
        return None
    return fired_signal(
        detector=DETECTOR_CORE_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.BREAKDOWN,
        dearm_grade=Grade.CORE,
        score=cfg.breakdown_severity,
        label=(
            f"Structural break: close {last_close:.2f} closed below the "
            f"{cfg.breakdown_core_sma_window}-day base {last_sma:.2f} — the core hold's entry signal is "
            f"no longer valid (a de-arm, not a sell)"
        ),
        provenance=[
            source_provenance(
                "price",
                f"price:{security_id}:{event_date.isoformat()}",
                detail={
                    "close": round(last_close, 4),
                    "sma": round(last_sma, 4),
                    "sma_window": cfg.breakdown_core_sma_window,
                },
            )
        ],
        asof=event_date,
    )


def _recent_flip_base(
    bars: list[dict[str, Any]], closes: list[float], asof: date, cfg: CallConfig
) -> tuple[float, date] | None:
    """The 8-day base high of the most recent ``volume_breakout``-style breakout still inside the flip
    liveness window, plus that breakout's bar date — the base the live flip arm cleared (R11). None when no
    such breakout is in the window (so there is no live flip to de-arm). Reads the SAME 8-day base +
    freshness dials the breakout cleared, so the breakdown is exactly its mirror."""
    earliest = max(cfg.breakout_base_window, cfg.breakout_return_days)
    for i in range(len(bars) - 1, earliest - 1, -1):
        if not entry_signal_is_live(bars[i]["d"], cfg.breakout_alpha_liveness_days, asof):
            break  # bars are ascending; everything earlier is past the freshness window too
        base_high = max(closes[i - cfg.breakout_base_window : i])
        ret = closes[i] / closes[i - cfg.breakout_return_days] - 1.0
        if closes[i] > base_high and ret >= cfg.breakout_min_return:
            return base_high, bars[i]["d"]
    return None


def flip_score(
    bars: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure FLIP breakdown over ascending EOD bars (last bar = the asof bar), or None.

    Fires when a recent 8-day breakout (still inside its flip liveness window) has been given back — the
    latest close has fallen back below the base high that breakout cleared (R11). No recent breakout, or
    price still above the base it cleared, -> None.
    """
    bars = [b for b in bars if b.get("close") is not None]
    if len(bars) <= max(cfg.breakout_base_window, cfg.breakout_return_days):
        return None
    closes = [float(b["close"]) for b in bars]
    found = _recent_flip_base(bars, closes, asof, cfg)
    if found is None:
        return None
    base_high, breakout_date = found
    last_close = closes[-1]
    if last_close >= base_high:  # still holding the base it cleared — not a breakdown
        return None
    event_date = bars[-1]["d"]
    return fired_signal(
        detector=DETECTOR_FLIP_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.BREAKDOWN,
        dearm_grade=Grade.FLIP,
        score=cfg.breakdown_severity,
        label=(
            f"Fast breakdown: close {last_close:.2f} fell back below the "
            f"{cfg.breakout_base_window}-day breakout base {base_high:.2f} — the flip entry signal is no "
            f"longer valid (a de-arm, not a sell)"
        ),
        provenance=[
            source_provenance(
                "price",
                f"price:{security_id}:{event_date.isoformat()}",
                detail={
                    "close": round(last_close, 4),
                    "base_high": round(base_high, 4),
                    "breakout_date": breakout_date.isoformat(),
                },
            )
        ],
        asof=event_date,
    )


def detect_core(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Core structural de-arm (200d SMA break). Reads EOD bars via the point-in-time view; the assembler's
    grade-aware veto applies it only to a CORE hold (R12).

    MASTER SWITCH (``breakdown_dearm_enabled``) — **DEFAULT ON since the operator's "go honest" flip,
    2026-08-15** (shipped OFF; measured on the lab first — see the dial's comment in ``domain/config.py``).
    Explicitly OFF it still no-ops (nothing enters the stream / counter-case / de-arm path), which is what
    the goldens recorded pre-flip. Stays REGISTERED either way (the registry-list tests expect it). The
    pure ``core_score`` is UNGATED (the math is testable independent of the switch)."""
    if not cfg.breakdown_dearm_enabled:
        return None
    bars = pit.price_history(security_id, lookback_days=cfg.breakdown_core_lookback_days)
    return core_score(bars, security_id, asof, cfg)


def detect_flip(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Fast flip de-arm (8-day breakout-base break). Reads EOD bars via the point-in-time view; the
    assembler's grade-aware veto applies it only to a FLIP entry (R12).

    MASTER SWITCH (``breakdown_dearm_enabled``) — **DEFAULT ON since 2026-08-15** (the same gate as
    ``detect_core`` — see it); explicitly OFF it no-ops and the live app emits no flip-breakdown. The
    pure ``flip_score`` is UNGATED."""
    if not cfg.breakdown_dearm_enabled:
        return None
    bars = pit.price_history(security_id, lookback_days=cfg.breakout_lookback_days)
    return flip_score(bars, security_id, asof, cfg)


DETECTOR_CORE = register_detector(Detector(name=DETECTOR_CORE_NAME, detect=detect_core))
DETECTOR_FLIP = register_detector(Detector(name=DETECTOR_FLIP_NAME, detect=detect_flip))
