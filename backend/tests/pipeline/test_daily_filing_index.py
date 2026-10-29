"""The filing index (migration 0070) in the nightly: a PASSENGER — its failure never withholds a call nor makes
``ingest_fresh`` false — reported as ONE pass line and two keys on the run artifact. The DB is real (the ``db``
fixture); the cron's ingest step is stubbed with controlled ``NameResult``s (the ``test_daily`` pattern).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID
from pipeline import daily
from pipeline.cron_run_log import build_run_payload
from pipeline.ingest_thesis import NameResult
from repositories import calls_repo

_ASOF = date(2026, 6, 10)


def _thesis(db, name) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


def _nr(**kw) -> NameResult:
    base = dict(ticker="AAA", security_id=uuid.uuid4(), form4_appended=0, price_bars_appended=0)
    return NameResult(**{**base, **kw})


def _health(db, thesis_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT ingest_fresh, ingest_errors FROM calls WHERE thesis_id=%s ORDER BY seq DESC LIMIT 1",
            (thesis_id,),
        )
        return cur.fetchone()


def test_a_FAILED_filing_index_never_withholds_the_call_nor_spoils_ingest_fresh(db, monkeypatch):
    """Every name's filing index failed tonight, and the thesis still records — with ``ingest_fresh`` true
    and zero ingest errors: the R2 gate reads ``error``, which the passenger never touches."""
    names = [
        _nr(ticker="AAA", filing_index_error="index exploded"),
        _nr(ticker="BBB", filing_index_error="index exploded"),
    ]
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: list(names))
    tid = _thesis(db, "Passenger")

    by = {r.thesis_id: r for r in daily.run_daily(db, asof=_ASOF, allow_live=True)}

    assert by[tid].recorded is True and by[tid].withheld_reason is None and by[tid].error is None
    assert len(calls_repo.list_for_thesis(db, tid)) == 1
    assert _health(db, tid) == {"ingest_fresh": True, "ingest_errors": 0}


def _result(*names: NameResult, name: str = "T") -> daily.ThesisRunResult:
    return daily.ThesisRunResult(
        thesis_id=uuid.uuid4(), name=name, recorded=True, ingested=list(names)
    )


def test_the_pass_line_counts_each_company_once_and_names_every_failure(capsys):
    sid, other, broken = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first = _result(
        _nr(
            ticker="AAA",
            security_id=sid,
            filing_index_appended=40,
            filing_index_accepted_set_aside=3,
            filing_index_skipped=1,
            filing_index_skips=(("row 7", "no accession number"),),
        ),
        _nr(ticker="BBB", security_id=other, filing_index_appended=2),
        _nr(ticker="CCC", security_id=broken, filing_index_error="index exploded"),
    )
    # a second thesis holding AAA: its walk appended nothing and re-counted the same skipped row
    second = _result(
        _nr(
            ticker="AAA",
            security_id=sid,
            filing_index_skipped=1,
            filing_index_skips=(("row 7", "no accession number"),),
        ),
        _nr(ticker="CCC", security_id=broken, filing_index_error="index exploded"),
    )

    daily._print_filing_index([first, second])

    out = capsys.readouterr().out.splitlines()
    assert out[0] == (
        "filing index: +42 filing(s) indexed for 2 name(s) · 1 index row(s) skipped "
        "(no accession number 1) · 3 listed acceptance time(s) set aside (not on the filing date within "
        "EDGAR's hours; stored without one)"
    )
    assert out[1:] == [
        "  filing index ERROR: CCC: index exploded"
    ]  # once, though two theses hold it


def test_the_pass_line_is_SILENT_on_a_night_with_nothing_to_say(capsys):
    daily._print_filing_index([_result(_nr(), _nr())])
    assert capsys.readouterr().out == ""


def test_the_pass_line_groups_an_unreadable_date_by_its_reason(capsys):
    daily._print_filing_index(
        [
            _result(
                _nr(
                    filing_index_skipped=2,
                    filing_index_skips=(
                        ("A-1", "unreadable filing date '06/03/2026'"),
                        ("A-2", "unreadable filing date ''"),
                    ),
                )
            )
        ]
    )
    out = capsys.readouterr().out
    assert "2 index row(s) skipped (unreadable filing date 2)" in out


def test_the_run_artifact_records_the_index_count_and_each_failed_company_once():
    sid = uuid.uuid4()
    now = datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc)
    payload = build_run_payload(
        [
            _result(
                _nr(ticker="AAA", filing_index_appended=5),
                _nr(ticker="CCC", security_id=sid, filing_index_error="index exploded"),
                _nr(ticker="CCC", security_id=sid, filing_index_error="index exploded"),
            )
        ],
        asof=_ASOF,
        allow_live=True,
        started_at=now,
        finished_at=now,
    )
    entry = payload["theses"][0]
    assert entry["filing_index_appended"] == 5
    assert entry["filing_index_errors"] == [
        {"security_id": str(sid), "ticker": "CCC", "error": "index exploded"}
    ]
    assert entry["names_errored"] == 0 and entry["name_errors"] == []  # never a name error
