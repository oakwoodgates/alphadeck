from __future__ import annotations

from datetime import date, datetime, timezone

from ingest.revenue_mix import ingest_revenue_mix
from signals.base import PointInTimeData

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_revenue_mix_ingest_and_asof_read(db, security_id):
    """A ratified revenue-mix fact reads back through the PIT accessor at a later as-of."""
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="nuclear",
        mix_pct=100,
        source="ratified",
        source_ref="10-K-2025-segments",
        event_date=date(2026, 1, 1),
        ratified_by="operator",
    )
    db.commit()
    rows = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN).revenue_mix_facts(
        security_id
    )
    assert len(rows) == 1
    assert rows[0]["segment_label"] == "nuclear"
    assert float(rows[0]["mix_pct"]) == 100.0


def test_revenue_mix_latest_version_wins_on_correction(db, security_id):
    """A restatement (same source_ref, later recorded_at) supersedes — and no lookahead on the correction."""
    t1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="nuclear",
        mix_pct=80,
        source="ratified",
        source_ref="10-K-segments-X",
        event_date=date(2026, 1, 1),
        recorded_at=t1,
    )
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="nuclear",
        mix_pct=100,  # the restatement
        source="ratified",
        source_ref="10-K-segments-X",
        event_date=date(2026, 1, 1),
        recorded_at=t2,
    )
    db.commit()
    at_t1 = PointInTimeData(db, asof=date(2026, 6, 1), known_at=t1).revenue_mix_facts(security_id)
    at_t2 = PointInTimeData(db, asof=date(2026, 6, 1), known_at=t2).revenue_mix_facts(security_id)
    assert len(at_t1) == 1 and float(at_t1[0]["mix_pct"]) == 80.0  # correction not yet known at t1
    assert len(at_t2) == 1 and float(at_t2[0]["mix_pct"]) == 100.0  # by t2 the restatement is live
