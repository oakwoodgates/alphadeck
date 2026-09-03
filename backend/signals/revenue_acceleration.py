"""§2.2 — the revenue/earnings ACCELERATION inflection detector: the structural CONVICTION behind the
5–10x "breakout" event (the NVDA-Jan-2024 tell). A Key-1 conviction (it WARMS; arming still needs a
co-located confirmation), core-grade.

Rule (R6/R7/R8, ratified):
  * YoY growth       g_q  = rev_q / rev_{q-4} − 1        (same fiscal quarter a year prior)
  * acceleration     a_q  = g_q − g_{q-1}                (quarter-over-quarter change in YoY growth)
  * FIRE when ``a_q`` flips strictly > 0 after being <= 0, with (a) a MAGNITUDE floor
    ``a_q >= revenue_accel_min_accel`` (a sub-threshold tick isn't a re-acceleration) and (b) a LEVEL
    floor ``g_q >= revenue_accel_min_yoy``.
  * role=entry_trigger · kind=CATALYST · type=EARNINGS (R7 — REUSE, no new Kind, so no OpenAPI change) ·
    grade=CORE (R6 — a fundamental inflection earns core) · liveness 180d, grade-decoupled (R8).

A SEPARATE detector, NOT folded into ``catalyst_conviction``: that one reads *ratified* ``fact_catalyst``
with the grade ON the fact (invariant #3); a COMPUTED inflection has no ratified grade, so its grade is a
detector rule here (core), and its number is a DETERMINISTIC companyfacts parse — the LLM never sources it
(#3). Provenance = the inflection quarter's 10-Q/10-K accession (#6). Fire-date-anchored at that quarter's
``filed`` date (not the period end) — the honest knowability (#1).

Honesty (#9/#3): a missing quarter, a non-positive YoY base, or a gap in the series -> the detector DECLINES
(returns None), never fabricates. It reads ``fundamentals_facts`` via the point-in-time view, so it runs
identically live and in replay (A.1).

§2.4 DEFERRED (margin / FCF crossover): the same shape over the same ``fact_fundamentals`` family under new
metric keys — built here for that, but only revenue-acceleration ships now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "revenue_acceleration"
# A fundamental inflection reads as strong conviction (mirrors a core catalyst's inline calibration; the
# magic-number guard is on the assembler). Grade is fixed CORE (R6), so this is the only score it emits.
_CORE_SCORE = 0.9

# The fact_fundamentals metric_key the ingest writes (ingest.fundamentals.REVENUE_METRIC) — kept a literal
# here so the pure detector needs no ingest import. §2.4's margin/FCF variants will gate on their own keys.
_REVENUE_METRIC = "revenue"

# Series-matching windows (period_end proximity — the detector reads period_end, the data column, and never
# trusts the fp/fy tags for its math). Inline detector calibration.
_YEAR_DAYS = 365
_YEAR_TOL_DAYS = 45  # ±45d absorbs 52/53-week fiscal calendars, leap years, small period-end drift
_QUARTER_MIN_GAP_DAYS = (
    60  # the immediately-preceding quarter sits ~1 quarter (>= ~2 months) earlier
)
_QUARTER_MAX_GAP_DAYS = (
    130  # ...and no more than ~4.3 months — a wider gap is NOT a consecutive quarter
)


@dataclass(frozen=True)
class _Q:
    """One as-of quarterly revenue point the detector reasons over (already deduped to the latest version per
    period_end by the bitemporal read). ``filed`` = the knowability/fire date."""

    period_end: date
    value: float
    filed: date
    accession: str
    fiscal_period: str | None


def _quarters(rows: list[dict[str, Any]]) -> list[_Q]:
    """The revenue points from the as-of fundamentals rows, sorted by period_end. Non-revenue metric keys
    (a future §2.4 margin/FCF row) and any row missing value/period_end/filed are skipped."""
    out: list[_Q] = []
    for r in rows:
        if r.get("metric_key") != _REVENUE_METRIC:
            continue
        val, pe, vf = r.get("value"), r.get("period_end"), r.get("valid_from")
        if val is None or pe is None or vf is None:
            continue
        out.append(
            _Q(
                period_end=pe,
                value=float(val),
                filed=vf,
                accession=r.get("accession") or "",
                fiscal_period=r.get("fiscal_period"),
            )
        )
    out.sort(key=lambda q: q.period_end)
    return out


def _year_prior(quarters: list[_Q], e: date) -> _Q | None:
    """The quarter ~1 year before period_end ``e`` (the same fiscal quarter a year prior), matched by
    period_end proximity within ±tolerance — the closest wins."""
    target = e - timedelta(days=_YEAR_DAYS)
    best: _Q | None = None
    best_gap: int | None = None
    for q in quarters:
        gap = abs((q.period_end - target).days)
        if gap <= _YEAR_TOL_DAYS and (best_gap is None or gap < best_gap):
            best, best_gap = q, gap
    return best


def _prior_quarter(quarters: list[_Q], e: date) -> _Q | None:
    """The immediately-preceding CONSECUTIVE quarter before period_end ``e`` (gap within one quarter). A
    wider gap returns None — so the detector declines across a hole rather than comparing non-adjacent
    quarters (honest, #9)."""
    best: _Q | None = None
    for q in quarters:
        d = (e - q.period_end).days
        if _QUARTER_MIN_GAP_DAYS <= d <= _QUARTER_MAX_GAP_DAYS and (
            best is None or q.period_end > best.period_end
        ):
            best = q
    return best


def _yoy(quarters: list[_Q], q: _Q) -> float | None:
    """g = value / value_{year prior} − 1, or None when the year-prior quarter is missing or its base is
    non-positive (no fabricated ratio off a zero/negative base)."""
    p = _year_prior(quarters, q.period_end)
    if p is None or p.value <= 0:
        return None
    return q.value / p.value - 1.0


@dataclass(frozen=True)
class _Inflection:
    g_q: float
    g_prev: float
    a_q: float
    a_prev: float


def _inflection(quarters: list[_Q], q: _Q, cfg: CallConfig) -> _Inflection | None:
    """Is ``q`` a strict acceleration flip (a_prev <= 0 < a_q) clearing the YoY floor? Needs q, its prior
    quarter, and the prior-of-prior — each with a computable YoY — else None (declines on any gap).
    """
    prev = _prior_quarter(quarters, q.period_end)
    if prev is None:
        return None
    prev2 = _prior_quarter(quarters, prev.period_end)
    if prev2 is None:
        return None
    g_q, g_prev, g_prev2 = _yoy(quarters, q), _yoy(quarters, prev), _yoy(quarters, prev2)
    if g_q is None or g_prev is None or g_prev2 is None:
        return None
    a_q = g_q - g_prev
    a_prev = g_prev - g_prev2
    if not (a_prev <= 0 < a_q):  # the flip: acceleration crosses from <= 0 to strictly > 0
        return None
    if a_q < cfg.revenue_accel_min_accel:
        # MAGNITUDE floor: a sub-threshold tick isn't a re-acceleration (UNH's real +0.07pp / "+12% up
        # from +12%" fire). Gates the CHANGE (a_q); the YoY floor below gates the LEVEL (g_q).
        return None
    if (
        g_q < cfg.revenue_accel_min_yoy
    ):  # the floor: don't fire off a collapse into a still-tiny base
        return None
    return _Inflection(g_q=g_q, g_prev=g_prev, a_q=a_q, a_prev=a_prev)


def score(
    rows: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the as-of fundamentals rows -> a core Key-1 conviction SignalEvent on the most recent LIVE
    revenue-acceleration inflection (or None). The rows are already as-of capped (valid_from <= asof), so a
    fired quarter's ``filed`` <= asof by construction; liveness then keeps it live for its horizon.
    """
    quarters = _quarters(rows)
    lv = cfg.revenue_accel_alpha_liveness_days
    hits: list[tuple[_Q, _Inflection]] = []
    for q in quarters:
        infl = _inflection(quarters, q, cfg)
        if infl is None:
            continue
        if not entry_signal_is_live(
            q.filed, lv, asof
        ):  # anchored at the inflection quarter's FILED date
            continue
        hits.append((q, infl))
    if not hits:
        return None
    q, infl = max(hits, key=lambda h: h[0].filed)  # the most recent live inflection
    label = (
        f"Revenue growth re-accelerated — YoY {infl.g_q:+.0%} (up from {infl.g_prev:+.0%}); "
        f"quarterly acceleration flipped positive at the {q.fiscal_period or 'quarter'} ending {q.period_end}"
    )
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.CATALYST,
        catalyst_type=CatalystType.EARNINGS,
        grade=Grade.CORE,
        score=_CORE_SCORE,
        label=label,
        alpha_liveness_days=lv,
        provenance=[
            source_provenance(
                "xbrl",
                q.accession,
                detail={
                    "metric": _REVENUE_METRIC,
                    "period_end": str(q.period_end),
                    "yoy_growth": round(infl.g_q, 4),
                    "prior_yoy_growth": round(infl.g_prev, 4),
                    "acceleration": round(infl.a_q, 4),
                    "prior_acceleration": round(infl.a_prev, 4),
                },
            )
        ],
        asof=q.filed,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — revenue-acceleration conviction (warms). Reads the as-of quarterly revenue series via the
    point-in-time view; arming still needs a co-located confirmation (a fresh breakout)."""
    return score(pit.fundamentals_facts(security_id), security_id, asof, cfg)


def horizons(cfg: CallConfig) -> dict[str, int | None]:
    """The READ-HORIZON declaration (``signals/horizons.py``): the quarterly XBRL series is read UNBOUNDED
    (``None``) — the detector's own liveness/asof filters select from the full as-of tape, so no event-
    time floor may ever be applied to it."""
    return {"fact_fundamentals": None}


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect, horizons=horizons))
