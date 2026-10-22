"""§2.2 revenue-acceleration detector — the R6 rule, unit-level (pure ``score`` over hand-built as-of rows).

The rule: g_q = rev_q/rev_{q-4} − 1; a_q = g_q − g_{q-1}; FIRE when a_q flips strictly > 0 after <= 0 with
the floor g_q >= 10%. Grade CORE, kind CATALYST/EARNINGS, liveness 180d anchored at the inflection quarter's
FILED date. The negatives pin the guards (still-decelerating, below-floor, a missing quarter, aged-out).
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

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
    """One as-of row as the PIT returns it. One version per quarter here, so each quarter was first
    knowable at its own filing (the D3a ``knowable_*`` keys equal ``valid_from`` / ``accession``).
    """
    return {
        "metric_key": "revenue",
        "period_end": period_end,
        "value": value,
        "valid_from": _FILED[period_end],
        "accession": f"{accn}-{period_end.isoformat()}",
        "knowable_from": _FILED[period_end],
        "knowable_accession": f"{accn}-{period_end.isoformat()}",
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
            "knowable_from": _FIRE_FILED,
            "knowable_accession": "margin",
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


# --- D3a: first knowability + the tie-break (pure contract tests, direct inputs) -----------------------


def test_anchors_on_first_knowability_not_the_picked_versions_valid_from():
    """The fire quarter re-reported UNCHANGED a year later (its next-year comparative): the picked version's
    ``valid_from`` moves to 2025-02-14, its ``knowable_from`` stays 2024-02-15. At 2025-03-01 the inflection
    is 380 days old — not live — so the detector declines; dated by ``valid_from`` it would re-fire.
    """
    rows = _series()
    fire = next(r for r in rows if r["period_end"] == date(2023, 12, 31))
    fire["valid_from"], fire["accession"] = date(2025, 2, 14), "acc-comparative"
    assert ra.score(rows, _SID, date(2025, 3, 1), DEFAULT_CONFIG) is None
    # inside its own window it fires, dated AND cited at the first-knowable filing
    ev = ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.asof == _FIRE_FILED
    assert ev.provenance[0].ref == f"acc-{date(2023, 12, 31).isoformat()}"


def test_a_row_without_first_knowability_fails_loud():
    """A reader that skipped the first-knowability walk is the path that re-dated stale comparatives; the
    detector refuses its rows (a KeyError) rather than quietly falling back to ``valid_from``."""
    rows = _series()
    for r in rows:
        r.pop("knowable_from")
    with pytest.raises(KeyError, match="knowable_from"):
        ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG)


def test_tie_break_cites_the_newest_period_when_inflections_share_a_filing_date():
    """GEV's real revenue as the pre-D3a read dated it at 2026-10-01: Q1-2025 ($8,032M) carried its
    2026-04-22 comparative's date, the SAME date as the genuine Q1-2026 ($9,339M) — two live inflections on
    one date. ``max`` on the date alone returned the first, OLDER one (the card's "Q1 ending 2025-03-31");
    the tie-break cites the newest period that filing reported."""
    gev = [  # (period_end, revenue $M, the date the pre-D3a read gave it)
        (date(2023, 3, 31), 6822, date(2024, 4, 30)),
        (date(2023, 6, 30), 8119, date(2024, 7, 24)),
        (date(2023, 9, 30), 8253, date(2024, 10, 23)),
        (date(2023, 12, 31), 10045, date(2026, 1, 29)),
        (date(2024, 3, 31), 7260, date(2025, 4, 23)),
        (date(2024, 6, 30), 8204, date(2025, 7, 23)),
        (date(2024, 9, 30), 8913, date(2025, 10, 22)),
        (date(2024, 12, 31), 10559, date(2026, 1, 29)),
        (date(2025, 3, 31), 8032, date(2026, 4, 22)),
        (date(2025, 6, 30), 9111, date(2026, 7, 22)),
        (date(2025, 9, 30), 9969, date(2025, 10, 22)),
        (date(2025, 12, 31), 10956, date(2026, 1, 29)),
        (date(2026, 3, 31), 9339, date(2026, 4, 22)),
        (date(2026, 6, 30), 11104, date(2026, 7, 22)),
    ]
    rows = [
        {
            "metric_key": "revenue",
            "period_end": pe,
            "value": v * 1_000_000,
            "valid_from": d,
            "accession": f"accn-{d.isoformat()}",
            "knowable_from": d,
            "knowable_accession": f"accn-{d.isoformat()}",
            "fiscal_period": f"Q{(pe.month - 1) // 3 + 1}",  # GEV's fiscal year is the calendar year
        }
        for pe, v, d in gev
    ]
    # the tie-break itself, stated with rule (c) held off (``None``) — under rule (c) the stale Q1-2025 (387
    # days past its period end) is never a candidate, so the tie no longer arises and the answer is the same
    no_cap = DEFAULT_CONFIG.model_copy(update={"revenue_accel_max_quarter_age_days": None})
    for cfg in (no_cap, DEFAULT_CONFIG):
        ev = ra.score(rows, _SID, date(2026, 10, 1), cfg)
        assert ev is not None and ev.asof == date(2026, 4, 22)
        assert "ending 2026-03-31" in ev.label
        assert ev.provenance[0].detail["period_end"] == "2026-03-31"


# --- rule (c): the cited quarter must be fresh at knowability (operator sign-off 2026-10-22) ------------


def _restated(rows: list[dict], period_end: date, knowable: date, accn: str) -> list[dict]:
    """The rows with ONE quarter first knowable at ``knowable`` (a later filing that restated it)."""
    out = [dict(r) for r in rows]
    q = next(r for r in out if r["period_end"] == period_end)
    q.update(valid_from=knowable, accession=accn, knowable_from=knowable, knowable_accession=accn)
    return out


def test_rule_c_boundary_at_135_days():
    """Contract (direct inputs): the fire quarter (2023-12-31) first knowable exactly 135 days after its period
    end still fires; at 136 days it may not be the cited quarter. ``None`` lifts the cap (the rule before).
    """
    assert DEFAULT_CONFIG.revenue_accel_max_quarter_age_days == 135
    fire = date(2023, 12, 31)
    at = _restated(_series(), fire, fire + timedelta(days=135), "acc-135")
    ev = ra.score(at, _SID, date(2024, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.asof == fire + timedelta(days=135)
    assert ev.provenance[0].ref == "acc-135"

    past = _restated(_series(), fire, fire + timedelta(days=136), "acc-136")
    assert ra.score(past, _SID, date(2024, 6, 1), DEFAULT_CONFIG) is None
    no_cap = DEFAULT_CONFIG.model_copy(update={"revenue_accel_max_quarter_age_days": None})
    assert ra.score(past, _SID, date(2024, 6, 1), no_cap) is not None


def test_rule_c_a_restated_year_old_quarter_never_fires_but_stays_a_comparison_base():
    """Contract (direct inputs), the RKT shape: a filing restating last year's quarter makes its number new
    knowledge at the later filing (D3a rule (a)), but a quarter that ended a year before cannot be the one the
    trigger fires on. Stale quarters still serve as a fresh quarter's year-prior and prior bases."""
    # every quarter but the fire quarter re-dated to the fire quarter's 10-K (restated in it): all stale
    rows = _series()
    for pe in _REV:
        if pe != date(2023, 12, 31):
            rows = _restated(rows, pe, _FIRE_FILED, f"acc-restated-{pe.isoformat()}")
    ev = ra.score(rows, _SID, date(2024, 3, 1), DEFAULT_CONFIG)
    assert (
        ev is not None and ev.asof == _FIRE_FILED
    )  # the fresh quarter (46 d) fires on those bases
    assert ev.provenance[0].detail["period_end"] == "2023-12-31"

    # the fire quarter itself restated a year later (403 days — RKT's Q2-2025 -> 2026-08-07 shape): never cited
    stale = _restated(_series(), date(2023, 12, 31), date(2025, 2, 6), "acc-comparative-restated")
    assert ra.score(stale, _SID, date(2025, 3, 1), DEFAULT_CONFIG) is None
    no_cap = DEFAULT_CONFIG.model_copy(update={"revenue_accel_max_quarter_age_days": None})
    assert ra.score(stale, _SID, date(2025, 3, 1), no_cap) is not None  # what fired before rule (c)
