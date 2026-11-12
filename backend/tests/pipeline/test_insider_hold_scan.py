"""The nightly's ``insider_holds`` block (``pipeline/insider_hold_scan.py``): every held insider row is
counted, a NEW one is told to the operator once (the artifact, the printed report, the health page), and the
known ones are one quiet count line.

The held rows come from ingesting the REAL filings committed as fixtures (see ``tests/ingest/test_form4.py``),
through the ingest itself — nothing is inserted by hand.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from db.session import DEFAULT_TENANT_ID, connect
from domain.insider_holds import PAGE_NAME_CAP
from ingest.edgar.form4 import ingest_form4
from pipeline import cron_run_log, daily
from pipeline.insider_hold_scan import (
    BLOCK,
    HeldRow,
    HoldScan,
    keys_of,
    new_labels_of,
    new_more_of,
    previous_hold_keys,
    report_lines,
    run_hold_scan,
)

_FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
_BBAI = ("BBAI", "0001836981", "0001628280-24-010030", "form4_bbai_leading_zero_year.xml")
_LSCC = ("LSCC", "0000855658", "0001437749-24-004874", "form4_lscc_future_year.xml")
_ASOF = date(2026, 11, 10)
_T0 = datetime(2026, 11, 11, 3, 30, tzinfo=timezone.utc)


def _hold(db, case) -> uuid.UUID:
    """Ingest one real filing onto a master row carrying its issuer's CIK; its bad row is held."""
    ticker, cik, accession, fname = case
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
        )
    out = ingest_form4(
        db, sid, (_FIX / fname).read_text(encoding="utf-8"), accession, member_cik=cik
    )
    db.commit()
    assert out.rejected == 1
    return sid


def _scan(prior: set[str] | None) -> HoldScan:
    return run_hold_scan(
        allow_live=True, connect=connect, previous=lambda: prior, clock=lambda: 0.0
    )


def _write(tmp_path, i: int, scan: HoldScan | None, *, allow_live: bool = True) -> None:
    at = _T0 + timedelta(hours=i)
    cron_run_log.write_cron_run_log(
        [],
        asof=_ASOF,
        allow_live=allow_live,
        started_at=at,
        finished_at=at,
        base_dir=tmp_path,
        holds=scan,
    )


def test_a_hold_is_new_on_the_first_pass_and_known_on_the_next(db, tmp_path, capsys):
    sid = _hold(db, _BBAI)
    capsys.readouterr()

    # the first pass after this ships has no baseline: every existing hold is new, once
    assert previous_hold_keys(base_dir=tmp_path) is None
    first = _scan(previous_hold_keys(base_dir=tmp_path))
    key = f"{sid}:{_BBAI[2]}:0:4"
    assert first.evaluated and not first.baseline and first.error is None
    assert (first.total, first.known, first.by_reason) == (1, 0, {"year_before_1934": 1})
    assert first.payload()[BLOCK] == {
        "evaluated": True,
        "total": 1,
        "by_reason": {"year_before_1934": 1},
        "new": [
            {
                "key": key,
                "ticker": "BBAI",
                "accession": _BBAI[2],
                "txn_seq": 0,
                "form": "4",
                "code": first.new[0].code,
                "shares": first.new[0].shares,
                "price": first.new[0].price,
                "date_as_filed": "0023-03-23",
                "reason": "year_before_1934",
            }
        ],
        "known": 0,
        "error": None,
        "duration_s": 0.0,
        "keys": [key],
    }
    assert first.new[0].code and first.new[0].shares  # read off the held row, not invented
    lines = report_lines(first)
    assert lines[0].startswith("INSIDER ROWS HELD — 1 NEW")
    assert _BBAI[2] in lines[1] and "0023-03-23" in lines[1] and "year_before_1934" in lines[1]
    assert len(lines) == 2  # no known line: nothing is known yet

    # ...the pass writes its artifact; the next pass reads it as the baseline
    _write(tmp_path, 0, first)
    assert previous_hold_keys(base_dir=tmp_path) == {key}
    second = _scan(previous_hold_keys(base_dir=tmp_path))
    assert second.evaluated and second.baseline
    assert (second.total, second.known, second.new) == (1, 1, ())
    assert second.payload()[BLOCK]["new"] == [] and second.payload()[BLOCK]["known"] == 1
    assert report_lines(second) == [
        "1 known held insider row(s) — the same rows as the last pass (quiet; kept in insider_txn_hold)"
    ]

    # a hold written after that pass (another filing) is the only new one on the third
    _write(tmp_path, 1, second)
    sid2 = _hold(db, _LSCC)
    third = _scan(previous_hold_keys(base_dir=tmp_path))
    assert [h.key for h in third.new] == [f"{sid2}:{_LSCC[2]}:1:4"]
    assert (third.total, third.known) == (2, 1)
    assert third.by_reason == {"year_after_filing_year": 1, "year_before_1934": 1}


