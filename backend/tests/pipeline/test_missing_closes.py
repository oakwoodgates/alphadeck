"""MISSING CLOSES (``pipeline/missing_closes.py``, operator decision 2026-10-23) — "an admin flag should fire if
no closing price was found for a company on a day".

The judgment is pure, so most of these drive it pass by pass the way the nightly does: each pass's block is
written into a run-artifact-shaped doc, and the NEXT pass reads the previous one back through the same readers
(``evaluated_blocks`` / ``open_entries``). The DB tests prove the facts read (the benchmark session, the exact
stored bar with its first ``recorded_at``, the active basket union, the master venue), that the check writes
nothing, and the wiring end to end through ``run_daily_pass`` into the artifact.

Every company here is synthetic (AAA / BBB / ...): the rules are proved on constructed inputs whose expected
outcome follows from the rule itself, never on a fixture fitted to a number."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from db.session import DEFAULT_TENANT_ID, connect
from pipeline import cron_run_log, daily
from pipeline.cron_run_log import build_run_payload, list_run_logs, write_cron_run_log
from pipeline.ingest_thesis import NameResult
from pipeline.missing_closes import (
    REASON_FETCH_ERROR,
    REASON_NO_BAR,
    REASON_NOT_INGESTED,
    RESOLVED_LEFT,
    RESOLVED_STALE,
    USUAL_LATE_MIN_NIGHTS,
    Facts,
    MissingCloses,
    candidates_of,
    evaluated_blocks,
    judge,
    loud_lines,
    open_entries,
    quiet_line,
    report_lines,
    run_missing_closes,
)
from pipeline.tape_health import StaleTape
from securities.benchmarks import seed_benchmarks
from tests.pipeline.catchup_pass_stubs import next_second

# A synthetic week: Mon 2026-10-19 .. Fri 10-23 (Wed = the session a close goes missing on)
MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in (19, 20, 21, 22, 23))
NEXT_MON = date(2026, 10, 26)


def _night(d: date) -> datetime:
    """The nightly pass's start for session ``d`` — 22:30 ET (02:30 UTC the next day, EDT)."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(hours=26, minutes=30)


class Co:
    """A synthetic basket company: a ticker and the security id every rule keys on."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.sid = uuid.uuid4()

    def walk(self, edge: date | None, **kw) -> NameResult:
        """Its per-name result after tonight's price leg, with its tape edge."""
        return NameResult(self.ticker, self.sid, 0, 0, tape_edge=edge, **kw)


def _thesis(*names: NameResult, stale: tuple[StaleTape, ...] = ()) -> daily.ThesisRunResult:
    return daily.ThesisRunResult(
        thesis_id=uuid.uuid4(), name="T", ingested=list(names), tape_stale=stale
    )


def _doc(mc: MissingCloses, d: date, at: datetime) -> dict:
    """The pass's run artifact as the next pass reads it (only the keys the readers use)."""
    return {
        "status": "finished",
        "mode": "live",
        "asof": d.isoformat(),
        "started_at": at.isoformat(),
        **mc.payload(),
    }


def _pass(
    theses,
    *,
    session: date,
    docs: list[dict] = (),
    bench=("IWM", "SPY"),
    stored=None,
    members=None,
    exchanges=None,
    at: datetime | None = None,
) -> MissingCloses:
    """One pass's judgment: the facts off ``theses`` + the previous artifacts ``docs`` (newest-first) + the
    DB facts, judged exactly as ``run_missing_closes`` does."""
    candidates, excluded, stale = candidates_of(theses)
    blocks = evaluated_blocks(docs)
    return judge(
        session=session,
        candidates=candidates,
        excluded=excluded,
        stale_ids=stale,
        previous=open_entries(blocks[0] if blocks else None),
        history=blocks,
        facts=Facts(
            benchmarks=tuple(bench),
            stored=stored or {},
            members=members,
            exchanges=exchanges or {},
        ),
        now=at or _night(session),
    )


def _run(nights, *, docs=None):
    """Drive several passes in order: each ``(session, theses, kwargs)`` reads every earlier pass's artifact.
    Returns ``(the passes' results, the artifacts newest-first)``."""
    docs = list(docs or [])
    out = []
    for session, theses, kw in nights:
        at = kw.pop("at", None) or _night(session)
        mc = _pass(theses, session=session, docs=docs, at=at, **kw)
        docs.insert(0, _doc(mc, session, at))
        out.append(mc)
    return out, docs


