"""The filing index (migration 0070) through the point-in-time view — NO LOOKAHEAD on either clock (invariant
#1). ``PointInTimeData.filing_index`` inherits both clocks from ``_rows``: the RECORD clock (the live read:
``valid_from <= asof AND recorded_at <= known_at``) and the PUBLIC clock (a history view: from the row's ``filed``
date). Display only: the accessor is deliberately off the detectors' protocol. DB-backed."""

from __future__ import annotations

from datetime import date, datetime, timezone

from domain.market_time import known_at_for_asof
from ingest.edgar.filing_index import ingest_filing_index
from signals.base import PointInTimeData, SignalPointInTimeData

CIK = "0001234567"
_ASOF = date(2026, 6, 5)


def _row(form, accession, filed):
    return {
        "form": form,
        "accession": accession,
        "primary_doc": f"{accession}.htm",
        "filed": filed,
        "accepted_raw": "",
    }


def _seed(db, sid) -> None:
    """Three filings, each recorded at its own instant:
    - ON-TIME: filed 06-01, recorded that evening;
    - LATE-HELD: filed 06-02 (public then), first RECORDED 06-20 (a name onboarded later);
    - AFTER: filed 06-10, after the as-of."""
    for row, recorded in (
        (_row("S-1", "ON-TIME", "2026-06-01"), datetime(2026, 6, 1, 23, tzinfo=timezone.utc)),
        (_row("424B5", "LATE-HELD", "2026-06-02"), datetime(2026, 6, 20, 23, tzinfo=timezone.utc)),
        (_row("DEF 14A", "AFTER", "2026-06-10"), datetime(2026, 6, 10, 23, tzinfo=timezone.utc)),
    ):
        ingest_filing_index(db, sid, CIK, [row], recorded_at=recorded)
    db.commit()


def _accessions(rows) -> set[str]:
    return {r["accession"] for r in rows}


def test_the_RECORD_clock_sees_neither_a_later_filing_nor_one_recorded_after_known_at(
    db, security_id
):
    _seed(db, security_id)
    known_at = datetime(2026, 6, 5, 23, tzinfo=timezone.utc)
    pit = PointInTimeData(db, asof=_ASOF, known_at=known_at)
    assert _accessions(pit.filing_index(security_id)) == {"ON-TIME"}
    # ...and with the basket prefetch (the dashboards' read path), the same rows
    batch = PointInTimeData(db, asof=_ASOF, known_at=known_at, basket=[security_id])
    assert _accessions(batch.filing_index(security_id)) == {"ON-TIME"}


def test_the_PUBLIC_clock_sees_what_was_FILED_by_the_as_of_never_a_later_filing(db, security_id):
    """On a history view a row counts from its filing date: the late-recorded 06-02 filing was public by the
    as-of (visible); the 06-10 filing was not (invisible), whenever we stored either."""
    _seed(db, security_id)
    pit = PointInTimeData(
        db, asof=_ASOF, known_at=known_at_for_asof(_ASOF), basket=[security_id], clock="public"
    )
    assert _accessions(pit.filing_index(security_id)) == {"ON-TIME", "LATE-HELD"}
    later = date(2026, 6, 12)
    pit = PointInTimeData(db, asof=later, known_at=known_at_for_asof(later), clock="public")
    assert _accessions(pit.filing_index(security_id)) == {"ON-TIME", "LATE-HELD", "AFTER"}


def test_the_accessor_is_OFF_the_detectors_protocol():
    """Display data: a detector cannot grow a dependency on it through the protocol it is typed against."""
    assert hasattr(PointInTimeData, "filing_index")
    assert "filing_index" not in SignalPointInTimeData.__dict__
