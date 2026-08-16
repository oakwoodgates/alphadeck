"""Theme-breadth thrust (§1.1) — a THESIS-LEVEL, DISPLAY-only reading of basket participation.

Breadth = the share of RESOLVED basket members whose latest close sits at/above their 50-day SMA. The
"thrust" is the loud state (#7): breadth has crossed a majority AND surged versus 20 trading days ago
— the theme turning up together. DISPLAY-only, exactly like ``signals.display.sma``: a computed read
beside the call, never a ``SignalEvent`` — no role, it cannot fire / arm / veto / grade (#4), and it is
never persisted (compute-on-read). Distinct from the per-name display members: it is a THESIS-level
aggregate over the WHOLE basket, so it runs off a list of member close-series rather than one security
— it is deliberately NOT in the per-security display registry.

#9: a member with too little history to compute BOTH readings is SHOWN-not-counted — surfaced in the
member counts, never silently dropped and never fabricated as a false "below". Dials are named module
constants surfaced in ``basis.params`` (the display-seam convention), never ``CallConfig`` call dials —
§1.1 is display-first, so its thresholds do not live on the trust-validated call-engine dial set.
"""

from __future__ import annotations

from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayHeadline,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)

KIND = "theme_breadth"
LABEL = "Theme breadth (50d)"
SMA_WINDOW = 50  # members at/above their 50-day SMA
DELTA_LOOKBACK_BARS = 20  # breadth "then" = 20 TRADING bars ago
# Both readings need SMA50 computable at their OWN bar: asof needs 50 bars; the 20-ago reading needs 50
# bars ending 20 bars back = 70 bars. A member with fewer is shown-not-counted (its reading is UNKNOWN).
MIN_BARS = SMA_WINDOW + DELTA_LOOKBACK_BARS  # 70
# ~70 trading bars ≈ 100 calendar days; pull generously so holidays / thin tapes still clear MIN_BARS.
LOOKBACK_DAYS = 250
# The thrust (the loud state, #7): a majority above the line AND a surge vs DELTA_LOOKBACK_BARS ago.
THRUST_MIN_BREADTH = 0.50  # >= 50% of counted members at/above their 50d SMA
THRUST_MIN_DELTA_PTS = 25.0  # AND breadth rose by >= 25 percentage points vs 20 bars ago


def _sma_at(closes: list[float], end_idx: int, window: int) -> float | None:
    """Simple mean of ``closes[end_idx-window+1 .. end_idx]`` inclusive — the SAME arithmetic as
    ``signals.display.sma._sma_series`` evaluated at one point. ``None`` when ``end_idx`` lacks
    ``window`` prior bars (or is out of range)."""
    start = end_idx - window + 1
    if end_idx < 0 or start < 0 or end_idx >= len(closes):
        return None
    return sum(closes[start : end_idx + 1]) / window


