"""F6 (rule log ``2026-11-night-ends-at-edgar-open``): a night's history read runs to 06:00 ET on the next
weekday, and the DATE-ONLY TRAP stays shut.

A date-clocked fact's public instant is the START of its market day (``db.public_clock``), so a Tuesday 8-K is
"public" at Tuesday 00:00 — before Monday's new cap (Tuesday 05:59:59.999999 ET). What keeps it out of Monday's
night is the read's other half, ``valid_from <= asof`` (every date-clocked table sets ``valid_from`` to its
public date). An INSTANT-clocked fact is the opposite case: a Form 4 accepted Tuesday 00:30 ET (a time SEC's list
drifted past midnight) was public before EDGAR's next open, so it now counts for Monday. Both engines — the
Postgres history read and the public replay mirror the editions run on — must agree.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from db.bitemporal import append_fact, as_of
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_tz
from signals.base import PointInTimeData

T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
MON, TUE = date(2026, 6, 1), date(2026, 6, 2)


def _et(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=market_tz())


def _plant(db, sid) -> None:
    # an 8-K filed (and dated) Tuesday, stored Tuesday evening
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": T,
            "security_id": sid,
            "form": "8-K",
            "items": ["2.02"],
            "accession": "tue-8k",
            "filed": TUE,
            "source_ref": "https://www.sec.gov/x",
            "valid_from": TUE,
            "recorded_at": _et(TUE, 20),
        },
    )
    # a Form 4 for a Monday trade, accepted 00:30 ET Tuesday, stored Tuesday 01:00
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": T,
            "security_id": sid,
            "insider_name": "A Director",
            "insider_role": "Director",
            "txn_code": "P",
            "shares": 1_000,
            "price": 10.0,
            "usd": 10_000.0,
            "accession": "past-midnight-4",
            "txn_seq": 0,
            "valid_from": MON,
            "accepted": _et(TUE, 0, 30),
            "recorded_at": _et(TUE, 1),
        },
    )
    db.commit()


def _accessions(db, table, sid, asof) -> list[str]:
    rows = as_of(
        db,
        table,
        security_id=sid,
        asof=asof,
        known_at=known_at_for_asof(asof, now=FAR),
        tenant_id=T,
        clock="public",
    )
    return [r["accession"] for r in rows]


def test_a_tuesday_8k_stays_out_of_mondays_night_and_a_0030_form4_counts_for_it(db, security_id):
    _plant(db, security_id)
    cap = known_at_for_asof(MON, now=FAR)
    assert cap == _et(TUE, 6) - timedelta(
        microseconds=1
    )  # the last instant before EDGAR's next open
    # the trap's premise: the 8-K's public instant (Tuesday 00:00) IS inside Monday's new cap ...
    assert datetime.combine(TUE, time(0), tzinfo=market_tz()) <= cap
    # ... and the valid-time half keeps it out of Monday's night anyway
    assert _accessions(db, "fact_corporate_event", security_id, MON) == []
    assert _accessions(db, "fact_corporate_event", security_id, TUE) == ["tue-8k"]
    # the Form 4 accepted at 00:30 Tuesday was public before EDGAR's next open: Monday's night holds it
    assert _accessions(db, "fact_insider_txn", security_id, MON) == ["past-midnight-4"]
    # (before F6 the night ended at midnight, and the same filing moved to Tuesday's night)
    sunday = date(2026, 5, 31)
    assert _accessions(db, "fact_insider_txn", security_id, sunday) == []


def test_the_point_in_time_view_and_the_public_mirror_agree(db, security_id, tmp_path):
    """The PIT the call path reads (``serve_view``'s public view of Monday) and the public replay mirror the
    editions engine reads: the same two answers, row for row."""
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    _plant(db, security_id)
    cap = known_at_for_asof(MON, now=FAR)
    pit = PointInTimeData(db, asof=MON, known_at=cap, clock="public")
    assert [r["accession"] for r in pit.corporate_event_facts(security_id)] == []
    assert [r["accession"] for r in pit.insider_txns(security_id)] == ["past-midnight-4"]

    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    mirror = ReplayPointInTimeData(con, asof=MON, known_at=cap)
    assert [r["accession"] for r in mirror.corporate_event_facts(security_id)] == []
    assert [r["accession"] for r in mirror.insider_txns(security_id)] == ["past-midnight-4"]
    on_tue = ReplayPointInTimeData(con, asof=TUE, known_at=known_at_for_asof(TUE, now=FAR))
    assert [r["accession"] for r in on_tue.corporate_event_facts(security_id)] == ["tue-8k"]
