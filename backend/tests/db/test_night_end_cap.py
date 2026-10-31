"""F6 (rule log ``2026-11-night-ends-at-edgar-open``): a night's history read runs to 06:00 ET on the next
weekday, and the DATE-ONLY TRAP stays shut.

A date-only public instant is 06:00 market time of its date (``db.public_clock._start_of_day`` — the first
instant of that date's night; it was 00:00, which Monday's new cap, Tuesday 05:59:59.999999 ET, would have
reached). So a Tuesday 8-K with no acceptance stored is public at Tuesday 06:00, outside Monday's night, and the
read's other half, ``valid_from <= asof``, keeps it out too. Where no valid-time gate exists — a Form 4/A whose
acceptance was never stored falls back to its FILING DAY — the 06:00 instant is the only guard, and it holds.
An INSTANT-clocked fact is the opposite case: a Form 4 accepted Tuesday 00:30 ET (a time SEC's list drifted
past midnight) was public before EDGAR's next open, so it counts for Monday. Both engines — the Postgres
history read and the public replay mirror the editions run on — must agree.
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
    # the 8-K carries no acceptance: its public instant is its filing day's 06:00 — outside Monday's cap (at
    # 00:00 it would have sat inside it) — and the valid-time half keeps it out of Monday's night as well
    assert _accessions(db, "fact_corporate_event", security_id, MON) == []
    assert _accessions(db, "fact_corporate_event", security_id, TUE) == ["tue-8k"]
    (row,) = as_of(
        db,
        "fact_corporate_event",
        security_id=security_id,
        asof=TUE,
        known_at=known_at_for_asof(TUE, now=FAR),
        tenant_id=T,
        clock="public",
    )
    assert row["recorded_at"] == _et(TUE, 6) and row["recorded_at"] > cap
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


def test_a_4a_with_no_stored_acceptance_never_counts_on_the_night_before_its_filing_day(
    db, tmp_path
):
    """The case with NO valid-time gate: a Form 4/A record, its link and the system's decision count from the
    4/A's acceptance, else its FILING DAY (``db.public_clock._amendment_public``). ATCH's real 4/As, here STATED
    as filed Tuesday 09-29 with no acceptance stored (a test input; SEC accepted them 21:01 ET on 09-28): on
    Monday 09-28's night — which runs to Tuesday 05:59:59 ET — the originals stand (code A); from Tuesday's
    night the correction reads (code P). At a 00:00 anchor Monday's cap would have held the filing day. Both
    engines agree."""
    from ingest.edgar.form4a import ingest_form4a
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror
    from tests.form4a_fixtures import (
        FILINGS,
        accepted_by_accession,
        amendments_of,
        filed_by_accession,
        ingest_originals,
        make_security,
        xml,
    )

    mon, tue = date(2026, 9, 28), date(2026, 9, 29)
    sid = make_security(db, "ATCH")
    ingest_originals(db, sid, "ATCH")  # accepted ~13:22 ET Monday 09-28
    for acc in amendments_of("ATCH"):
        f = FILINGS[acc]
        ingest_form4a(
            db,
            sid,
            xml(acc),
            acc,
            member_cik=f.cik,
            filed_by_accession=filed_by_accession(f.key),
            accepted_by_accession=accepted_by_accession(f.key),
            filed=tue,
            accepted=None,
        )
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment WHERE accepted IS NULL")
        assert cur.fetchone()["n"] == len(amendments_of("ATCH"))

    def codes(pit) -> set[str]:
        return {r["txn_code"] for r in pit.insider_txns(sid)}

    cap_mon = known_at_for_asof(mon, now=FAR)
    cap_tue = known_at_for_asof(tue, now=FAR)
    assert codes(PointInTimeData(db, asof=mon, known_at=cap_mon, clock="public")) == {"A"}
    assert codes(PointInTimeData(db, asof=tue, known_at=cap_tue, clock="public")) == {"P"}
    export_snapshot(db, tmp_path, clock="public")
    con = connect_mirror(tmp_path)
    assert codes(ReplayPointInTimeData(con, asof=mon, known_at=cap_mon)) == {"A"}
    assert codes(ReplayPointInTimeData(con, asof=tue, known_at=cap_tue)) == {"P"}