# --- (1) the session's missing set --------------------------------------------------------------------------


def test_a_SESSION_with_a_missing_company_LISTS_it_with_its_reason():
    aaa, bbb, ccc, ddd = Co("AAA"), Co("BBB"), Co("CCC"), Co("DDD")
    mc = _pass(
        [
            _thesis(
                aaa.walk(WED),  # tonight's close stored
                bbb.walk(TUE),  # the vendor had no close for Wed yet (the leg ran clean)
                ccc.walk(TUE, leg_errors=(("price", "HTTPStatusError: 503 Service Unavailable"),)),
            ),
            # BBB held by a second thesis (one company, one row); DDD's company another writer held
            _thesis(bbb.walk(TUE), ddd.walk(TUE, lock_busy="held by workbench")),
        ],
        session=WED,
    )
    assert mc.evaluated and mc.is_session and mc.judged == 4
    rows = {m.ticker: m for m in mc.missing}
    assert set(rows) == {"BBB", "CCC", "DDD"}  # AAA had its close
    assert rows["BBB"].reason == REASON_NO_BAR and rows["BBB"].edge == TUE
    assert rows["CCC"].reason == REASON_FETCH_ERROR and "503" in rows["CCC"].error
    assert rows["DDD"].reason == REASON_NOT_INGESTED
    assert all(
        m.session == WED and m.first_seen_at == _night(WED) and not m.loud for m in mc.missing
    )
    assert mc.still_missing == () and loud_lines(mc) == []  # tonight's misses are QUIET
    assert quiet_line(mc) == (
        "Closing prices · 3 of 4 companies had no close for Oct 21 at the night's run · usually arrive "
        "with the next night's pull"
    )


def test_a_clean_session_still_gets_its_quiet_line():
    mc = _pass([_thesis(Co("AAA").walk(WED), Co("BBB").walk(WED))], session=WED)
    assert mc.missing == () and mc.judged == 2
    assert (
        quiet_line(mc)
        == "Closing prices · all 2 companies had a close for Oct 21 at the night's run"
    )


def test_a_HOLIDAY_no_benchmark_close_flags_NOTHING():
    """Neither SPY nor IWM has a bar for the session (a market holiday, or a pass before it settled):
    nothing is judged, however many companies lack a bar for it."""
    mc = _pass([_thesis(Co("AAA").walk(WED), Co("BBB").walk(WED))], session=THU, bench=())
    assert mc.evaluated and not mc.is_session
    assert mc.missing == () and mc.judged == 0 and loud_lines(mc) == []
    assert quiet_line(mc).startswith("Closing prices · not judged for Oct 22 — neither SPY nor IWM")


def test_a_STALE_inventory_company_is_NOT_double_counted():
    """A company the stopped-feed flag owns (any classification) is that flag's, never this one's too."""
    dead, closed, live = Co("DEAD"), Co("GONE"), Co("BBB")
    mc = _pass(
        [
            _thesis(
                dead.walk(date(2026, 10, 1)),
                closed.walk(date(2026, 9, 30)),
                live.walk(TUE),
                stale=(
                    StaleTape("DEAD", dead.sid, date(2026, 10, 1)),
                    StaleTape("GONE", closed.sid, date(2026, 9, 30), closed_at=date(2026, 10, 2)),
                ),
            )
        ],
        session=WED,
    )
    assert [m.ticker for m in mc.missing] == ["BBB"]
    assert mc.judged == 1 and mc.excluded["stale"] == 2


def test_an_ONBOARDING_company_is_SKIPPED():
    """X1 — a company whose history is still loading is not judged: windowed or deferred past the cap."""
    windowed, deferred, live = Co("NEW1"), Co("NEW2"), Co("BBB")
    mc = _pass(
        [
            _thesis(
                windowed.walk(TUE, onboarding="in_progress"),
                deferred.walk(None, onboarding="queued", onboarding_deferred=True),
                live.walk(TUE),
            )
        ],
        session=WED,
    )
    assert [m.ticker for m in mc.missing] == ["BBB"]
    assert mc.excluded["onboarding"] == 2 and mc.judged == 1


