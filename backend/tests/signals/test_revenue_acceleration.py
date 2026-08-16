"""§2.2 revenue-acceleration detector — the R6 rule, unit-level (pure ``score`` over hand-built as-of rows).

The rule: g_q = rev_q/rev_{q-4} − 1; a_q = g_q − g_{q-1}; FIRE when a_q flips strictly > 0 after <= 0 with
the floor g_q >= 10%. Grade CORE, kind CATALYST/EARNINGS, liveness 180d anchored at the inflection quarter's
FILED date. The negatives pin the guards (still-decelerating, below-floor, a missing quarter, aged-out).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import CatalystType, Grade, Kind, Role
from signals import revenue_acceleration as ra

_SID = uuid4()

# Eight CONSECUTIVE calendar quarters (period_end at Mar/Jun/Sep/Dec). The fire quarter is the last
# (2023-12-31): YoY growth decelerates 0.30 -> 0.25 (accel −0.05, <= 0) then re-accelerates to 0.35 (accel
# +0.10, > 0) — the flip. Values are chosen so ONLY the last quarter is an inflection (the earlier ones lack
# a full year-prior lineage or don't flip). filed ~ +46d after each period end (the knowability date).
_FILED = {  # period_end -> filed (10-Q/10-K acceptance)
    date(2022, 3, 31): date(2022, 5, 16),
    date(2022, 6, 30): date(2022, 8, 15),
    date(2022, 9, 30): date(2022, 11, 14),
    date(2022, 12, 31): date(2023, 2, 15),
    date(2023, 3, 31): date(2023, 5, 15),
    date(2023, 6, 30): date(2023, 8, 14),
    date(2023, 9, 30): date(2023, 11, 13),
    date(2023, 12, 31): date(2024, 2, 15),  # THE inflection quarter's filing
}
_FIRE_FILED = date(2024, 2, 15)
# revenue per quarter (period_end -> USD). Year-prior (2022) bases are 100; 2023 currents set the YoY %.
_REV = {
    date(2022, 3, 31): 100.0,
    date(2022, 6, 30): 100.0,
    date(2022, 9, 30): 100.0,
    date(2022, 12, 31): 100.0,
    date(2023, 3, 31): 120.0,  # g=0.20
    date(2023, 6, 30): 130.0,  # g=0.30  (prev2 of the fire quarter)
    date(2023, 9, 30): 125.0,  # g=0.25  (prev of the fire quarter) -> accel −0.05
    date(2023, 12, 31): 135.0,  # g=0.35 -> accel +0.10  => FLIP
}


def _row(period_end: date, value: float, *, accn: str = "acc") -> dict:
    return {
        "metric_key": "revenue",
        "period_end": period_end,
        "value": value,
        "valid_from": _FILED[period_end],
        "accession": f"{accn}-{period_end.isoformat()}",
        "fiscal_period": "Q4" if period_end.month == 12 else "Q?",
        "fiscal_year": period_end.year,
    }


def _series(rev: dict[date, float] | None = None) -> list[dict]:
    rev = rev or _REV
    return [_row(pe, v) for pe, v in rev.items()]


def test_fires_core_on_a_yoy_reacceleration_flip():
    asof = date(2024, 3, 1)  # within the 180d liveness of the 2024-02-15 filing
    ev = ra.score(_series(), _SID, asof, DEFAULT_CONFIG)
    assert ev is not None
    assert ev.fired and ev.role is Role.ENTRY_TRIGGER
    assert ev.kind is Kind.CATALYST and ev.type is CatalystType.EARNINGS
    assert ev.grade is Grade.CORE  # R6: a fundamental inflection earns core
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.revenue_accel_alpha_liveness_days == 180
    # fire-date-anchored at the inflection quarter's FILED date, not the period end (#1)
    assert ev.asof == _FIRE_FILED
    # provenance = that quarter's accession, with the computation shown (#6)
    assert ev.provenance and ev.provenance[0].ref == f"acc-{date(2023, 12, 31).isoformat()}"
    assert ev.provenance[0].detail["yoy_growth"] == 0.35
    assert ev.provenance[0].detail["acceleration"] == 0.10


def test_declines_when_acceleration_is_still_negative():
    # the last quarter DECELERATES instead of re-accelerating (135 -> 115: g=0.15 < prev g=0.25) => accel <=0
    rev = dict(_REV)
    rev[date(2023, 12, 31)] = 115.0
    assert ra.score(_series(rev), _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_declines_below_the_yoy_floor_even_on_a_flip():
    # acceleration flips positive but g_q is only 5% (< the 10% floor): a collapse-then-tiny-bounce, not the
    # structural inflection. prev2 g=0.02, prev g=−0.02 (accel −0.04, <=0), q g=0.05 (accel +0.07, >0).
    rev = {
        date(2022, 3, 31): 100.0,
        date(2022, 6, 30): 100.0,
        date(2022, 9, 30): 100.0,
        date(2022, 12, 31): 100.0,
        date(2023, 3, 31): 100.0,
        date(2023, 6, 30): 102.0,  # g=0.02
        date(2023, 9, 30): 98.0,  # g=−0.02 -> accel −0.04
        date(2023, 12, 31): 105.0,  # g=0.05 -> accel +0.07 (a flip) BUT below the 10% floor
    }
    assert ra.score(_series(rev), _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_declines_on_a_missing_quarter_in_the_lineage():
    # drop the immediately-preceding quarter (2023-09-30): the fire quarter's prior-quarter now falls in a
    # >1-quarter gap -> no consecutive prior -> the detector declines rather than compare non-adjacent quarters
    rows = [r for r in _series() if r["period_end"] != date(2023, 9, 30)]
    assert ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_declines_when_the_year_prior_base_is_missing():
    # drop the fire quarter's year-prior (2022-12-31): g_q is uncomputable -> decline (no fabricated ratio)
    rows = [r for r in _series() if r["period_end"] != date(2022, 12, 31)]
    assert ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_declines_once_the_inflection_has_aged_past_liveness():
    # the flip is real but asof is > 180d after the 2024-02-15 filing -> the edge has lapsed (R8)
    assert ra.score(_series(), _SID, date(2024, 9, 1), DEFAULT_CONFIG) is None


def test_no_fundamentals_declines():
    assert ra.score([], _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_ignores_non_revenue_metric_rows():
    # a future §2.4 margin/FCF row (a different metric_key) must not feed the revenue detector
    rows = _series() + [
        {
            "metric_key": "gross_margin",
            "period_end": date(2023, 12, 31),
            "value": 0.6,
            "valid_from": _FIRE_FILED,
            "accession": "margin",
            "fiscal_period": "Q4",
            "fiscal_year": 2023,
        }
    ]
    ev = ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.provenance[0].detail["metric"] == "revenue"


def test_detect_reads_the_pit_accessor():
    # the detector path: detect() pulls fundamentals_facts from the pit, then scores
    pit = SimpleNamespace(fundamentals_facts=lambda sid: _series())
    ev = ra.detect(pit, _SID, date(2024, 3, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE


def test_declines_on_a_sub_threshold_acceleration():
    # A REAL flip (a_prev <= 0 < a_q) that clears the 10% YoY LEVEL floor but whose acceleration is a sub-2pp
    # tick — the UNH-class noise fire that read "+12% up from +12%" (YoY 12.2% -> 12.3%, a_q=+0.1pp). The
    # MAGNITUDE floor (revenue_accel_min_accel=0.02) now declines it. Year-prior (2022) bases = 100.
    rev = {
        date(2022, 3, 31): 100.0,
        date(2022, 6, 30): 100.0,
        date(2022, 9, 30): 100.0,
        date(2022, 12, 31): 100.0,
        date(2023, 3, 31): 112.0,  # g=0.120
        date(2023, 6, 30): 112.5,  # g=0.125   (prev2 of the fire quarter)
        date(2023, 9, 30): 112.2,  # g=0.122 -> accel -0.003 (<= 0)
        date(2023, 12, 31): 112.3,  # g=0.123 -> accel +0.001 (a flip, but +0.1pp << the 2pp floor)
    }
    assert ra.score(_series(rev), _SID, date(2024, 3, 1), DEFAULT_CONFIG) is None


def test_sub_threshold_acceleration_fires_once_the_floor_is_lowered():
    # The no-magic-number guard (mirrors CallConfig's discipline): the SAME sub-2pp flip FIRES when the dial
    # is set to 0.0 — proving the floor is load-bearing, not incidental to another guard.
    from domain.config import CallConfig

    rev = {
        date(2022, 3, 31): 100.0,
        date(2022, 6, 30): 100.0,
        date(2022, 9, 30): 100.0,
        date(2022, 12, 31): 100.0,
        date(2023, 3, 31): 112.0,
        date(2023, 6, 30): 112.5,
        date(2023, 9, 30): 112.2,
        date(2023, 12, 31): 112.3,
    }
    ev = ra.score(_series(rev), _SID, date(2024, 3, 1), CallConfig(revenue_accel_min_accel=0.0))
    assert ev is not None and ev.grade is Grade.CORE
