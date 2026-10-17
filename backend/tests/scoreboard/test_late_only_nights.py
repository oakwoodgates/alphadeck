"""The LATE-ONLY nights (operator decision 2026-10-17) — ``scoreboard/late.py``'s subset of the L1 banner list
that Admin names: a (thesis, night) with at least one LATE row and NO on-time row.

The pure judge is stated with direct inputs (its contract), on the MEASURED 2026-10-12 stamps: the 22:30 ET
pass recorded 4 theses at 22:42-22:43 and died; the boot catch-up recorded 9 at 17:04-17:13 ET on 10-13 (one
of them re-versioning an on-time thesis — AI Memory & Storage's seq 1334 at 22:42, then seq 1367 at 17:04).
"Late" and "on time" are ``pipeline/night_coverage.py``'s (``is_late`` / ``is_on_time`` — one window), and
the entries ARE ``judge_late_nights``'s, filtered — so Admin's list and the Scoreboard banner cannot drift.
The read is proved on real rows (``tests/scoreboard/helpers.record_day`` writes each at its real stamp).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from scoreboard.late import judge_late_nights, judge_late_only_nights, late_only_nights
from tests.scoreboard.helpers import keys_fired, persist_thesis, record_day

_NY = ZoneInfo("America/New_York")
_RUN_AT = time(22, 30)
_FRI = date(2026, 10, 9)
_MON = date(2026, 10, 12)  # the night that died
_TUE = date(2026, 10, 13)


def _et(m, d, hh, mm=0, ss=0) -> datetime:
    return datetime(2026, m, d, hh, mm, ss, tzinfo=_NY)


_seq = iter(range(1, 10_000))


def _row(tid, name, asof, stamp, *, reconstructed=False) -> dict:
    """One ``calls_repo.call_stamps`` row."""
    return {
        "id": uuid.uuid4(),
        "seq": next(_seq),
        "thesis_id": tid,
        "thesis_name": name,
        "asof": asof,
        "reconstructed": reconstructed,
        "recorded_at": stamp,
    }


def _judge(stamps):
    return judge_late_only_nights(stamps, run_at=_RUN_AT, tz=_NY)


def test_the_10_12_shape_lists_exactly_the_8_theses_with_no_on_time_row():
    """4 on-time rows (22:42-22:43); 9 late catch-up rows (10-13 17:04-17:13), the first of them a
    re-version of an on-time thesis. Exactly the 8 others are late-only; the re-versioned thesis is in the
    banner's late list (its night holds a late row) but NOT late-only (an on-time row stands)."""
    re_versioned = uuid.uuid4()
    quiet = [uuid.uuid4() for _ in range(3)]
    late_only = [uuid.uuid4() for _ in range(8)]
    stamps = [_row(re_versioned, "AIM", _MON, _et(10, 12, 22, 42))]
    stamps += [_row(t, f"Q{i}", _MON, _et(10, 12, 22, 43)) for i, t in enumerate(quiet)]
    stamps.append(_row(re_versioned, "AIM", _MON, _et(10, 13, 17, 4)))
    stamps += [_row(t, f"L{i}", _MON, _et(10, 13, 17, 5 + i)) for i, t in enumerate(late_only)]

    out = _judge(stamps)
    assert [n.thesis_id for n in out] == late_only  # ordered by night, then thesis name
    assert all(n.asof == _MON and n.n_rows == 1 for n in out)
    assert all(n.earlier_recorded_at is None for n in out)  # a gap on the Scoreboard
    assert out[0].recorded_at == _et(10, 13, 17, 5)
    assert out[0].edgar_open == _et(10, 13, 6, 0)
    # the banner names 9 late nights; Admin's late-only list is the subset with no on-time row
    banner = judge_late_nights(stamps, tz=_NY)
    assert len(banner) == 9 and re_versioned in {n.thesis_id for n in banner}
    assert re_versioned not in {n.thesis_id for n in out}
    assert [n for n in banner if n.thesis_id != re_versioned] == out  # the SAME entries, filtered