# --- (2) recovery: the next pass re-checks the previous pass's set against the stored bars ----------------


def test_the_next_pass_with_the_bar_ARRIVED_records_late_arrived_QUIETLY_with_the_lag():
    bbb = Co("BBB")
    arrived_at = _night(THU) + timedelta(minutes=12)  # tonight's price leg stored Wednesday's bar
    (wed, thu), _ = _run(
        [
            (WED, [_thesis(bbb.walk(TUE))], {}),
            (THU, [_thesis(bbb.walk(THU))], {"stored": {(bbb.sid, WED): arrived_at}}),
        ]
    )
    assert [m.ticker for m in wed.missing] == ["BBB"]
    assert thu.missing == () and thu.still_missing == () and loud_lines(thu) == []  # nothing loud
    (a,) = thu.arrived
    assert (a.ticker, a.session, a.arrived_at) == ("BBB", WED, arrived_at)
    assert a.lag_h == 24.2  # from the pass that found it missing to the bar's first recorded_at
    assert any(
        line.startswith("late closes arrived: 1 (median 24.2 h") for line in report_lines(thu)
    )


def test_the_next_pass_STILL_missing_is_LOUD():
    bbb, ccc = Co("BBB"), Co("CCC")
    (_, thu), _ = _run(
        [
            (WED, [_thesis(bbb.walk(TUE), ccc.walk(TUE))], {}),
            # Thursday's pull: CCC's Wednesday close arrived, BBB's did not (its tape still ends Tuesday)
            (
                THU,
                [_thesis(bbb.walk(TUE), ccc.walk(THU))],
                {"stored": {(ccc.sid, WED): _night(THU)}},
            ),
        ]
    )
    (still,) = thu.still_missing
    assert (still.ticker, still.session, still.loud, still.checks) == ("BBB", WED, True, 1)
    assert still.first_seen_at == _night(WED)  # the lag's origin is kept across passes
    assert loud_lines(thu) == ["1 company still has no close for Oct 21: BBB"]
    assert [m.ticker for m in thu.missing] == [
        "BBB"
    ]  # ...and Thursday's own close is missing too (quiet)
    assert [a.ticker for a in thu.arrived] == ["CCC"]
    assert "MISSING CLOSES — 1 company still has no close for Oct 21: BBB" in report_lines(thu)


def test_loud_PERSISTS_until_the_bar_arrives_and_a_holiday_never_makes_one_loud():
    """Wed's close missing -> Thu is a holiday (no benchmark close): still missing but NOT loud (no later
    session's pull has re-checked it) -> Fri's pass: LOUD -> Mon's pass: Friday's bar arrived but Wednesday's
    did not (a hole), still LOUD -> Tue's pass: Wednesday's arrived, gone from the loud flag (recorded as late,
    arrived)."""
    bbb = Co("BBB")
    tue2 = date(2026, 10, 27)
    nights = [
        (WED, [_thesis(bbb.walk(TUE))], {}),
        (THU, [_thesis(bbb.walk(TUE))], {"bench": ()}),
        (FRI, [_thesis(bbb.walk(TUE))], {}),
        (NEXT_MON, [_thesis(bbb.walk(FRI))], {"stored": {(bbb.sid, FRI): _night(NEXT_MON)}}),
        (
            tue2,
            [_thesis(bbb.walk(tue2))],
            {
                "stored": {
                    (bbb.sid, WED): _night(tue2),
                    (bbb.sid, NEXT_MON): _night(tue2),
                }
            },
        ),
    ]
    (wed, thu, fri, mon, tue), _ = _run(nights)
    assert loud_lines(wed) == []
    assert [(m.session, m.loud) for m in thu.still_missing] == [(WED, False)]  # the holiday: quiet
    assert loud_lines(thu) == [] and thu.missing == ()
    assert [(m.session, m.loud) for m in fri.still_missing] == [(WED, True)]
    assert loud_lines(fri) == ["1 company still has no close for Oct 21: BBB"]
    assert [(m.session, m.loud) for m in mon.still_missing] == [(WED, True)]  # it persists
    assert loud_lines(mon) == ["1 company still has no close for Oct 21: BBB"]
    assert [(a.session, a.ticker) for a in mon.arrived] == [(FRI, "BBB")]
    # ... and the bar arrives: Wednesday's entry leaves the loud flag, recorded quietly with its lag
    assert tue.still_missing == () and loud_lines(tue) == [] and tue.missing == ()
    assert [(a.session, a.lag_h) for a in tue.arrived] == [(WED, 144.0), (NEXT_MON, 24.0)]


