"""§2.2 in REPLAY — the acceleration detector runs over the DuckDB/Parquet mirror exactly as live (A.1),
fires CORE on a name whose YoY re-accelerated in a backtest window, and is anchored at the inflection
quarter's FILED date, NOT its period end: at the period-end date the inflection fact is not yet knowable,
so the detector declines. This is the honest "what would §2.2 have fired?" backtest guarantee (#1).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.config import DEFAULT_CONFIG
from domain.enums import CatalystType, Grade, Kind
from ingest.fundamentals import QuarterPoint, store_quarters
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from signals import revenue_acceleration as ra

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)

# The same re-acceleration series as the unit test, materialized as facts: YoY 0.30 -> 0.25 (accel −0.05)
# -> 0.35 (accel +0.10) — the flip at the 2023-12-31 quarter, filed 2024-02-15.
_FILED = {
    date(2022, 3, 31): date(2022, 5, 16),
    date(2022, 6, 30): date(2022, 8, 15),
    date(2022, 9, 30): date(2022, 11, 14),
    date(2022, 12, 31): date(2023, 2, 15),
    date(2023, 3, 31): date(2023, 5, 15),
    date(2023, 6, 30): date(2023, 8, 14),
    date(2023, 9, 30): date(2023, 11, 13),
    date(2023, 12, 31): date(2024, 2, 15),  # the inflection quarter's filing
}
_REV = {
    date(2022, 3, 31): 100.0,
    date(2022, 6, 30): 100.0,
    date(2022, 9, 30): 100.0,
    date(2022, 12, 31): 100.0,
    date(2023, 3, 31): 120.0,
    date(2023, 6, 30): 130.0,
    date(2023, 9, 30): 125.0,
    date(2023, 12, 31): 135.0,
}
_FIRE_FILED = date(2024, 2, 15)


def _quarters() -> list[QuarterPoint]:
    return [
        QuarterPoint(
            metric_key="revenue",
            period_end=pe,
            value=v,
            filed=_FILED[pe],
            accession=f"acc-{pe.isoformat()}",
            basis="native",
            fiscal_period="Q4" if pe.month == 12 else "Q?",
            fiscal_year=pe.year,
        )
        for pe, v in _REV.items()
    ]


def test_detector_fires_core_in_replay_anchored_at_filed_not_period_end(db, security_id, tmp_path):
    store_quarters(db, security_id, _quarters())
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        # BEFORE the filing — at the inflection's PERIOD END (2023-12-31 < filed 2024-02-15) the fact is not
        # yet knowable, so the mirror-backed detector declines (the anchor is FILED, not the period end).
        before = ReplayPointInTimeData(con, asof=date(2023, 12, 31), known_at=_PIN)
        assert ra.detect(before, security_id, date(2023, 12, 31), DEFAULT_CONFIG) is None

        # AT/after the filing — fires CORE, anchored at the filed date, provenance = the quarter's accession.
        rep = ReplayPointInTimeData(con, asof=date(2024, 3, 1), known_at=_PIN)
        ev = ra.detect(rep, security_id, date(2024, 3, 1), DEFAULT_CONFIG)
        assert ev is not None
        assert (
            ev.grade is Grade.CORE and ev.kind is Kind.CATALYST and ev.type is CatalystType.EARNINGS
        )
        assert ev.asof == _FIRE_FILED  # NOT the 2023-12-31 period end
        assert ev.provenance[0].ref == f"acc-{date(2023, 12, 31).isoformat()}"
    finally:
        con.close()
