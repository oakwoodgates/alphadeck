"""The public clock for filings dated by their SEC acceptance (F7) and share counts given a public date (F9).

- An evening 8-K re-dated by the repair (a new version: ``accepted`` + ``valid_from`` = the acceptance's market
  day) is read on MONDAY's history view, every version of the fact shares that one instant (the old version never
  wins a later read), and the backtest mirror keeps exactly that version.
- THE TWO GATES (the F6 trap): a Tuesday 8-K is never in Monday's view even with a history cutoff past its
  acceptance — ``valid_from <= asof`` still guards the day.
- A share count counts from its filing's acceptance (its ``filing_accepted``, or a restating version's), a version
  never counts before the one it replaced (the running max), and with every version visible the public pick is
  the record pick.
- The freeze ceiling reads each as it stood at the cut.

Dates: 2026-10-26 is a Monday; EDT (UTC-4). Fixed instants throughout.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from psycopg import sql

from db.bitemporal import append_fact, as_of
from db.public_clock import PUBLIC_COL, public_params, public_versions_sql
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_tz

T = DEFAULT_TENANT_ID
UTC = timezone.utc
FAR = datetime(2100, 1, 1, tzinfo=UTC)
MON, TUE = date(2026, 10, 26), date(2026, 10, 27)
EVENING = datetime(2026, 10, 26, 22, 5, tzinfo=UTC)  # 18:05 EDT Monday


def _end_of(d: date) -> datetime:
    """Midnight ET after ``d`` — today's history cutoff (``known_at_for_asof``)."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=market_tz())


def _read(db, table, sid, asof, known_at, clock="public"):
    return as_of(db, table, security_id=sid, asof=asof, known_at=known_at, tenant_id=T, clock=clock)


def _8k(sid, accession, *, valid_from, filed, recorded_at, accepted=None):
    return {
        "tenant_id": T,
        "security_id": sid,
        "form": "8-K",
        "items": ["8.01"],
        "accession": accession,
        "filed": filed,
        "accepted": accepted,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": valid_from,
        "recorded_at": recorded_at,
    }


def _redated_8k(db, sid) -> None:
    """Stored before the rule (dated by its Tuesday filing date), then re-dated by the repair (Monday evening)."""
    append_fact(
        db,
        "fact_corporate_event",
        _8k(
            sid,
            "ACC-EVE",
            valid_from=TUE,
            filed=TUE,
            recorded_at=datetime(2026, 10, 28, tzinfo=UTC),
        ),
    )
    append_fact(
        db,
        "fact_corporate_event",
        _8k(
            sid,
            "ACC-EVE",
            valid_from=MON,
            filed=TUE,
            recorded_at=datetime(2026, 10, 31, tzinfo=UTC),
            accepted=EVENING,
        ),
    )
    db.commit()


def test_a_re_dated_8k_is_read_on_the_night_it_appeared_and_its_old_version_never_wins(
    db, security_id
):
    _redated_8k(db, security_id)
    mon = _read(db, "fact_corporate_event", security_id, MON, _end_of(MON))
    assert [(r["valid_from"], r["recorded_at"]) for r in mon] == [(MON, EVENING)]
    # a later view reads the re-dated version too — every version shares the fact's one acceptance, and the
    # newest statement wins (the old version, dated Tuesday, never returns)
    tue = _read(db, "fact_corporate_event", security_id, TUE, _end_of(TUE))
    assert [(r["valid_from"], r["accepted"], r["recorded_at"]) for r in tue] == [
        (MON, EVENING, EVENING)
    ]
    # with every version visible the public pick is the record pick
    rec = _read(db, "fact_corporate_event", security_id, TUE, FAR, clock="record")
    assert [r["id"] for r in rec] == [r["id"] for r in tue]


