"""Thin price-history detection — a DATA-HEALTH indicator, structurally OUT of the call path.

The #1 visibility flag: a name whose stored EOD tape is too shallow starves the longest-window signals
(the FDCT-under-the-wrong-symbol failure, and any genuinely-uncovered name the resolver can't heal). This
module is the pure classifier — a bar COUNT vs a threshold — nothing more.

THRESHOLD reasoning — ``THIN_HISTORY_BARS`` is the LONGEST price-history lookback any ACTIVE reader needs:

- the volume-breakout DETECTOR reads ``breakout_lookback_days = 120`` (calendar days ≈ 82 trading bars);
- the SMA DISPLAY signal's slow window is ``SMA_SLOW = 200`` BARS — the 200-day SMA is uncomputable below
  200 bars (``signals/display/sma.py``).

``max(82, 200) = 200`` bars. Below 200 stored bar-dates in a trailing year a name cannot feed the slow SMA
and starves the longest-window reads — so 200 is the starvation threshold.

Pure + display-only (the ``origin.py`` / ``filer_coverage.py`` discipline): this imports NOTHING from
``backend/calls`` or ``backend/signals`` composition. It is derive-on-read from a bar count, NEVER persisted
and NEVER a call input — structurally unable to affect a call (bars landing under a ``security_id`` is what
the detectors read; a COUNT of them is data-health, a different thing). It COVERS the resolver's blind spots:
a genuinely-uncovered name (WONDF, 0 bars, no resolvable symbol) still flags thin.
"""

from __future__ import annotations

# The longest active price-history lookback (SMA slow window, 200 bars > the breakout's ~82) — the
# starvation threshold. A shared constant so the backfill candidate pre-gate (Slice D) and the visibility
# flag (Slice F) agree on one number.
THIN_HISTORY_BARS = 200


def is_thin_history(bar_count: int) -> bool:
    """True when a name's stored bar-dates fall below the starvation threshold (the longest active
    lookback). Derive-on-read from a bar count — never persisted, never a call input (#6/#7)."""
    return bar_count < THIN_HISTORY_BARS
