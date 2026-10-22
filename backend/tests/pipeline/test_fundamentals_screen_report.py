"""The fundamentals data-quality screen's report on the NIGHTLY path (the independent review's A1): a row the
screen keeps out of a company's revenue series is never stored, so the pass must COUNT it on its summary line,
name the ones from new filings, and RECORD every one on the run artifact — read back with ``.get`` (G4), so an
artifact written before the key existed still reads (as nothing screened). The CLI was already covered; the
nightly is the path that writes.

The screened rows are REAL: VICR's FY2025 10-K files Q3-2025 as $407,701,000 — the year — beside the 10-Q's
$110,423,000 (``tests/fundamentals_fixtures.py``). The summary/artifact tests feed that row (and a second real
one, LHX's) through the real printer and payload builder with no DB; the leg test is DB-backed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.fundamentals import select_revenue
from pipeline import daily
from pipeline.cron_run_log import build_run_payload, fundamentals_screened
from pipeline.ingest_fundamentals import NameResult, ingest_fundamentals_for_securities
from tests.fundamentals_fixtures import COMPANYFACTS_CIK, companyfacts_cache, companyfacts_revenue

VICR_ROW = select_revenue(companyfacts_revenue("VICR")).screened[0]  # filed 2026-03-02
LHX_ROWS = tuple(select_revenue(companyfacts_revenue("LHX")).screened)  # both filed 2026-02-12


def _result(*names: NameResult) -> daily.ThesisRunResult:
    return daily.ThesisRunResult(thesis_id=uuid.uuid4(), name="T", fundamentals=tuple(names))


def _names() -> tuple[NameResult, NameResult]:
    return (
        NameResult("VICR", uuid.uuid4(), appended=3, screened=(VICR_ROW,)),
        NameResult("LHX", uuid.uuid4(), appended=1, screened=LHX_ROWS),
    )


def test_the_nightly_leg_carries_the_screened_rows(db, tmp_path):
    """``ingest_fundamentals_for_securities`` — what ``pipeline.daily`` calls per thesis — hands back WHICH
    rows were screened (it used to keep only appended/skipped)."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "VICR", COMPANYFACTS_CIK["VICR"], "2020-01-01"),
        )
    db.commit()
    sec = Security(id=sid, tenant_id=DEFAULT_TENANT_ID, ticker="VICR", cik=COMPANYFACTS_CIK["VICR"])
    client = EdgarClient(cache_dir=companyfacts_cache(tmp_path, "VICR"), allow_live=False)
    [res] = ingest_fundamentals_for_securities(
        db, [sec], client=client, tenant_id=DEFAULT_TENANT_ID
    )
    assert res.error is None and res.appended > 0
    assert res.screened == (VICR_ROW,)
    assert (VICR_ROW.reason, VICR_ROW.period_end, VICR_ROW.value) == (
        "fy_total_in_quarter",
        date(2025, 9, 30),
        407_701_000.0,
    )


def test_the_pass_summary_counts_every_screened_row_and_names_the_new_ones(capsys):
    """Every rejection is COUNTED by reason on the one summary line; only rows from filings within a week of
    the pass are printed one by one (the cached history is re-judged every night — repeating it would be
    noise; a new filing's rejection is the news)."""
    daily._print_fundamentals([_result(*_names())], date(2026, 3, 5))
    out = capsys.readouterr().out
    assert (
        "fundamentals refresh: +4 quarters, 2 names, 3 revenue row(s) screened out (data quality: "
        "fy_total_in_quarter 2, year_in_q4_context 1)" in out
    )
    assert (
        "fundamentals screened (new filing): VICR fy_total_in_quarter — quarter ending 2025-09-30"
        in out
    )
    assert "LHX" not in out.split("\n", 1)[1]  # filed 2026-02-12: counted, not re-printed

    daily._print_fundamentals(
        [_result(NameResult("AMD", uuid.uuid4(), appended=2))], date(2026, 3, 5)
    )
    out = capsys.readouterr().out
    assert "screened" not in out  # loud only when nonzero


def test_the_run_artifact_records_every_screened_row_and_an_old_artifact_reads_empty():
    """The per-thesis ``fundamentals_screened`` rows land on the artifact; ``fundamentals_screened(doc)``
    sums them with ``.get``, so an artifact without the key — or a thesis entry without it — reads as nothing
    screened instead of raising (the G4 rule: a new key never makes the history unreadable)."""
    now = datetime(2026, 3, 6, 3, 0, tzinfo=timezone.utc)
    payload = build_run_payload(
        [_result(*_names())],
        asof=date(2026, 3, 5),
        allow_live=True,
        started_at=now,
        finished_at=now,
    )
    rows = payload["theses"][0]["fundamentals_screened"]
    assert [(r["ticker"], r["reason"], r["period_end"], r["filed"], r["value"]) for r in rows][
        0
    ] == (
        "VICR",
        "fy_total_in_quarter",
        "2025-09-30",
        "2026-03-02",
        407_701_000.0,
    )
    summary = fundamentals_screened(payload)
    assert summary["count"] == 3
    assert summary["by_reason"] == {"fy_total_in_quarter": 2, "year_in_q4_context": 1}

    old = {k: v for k, v in payload.items() if k != "theses"}
    assert fundamentals_screened(old) == {"count": 0, "by_reason": {}, "rows": []}
    thesis_without_key = {
        "theses": [{k: v for k, v in payload["theses"][0].items() if k != "fundamentals_screened"}]
    }
    assert fundamentals_screened(thesis_without_key)["count"] == 0