def test_the_health_line_names_a_new_hold_once(db, tmp_path):
    _hold(db, _BBAI)
    first = _scan(None)

    health = daily.assess_health(
        [],
        asof=_ASOF,
        allow_live=True,
        freeze_check=False,
        insider_holds_new=first.new_labels,
        insider_holds_more=first.new_more,
    )
    assert health is not None and health.insider_holds_new == first.new_labels
    label = health.label
    assert "1 insider row(s) held because the filing's transaction date cannot be true" in label
    h = first.new[0]
    assert (
        f"BBAI {_BBAI[2]} ({h.code}, {h.shares:,.0f} sh)" in label
    )  # ticker, accession, code, shares
    assert label.endswith("not a cron error")
    # benign-class on the Admin run row: it shows, and it never makes the verdict unhealthy
    from app.routers.admin import _BENIGN_MARK, _problems

    (line,) = _problems(health)
    assert _BENIGN_MARK in line and _BBAI[2] in line

    # the next pass knows the row: nothing to page
    _write(tmp_path, 0, first)
    second = _scan(previous_hold_keys(base_dir=tmp_path))
    assert (second.new_labels, second.new_more) == ((), 0)
    assert (
        daily.assess_health(
            [],
            asof=_ASOF,
            allow_live=True,
            freeze_check=False,
            insider_holds_new=second.new_labels,
            insider_holds_more=second.new_more,
        )
        is None
    )


def test_the_baseline_is_the_newest_live_finished_evaluated_pass(db, tmp_path):
    _hold(db, _BBAI)
    scan = _scan(None)
    _write(tmp_path, 0, scan)
    _write(tmp_path, 1, HoldScan(error="OperationalError: down"))  # a failed scan: skipped
    _write(tmp_path, 2, HoldScan(evaluated=True), allow_live=False)  # --no-live: never a baseline
    _write(tmp_path, 3, None)  # a pass that carried no scan writes the not-evaluated block: skipped
    assert previous_hold_keys(base_dir=tmp_path) == {h.key for h in scan.holds}
    doc = scan.payload()
    assert keys_of(doc) == {h.key for h in scan.holds}
    # the Admin history re-derives the page it shows from the artifact alone
    assert (new_labels_of(doc), new_more_of(doc)) == (scan.new_labels, scan.new_more)
    assert (new_labels_of({}), new_more_of({}), keys_of({})) == ((), 0, set())


def test_no_live_does_not_scan_and_a_fault_is_reported_never_raised():
    def _never():
        raise AssertionError("must not connect")

    off = run_hold_scan(allow_live=False, connect=_never, previous=_never, clock=lambda: 0.0)
    assert off == HoldScan() and report_lines(off) == []

    def _down():
        raise OSError("db down")

    broken = run_hold_scan(allow_live=True, connect=_down, previous=_never, clock=lambda: 0.0)
    assert not broken.evaluated and broken.error == "OSError: db down"
    assert report_lines(broken)[0].startswith("INSIDER HOLDS CHECK FAILED")


def test_a_night_with_no_held_row_prints_nothing_and_pages_nothing(db):
    clean = _scan(None)
    assert clean.evaluated and clean.total == 0 and report_lines(clean) == []
    assert (
        daily.assess_health(
            [],
            asof=_ASOF,
            allow_live=True,
            freeze_check=False,
            insider_holds_new=clean.new_labels,
            insider_holds_more=clean.new_more,
        )
        is None
    )