def test_05_59_the_next_weekday_is_on_time_06_00_is_late_only():
    t = uuid.uuid4()
    assert _judge([_row(t, "T", _MON, _et(10, 13, 5, 59, 59))]) == []
    [n] = _judge([_row(t, "T", _MON, _et(10, 13, 6, 0))])
    assert (n.thesis_id, n.asof, n.recorded_at) == (t, _MON, _et(10, 13, 6, 0))
    # an on-time row at 05:59:59 beside a 06:00 row: the on-time row stands, so it is NOT late-only
    assert (
        _judge([_row(t, "T", _MON, _et(10, 13, 5, 59, 59)), _row(t, "T", _MON, _et(10, 13, 6))])
        == []
    )


def test_a_FRIDAY_night_runs_to_MONDAY_06_00():
    t = uuid.uuid4()
    for stamp in (
        _et(10, 10, 12, 0),
        _et(10, 11, 23, 0),
        _et(10, 12, 5, 59),
    ):  # Sat · Sun · Mon 05:59
        assert _judge([_row(t, "T", _FRI, stamp)]) == [], stamp
    [n] = _judge([_row(t, "T", _FRI, _et(10, 12, 6, 0))])  # Monday 06:00
    assert n.edgar_open == _et(10, 12, 6, 0)


def test_a_reconstructed_row_is_its_own_marker_never_late_and_never_on_time():
    """A reconstructed-only night is NOT late-only (a backfill row is never judged late). And a
    reconstruction beside a late row does not stand in for an on-time one — the night's post-close record
    is still only late, and the Scoreboard sets both rows aside (a gap there)."""
    t = uuid.uuid4()
    recon = _row(t, "T", _MON, _et(10, 14, 9, 0), reconstructed=True)
    assert _judge([recon]) == []
    [n] = _judge([recon, _row(t, "T", _MON, _et(10, 13, 17, 4))])
    assert n.earlier_recorded_at is None
    # a reconstruction stamped INSIDE the window is still not an on-time row
    inside = _row(t, "T", _MON, _et(10, 12, 23, 0), reconstructed=True)
    assert len(_judge([inside, _row(t, "T", _MON, _et(10, 13, 17, 4))])) == 1


def test_a_daytime_row_beside_a_late_one_is_still_late_only_and_names_what_the_scoreboard_scores():
    """A pre-RUN_AT row (a 09:15 "Run daily now", on the prior session's bars) is neither late nor on time:
    the night's post-close record exists only late, so it is listed — and ``earlier_recorded_at`` names the
    daytime row the Scoreboard scores instead of the gap. Two late rows count once (the FIRST stamp).
    """
    t = uuid.uuid4()
    daytime = _et(10, 12, 9, 15)
    [n] = _judge(
        [
            _row(t, "T", _MON, daytime),
            _row(t, "T", _MON, _et(10, 13, 17, 4)),
            _row(t, "T", _MON, _et(10, 13, 17, 30)),
        ]
    )
    assert (n.earlier_recorded_at, n.n_rows, n.recorded_at) == (daytime, 2, _et(10, 13, 17, 4))


def test_the_read_lists_the_nights_asked_for_and_writes_nothing(db, security_id):
    """Real rows at real stamps: a late-only Monday, an on-time Tuesday, and a late-only Friday OUTSIDE the
    asked nights. The read names only the asked late-only night; the table is counted before and after.
    """
    thesis = persist_thesis(db, security_id)
    conv, _ = keys_fired(security_id, date(2026, 10, 8), conv_liveness=60, conf_liveness=60)
    record_day(db, thesis, [conv], _FRI, recorded_at=_et(10, 12, 7, 0))  # late-only, not asked
    record_day(db, thesis, [conv], _MON, recorded_at=_et(10, 13, 17, 4))  # late-only
    record_day(db, thesis, [conv], _TUE)  # on time

    def count() -> int:
        with db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM calls")
            return cur.fetchone()["n"]

    before = count()
    out = late_only_nights(db, asofs=[_MON, _TUE], run_at=_RUN_AT, tz=_NY)
    assert [(n.thesis_id, n.thesis_name, n.asof) for n in out] == [(thesis.id, thesis.name, _MON)]
    assert late_only_nights(db, asofs=[_FRI], run_at=_RUN_AT, tz=_NY)[0].asof == _FRI
    assert late_only_nights(db, asofs=[], run_at=_RUN_AT, tz=_NY) == []
    assert count() == before == 3
