from __future__ import annotations

from datetime import date, datetime, timezone

from ingest.shares import ingest_shares_outstanding
from signals.base import PointInTimeData

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_shares_ingest_and_asof_read(db, security_id):
    """A ratified shares-outstanding fact reads back through the PIT accessor at a later as-of."""
    ingest_shares_outstanding(
        db,
        security_id,
        shares=141_000_000,
        source="ratified",
        source_ref="10-Q-2026Q1-cover",
        event_date=date(2026, 5, 1),
        ratified_by="operator",
    )
    db.commit()
    rows = PointInTimeData(db, asof=date(2026, 6, 1), known_at=_KNOWN).shares_outstanding_facts(
        security_id
    )
    assert len(rows) == 1
    assert float(rows[0]["shares"]) == 141_000_000.0


def test_shares_latest_version_wins_on_correction(db, security_id):
    """A restatement (same source_ref, later recorded_at) supersedes — no lookahead on the correction."""
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    ingest_shares_outstanding(
        db,
        security_id,
        shares=141_000_000,
        source="ratified",
        source_ref="10-Q-cover-X",
        event_date=date(2026, 5, 1),
        recorded_at=t1,
    )
    ingest_shares_outstanding(
        db,
        security_id,
        shares=148_000_000,  # the restatement (a later count under the same cover ref)
        source="ratified",
        source_ref="10-Q-cover-X",
        event_date=date(2026, 5, 1),
        recorded_at=t2,
    )
    db.commit()
    at_t1 = PointInTimeData(db, asof=date(2026, 9, 1), known_at=t1).shares_outstanding_facts(
        security_id
    )
    at_t2 = PointInTimeData(db, asof=date(2026, 9, 1), known_at=t2).shares_outstanding_facts(
        security_id
    )
    assert len(at_t1) == 1 and float(at_t1[0]["shares"]) == 141_000_000.0
    assert len(at_t2) == 1 and float(at_t2[0]["shares"]) == 148_000_000.0
