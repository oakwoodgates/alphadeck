"""W6-4 — the nightly unit that puts Massive.com's reference evidence on the open stale price rows
(``pipeline/massive_evidence.py``), over the MEASURED shapes and a fake source (no network).

The open stale rows are prod's of 2026-11-24 (``get_admin_status section=tape``): SNDK (edge 11-19) and SCNI
(11-18) — vendor gaps that traded tonight; INDV (11-02) renamed, delisted on Massive.com 11-03; IXQWF (2025-10-16)
and ACLEW (2025-10-15) OTC warrants; TMRC and MTMTY never priced (404). The overviews are the probe's cached bodies
(``tests/fixtures/massive/reference``; INDV built from the recorded delisted-list row) and tonight's grouped day is
the 2026-11-25 fixture (SNDK 73.61, SCNI 1.69 traded).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingest.massive_bars import parse_grouped
from ingest.massive_reference import MassiveRef, MassiveReferenceError, parse_overview
from pipeline import massive_evidence as me
from pipeline.cron_run_log import list_run_logs, write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.tape_crosscheck import STATUS_ON, GroupedFetch
from pipeline.tape_health import StaleTape

NOV25 = date(2026, 11, 25)
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "massive"


def _grouped() -> GroupedFetch:
    text = (FIX / "grouped" / "2026-11-25.json").read_text(encoding="utf-8")
    return GroupedFetch(NOV25, STATUS_ON, day=parse_grouped(text, session=NOV25))


def _overview(symbol: str):
    path = FIX / "reference" / f"{symbol}.json"
    return (
        parse_overview(path.read_text(encoding="utf-8")) if path.exists() else None
    )  # absent = 404


class _Source:
    """The reference source's seam: answers from the fixtures (absent = 404), records each read."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.asked: list[tuple[str, date, bool]] = []
        self.fail_on = fail_on

    def overview(self, symbol: str, *, day: date, allow_live: bool):
        self.asked.append((symbol, day, allow_live))
        if symbol == self.fail_on:
            raise MassiveReferenceError("Massive.com ticker overview: HTTP 500 for <url>")
        return _overview(symbol)


IDS = {t: uuid.uuid4() for t in ("SNDK", "SCNI", "INDV", "IXQWF", "ACLEW", "TMRC", "MTMTY", "ACA")}


def _rows() -> dict[str, StaleTape]:
    return {
        "SNDK": StaleTape("SNDK", IDS["SNDK"], date(2026, 11, 19), edgar_tickers=("SNDK",)),
        "SCNI": StaleTape("SCNI", IDS["SCNI"], date(2026, 11, 18), edgar_tickers=("SCNI",)),
        "INDV": StaleTape(
            "INDV", IDS["INDV"], date(2026, 11, 2), renamed=True, edgar_tickers=("SUPN",)
        ),
        "IXQWF": StaleTape("IXQWF", IDS["IXQWF"], date(2025, 10, 16)),
        "ACLEW": StaleTape("ACLEW", IDS["ACLEW"], date(2025, 10, 15)),
        "TMRC": StaleTape("TMRC", IDS["TMRC"], None, edgar_tickers=()),
        "MTMTY": StaleTape("MTMTY", IDS["MTMTY"], None, edgar_tickers=("MTMTY", "MTLPF")),
        # a CLOSED row (a delisting form near its last bar): explained by SEC, never read
        "ACA": StaleTape(
            "ACA", IDS["ACA"], date(2026, 10, 22), closed_at=date(2026, 10, 23), edgar_tickers=()
        ),
    }


def _night(rows: dict[str, StaleTape] | None = None, **symbols: str) -> list[ThesisRunResult]:
    """Two theses; SNDK is held by both (one company, one read). ``symbols`` = a vendor price symbol per ticker."""
    rows = rows or _rows()

    def walked(tickers):
        return [
            NameResult(t, IDS[t], 0, 0, tape_edge=rows[t].edge, price_symbol=symbols.get(t))
            for t in tickers
        ]

    first = ("SNDK", "SCNI", "INDV", "ACA")
    second = ("SNDK", "IXQWF", "ACLEW", "TMRC", "MTMTY")
    return [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name=f"Thesis {i}",
            recorded=True,
            ingested=walked(tickers),
            tape_stale=tuple(rows[t] for t in tickers),
        )
        for i, tickers in enumerate((first, second), start=1)
    ]