def test_a_tuesday_8k_never_reaches_mondays_view_whatever_the_cutoff(db, security_id):
    """The F6 trap: a cutoff later than midnight (06:00 the next session, say) admits a Tuesday-morning
    acceptance's INSTANT — the ``valid_from <= asof`` gate still keeps the Tuesday filing out of Monday.
    """
    tue_morning = datetime(2026, 10, 27, 14, 0, tzinfo=UTC)  # 10:00 EDT Tuesday
    append_fact(
        db,
        "fact_corporate_event",
        _8k(
            security_id,
            "ACC-TUE",
            valid_from=TUE,
            filed=TUE,
            recorded_at=datetime(2026, 10, 28, tzinfo=UTC),
            accepted=tue_morning,
        ),
    )
    db.commit()
    late_cutoff = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # past the acceptance
    assert _read(db, "fact_corporate_event", security_id, MON, late_cutoff) == []
    assert [
        r["accession"] for r in _read(db, "fact_corporate_event", security_id, TUE, late_cutoff)
    ] == ["ACC-TUE"]


def test_an_8k_with_no_acceptance_counts_from_06_00_of_its_filing_day(db, security_id):
    append_fact(
        db,
        "fact_corporate_event",
        _8k(
            security_id,
            "ACC-OLD",
            valid_from=TUE,
            filed=TUE,
            recorded_at=datetime(2026, 11, 5, tzinfo=UTC),
        ),
    )
    db.commit()
    (row,) = _read(db, "fact_corporate_event", security_id, TUE, _end_of(TUE))
    # its filing day at EDGAR's open — the first instant of that day's night (F6; it was 00:00, which the night
    # before's cap, to 06:00, would have reached)
    assert row["recorded_at"] == datetime.combine(TUE, time(6), tzinfo=market_tz())


def test_the_mirror_keeps_the_re_dated_version_at_the_acceptance(db, security_id):
    from replay.export import _read_table_public

    _redated_8k(db, security_id)
    _schema, kept, counts = _read_table_public(db, "fact_corporate_event", T)
    mine = [r for r in kept if r["security_id"] == security_id]
    assert [(r["valid_from"], r["recorded_at"]) for r in mine] == [(MON, EVENING)]
    assert counts["identities_lost"] == 0
    db.commit()


def test_the_freeze_ceiling_reads_an_8k_as_it_stood_at_the_cut(db, security_id):
    _redated_8k(db, security_id)

    def instants(cut: datetime | None) -> set[datetime]:
        q = public_versions_sql(
            "fact_corporate_event",
            where=sql.SQL("tenant_id = %(t)s AND security_id = %(s)s"),
            ceiling=cut is not None,
        )
        with db.cursor() as cur:
            cur.execute(q, {"t": T, "s": security_id, **public_params(cut)})
            return {r[PUBLIC_COL] for r in cur.fetchall()}

    start_tue = datetime.combine(TUE, time(6), tzinfo=market_tz())  # the filing day's 06:00 (F6)
    assert instants(datetime(2026, 10, 29, tzinfo=UTC)) == {
        start_tue
    }  # the cut held the old version only
    assert instants(None) == {EVENING}
    db.commit()


# --- F9: share counts -----------------------------------------------------------------------------------------

REF = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/q3.htm"
FILED_AT = datetime(2026, 8, 7, 20, 0, tzinfo=UTC)  # the 10-Q's acceptance
RATIFIED = datetime(2026, 9, 10, tzinfo=UTC)


def _shares(db, sid, shares, *, recorded_at, accepted=None, valid_from=date(2026, 8, 3)):
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": T,
            "security_id": sid,
            "shares": shares,
            "source": "10-q-cover",
            "source_ref": REF,
            "ratified_by": "auto",
            "valid_from": valid_from,
            "recorded_at": recorded_at,
            "filing_accession": "0000000001-26-000001" if accepted else None,
            "filing_accepted": accepted,
        },
    )
    db.commit()


def test_a_share_count_counts_from_its_filing_not_its_ratification(db, security_id):
    _shares(db, security_id, 100.0, recorded_at=RATIFIED, accepted=FILED_AT)
    between = datetime(2026, 8, 20, tzinfo=UTC)
    (row,) = _read(db, "fact_shares_outstanding", security_id, date(2026, 8, 20), between)
    assert row["recorded_at"] == FILED_AT and float(row["shares"]) == 100.0
    assert (
        _read(
            db,
            "fact_shares_outstanding",
            security_id,
            date(2026, 8, 6),
            datetime(2026, 8, 7, tzinfo=UTC),
        )
        == []
    )


