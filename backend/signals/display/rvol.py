"""Relative volume (RVOL) — is the name's as-of move backed by volume?

Display-only tape context: the as-of bar's volume ÷ the mean volume over the ``BASELINE_BARS``
trading bars immediately before it (the same base-window idea the breakout detector grades on), so
the operator sees at a glance whether a move is volume-backed. Read-only, structurally OFF the call
path (#4/#6): no ``role``, it cannot fire/arm/veto/grade, and it never feeds the call's OWN volume
confirmation — it only SURFACES the same kind of read beside it.

NOT the call's number. The breakout detector computes its ``vol_ratio`` at the BREAKOUT bar's date;
this column computes at the AS-OF bar. Same base window (8 bars) and same loud threshold (1.5x) —
both MIRROR ``CallConfig`` by hand — so the two never CONTRADICT, but on a non-breakout day they
legitimately differ (a different anchor bar). A name with a volumeless as-of bar (a halt / thin OTC)
reads an honest "—", never a stale bar's ratio (#6).

The two dials mirror the call's ``breakout_base_window`` / ``breakout_volume_mult`` and are re-tuned
BY HAND — the display seam is structurally barred from importing ``domain.config`` / ``CallConfig``
(``base.py`` + the ``test_registry`` import-ban pin), the same discipline ``insider_flow_90d``
follows. ``test_rvol.py`` pins them equal to the call's so a drift is caught.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "rvol"
LABEL = "Relative volume"
# MIRROR the call's dials (breakout_base_window=8, breakout_volume_mult=1.5) as DISPLAY module
# constants — the seam cannot import CallConfig, so they are hand-kept equal (test_rvol drift-guard).
BASELINE_BARS = 8
LOUD_MULT = 1.5
LOOKBACK_DAYS = 40  # calendar — comfortably covers the 9 trading bars (as-of bar + 8 base)

_KEY = "rvol"
_MLABEL = "RVOL"


def _gap(note: str) -> DisplayMetric:
    """An HONEST gap (value=None + the reason), never a fabricated or zero-filled ratio (#6/#9)."""
    return DisplayMetric(key=_KEY, label=_MLABEL, unit="ratio", note=note)


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure RVOL over ascending EOD bars: the last (as-of) bar's volume ÷ the mean volume of the
    ``BASELINE_BARS`` bars before it. Volumeless base bars are dropped (and counted in the basis),
    mirroring the breakout detector; an as-of bar with no volume is an honest "—", never a stale
    ratio. The loud accent is left to the FE (value >= ``params.loud_mult``) so the threshold lives
    in ONE place — this module — and never greens as a return would (it renders a warm 'hot')."""
    priced = [b for b in bars if b.get("close") is not None]
    if not priced:
        return None
    anchor_vol = priced[-1].get("volume")
    base_slice = priced[-(BASELINE_BARS + 1) : -1]  # up to 8 bars immediately before the as-of bar
    base_vols = [float(b["volume"]) for b in base_slice if b.get("volume") is not None]

    if anchor_vol is None:
        metric = _gap("n/a: no volume on the as-of bar")
    elif len(base_slice) < BASELINE_BARS:
        metric = _gap(f"n/a: {len(base_slice)}/{BASELINE_BARS} base bars")
    elif not base_vols or fmean(base_vols) <= 0.0:
        metric = _gap("n/a: zero base volume")
    else:
        ratio = round(float(anchor_vol) / fmean(base_vols), 2)
        metric = DisplayMetric(key=_KEY, label=_MLABEL, value=ratio, unit="ratio")

    dropped = len(base_slice) - len(base_vols) if len(base_slice) >= BASELINE_BARS else 0
    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "baseline_bars": BASELINE_BARS,
            "loud_mult": LOUD_MULT,
            "lookback_days": LOOKBACK_DAYS,
        },
        bars_used=len(priced),
        window_start=priced[0]["d"],
        window_end=priced[-1]["d"],
        note=f"{dropped} base bars without volume excluded" if dropped else None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=[metric], basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