def test_a_TAPE_GONE_STALE_hands_the_company_to_the_stale_flag():
    """It persists until the bar arrives OR the tape goes stale — then the stopped-feed flag takes over and
    this one lets go (recorded as resolved, never double-flagged)."""
    bbb = Co("BBB")
    (_, fri, mon), _ = _run(
        [
            (THU, [_thesis(bbb.walk(WED))], {}),
            (FRI, [_thesis(bbb.walk(WED))], {}),
            # Monday: the tape's edge (Wed) is 5 days old — the stale monitor's row for it
            (
                NEXT_MON,
                [_thesis(bbb.walk(WED), stale=(StaleTape("BBB", bbb.sid, WED),))],
                {},
            ),
        ]
    )
    assert loud_lines(fri) == ["1 company still has no close for Oct 22: BBB"]
    assert mon.still_missing == () and mon.missing == () and loud_lines(mon) == []
    assert {(r.ticker, r.session, r.why) for r in mon.resolved} == {
        ("BBB", THU, RESOLVED_STALE),
        ("BBB", FRI, RESOLVED_STALE),
    }
    assert mon.excluded["stale"] == 1


def test_a_company_that_LEFT_every_basket_is_dropped_quietly():
    bbb = Co("BBB")
    (_, thu), _ = _run(
        [
            (WED, [_thesis(bbb.walk(TUE))], {}),
            (THU, [_thesis()], {"members": set()}),  # no active basket holds BBB now
        ]
    )
    assert thu.still_missing == () and [r.why for r in thu.resolved] == [RESOLVED_LEFT]


def test_a_same_session_re_run_keeps_the_first_pass_instant_and_never_goes_loud():
    """An Admin "Run daily now" after the nightly, for the same session: the entry is the same close, still
    quiet, and its lag origin stays the nightly's start."""
    bbb = Co("BBB")
    later = _night(WED) + timedelta(hours=1)
    (_, rerun), _ = _run(
        [
            (WED, [_thesis(bbb.walk(TUE))], {}),
            (WED, [_thesis(bbb.walk(TUE))], {"at": later}),
        ]
    )
    (m,) = rerun.missing
    assert m.first_seen_at == _night(WED) and m.checks == 1 and not m.loud
    assert rerun.still_missing == ()


# --- (3) the usual-late marker + the OTC known issue --------------------------------------------------------


def test_the_USUAL_LATE_marker_is_late_on_MOST_of_its_recent_nights():
    """Six sessions: CHRONIC is late on 5 of them (more than half), RARE on 2 (not), FRESH only tonight."""
    chronic, rare, fresh, ok = Co("CHRONIC"), Co("RARE"), Co("FRESH"), Co("OK")
    sessions = [date(2026, 10, d) for d in (15, 16, 19, 20, 21, 22)]
    late = {
        sessions[0]: {chronic},
        sessions[1]: {chronic, rare},
        sessions[2]: set(),
        sessions[3]: {chronic},
        sessions[4]: {chronic},
        sessions[5]: {chronic, rare, fresh},  # tonight
    }
    nights = []
    for i, d in enumerate(sessions):
        walks = [
            c.walk(d - timedelta(days=1) if c in late[d] else d) for c in (chronic, rare, fresh, ok)
        ]
        # every earlier night's late close arrives with the next pull (the common shape)
        prev = sessions[i - 1] if i else None
        stored = {(c.sid, prev): _night(d) for c in late.get(prev, ())} if prev else {}
        nights.append((d, [_thesis(*walks)], {"stored": stored}))
    results, _ = _run(nights)
    assert all(r.still_missing == () for r in results)  # each late close arrived the next night
    tonight = {m.ticker: m for m in results[-1].missing}
    assert USUAL_LATE_MIN_NIGHTS <= 6
    assert (tonight["CHRONIC"].late_nights, tonight["CHRONIC"].judged_nights) == (5, 6)
    assert tonight["CHRONIC"].usual_late is True
    assert (tonight["RARE"].late_nights, tonight["RARE"].usual_late) == (2, False)
    assert (tonight["FRESH"].late_nights, tonight["FRESH"].usual_late) == (1, False)


