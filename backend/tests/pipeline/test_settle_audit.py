"""The settle guard, AUDITED — ``self_audit.check_no_unsettled_bars`` + its judging window.

No fitting: every bar the check reads is written by the REAL append writers (``eod_loader.ingest_prices``
with the stamp the writer of record used, and ``ingest_prices_backfill`` — the pre-D1 benchmark leg's
writer), and the stamps are prod's own (MEASURED read-only on prod 2026-10-21):

- CDRE's 2026-10-13 bar: first stored 2026-10-13 21:08:14.980352Z (17:08 ET, by the next day's 16:57 boot
  catch-up of 10-12 — before the 18:00 settle) with volume 627,717, then re-versioned by the 22:30 pass at
  2026-10-14 02:35:28.965696Z with 627,700. The same 17:08 pull stored the 10-12 bar (a PRIOR session —
  settled) and the 10-14 bar landed at 22:43 ET the next night.
- SPY's 2026-08-25 bar: the pre-D1 benchmark leg stored the market-hours value (close 765.27, volume
  13,587,691) stamped ``d 00:00 UTC`` — before its own session opened — and the Q3 repair appended the final
  (765.91, 27,422,300) at 2026-10-17 03:25:34.195120Z.

The DST tests use the real 2026 transitions (EDT began Sun 2026-03-08, ended Sun 2026-11-01): the check
builds the settle instant in MARKET time, so 18:00 is 22:00Z the Monday after spring-forward and 23:00Z the
Monday after fall-back.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from db.session import DEFAULT_TENANT_ID, connect
from ingest.prices.eod_loader import ingest_prices, ingest_prices_backfill
from pipeline.cron_run_log import (
    previous_settle_audit_end,
    write_cron_run_log,
    write_run_start,
)
from pipeline.self_audit import (
    COULD_NOT_RUN,
    DEFAULT_CHECKS,
    SETTLE_CHECK,
    SETTLE_FALLBACK_LOOKBACK,
    SETTLE_GUARD_DEPLOYED_AT,
    SETTLE_WINDOW_OVERLAP,
    AuditContext,
    AuditResult,
    check_code_sha_known,
    check_no_unsettled_bars,
    run_audit,
    settle_window_since,
)
from securities.benchmarks import seed_benchmarks

_NY = ZoneInfo("America/New_York")
_SETTLE = time(18, 0)  # the prod `Settings.price_settle_time`


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0001234567", "2020-01-01"),
        )
    db.commit()
    return sid


def _bar(
    db, sid, d: date, close, volume, recorded_at: datetime, *, o=None, h=None, lo=None
) -> None:
    """One bar version through the REAL append writer, stamped as its writer of record stamped it."""
    row = {"d": d, "open": o, "high": h, "low": lo, "close": Decimal(close), "volume": volume}
    ingest_prices(db, sid, [row], recorded_at=recorded_at)
    db.commit()


def _ctx(*, since: datetime | None, settle_at: time | None = _SETTLE, connect_=connect):
    now = _utc(2026, 10, 22, 2, 45)
    return AuditContext(
        asof=date(2026, 10, 21),
        started_at=now,
        allow_live=True,
        run_kind="cron",
        code_sha="abc1234",
        results=[],
        run_at=time(22, 30),
        tz=_NY,
        connect=connect_,
        settle_at=settle_at,
        bars_since=since,
    )


def _cdre(db) -> uuid.UUID:
    """CDRE's tape around 2026-10-13 exactly as prod stores it (every version, prod's own stamps)."""
    sid = _security(db, "CDRE")
    pull_1708 = _utc(2026, 10, 13, 21, 8, 14, 980352)  # 17:08:14 ET — the boot catch-up's pull
    _bar(db, sid, date(2026, 10, 12), "25.8400001525879", 837600, pull_1708)
    _bar(db, sid, date(2026, 10, 13), "26.1499996185303", 627717, pull_1708)  # in progress
    _bar(
        db,
        sid,
        date(2026, 10, 13),
        "26.1499996185303",
        627700,
        _utc(2026, 10, 14, 2, 35, 28, 965696),
    )
    _bar(
        db,
        sid,
        date(2026, 10, 14),
        "25.7700004577637",
        431500,
        _utc(2026, 10, 15, 2, 43, 20, 261287),
    )
    return sid


# --- the check, on the real CDRE shape ---------------------------------------------------------------


def test_CDRE_2026_10_13_stored_at_17_08_ET_FAILS_naming_the_bar(db):
    _cdre(db)
    res = check_no_unsettled_bars(_ctx(since=datetime(2026, 10, 13, tzinfo=_NY)))
    assert res.check == SETTLE_CHECK and res.ok is False
    # exactly ONE version breaks the rule: the 17:08 pull of 10-13's own bar. The 10-12 bar from the SAME
    # pull is a prior session (settled), the 22:35 re-version and the 10-14 bar landed after 18:00.
    assert res.detail.startswith("1 EOD bar version(s) stored BEFORE their settle instant")
    assert "2026-10-13 x1" in res.detail and "CDRE 2026-10-13 @ 17:08 EDT" in res.detail
    assert "2026-10-12" not in res.detail.split("by bar date:")[1]


def test_a_SETTLED_tape_PASSES_and_reports_what_it_judged(db):
    """CDRE's settled versions only (the 17:08 in-progress pull left out): a prior session stored at 17:08
    on the next day is settled, the 22:35 re-version is past 18:00 — the check passes and says how much it
    read."""
    sid = _security(db, "CDRE")
    _bar(db, sid, date(2026, 10, 12), "25.84", 837600, _utc(2026, 10, 13, 21, 8, 14, 980352))
    _bar(db, sid, date(2026, 10, 13), "26.15", 627700, _utc(2026, 10, 14, 2, 35, 28, 965696))
    _bar(db, sid, date(2026, 10, 14), "25.77", 431500, _utc(2026, 10, 15, 2, 43, 20, 261287))
    res = check_no_unsettled_bars(_ctx(since=datetime(2026, 10, 13, tzinfo=_NY)))
    assert res.ok is True, res.detail
    assert res.detail.startswith("3 bar version(s) dated 2026-10-12 or later")
    assert "none stored before its settle instant" in res.detail


def test_a_bar_recorded_EXACTLY_at_its_settle_instant_is_settled(db):
    """The guard's comparison is ``now >= settle`` (``settle.is_settled``), so the audit's is ``<``."""
    sid = _security(db, "EDGE")
    _bar(db, sid, date(2026, 10, 20), "10", 1, datetime(2026, 10, 20, 18, 0, tzinfo=_NY))
    assert check_no_unsettled_bars(_ctx(since=datetime(2026, 10, 20, tzinfo=_NY))).ok is True
    _bar(db, sid, date(2026, 10, 21), "10", 1, datetime(2026, 10, 21, 17, 59, 59, tzinfo=_NY))
    assert check_no_unsettled_bars(_ctx(since=datetime(2026, 10, 20, tzinfo=_NY))).ok is False


# --- DST: the settle instant is 18:00 in MARKET time on both sides of a change -----------------------


def test_FALL_BACK_18_00_EST_is_23_00Z_a_22_30Z_pull_is_still_in_progress(db):
    """Mon 2026-11-02, the first session after EDT ended: 22:30Z is 17:30 EST — before the settle. (A
    fixed EDT offset would read it as 18:30 and pass it.)"""
    sid = _security(db, "FALL")
    _bar(db, sid, date(2026, 11, 2), "10", 1, _utc(2026, 11, 2, 22, 30))
    res = check_no_unsettled_bars(_ctx(since=_utc(2026, 11, 2)))
    assert res.ok is False and "FALL 2026-11-02 @ 17:30 EST" in res.detail
    _bar(db, sid, date(2026, 11, 2), "10", 2, _utc(2026, 11, 2, 23, 0))  # 18:00 EST exactly
    res = check_no_unsettled_bars(_ctx(since=_utc(2026, 11, 2, 22, 45)))
    assert res.ok is True, res.detail


def test_SPRING_FORWARD_18_00_EDT_is_22_00Z_a_22_30Z_pull_is_settled(db):
    """Mon 2026-03-09, the first session after EDT began: 22:30Z is 18:30 EDT — settled. (A fixed EST
    offset would read it as 17:30 and fail it.) 21:30Z is 17:30 EDT — in progress."""
    sid = _security(db, "SPRG")
    _bar(db, sid, date(2026, 3, 9), "10", 1, _utc(2026, 3, 9, 22, 30))
    assert check_no_unsettled_bars(_ctx(since=_utc(2026, 3, 9))).ok is True
    _bar(db, sid, date(2026, 3, 10), "10", 1, _utc(2026, 3, 10, 21, 30))
    res = check_no_unsettled_bars(_ctx(since=_utc(2026, 3, 9)))
    assert res.ok is False and "SPRG 2026-03-10 @ 17:30 EDT" in res.detail


# --- benchmarks are judged too --------------------------------------------------------------------------


def test_the_BENCHMARK_tape_is_judged_SPY_2026_08_25_backdated_market_hours_bar_FAILS(db):
    """The pre-D1 leg wrote SPY's 08-25 market-hours bar through ``ingest_prices_backfill`` (its stamp:
    ``d 00:00 UTC``, before the session opened); the Q3 repair appended the final. The first claims a close
    known before it settled — exactly what the guard forbids — and the repair's version is settled.
    """
    spy = seed_benchmarks(db)["SPY"]
    db.commit()
    row = {
        "d": date(2026, 8, 25),
        "open": Decimal("766.159973144531"),
        "high": Decimal("766.780029296875"),
        "low": Decimal("763.050109863281"),
        "close": Decimal("765.27001953125"),
        "volume": 13587691,
    }
    ingest_prices_backfill(db, spy, [row])
    db.commit()
    _bar(
        db,
        spy,
        date(2026, 8, 25),
        "765.909973144531",
        27422300,
        _utc(2026, 10, 17, 3, 25, 34, 195120),
    )

    res = check_no_unsettled_bars(_ctx(since=_utc(2026, 8, 24)))
    assert res.ok is False
    # stamped the evening BEFORE its session: the label carries the date so it cannot read as 08-25 20:00
    assert res.detail.startswith("1 EOD bar version(s)")
    assert "SPY 2026-08-25 @ 2026-08-24 20:00 EDT" in res.detail


def test_a_settled_BENCHMARK_bar_PASSES(db):
    """SPY 2026-10-20 as the recurring leg now stamps it (D1: ``now`` from the DB clock, 22:30 ET)."""
    spy = seed_benchmarks(db)["SPY"]
    db.commit()
    _bar(
        db,
        spy,
        date(2026, 10, 20),
        "769.049987792969",
        34904800,
        _utc(2026, 10, 21, 2, 30, 3, 261020),
    )
    res = check_no_unsettled_bars(_ctx(since=_utc(2026, 10, 20, 3)))
    assert res.ok is True and res.detail.startswith("1 bar version(s)")


# --- it never raises into the pass -----------------------------------------------------------------------


def test_the_check_NEVER_raises_into_the_pass_a_broken_DB_is_a_FAILED_check_and_the_rest_run():
    def _dead():
        raise ConnectionError("database unreachable")

    out = run_audit(
        _ctx(since=_utc(2026, 10, 20), connect_=_dead),
        checks=(check_no_unsettled_bars, check_code_sha_known),
    )
    assert [a.check for a in out] == [SETTLE_CHECK, "code_sha_known"]
    assert out[0].ok is False and out[0].detail.startswith(f"{COULD_NOT_RUN}: ConnectionError")
    assert out[1].ok is True


def test_NOT_APPLICABLE_without_a_settle_time_or_a_window_never_a_pass():
    assert check_no_unsettled_bars(_ctx(since=None)).ok is None
    assert check_no_unsettled_bars(_ctx(since=_utc(2026, 10, 20), settle_at=None)).ok is None


def test_the_settle_check_is_registered_LAST():
    assert DEFAULT_CHECKS[-1] is check_no_unsettled_bars


# --- the window: pre-guard history never pages; each bar is judged by one pass ---------------------------


def test_the_window_is_FLOORED_at_the_guard_deploy_so_pre_guard_history_never_pages(db):
    """The real CDRE violation (10-13, pre-guard) is in the table; the first pass after this check ships
    (no previous judging pass) looks back a week from 10-21 — and the floor keeps it out."""
    _cdre(db)
    started = datetime(2026, 10, 21, 22, 30, tzinfo=_NY)
    since = settle_window_since(started, None)
    assert since == SETTLE_GUARD_DEPLOYED_AT  # a week back (10-14) is before the floor
    res = check_no_unsettled_bars(_ctx(since=since))
    assert res.ok is True, res.detail


def test_window_starts_at_the_previous_judging_pass_less_the_overlap_else_a_week_back():
    started = _utc(2026, 11, 20, 3, 30)
    assert settle_window_since(started, None) == started - SETTLE_FALLBACK_LOOKBACK
    prev = _utc(2026, 11, 19, 3, 10)
    assert settle_window_since(started, prev) == prev - SETTLE_WINDOW_OVERLAP
    # a previous pass from before the guard shipped (a restored old run home) still never reaches past it
    assert settle_window_since(started, _utc(2026, 10, 1)) == SETTLE_GUARD_DEPLOYED_AT


def _finished(tmp_path, started: datetime, audit: list[AuditResult]) -> datetime:
    """A FINISHED pass's artifact through the real writer, stamped in UTC as the pass stamps it."""
    asof = started.astimezone(_NY).date()
    started = started.astimezone(timezone.utc)
    finished = started + timedelta(minutes=12)
    write_cron_run_log(
        [],
        asof=asof,
        allow_live=True,
        started_at=started,
        finished_at=finished,
        base_dir=tmp_path,
        audit=audit,
    )
    return finished