def _refs(results) -> dict[str, MassiveRef | None]:
    out: dict[str, MassiveRef | None] = {}
    for r in results:
        for s in r.tape_stale:
            assert out.get(s.ticker, s.massive_ref) == s.massive_ref  # one company, one value
            out[s.ticker] = s.massive_ref
    return out


def _run(results, *, source=None, payloads=(), grouped=None, allow_live=True, **kw):
    return me.run_massive_reference(
        results=results,
        asof=NOV25,
        allow_live=allow_live,
        grouped=grouped if grouped is not None else _grouped(),
        payloads=lambda: list(payloads),
        source=source if source is not None else _Source(),
        **kw,
    )


@pytest.fixture
def cap(monkeypatch):
    """``Settings.massive_reference_max_names`` for the test (read by the unit inside its fail-open try)."""

    def set_cap(n):
        monkeypatch.setattr(
            me, "get_settings", lambda: type("S", (), {"massive_reference_max_names": n})()
        )

    set_cap(5)
    return set_cap


# --- the cap and the order ------------------------------------------------------------------------------------


def test_the_first_night_reads_FIVE_newest_stale_first_and_defers_the_rest(cap):
    """Nine open rows' worth of shapes, seven never read: the five with the newest edge are read (one request
    each, the market day as the cache key), the two never-priced ones wait for the next pass; the closed row is
    never read."""
    src = _Source()
    results = _night()
    run = _run(results, source=src)

    assert [s for s, _, _ in src.asked] == ["SNDK", "SCNI", "INDV", "IXQWF", "ACLEW"]
    assert all(day == NOV25 and live for _, day, live in src.asked)
    assert (run.status, run.cap, run.read, run.deferred) == (
        "on",
        5,
        ("SNDK", "SCNI", "INDV", "IXQWF", "ACLEW"),
        ("TMRC", "MTMTY"),
    )
    refs = _refs(results)
    assert refs["ACLEW"] == MassiveRef("ACLEW", NOV25, True, True, "WARRANT")
    assert refs["INDV"] == MassiveRef("INDV", NOV25, True, False, "CS", date(2026, 11, 3))
    # tonight's trade comes off the grouped day the cross-check fetched (no request)
    assert refs["SNDK"] == MassiveRef("SNDK", NOV25, True, True, "CS", traded_on=NOV25)
    assert refs["SCNI"].traded_on == NOV25 and refs["SCNI"].type == "ADRC"
    assert run.traded == 2
    # deferred, nothing known yet: no evidence; the closed row never carries any
    assert refs["TMRC"] is None and refs["MTMTY"] is None and refs["ACA"] is None


def test_the_cap_is_the_setting_and_zero_reads_nothing_but_the_trade(cap):
    cap(2)
    src = _Source()
    results = _night()
    run = _run(results, source=src)
    assert [s for s, _, _ in src.asked] == ["SNDK", "SCNI"] and len(run.deferred) == 5
    cap(0)
    src = _Source()
    results = _night()
    run = _run(results, source=src)
    assert src.asked == [] and run.status == "off" and run.read == ()
    refs = _refs(results)
    assert refs["SNDK"] == MassiveRef(
        "SNDK", traded_on=NOV25
    )  # traded tonight, the overview not read
    assert refs["ACLEW"] is None


def test_a_404_is_read_evidence_not_listed_and_is_not_asked_again(cap):
    """The next pass carries the earlier reads (the artifact's rows), so only the never-read rows are asked: TMRC
    and MTMTY — 404s, read as ``listed`` false."""
    first = _night()
    _run(first, source=_Source())
    _write(first, at=datetime(2026, 11, 25, 3, 30, tzinfo=timezone.utc))
    src = _Source()
    second = _night()
    run = _run(second, source=src, payloads=list_run_logs())
    assert [s for s, _, _ in src.asked] == ["TMRC", "MTMTY"]
    assert run.not_listed == ("TMRC", "MTMTY") and run.carried == 5 and run.deferred == ()
    refs = _refs(second)
    assert refs["TMRC"] == MassiveRef("TMRC", NOV25, listed=False)
    # a carried read keeps its own values; tonight's trade is re-read off tonight's grouped day
    assert refs["ACLEW"] == MassiveRef("ACLEW", NOV25, True, True, "WARRANT")
    assert refs["SNDK"].traded_on == NOV25


