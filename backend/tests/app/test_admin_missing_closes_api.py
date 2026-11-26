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
from pipeline.tape_health import StaleTape

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


# --- W6-3a: the Massive.com tape cross-check rides the same pass's block, and marks the stale rows that traded ----


def _crosscheck(session: date):
    """The REAL judge over a REAL grouped day (``tests/fixtures/massive/grouped``): SNDK and SCNI stale (no close
    since their splits), AITX with no close tonight, MARA stored at Massive.com's close."""
    from decimal import Decimal
    from pathlib import Path

    from domain.splits import ListedSplit
    from ingest.massive_bars import parse_grouped
    from pipeline import tape_crosscheck as tc

    text = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "massive"
        / "grouped"
        / f"{session.isoformat()}.json"
    ).read_text("utf-8")
    ids = {t: uuid.uuid4() for t in ("SNDK", "SCNI", "AITX", "MARA")}
    sndk_split = ListedSplit(
        date(2026, 11, 20), date(2026, 11, 20), Decimal(1), Decimal(8), "confirmed"
    )
    out = tc.judge(
        session=session,
        asof=session,
        judged=[tc.Name(ids["AITX"], "AITX"), tc.Name(ids["MARA"], "MARA")],
        stale=[
            tc.Name(ids["SNDK"], "SNDK", edge=date(2026, 11, 19)),
            tc.Name(ids["SCNI"], "SCNI", edge=date(2026, 11, 18)),
        ],
        stored={ids["MARA"]: Decimal("5.42")},
        massive=parse_grouped(text, session=session),
        listed={ids["SNDK"]: [sndk_split]},
        tolerance=0.02,
    )
    return out, ids


def test_the_CROSSCHECK_rides_the_missing_close_flag_and_marks_the_stale_rows_that_traded(client):
    nov25 = date(2026, 11, 25)
    cc, ids = _crosscheck(nov25)
    mc = _judge(nov25, [NameResult("MARA", ids["MARA"], 0, 1, tape_edge=nov25)])
    at = _at(nov25)
    write_cron_run_log(
        [
            daily.ThesisRunResult(
                thesis_id=uuid.uuid4(),
                name="T",
                recorded=True,
                edgar_fetches=88,
                tape_stale=(
                    StaleTape("SNDK", ids["SNDK"], date(2026, 11, 19)),
                    StaleTape("SCNI", ids["SCNI"], date(2026, 11, 18)),
                ),
            )
        ],
        asof=nov25,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        tape_evaluated=True,
        tape_stale_days=5,
        missing_closes=mc,
        tape_crosscheck=cc,
    )
    body = client.get("/admin/status").json()
    got = body["missing_closes"]["crosscheck"]
    assert got["line"] == (
        "Massive.com cross-check · Nov 25: 1 compared, 0 disagree; Massive.com traded 1 we have no close "
        "for: AITX 0.0002"
    )
    assert (got["status"], got["session"], got["judged"], got["compared"]) == (
        "on",
        "2026-11-25",
        2,
        1,
    )
    assert (got["vendor_gap"], got["disagree"], got["no_trade"], got["unmapped"]) == (1, 0, 0, 0)
    assert [(r["ticker"], r["kind"], r["massive"], r["otc"]) for r in got["rows"]] == [
        ("AITX", "vendor_gap", 0.0002, True)
    ]
    assert got["stale_lines"] == [
        "SCNI: no close stored since 2026-11-18, but Massive.com shows it traded tonight (1.69) — a vendor "
        "gap, not a rename",
        "SNDK: no close stored since 2026-11-19, but Massive.com shows it traded tonight (73.61) — a vendor "
        "gap, not a rename; the 8-for-1 forward split of 2026-11-20 is on the list",
    ]
    stale = {r["ticker"]: r for r in body["tape"]["stale"]}
    assert (stale["SNDK"]["massive_traded"], stale["SNDK"]["massive_close"]) == (
        "2026-11-25",
        73.61,
    )
    assert (stale["SCNI"]["massive_traded"], stale["SCNI"]["massive_close"]) == ("2026-11-25", 1.69)
    # never the cron verdict, never a run-row problem (pages: none)
    assert "massive" not in body["cron"]["detail"].lower()
    assert all("massive" not in p.lower() for p in body["last_run"]["problems"])


def test_a_pass_that_never_ran_the_CROSSCHECK_reads_not_run_and_an_OFF_check_says_so(client):
    from pipeline import tape_crosscheck as tc

    bbb = uuid.uuid4()
    _artifact(WED, _judge(WED, [NameResult("BBB", bbb, 0, 0, tape_edge=date(2026, 10, 20))]))
    got = client.get("/admin/status").json()["missing_closes"]
    # the writer always writes the block: a pass that never ran the check reads "not run"
    assert got["crosscheck"]["status"] == "not_run" and got["crosscheck"]["rows"] == []
    at = _at(THU)
    write_cron_run_log(
        [daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True, edgar_fetches=88)],
        asof=THU,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        missing_closes=_judge(THU, [NameResult("BBB", bbb, 0, 1, tape_edge=THU)]),
        tape_crosscheck=tc.TapeCrossCheck(
            session=THU, status=tc.STATUS_OFF, note="no MASSIVE_API_KEY"
        ),
    )
    got = client.get("/admin/status").json()["missing_closes"]["crosscheck"]
    assert got["line"] == "Massive.com cross-check · off (no MASSIVE_API_KEY)"
    assert (got["status"], got["note"]) == ("off", "no MASSIVE_API_KEY")