def test_previous_settle_audit_end_reads_the_newest_pass_that_JUDGED_the_table(tmp_path):
    judged_fail = _utc(2026, 10, 22, 2, 30)
    judged_pass = _utc(2026, 10, 23, 2, 30)
    end_fail = _finished(tmp_path, judged_fail, [AuditResult(SETTLE_CHECK, False, "1 EOD bar ...")])
    assert (
        previous_settle_audit_end(base_dir=tmp_path) == end_fail
    )  # a FAILED judgment still judged
    end_pass = _finished(tmp_path, judged_pass, [AuditResult(SETTLE_CHECK, True, "809 bar ...")])
    assert previous_settle_audit_end(base_dir=tmp_path) == end_pass

    # newer passes that did NOT judge are read past: the check raised, was not applicable, was not carried
    _finished(
        tmp_path,
        _utc(2026, 10, 24, 2, 30),
        [AuditResult(SETTLE_CHECK, False, f"{COULD_NOT_RUN}: OperationalError: gone")],
    )
    _finished(tmp_path, _utc(2026, 10, 25, 2, 30), [AuditResult(SETTLE_CHECK, None, "n/a")])
    _finished(tmp_path, _utc(2026, 10, 26, 2, 30), [AuditResult("code_sha_known", True, "x")])
    # and a pass that never finished (M1 — killed mid-loop; its audit never ran)
    write_run_start(
        asof=date(2026, 10, 26),
        allow_live=True,
        started_at=_utc(2026, 10, 27, 2, 30),
        base_dir=tmp_path,
    )
    assert previous_settle_audit_end(base_dir=tmp_path) == end_pass


