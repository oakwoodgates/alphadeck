"""MISSING CLOSES on the Admin surface — ``/admin/status``'s ``missing_closes`` block, read from run artifacts
written through the REAL writer (``write_cron_run_log``) into the conftest-redirected tmp home.

The loudness rule (operator decision 2026-10-23): a QUIET line every night, LOUD only for an older session's
close still missing after a later session's pass — and never the cron verdict, never a run-row problem.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from pipeline import daily
from pipeline.cron_run_log import write_cron_run_log
from pipeline.ingest_thesis import NameResult
from pipeline.missing_closes import Facts, MissingCloses, candidates_of, judge, open_entries

WED, THU = date(2026, 10, 21), date(2026, 10, 22)


def _at(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 22, 30, tzinfo=timezone.utc) + timedelta(hours=4)


def _artifact(d: date, mc: MissingCloses, *, at: datetime | None = None):
    at = at or _at(d)
    path = write_cron_run_log(
        [daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True, edgar_fetches=88)],
        asof=d,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        missing_closes=mc,
    )
    assert path is not None


def _judge(session, walks, *, previous=(), stored=None, exchanges=None):
    theses = [daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", ingested=list(walks))]
    cands, excluded, stale = candidates_of(theses)
    return judge(
        session=session,
        candidates=cands,
        excluded=excluded,
        stale_ids=stale,
        previous=list(previous),
        history=[],
        facts=Facts(benchmarks=("SPY",), stored=stored or {}, exchanges=exchanges or {}),
        now=_at(session),
    )


def test_missing_closes_is_NULL_until_a_live_pass_has_run_the_check(client):
    assert client.get("/admin/status").json()["missing_closes"] is None


def test_the_QUIET_nightly_line_with_its_companies_and_the_OTC_known_issue(client):
    pink, bbb, aaa = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mc = _judge(
        WED,
        [
            NameResult("PINK", pink, 0, 0, tape_edge=date(2026, 10, 20)),
            NameResult("BBB", bbb, 0, 0, tape_edge=date(2026, 10, 20)),
            NameResult("AAA", aaa, 0, 1, tape_edge=WED),
        ],
        exchanges={pink: "OTC"},
    )
    _artifact(WED, mc)

    body = client.get("/admin/status").json()
    got = body["missing_closes"]
    assert got["line"] == (
        "Closing prices · 2 of 3 companies had no close for Oct 21 at the night's run (1 OTC, a known "
        "issue) · usually arrive with the next night's pull"
    )
    assert got["loud_lines"] == [] and got["still_missing"] == []
    assert {(m["ticker"], m["otc"], m["reason"]) for m in got["missing"]} == {
        ("PINK", True, "no_bar"),
        ("BBB", False, "no_bar"),
    }
    assert got["session"] == WED.isoformat() and got["is_session"] is True and got["judged"] == 3
    # never the cron verdict, never a run-row problem (pages: none)
    assert "close" not in body["cron"]["detail"].lower()
    assert all("close" not in p.lower() for p in body["last_run"]["problems"])


def test_a_close_STILL_missing_after_the_next_pass_is_LOUD_and_an_arrival_is_quiet(client):
    bbb, ccc = uuid.uuid4(), uuid.uuid4()
    wed = _judge(
        WED,
        [
            NameResult("BBB", bbb, 0, 0, tape_edge=date(2026, 10, 20)),
            NameResult("CCC", ccc, 0, 0, tape_edge=date(2026, 10, 20)),
        ],
    )
    _artifact(WED, wed)
    thu = _judge(
        THU,
        [
            NameResult("BBB", bbb, 0, 0, tape_edge=date(2026, 10, 20)),
            NameResult("CCC", ccc, 0, 2, tape_edge=THU),
        ],
        previous=open_entries(wed.payload()["missing_closes"]),
        stored={(ccc, WED): _at(THU) + timedelta(minutes=6)},
    )
    _artifact(THU, thu)

    got = client.get("/admin/status").json()["missing_closes"]
    assert got["loud_lines"] == ["1 company still has no close for Oct 21: BBB"]
    (loud,) = got["still_missing"]
    assert (loud["ticker"], loud["session"], loud["loud"]) == ("BBB", WED.isoformat(), True)
    assert [(a["ticker"], a["session"], a["lag_h"]) for a in got["arrived"]] == [
        ("CCC", WED.isoformat(), 24.1)
    ]
    assert got["asof"] == THU.isoformat()


def test_a_FAILED_check_shows_its_error_over_the_last_good_flag(client):
    bbb = uuid.uuid4()
    good = _judge(WED, [NameResult("BBB", bbb, 0, 0, tape_edge=date(2026, 10, 20))])
    _artifact(WED, good)
    _artifact(THU, MissingCloses(session=THU, error="OperationalError: db down"))

    got = client.get("/admin/status").json()["missing_closes"]
    assert got["error"] == "OperationalError: db down"
    assert got["asof"] == WED.isoformat()  # the rows stay the last evaluated pass's
    assert [m["ticker"] for m in got["missing"]] == ["BBB"]


def test_a_check_that_FAILED_with_no_good_pass_before_it_says_so(client):
    _artifact(THU, MissingCloses(session=THU, error="OperationalError: db down"))
    got = client.get("/admin/status").json()["missing_closes"]
    assert got["error"] == "OperationalError: db down"
    assert got["line"].startswith("Closing prices · not checked")