def test_a_restating_version_gives_the_older_one_its_publication(db, security_id):
    """The repair appends a RESTATEMENT (same count, same date) carrying the publication: both versions are one
    statement, so both count from the filing, and the restatement (newest) is the one read."""
    _shares(db, security_id, 100.0, recorded_at=RATIFIED)  # stored before F9
    _shares(
        db, security_id, 100.0, recorded_at=datetime(2026, 10, 31, tzinfo=UTC), accepted=FILED_AT
    )
    (row,) = _read(
        db,
        "fact_shares_outstanding",
        security_id,
        date(2026, 8, 20),
        datetime(2026, 8, 20, tzinfo=UTC),
    )
    assert row["recorded_at"] == FILED_AT and row["filing_accepted"] == FILED_AT


def test_a_later_version_never_counts_before_the_one_it_replaced(db, security_id):
    """An operator override (no cover row: no publication) counts from its own ratification; the version before
    it keeps its filing's instant — the running max keeps the pick monotone."""
    _shares(db, security_id, 100.0, recorded_at=RATIFIED, accepted=FILED_AT)
    override = datetime(2026, 9, 20, tzinfo=UTC)
    _shares(db, security_id, 120.0, recorded_at=override)
    mid = _read(
        db,
        "fact_shares_outstanding",
        security_id,
        date(2026, 9, 15),
        datetime(2026, 9, 15, tzinfo=UTC),
    )
    assert [float(r["shares"]) for r in mid] == [100.0]
    late = _read(db, "fact_shares_outstanding", security_id, date(2026, 10, 1), FAR)
    assert [(float(r["shares"]), r["recorded_at"]) for r in late] == [(120.0, override)]
    rec = _read(db, "fact_shares_outstanding", security_id, date(2026, 10, 1), FAR, clock="record")
    assert [r["id"] for r in rec] == [r["id"] for r in late]


def test_an_older_filings_count_cannot_jump_ahead_of_its_predecessor(db, security_id):
    """A later version whose count is an OLDER filing's cover (its own instant earlier) is lifted to the instant
    of the version it replaced — never earlier — so the newest version still wins every later read.
    """
    _shares(db, security_id, 100.0, recorded_at=RATIFIED, accepted=FILED_AT)
    older = datetime(2026, 5, 8, 20, 0, tzinfo=UTC)  # the previous 10-Q, its cover as of 05-04
    _shares(
        db,
        security_id,
        90.0,
        recorded_at=datetime(2026, 9, 20, tzinfo=UTC),
        accepted=older,
        valid_from=date(2026, 5, 4),
    )
    (row,) = _read(db, "fact_shares_outstanding", security_id, date(2026, 10, 1), FAR)
    assert (
        float(row["shares"]) == 90.0 and row["recorded_at"] == FILED_AT
    )  # max(own 05-08, predecessor 08-07)


def test_the_freeze_ceiling_reads_a_share_count_as_it_stood_at_the_cut(db, security_id):
    _shares(db, security_id, 100.0, recorded_at=RATIFIED)
    _shares(
        db, security_id, 100.0, recorded_at=datetime(2026, 10, 31, tzinfo=UTC), accepted=FILED_AT
    )

    def instants(cut: datetime | None) -> list[datetime]:
        q = public_versions_sql(
            "fact_shares_outstanding",
            where=sql.SQL("tenant_id = %(t)s AND security_id = %(s)s"),
            ceiling=cut is not None,
        )
        with db.cursor() as cur:
            cur.execute(q, {"t": T, "s": security_id, **public_params(cut)})
            return sorted(r[PUBLIC_COL] for r in cur.fetchall())

    assert instants(datetime(2026, 10, 1, tzinfo=UTC)) == [
        RATIFIED
    ]  # before the restatement: ratification
    assert instants(None) == [FILED_AT, FILED_AT]
    db.commit()