def test_previous_settle_audit_end_is_None_on_an_empty_or_pre_check_history(tmp_path):
    assert previous_settle_audit_end(base_dir=tmp_path / "absent") is None
    _finished(tmp_path, _utc(2026, 10, 21, 2, 30), [AuditResult("on_time_rows", True, "12 of 12")])
    assert previous_settle_audit_end(base_dir=tmp_path) is None


def test_a_violation_JUDGED_by_the_previous_pass_does_not_page_again(db, tmp_path):
    """Two nights through the real pieces: the bar stored in progress at 17:08 is judged by the night that
    follows it (FAIL — it pages) and by no later night, because the next window starts where that pass
    finished."""
    sid = _security(db, "ONCE")
    _bar(db, sid, date(2026, 10, 28), "10", 1, datetime(2026, 10, 28, 17, 8, tzinfo=_NY))
    night1 = datetime(2026, 10, 28, 22, 30, tzinfo=_NY)
    first = check_no_unsettled_bars(_ctx(since=settle_window_since(night1, None)))
    assert first.ok is False and "ONCE 2026-10-28 @ 17:08 EDT" in first.detail
    _finished(tmp_path, night1, [first])

    night2 = datetime(2026, 10, 29, 22, 30, tzinfo=_NY)
    since = settle_window_since(night2, previous_settle_audit_end(base_dir=tmp_path))
    assert since == night1 + timedelta(minutes=12) - SETTLE_WINDOW_OVERLAP
    assert check_no_unsettled_bars(_ctx(since=since)).ok is True