def test_no_one_is_USUAL_LATE_off_too_short_a_history():
    """Late on every night on record — but fewer nights than the floor: no marker yet."""
    bbb = Co("BBB")
    nights = [(d, [_thesis(bbb.walk(d - timedelta(days=1)))], {}) for d in (MON, TUE, WED)]
    results, _ = _run(nights)
    (m,) = results[-1].missing
    assert (m.late_nights, m.judged_nights) == (3, 3) and m.usual_late is False


def test_an_OTC_company_carries_the_known_issue_marker_and_the_line_counts_them():
    """The operator (2026-10-23): OTC closes arrive a night late — a KNOWN issue, logged, not fixed. The
    marker comes from the master's venue only (no network call)."""
    pink, listed = Co("PINK"), Co("LIST")
    mc = _pass(
        [_thesis(pink.walk(TUE), listed.walk(TUE), Co("AAA").walk(WED))],
        session=WED,
        exchanges={pink.sid: "OTC", listed.sid: "Nasdaq"},
    )
    rows = {m.ticker: m for m in mc.missing}
    assert rows["PINK"].otc is True and rows["LIST"].otc is False
    assert quiet_line(mc) == (
        "Closing prices · 2 of 3 companies had no close for Oct 21 at the night's run (1 OTC, a known "
        "issue) · usually arrive with the next night's pull"
    )


# --- (4) the artifact keys ----------------------------------------------------------------------------------

ARTIFACT_KEYS = {
    "evaluated",
    "error",
    "session",
    "is_session",
    "benchmarks",
    "judged",
    "excluded",
    "missing",
    "still_missing",
    "arrived",
    "resolved",
    "usual_late_window",
    "duration_s",
}
MISSING_ROW_KEYS = {
    "security_id",
    "ticker",
    "session",
    "edge",
    "reason",
    "error",
    "first_seen_at",
    "checks",
    "loud",
    "late_nights",
    "judged_nights",
    "usual_late",
    "otc",
}
ARRIVED_ROW_KEYS = {
    "security_id",
    "ticker",
    "session",
    "first_seen_at",
    "arrived_at",
    "lag_h",
    "checks",
    "otc",
}


def test_the_ARTIFACT_carries_the_named_keys_and_round_trips_as_the_next_baseline(tmp_path):
    bbb, ccc = Co("BBB"), Co("CCC")
    (_, thu), _ = _run(
        [
            (WED, [_thesis(bbb.walk(TUE), ccc.walk(TUE))], {}),
            (
                THU,
                [_thesis(bbb.walk(TUE), ccc.walk(THU))],
                {"stored": {(ccc.sid, WED): _night(THU)}},
            ),
        ]
    )
    at = _night(THU)
    payload = build_run_payload(
        [], asof=THU, allow_live=True, started_at=at, finished_at=at, missing_closes=thu
    )
    block = payload["missing_closes"]
    assert set(block) == ARTIFACT_KEYS
    assert set(block["missing"][0]) == MISSING_ROW_KEYS
    assert set(block["still_missing"][0]) == MISSING_ROW_KEYS
    assert set(block["arrived"][0]) == ARRIVED_ROW_KEYS
    assert block["session"] == THU.isoformat() and block["is_session"] is True
    # an artifact written without the check (an older caller) carries the not-evaluated block
    bare = build_run_payload([], asof=THU, allow_live=True, started_at=at, finished_at=at)
    assert bare["missing_closes"]["evaluated"] is False
    # through the REAL writer and reader: the next pass reads it back as its previous pass
    write_cron_run_log(
        [],
        asof=THU,
        allow_live=True,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
        missing_closes=thu,
    )
    (back,) = evaluated_blocks(list_run_logs(base_dir=tmp_path))
    opened = open_entries(back)
    assert {(e.ticker, e.session, e.loud) for e in opened} == {
        ("BBB", THU, False),
        ("BBB", WED, True),
    }