def _write(results, *, at):
    write_cron_run_log(
        results,
        asof=NOV25,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=30),
        tape_evaluated=True,
        tape_stale_days=5,
    )


def test_a_carried_read_of_ANOTHER_symbol_is_read_again(cap):
    """The steward pointed ACLEW's vendor symbol elsewhere since the read: the evidence names the symbol it was
    read for, so the new one is read."""
    first = _night()
    _run(first, source=_Source())
    _write(first, at=datetime(2026, 11, 25, 3, 30, tzinfo=timezone.utc))
    src = _Source()
    second = _night(ACLEW="ACLE")
    _run(second, source=src, payloads=list_run_logs())
    assert ("ACLE", NOV25, True) in src.asked
    assert _refs(second)["ACLEW"] == MassiveRef("ACLE", NOV25, listed=False)


# --- fail-open ------------------------------------------------------------------------------------------------


def test_a_failed_read_stops_the_nights_reads_and_the_rest_wait(cap):
    src = _Source(fail_on="SCNI")
    results = _night()
    run = _run(results, source=src)
    assert [s for s, _, _ in src.asked] == ["SNDK", "SCNI"]  # nothing after a failure
    assert run.read == ("SNDK",) and run.deferred == (
        "SCNI",
        "INDV",
        "IXQWF",
        "ACLEW",
        "TMRC",
        "MTMTY",
    )
    assert run.failed and "HTTP 500" in run.failed and run.error is None
    refs = _refs(results)
    assert refs["SNDK"].read_on == NOV25 and refs["SCNI"] == MassiveRef("SCNI", traded_on=NOV25)


def test_no_key_reads_nothing_and_still_carries_and_marks_the_trade(cap, monkeypatch):
    monkeypatch.setattr(me, "default_reference_source", lambda: None)
    results = _night()
    run = me.run_massive_reference(
        results=results, asof=NOV25, allow_live=True, grouped=_grouped(), payloads=lambda: []
    )
    assert (run.status, run.note) == ("off", "no MASSIVE_API_KEY")
    assert _refs(results)["SNDK"] == MassiveRef("SNDK", traded_on=NOV25)


def test_a_settings_fault_or_a_unit_fault_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(me, "get_settings", boom)
    results = _night()
    run = _run(results)
    assert run.error and run.error.startswith("RuntimeError") and run.status == "failed"
    assert all(s.massive_ref is None for r in results for s in r.tape_stale)


def test_a_no_live_pass_reads_and_marks_nothing(cap):
    src = _Source()
    results = _night()
    run = _run(results, source=src, allow_live=False)
    assert run.status == "not_run" and src.asked == []
    assert all(s.massive_ref is None for r in results for s in r.tape_stale)


def test_no_grouped_day_leaves_the_trade_unknown(cap):
    results = _night()
    _run(results, grouped=GroupedFetch(NOV25, "off", "no MASSIVE_API_KEY"))
    assert _refs(results)["SNDK"].traded_on is None


# --- the words ------------------------------------------------------------------------------------------------


def test_the_report_line_is_one_quiet_line_and_silent_when_nothing_happened(cap):
    run = _run(_night(), source=_Source())
    assert me.report_lines(run) == [
        "Massive.com reference · 5 stale symbols read (5-a-night cap); 2 wait for a later pass"
    ]
    first = _night()
    _run(first, source=_Source())
    _write(first, at=datetime(2026, 11, 25, 3, 30, tzinfo=timezone.utc))
    again = _run(_night(), source=_Source(), payloads=list_run_logs())
    assert me.report_lines(again) == [
        "Massive.com reference · 2 stale symbols read (5-a-night cap), 2 not listed: TMRC, MTMTY"
    ]
    assert me.report_lines(me.ReferenceRun(status="on", cap=5)) == []
    failed = _run(_night(), source=_Source(fail_on="SNDK"))
    assert me.report_lines(failed)[0].startswith(
        "Massive.com reference · the read failed (Massive.com ticker overview: HTTP 500"
    )
    assert me.report_lines(me.ReferenceRun(status="failed", error="RuntimeError: x")) == [
        "MASSIVE.COM REFERENCE FAILED — RuntimeError: x (tonight's stale rows carry no Massive.com reference)"
    ]