def compute(member_closes: list[list[float]]) -> DisplaySignal | None:
    """Pure breadth-thrust over a list of per-member ASCENDING close series (one list per RESOLVED
    member; the no-lookahead read happens upstream in ``breadth_for``). ``None`` only when there are no
    resolved members at all; otherwise a ``DisplaySignal`` that honestly reports thin history as
    shown-not-counted."""
    n_members = len(member_closes)
    if n_members == 0:
        return None

    now_above = ago_above = counted = 0
    for closes in member_closes:
        now_idx = len(closes) - 1
        ago_idx = now_idx - DELTA_LOOKBACK_BARS
        sma_now = _sma_at(closes, now_idx, SMA_WINDOW)
        sma_ago = _sma_at(closes, ago_idx, SMA_WINDOW)
        if sma_now is None or sma_ago is None:
            continue  # too little history for BOTH readings — shown-not-counted (#9)
        counted += 1
        now_above += 1 if closes[now_idx] >= sma_now else 0
        ago_above += 1 if closes[ago_idx] >= sma_ago else 0

    thin = n_members - counted
    if counted == 0:
        breadth_now = breadth_ago = delta_pts = None
        thrust = False
        thin_note = f"n/a: 0/{n_members} members have {MIN_BARS}+ bars"
    else:
        breadth_now = now_above / counted
        breadth_ago = ago_above / counted
        delta_pts = round((breadth_now - breadth_ago) * 100.0, 2)
        thrust = breadth_now >= THRUST_MIN_BREADTH and delta_pts >= THRUST_MIN_DELTA_PTS
        thin_note = None

    metrics = [
        DisplayMetric(
            key="breadth",
            label=f"above {SMA_WINDOW}d SMA",
            value=round(breadth_now * 100.0, 2) if breadth_now is not None else None,
            unit="pct",
            note=thin_note,
        ),
        DisplayMetric(
            key="breadth_prior",
            label=f"{DELTA_LOOKBACK_BARS}d ago",
            value=round(breadth_ago * 100.0, 2) if breadth_ago is not None else None,
            unit="pct",
            note=thin_note,
        ),
        DisplayMetric(
            key="breadth_delta",
            label=f"Δ vs {DELTA_LOOKBACK_BARS}d",
            value=delta_pts,
            unit="pct",
            tone=_delta_tone(delta_pts),
            note=thin_note,
        ),
        DisplayMetric(
            key="members_counted", label="members counted", value=float(counted), unit="count"
        ),
        DisplayMetric(
            key="members_thin",
            label="thin history",
            value=float(thin),
            unit="count",
            note=(f"{thin} shown-not-counted (<{MIN_BARS} bars)" if thin else None),
        ),
    ]
    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "sma_window": SMA_WINDOW,
            "delta_lookback_bars": DELTA_LOOKBACK_BARS,
            "min_bars": MIN_BARS,
            "lookback_days": LOOKBACK_DAYS,
            "thrust_min_breadth_pct": THRUST_MIN_BREADTH * 100.0,
            "thrust_min_delta_pts": THRUST_MIN_DELTA_PTS,
            "members_resolved": n_members,
        },
        bars_used=counted,
    )
    return DisplaySignal(
        kind=KIND,
        label=LABEL,
        headline=_headline(thrust, breadth_now, delta_pts, counted),
        metrics=metrics,
        basis=basis,
    )


def _delta_tone(delta_pts: float | None) -> str | None:
    if delta_pts is None or delta_pts == 0.0:
        return None
    return "pos" if delta_pts > 0.0 else "neg"


def _headline(
    thrust: bool, breadth_now: float | None, delta_pts: float | None, counted: int
) -> DisplayHeadline:
    """The posture chip — LOUD only when the thrust condition holds (#7); otherwise it states the tape
    quietly. ``key`` is the stable categorical (``thrust`` / ``quiet`` / ``unknown``) a Board / Cockpit
    chip consumes directly."""
    if counted == 0 or breadth_now is None or delta_pts is None:
        return DisplayHeadline(key="unknown", label="breadth n/a — thin history", glyph=None)
    pct = round(breadth_now * 100.0)
    if thrust:
        return DisplayHeadline(
            key="thrust",
            label=f"Breadth thrust — {pct}% above 50d, {delta_pts:+.0f}pts",
            glyph="up",
            detail=(
                f"≥{THRUST_MIN_BREADTH * 100:.0f}% above the line and {delta_pts:+.0f}pts vs "
                f"{DELTA_LOOKBACK_BARS}d — the theme is turning up together"
            ),
        )
    return DisplayHeadline(
        key="quiet",
        label=f"{pct}% above 50d",
        glyph=None,
        detail=f"Δ {delta_pts:+.0f}pts vs {DELTA_LOOKBACK_BARS}d — no thrust",
    )


def breadth_for(pit: DisplayPointInTimeData, security_ids: list[UUID]) -> DisplaySignal | None:
    """Read each resolved member's ascending closes via the point-in-time view (as-of capped on
    ``pit.asof`` — the no-lookahead boundary, #1), then compute the pure breadth-thrust. The DB touch is
    confined here (like ``sma.display``); the arithmetic is the pure ``compute``."""
    member_closes = [
        [
            float(b["close"])
            for b in pit.price_history(sid, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        ]
        for sid in security_ids
    ]
    return compute(member_closes)