def test_a_NO_LIVE_pass_never_evaluates_and_is_never_a_baseline():
    out = run_missing_closes(
        results=[],
        asof=WED,
        allow_live=False,
        started_at=_night(WED),
        tenant_id=DEFAULT_TENANT_ID,
        connect=lambda: (_ for _ in ()).throw(AssertionError("no connection on --no-live")),
        payloads=lambda: (_ for _ in ()).throw(AssertionError("no read on --no-live")),
        clock=lambda: 0.0,
    )
    assert out.evaluated is False and out.error is None and report_lines(out) == []
    doc = {"status": "finished", "mode": "no-live", **out.payload()}
    assert evaluated_blocks([doc]) == []


def test_a_FAULT_is_reported_never_raised():
    def _boom():
        raise RuntimeError("db down")

    out = run_missing_closes(
        results=[],
        asof=WED,
        allow_live=True,
        started_at=_night(WED),
        tenant_id=DEFAULT_TENANT_ID,
        connect=_boom,
        payloads=lambda: [],
        clock=lambda: 0.0,
    )
    assert out.evaluated is False and out.error == "RuntimeError: db down"
    assert report_lines(out) == [
        "MISSING-CLOSE CHECK FAILED — RuntimeError: db down (tonight's closes were not checked)"
    ]


# --- (5) the DB facts and the wiring (these EXECUTE against the per-worktree test DB) ------------------------


def _security(db, ticker, *, exchange=None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, exchange, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                sid,
                DEFAULT_TENANT_ID,
                ticker,
                f"{uuid.uuid4().int % 10**10:010d}",
                exchange,
                "2025-01-01",
            ),
        )
    db.commit()
    return sid


def _basket(db, name, sids, *, archived=False):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", datetime.now(timezone.utc) if archived else None),
        )
        for i, (ticker, sid) in enumerate(sids):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _bar(db, sid, d, recorded_at):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_price_eod (tenant_id, security_id, d, close, volume, valid_from, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (DEFAULT_TENANT_ID, sid, d, 10, 1000, d, recorded_at),
        )
    db.commit()


def _count(db, table):
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        n = cur.fetchone()["n"]
    db.commit()
    return n


def test_run_missing_closes_reads_the_DB_facts_READ_ONLY(db):
    """The session from the benchmark bar, the carried pair's exact bar with its FIRST recorded_at (a later
    re-version never moves the arrival), the active basket union, and the master venue — on its own
    read-only connection, writing nothing."""
    spy = seed_benchmarks(db)["SPY"]
    db.commit()
    _bar(db, spy, THU, _night(THU))
    late = _security(db, "LATE", exchange="OTC")  # Wednesday's close arrived with Thursday's pull
    still = _security(db, "STILL")  # Wednesday's close never arrived; a later bar did (a hole)
    gone = _security(db, "GONE")  # only an ARCHIVED thesis holds it now
    tonight = _security(db, "TONIGHT", exchange="OTC")  # no close for Thursday
    first_at = _night(THU) + timedelta(minutes=5)
    _bar(db, late, WED, first_at)
    _bar(db, late, WED, first_at + timedelta(hours=24))  # a vendor restatement: a second version
    _bar(db, late, THU, first_at)
    _bar(db, still, THU, first_at)
    _bar(db, tonight, WED, _night(WED))
    _basket(db, "active", [("LATE", late), ("STILL", still), ("TONIGHT", tonight)])
    _basket(db, "archived", [("GONE", gone)], archived=True)
    wed_doc = {
        "status": "finished",
        "mode": "live",
        "asof": WED.isoformat(),
        "started_at": _night(WED).isoformat(),
        "missing_closes": {
            "evaluated": True,
            "session": WED.isoformat(),
            "is_session": True,
            "missing": [
                {
                    "security_id": str(s),
                    "ticker": t,
                    "session": WED.isoformat(),
                    "edge": TUE.isoformat(),
                    "reason": "no_bar",
                    "first_seen_at": _night(WED).isoformat(),
                }
                for t, s in (("LATE", late), ("STILL", still), ("GONE", gone))
            ],
        },
    }
    results = [
        _thesis(
            NameResult("LATE", late, 0, 1, tape_edge=THU),
            NameResult("STILL", still, 0, 1, tape_edge=THU),
            NameResult("TONIGHT", tonight, 0, 0, tape_edge=WED),
        )
    ]
    opened = []

    def _connect():
        c = connect()
        opened.append(c)
        return c

    before = {t: _count(db, t) for t in ("fact_price_eod", "security_master", "basket_member")}
    out = run_missing_closes(
        results=results,
        asof=THU,
        allow_live=True,
        started_at=_night(THU),
        tenant_id=DEFAULT_TENANT_ID,
        connect=_connect,
        payloads=lambda: [wed_doc],
        clock=lambda: 0.0,
    )
    assert {t: _count(db, t) for t in before} == before  # count the tables: nothing written
    assert opened and opened[0].read_only is True and opened[0].closed
    assert out.evaluated and out.error is None and out.is_session and out.benchmarks == ("SPY",)
    assert [(m.ticker, m.otc) for m in out.missing] == [("TONIGHT", True)]
    (arrived,) = out.arrived
    assert (arrived.ticker, arrived.arrived_at, arrived.otc) == ("LATE", first_at, True)
    assert (
        arrived.lag_h == 24.1
    )  # from its FIRST version (the restatement a day later would read 48.1)
    (still_row,) = out.still_missing
    assert (still_row.ticker, still_row.session, still_row.loud, still_row.edge) == (
        "STILL",
        WED,
        True,
        THU,
    )
    assert [(r.ticker, r.why) for r in out.resolved] == [("GONE", RESOLVED_LEFT)]


