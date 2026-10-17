"""M1/M2 — per-(thesis, night) coverage and the on-time window (``pipeline/night_coverage.py``).

The pure judge is stated with direct inputs (its contract), shaped on the MEASURED 2026-10-12 night: the
22:30 ET pass recorded 4 of 12 theses at 22:42-22:43 and died; the next day's 16:57 boot catch-up recorded
the rest (and, before M2, re-versioned the 4 at ~17:04). The DB read is proved against real rows written by
the cron's own writer, with the cutoff placed BETWEEN genuine writes (the ``test_self_audit`` technique), so
a row the judge calls "before the cutoff" really was recorded before it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline import daily
from pipeline.night_coverage import (
    CallStamp,
    ThesisSpan,
    is_on_time,
    judge_coverage,
    next_edgar_open,
    on_time_theses,
    on_time_window,
    read_night_coverage,
)
from repositories import calls_repo

_NY = ZoneInfo("America/New_York")
_RUN_AT = time(22, 30)
_OCT12 = date(2026, 10, 12)  # a Monday


def _et(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=_NY)


# --- the on-time window ---------------------------------------------------------------------------------


def test_the_on_time_window_runs_from_RUN_AT_to_the_NEXT_WEEKDAYS_EDGAR_open():
    assert on_time_window(_OCT12, _RUN_AT, _NY) == (
        _et(2026, 10, 12, 22, 30),
        _et(2026, 10, 13, 6, 0),
    )
    # a Friday night's window runs to MONDAY 06:00 (the trigger's Mon-Fri calendar)
    assert next_edgar_open(date(2026, 10, 16), _NY) == _et(2026, 10, 19, 6, 0)
    # [start, end): the cutoff instant is on time, the EDGAR open instant is late
    assert is_on_time(_et(2026, 10, 12, 22, 30), _OCT12, run_at=_RUN_AT, tz=_NY)
    assert is_on_time(_et(2026, 10, 13, 5, 59, 59), _OCT12, run_at=_RUN_AT, tz=_NY)
    assert not is_on_time(_et(2026, 10, 13, 6, 0), _OCT12, run_at=_RUN_AT, tz=_NY)
    assert not is_on_time(_et(2026, 10, 12, 22, 29, 59), _OCT12, run_at=_RUN_AT, tz=_NY)


# --- the judge: the 10-12 night -------------------------------------------------------------------------


def _roster(n: int) -> list[ThesisSpan]:
    born = _et(2026, 9, 1, 12, 0)
    return [ThesisSpan(thesis_id=uuid.uuid4(), created_at=born) for _ in range(n)]


def _row(tid, at: datetime, *, asof: date = _OCT12, reconstructed: bool = False) -> CallStamp:
    return CallStamp(thesis_id=tid, asof=asof, reconstructed=reconstructed, recorded_at=at)


def test_the_KILLED_night_reads_4_of_12_and_the_catch_up_completes_it_LATE():
    spans = _roster(12)
    ids = [s.thesis_id for s in spans]
    killed = [_row(t, _et(2026, 10, 12, 22, 42)) for t in ids[:4]]  # the pass died after 4
    cov = judge_coverage([_OCT12], spans, killed, run_at=_RUN_AT, tz=_NY)[_OCT12]
    assert not cov.complete
    assert (len(cov.covered), len(cov.expected), len(cov.missing)) == (4, 12, 8)
    assert cov.describe() == "4 of 12 theses"
    assert cov.any_post_cutoff

    # the next day's catch-up (16:57 boot -> rows ~17:04): the 8 missing now covered, but LATE
    caught = killed + [_row(t, _et(2026, 10, 13, 17, 4)) for t in ids[4:]]
    cov = judge_coverage([_OCT12], spans, caught, run_at=_RUN_AT, tz=_NY)[_OCT12]
    assert cov.complete and len(cov.on_time) == 4
    assert cov.describe() == "12 of 12 theses (8 recorded late)"


def test_a_RECONSTRUCTED_or_DAYTIME_row_never_covers_and_says_which():
    spans = _roster(3)
    a, b, c = (s.thesis_id for s in spans)
    stamps = [
        _row(a, _et(2026, 10, 12, 22, 41)),  # the honest night row
        _row(b, _et(2026, 10, 20, 9, 0), reconstructed=True),  # a pipeline.backfill row, days later
        _row(c, _et(2026, 10, 12, 9, 15)),  # a pre-open "Run daily now" on the prior session's bars
    ]
    cov = judge_coverage([_OCT12], spans, stamps, run_at=_RUN_AT, tz=_NY)[_OCT12]
    assert cov.covered == {a} and cov.missing == {b, c}
    assert cov.reconstructed_only == {b} and cov.daytime_only == {c}
    assert cov.describe() == "1 of 3 theses (1 reconstructed only, 1 with a daytime row only)"
    assert cov.any_post_cutoff  # the honest row is evidence a pass recorded the night
    # a reconstructed row alone is NOT that evidence
    recon_only = judge_coverage([_OCT12], spans, stamps[1:2], run_at=_RUN_AT, tz=_NY)[_OCT12]
    assert not recon_only.any_post_cutoff


def test_the_ROSTER_is_the_nights_created_after_the_cutoff_is_out_archived_after_is_in():
    cutoff = _et(2026, 10, 12, 22, 30)
    born_before = ThesisSpan(uuid.uuid4(), created_at=cutoff - timedelta(hours=3))
    born_after = ThesisSpan(uuid.uuid4(), created_at=cutoff + timedelta(minutes=30))  # 23:00
    archived_before = ThesisSpan(
        uuid.uuid4(), created_at=cutoff - timedelta(days=9), archived_at=cutoff - timedelta(hours=1)
    )
    archived_after = ThesisSpan(
        uuid.uuid4(), created_at=cutoff - timedelta(days=9), archived_at=cutoff + timedelta(days=2)
    )
    cov = judge_coverage(
        [_OCT12],
        [born_before, born_after, archived_before, archived_after],
        [],
        run_at=_RUN_AT,
        tz=_NY,
    )[_OCT12]
    assert cov.expected == {born_before.thesis_id, archived_after.thesis_id}
    # a night before any thesis existed is vacuously complete — nothing was due
    empty = judge_coverage([date(2026, 1, 5)], [born_before], [], run_at=_RUN_AT, tz=_NY)
    assert empty[date(2026, 1, 5)].complete and not empty[date(2026, 1, 5)].expected


def test_on_time_theses_is_the_M2_skip_set_earliest_on_time_row_only():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    stamps = [
        _row(a, _et(2026, 10, 12, 22, 43)),
        _row(a, _et(2026, 10, 13, 17, 4)),  # a later re-version: the on-time one still stands
        _row(b, _et(2026, 10, 13, 17, 4)),  # LATE only -> the catch-up still owes it the night
        _row(c, _et(2026, 10, 12, 22, 50), reconstructed=True),  # reconstructed never counts
        _row(d, _et(2026, 10, 12, 22, 44), asof=date(2026, 10, 13)),  # another night
    ]
    assert on_time_theses(stamps, _OCT12, run_at=_RUN_AT, tz=_NY) == {a: _et(2026, 10, 12, 22, 43)}


# --- the DB read: counted in the table, real writer, cutoff between genuine writes ----------------------


def _thesis(db, name):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


def _db_now(db) -> datetime:
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        t = cur.fetchone()["t"]
    db.commit()
    return t.astimezone(timezone.utc)


def _record(db, tid, asof, **kw):
    card = daily.call_for_thesis(db, tid, asof, record=False)
    calls_repo.record_if_changed(db, card, DEFAULT_TENANT_ID, run_kind="cron", **kw)
    db.commit()


def test_read_night_coverage_counts_REAL_rows_against_a_cutoff_between_writes(db):
    early, ok, recon, none_ = (_thesis(db, n) for n in ("early", "ok", "recon", "none"))
    asof = _db_now(db).date()
    _record(db, early, asof)  # before the cutoff
    cutoff = _db_now(db)
    if cutoff.date() != asof:  # pragma: no cover — crossed UTC midnight between two reads
        pytest.skip("crossed UTC midnight mid-test")
    _record(db, ok, asof)
    _record(db, recon, asof, reconstructed=True)
    run_at = cutoff.time()

    cov = read_night_coverage(db, [asof], run_at=run_at, tz=timezone.utc)[asof]
    db.rollback()

    assert cov.expected == {early, ok, recon, none_}  # all created before the cutoff
    assert cov.covered == {ok} and cov.on_time == {ok}
    assert cov.daytime_only == {early} and cov.reconstructed_only == {recon}
    assert cov.describe() == "1 of 4 theses (1 reconstructed only, 1 with a daytime row only)"
    # every row counted, none deduped away: the table holds exactly the 3 rows written
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls WHERE asof = %s", (asof,))
        assert cur.fetchone()["n"] == 3
    db.rollback()