def test_the_page_names_up_to_the_cap_and_counts_the_rest():
    """The cap is the Form 4/A page's (a pure shape test: the rows are built in memory, no fact asserted)."""
    rows = tuple(
        HeldRow(
            key=f"s{i}:acc-{i}:0:4",
            security_id=f"s{i}",
            ticker=f"T{i:02d}",
            accession=f"acc-{i}",
            txn_seq=0,
            form="4",
            code="S",
            shares=100.0 + i,
            price=1.0,
            date_as_filed="0024-01-01",
            reason="year_before_1934",
        )
        for i in range(PAGE_NAME_CAP + 3)
    )
    scan = HoldScan(evaluated=True, holds=rows, new=rows)
    assert len(scan.new_labels) == PAGE_NAME_CAP and scan.new_more == 3
    health = daily.assess_health(
        [],
        asof=_ASOF,
        allow_live=True,
        freeze_check=False,
        insider_holds_new=scan.new_labels,
        insider_holds_more=scan.new_more,
    )
    assert health is not None
    assert f"{PAGE_NAME_CAP + 3} insider row(s) held" in health.label
    assert "(+3 more in the run artifact)" in health.label
    lines = report_lines(scan)
    assert len(lines) == 1 + PAGE_NAME_CAP + 1 and lines[-1].startswith("  +3 more")
    doc = scan.payload()
    assert len(doc[BLOCK]["new"]) == PAGE_NAME_CAP + 3  # the artifact lists every new row
    assert (new_labels_of(doc), new_more_of(doc)) == (scan.new_labels, 3)


# --- the pass itself: the block rides the artifact, the page fires once, the report prints the lines ------


def test_the_real_pass_writes_the_block_and_pages_a_new_hold_once(db, monkeypatch, tmp_path):
    """``run_daily_pass`` with its network / roster seams stubbed (``catchup_pass_stubs``) and the hold scan
    REAL against the test DB: the first pass names the held row on its page and writes it as new; the second
    pass reads that artifact as its baseline, pages nothing and counts the row as known."""
    from tests.pipeline.catchup_pass_stubs import Name, covered, prime_pass, run_pass

    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    pager = prime_pass(monkeypatch)
    real_scan = daily.run_hold_scan
    # the stubbed pass has no real connection; hand the scan the test DB's (its baseline read stays the pass's)
    monkeypatch.setattr(daily, "run_hold_scan", lambda **k: real_scan(**{**k, "connect": connect}))
    sid = _hold(db, _BBAI)
    key = f"{sid}:{_BBAI[2]}:0:4"
    roster = [covered(uuid.uuid4(), asof=_ASOF, healthy=Name("AOK", uuid.uuid4()))]

    out1, pages1 = run_pass(monkeypatch, pager, roster, asof=_ASOF)

    assert [h.key for h in out1.holds.new] == [key]
    (page,) = pages1
    assert page.insider_holds_new == out1.holds.new_labels and _BBAI[2] in page.label
    doc1 = cron_run_log.list_run_logs()[0]
    assert doc1[BLOCK]["evaluated"] and [r["key"] for r in doc1[BLOCK]["new"]] == [key]
    assert (doc1[BLOCK]["total"], doc1[BLOCK]["known"], doc1[BLOCK]["keys"]) == (1, 0, [key])

    out2, pages2 = run_pass(monkeypatch, pager, roster, asof=_ASOF)

    assert out2.holds.new == () and out2.holds.known == 1
    # told once: whatever else the stubbed pass pages (its roster fetched nothing, so it pages FROZEN both
    # times), no page names or counts a held row again
    assert all(not p.insider_holds_new and not p.insider_holds_more for p in pages2)
    assert all("insider row(s) held" not in p.label for p in pages2)
    doc2 = cron_run_log.list_run_logs()[0]
    assert (doc2[BLOCK]["new"], doc2[BLOCK]["known"], doc2[BLOCK]["total"]) == ([], 1, 1)


def test_the_printed_report_carries_the_hold_lines_and_the_per_thesis_count(db, capsys):
    """The cron's printed report: the per-thesis line says "held" (never "REJECTED"), the new hold is listed
    once, and the next pass prints only the quiet count."""
    from pipeline.ingest_thesis import NameResult

    _hold(db, _LSCC)
    first = _scan(None)
    capsys.readouterr()
    res = daily.ThesisRunResult(
        thesis_id=uuid.uuid4(),
        name="T",
        recorded=False,
        ingested=[
            NameResult(
                ticker="LSCC",
                security_id=uuid.uuid4(),
                form4_appended=3,
                price_bars_appended=0,
                form4_txn_rejected=1,
            )
        ],
    )
    daily._report([res], holds=first)
    out = capsys.readouterr().out
    assert "1 insider txn held (impossible date)" in out and "REJECTED" not in out
    assert "INSIDER ROWS HELD — 1 NEW" in out and _LSCC[2] in out and "2027-02-17" in out

    known = _scan({h.key for h in first.holds})
    daily._report([], holds=known)
    out = capsys.readouterr().out
    assert "1 known held insider row(s)" in out and "INSIDER ROWS HELD" not in out