def test_the_PASS_carries_the_check_onto_the_artifact_and_NEVER_pages_it(db, monkeypatch, tmp_path):
    """End to end through ``run_daily_pass`` on a real DB: the check runs after the loop on its own connection,
    rides the artifact, and the NEXT pass reads it back — the still-missing close goes LOUD on the artifact
    while no health page ever mentions it (pages: none)."""
    from domain.settings import get_settings

    spy = seed_benchmarks(db)["SPY"]
    db.commit()
    for d in (WED, THU):
        _bar(db, spy, d, _night(d))
    bbb = _security(db, "BBB")
    _bar(db, bbb, TUE, _night(TUE))
    _basket(db, "holds BBB", [("BBB", bbb)])

    roster = {"theses": [_thesis(NameResult("BBB", bbb, 0, 0, tape_edge=TUE))]}
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: list(roster["theses"]))
    monkeypatch.setenv("ALPHADECK_IMAGE_SHA", "mctestsha")
    get_settings.cache_clear()
    for target in (
        "pipeline.ingest_benchmarks.ingest_benchmarks",
        "pipeline.ingest_fundamentals.ingest_fundamentals",
    ):
        monkeypatch.setattr(target, lambda conn, **k: [])
    for target in ("radar.shell_sweep.run_shell_sweep", "radar.spac.run_spac_radar"):
        monkeypatch.setattr(target, lambda conn, **k: SimpleNamespace(summary="s", errors=[]))
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    pages = []
    notifier = SimpleNamespace(
        notify=lambda e: None, notify_health=pages.append, notify_spac_status=lambda e: None
    )
    try:
        first = daily.run_daily_pass(asof=WED, allow_live=True, notifier=notifier, run_kind="cron")
        doc1 = list_run_logs(base_dir=tmp_path)[0]
        next_second()  # an artifact is named by its start second
        second = daily.run_daily_pass(asof=THU, allow_live=True, notifier=notifier, run_kind="cron")
        doc2 = list_run_logs(base_dir=tmp_path)[0]
    finally:
        get_settings.cache_clear()

    assert [m.ticker for m in first.missing_closes.missing] == ["BBB"]
    assert doc1["missing_closes"]["evaluated"] is True
    assert [m["ticker"] for m in doc1["missing_closes"]["missing"]] == ["BBB"]
    assert loud_lines(second.missing_closes) == ["1 company still has no close for Oct 21: BBB"]
    assert loud_lines(doc2["missing_closes"]) == ["1 company still has no close for Oct 21: BBB"]
    assert all("close" not in (p.label or "").lower() for p in pages)  # never paged
