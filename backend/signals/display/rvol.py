"""Relative volume (RVOL) — is the name's as-of move backed by volume? Two windows, one fetch.

Display-only tape context: the as-of bar's volume ÷ the mean volume over the N trading bars
immediately before it, so the operator sees at a glance whether a move is volume-backed. Read-only,
structurally OFF the call path (#4/#6): no ``role``, it cannot fire/arm/veto/grade, and it never
feeds the call's OWN volume confirmation — it only SURFACES the same kind of read beside it.

TWO windows off the SAME price fetch (the ``trailing_returns`` multi-window idiom):

* ``rvol`` — the **8-bar** read, MIRRORING the breakout detector's base window (the call-matched
  column). Same base window (``BASELINE_BARS=8``) and loud threshold (``LOUD_MULT=1.5``) as
  ``CallConfig.breakout_base_window`` / ``breakout_volume_mult`` — the display seam is structurally
  barred from importing ``domain.config`` (``base.py`` + the ``test_registry`` import-ban), so they
  are hand-kept equal and ``test_rvol.py::test_dials_mirror_the_call_config_exactly`` catches a
  drift. Because window + threshold match, the column and the trigger never CONTRADICT — but the
  detector computes its ``vol_ratio`` at the BREAKOUT bar's date while this column computes at the
  AS-OF bar, so on a non-breakout day the two legitimately differ (a different anchor bar).
* ``rvol20`` — the **20-bar** read: a display-only TRADER CONVENTION ("is this name unusually
  active vs its month?"). Deliberately DECOUPLED from the call — 20 is NOT the call's window — so
  its dials (``BASELINE_BARS_20`` / ``LOUD_MULT_20``) are standalone display constants, NOT mirrored
  from ``CallConfig`` and NOT drift-guarded (there is no call dial to guard them against). It is
  context the operator reads beside the call, never a gate the call honors.

Each window handles its OWN gaps honestly (#6/#9): a name with only 9–20 bars gets a real ``rvol``
but an honest "—" + the bar shortfall on ``rvol20``; a volumeless as-of bar and a zero base sum
blank BOTH. The loud accent is left to the FE (``value >= params.loud_mult`` for the 8-bar,
``params.loud_mult_20`` for the 20-bar) so each threshold lives in ONE place — this module — and
renders a warm 'hot', never the return-green ``pos``/``neg`` tone (#7).
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
# The 8-bar read MIRRORS the call's dials (breakout_base_window=8, breakout_volume_mult=1.5) as
# DISPLAY module constants — the seam cannot import CallConfig, so they are hand-kept equal
# (test_rvol drift-guard).
BASELINE_BARS = 8
LOUD_MULT = 1.5
# The 20-bar read is a display-only trader convention, DECOUPLED from the call (20 is not the call's
# window): standalone display dials, deliberately NOT mirrored from CallConfig and NOT drift-guarded.
BASELINE_BARS_20 = 20
LOUD_MULT_20 = 1.5
# calendar — ONE fetch covering BOTH windows: the 20-bar needs 21 trading bars (20 base + the
# anchor); ~55 calendar days comfortably covers that with weekend/holiday slack (the 8-bar needs 9).
LOOKBACK_DAYS = 55

_KEY = "rvol"
_MLABEL = "RVOL"
_KEY_20 = "rvol20"
_MLABEL_20 = "RVOL20"


def _gap(key: str, label: str, note: str) -> DisplayMetric:
    """An HONEST gap (value=None + the reason), never a fabricated or zero-filled ratio (#6/#9)."""
    return DisplayMetric(key=key, label=label, unit="ratio", note=note)


def _window(
    priced: list[dict[str, Any]], anchor_vol: float | None, baseline: int, key: str, label: str
) -> tuple[DisplayMetric, int]:
    """One window's RVOL = the anchor (as-of) bar's volume ÷ the mean volume of the ``baseline`` bars
    immediately before it. Returns the metric + the count of volumeless base bars dropped (named in
    the basis, mirroring the breakout detector). An HONEST gap — never a fabricated ratio — when the
    as-of bar has no volume, the tape is short of ``baseline`` base bars, or the base sums to zero;
    the note distinguishes the three reasons (#6). A gap reports 0 dropped (nothing was averaged).
    """
    base_slice = priced[-(baseline + 1) : -1]  # up to `baseline` bars immediately before the anchor
    base_vols = [float(b["volume"]) for b in base_slice if b.get("volume") is not None]
    if anchor_vol is None:
        return _gap(key, label, "n/a: no volume on the as-of bar"), 0
    if len(base_slice) < baseline:
        return _gap(key, label, f"n/a: {len(base_slice)}/{baseline} base bars"), 0
    if not base_vols or fmean(base_vols) <= 0.0:
        return _gap(key, label, "n/a: zero base volume"), 0
    ratio = round(float(anchor_vol) / fmean(base_vols), 2)
    metric = DisplayMetric(key=key, label=label, value=ratio, unit="ratio")
    return metric, len(base_slice) - len(base_vols)


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure RVOL over ascending EOD bars: TWO windows off ONE fetch — the 8-bar call-matched read and
    the 20-bar trader-convention read — each the last (as-of) bar's volume ÷ its OWN base-window
    mean. Volumeless base bars are dropped per window (and counted in the basis, mirroring the
    breakout detector); an as-of bar with no volume blanks both honestly, never a stale ratio. The
    loud accent is left to the FE (value >= the window's params threshold) so each threshold lives in
    ONE place — this module — and never greens as a return would (it renders a warm 'hot')."""
    priced = [b for b in bars if b.get("close") is not None]
    if not priced:
        return None
    anchor_vol = priced[-1].get("volume")
    metric8, dropped8 = _window(priced, anchor_vol, BASELINE_BARS, _KEY, _MLABEL)
    metric20, dropped20 = _window(priced, anchor_vol, BASELINE_BARS_20, _KEY_20, _MLABEL_20)

    # Show-the-work per window (#6): each names its OWN volumeless exclusions. The 8-bar keeps its
    # original unlabeled phrasing; the 20-bar clause is window-labeled so the two never blur.
    drop_notes: list[str] = []
    if dropped8:
        drop_notes.append(f"{dropped8} base bars without volume excluded")
    if dropped20:
        drop_notes.append(f"{_MLABEL_20}: {dropped20} base bars without volume excluded")

    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "baseline_bars": BASELINE_BARS,
            "loud_mult": LOUD_MULT,
            "baseline_bars_20": BASELINE_BARS_20,
            "loud_mult_20": LOUD_MULT_20,
            "lookback_days": LOOKBACK_DAYS,
        },
        bars_used=len(priced),
        window_start=priced[0]["d"],
        window_end=priced[-1]["d"],
        note="; ".join(drop_notes) or None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=[metric8, metric20], basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
