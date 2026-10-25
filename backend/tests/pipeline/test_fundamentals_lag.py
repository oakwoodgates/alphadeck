"""The "fundamentals not current" reading (data hygiene, 2026-10-24): SEC's own data behind a filed report.

The fixtures are REAL documents from prod's EDGAR cache as it stood on 2026-10-24 (copied read-only):
``tests/fixtures/edgar/fundamentals_lag/submissions/CIK<10>.json`` keeps the index's ``cik`` / ``name`` /
``tickers`` and, from ``filings.recent``, the six columns the reader reads for every 10-Q / 10-K / amendment
and 8-K filed since 2025-06-01 (rows verbatim, others dropped); ``companyfacts/CIK<10>.json`` keeps ``cik`` /
``entityName`` and the dei cover-share and us-gaap ``Assets`` nodes, verbatim (GPJA: its only nodes). Each test
copies them into a temporary cache and stamps each companyfacts file with the cached copy's own write time
(``_FETCHED``), which the rule reads as "when our copy was fetched".

- ``PYPL`` — its Q2 10-Q (filed 2026-07-28) is in no companyfacts row; the Q1 10-Q is. Our copy: 2026-10-24.
- ``BWXT`` — its Q2 10-Q is not carried, but the 10-Q/A for the same period (2026-08-05) is: current.
- ``GPJA`` — Georgia Power, a co-registrant of Southern's combined filing: SEC's data carries none of its 10-Qs.
- ``APD`` — our cached copy was written 2026-07-09, before its 2026-07-30 10-Q: OUR copy is behind, not SEC's.
- ``AIR`` — current.

MEASURED with the same reader over the whole cached copy (1,026 companies with a cached companyfacts): 36
flagged; the measurement pass's 38 less HNRG and ORA, which have no cached companyfacts at all (that pass read a
missing file as "SEC lacks it").
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline import fundamentals_lag as fl
from pipeline.data_flags import ONBOARDING_DONE, fundamentals_flag, fundamentals_lags

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "fundamentals_lag"
_CIK = {
    "PYPL": "0001633917",
    "BWXT": "0001486957",
    "GPJA": "0000041091",
    "APD": "0000002969",
    "AIR": "0000001750",
}
# the cached companyfacts' own write times (epoch seconds) on prod's copy
_FETCHED = {
    "PYPL": 1792872246,  # 2026-10-24 20:04 UTC
    "BWXT": 1792868217,  # 2026-10-24 18:56 UTC
    "GPJA": 1792809401,  # 2026-10-24 02:36 UTC
    "APD": 1783609624,  # 2026-07-09 15:07 UTC
    "AIR": 1792809328,  # 2026-10-24 02:35 UTC
}
ASOF = date(2026, 10, 24)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    for sub in ("submissions", "companyfacts"):
        (tmp_path / sub).mkdir()
    for tk, cik in _CIK.items():
        name = f"CIK{cik}.json"
        shutil.copy(_FX / "submissions" / name, tmp_path / "submissions" / name)
        dst = tmp_path / "companyfacts" / name
        shutil.copy(_FX / "companyfacts" / name, dst)
        os.utime(dst, (_FETCHED[tk], _FETCHED[tk]))
    return tmp_path


def test_pypl_its_q2_10q_is_not_in_secs_data(cache):
    lag = fl.read_lag(_CIK["PYPL"], asof=ASOF, cache_dir=cache)
    assert lag is not None
    assert (lag.form, lag.filed, lag.period, lag.last_period) == (
        "10-Q",
        date(2026, 7, 28),
        date(2026, 6, 30),
        date(2026, 3, 31),
    )
    assert lag.accession == "0001633917-26-000082"
    f = fundamentals_flag(lag)
    assert f.reason == (
        "Fundamentals end at 2026-03-31; the 10-Q filed 2026-07-28 isn't in SEC's data yet."
    )
    assert f.kind == "fundamentals_not_current" and f.level == "in_doubt"
    assert (
        f.columns == () and f.affects == ()
    )  # no Cockpit cell reads fundamentals: never a row mark
    assert [e.url for e in f.evidence] == [
        "https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/" + lag.primary_doc,
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0001633917.json",
    ]


def test_bwxt_an_amendment_sec_carries_for_the_period_is_current(cache):
    assert fl.read_lag(_CIK["BWXT"], asof=ASOF, cache_dir=cache) is None


def test_gpja_sec_carries_none_of_its_reports(cache):
    lag = fl.read_lag(_CIK["GPJA"], asof=ASOF, cache_dir=cache)
    assert lag is not None and lag.last_period is None
    assert fundamentals_flag(lag).reason == (
        "SEC's data carries none of this company's recent 10-Q / 10-K reports; the 10-Q filed "
        "2026-07-30 isn't in it yet."
    )


def test_apd_our_copy_predates_the_filing_so_nothing_is_said_about_sec(cache):
    assert fl.read_lag(_CIK["APD"], asof=ASOF, cache_dir=cache) is None


def test_air_is_current(cache):
    assert fl.read_lag(_CIK["AIR"], asof=ASOF, cache_dir=cache) is None


def test_it_clears_on_its_own_when_the_cached_copy_carries_the_report(cache):
    """The nightly re-pulls the copy once SEC's data has the 10-Q: the same reader then says nothing (the
    memo is keyed on the file's version, so the new copy is read). A DIRECT-INPUT contract: the re-pulled
    copy is the real one plus one row from the 10-Q's accession (the shape SEC's data takes once it has it).
    """
    path = cache / "companyfacts" / f"CIK{_CIK['PYPL']}.json"
    lag = fl.read_lag(_CIK["PYPL"], asof=ASOF, cache_dir=cache)
    assert lag is not None
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["facts"]["us-gaap"]["Assets"]["units"]["USD"].append(
        {
            "end": "2026-06-30",
            "val": 1,
            "accn": lag.accession,
            "form": "10-Q",
            "filed": "2026-07-28",
        }
    )
    path.write_text(json.dumps(doc), encoding="utf-8")
    later = _FETCHED["PYPL"] + 86_400
    os.utime(path, (later, later))
    assert fl.read_lag(_CIK["PYPL"], asof=ASOF + timedelta(days=1), cache_dir=cache) is None


def test_nothing_cached_is_never_a_claim_about_sec(cache):
    (cache / "companyfacts" / f"CIK{_CIK['PYPL']}.json").unlink()
    assert fl.read_lag(_CIK["PYPL"], asof=ASOF, cache_dir=cache) is None
    assert fl.read_lag("0009999999", asof=ASOF, cache_dir=cache) is None


# --- the pure rule ---------------------------------------------------------------------------------------


def _f(form: str, accn: str, filed: str, period: str) -> fl.PeriodicFiling:
    d = date.fromisoformat(filed)
    return fl.PeriodicFiling(
        form=form,
        accession=accn,
        filed=d,
        period=date.fromisoformat(period),
        accepted=datetime(d.year, d.month, d.day, 20, tzinfo=timezone.utc),
    )


_FETCH = datetime(2026, 10, 24, 2, tzinfo=timezone.utc)
_Q1 = _f("10-Q", "q1", "2026-05-01", "2026-03-31")
_Q2 = _f("10-Q", "q2", "2026-08-01", "2026-06-30")


def _carried(*accns: str):
    return lambda a: a in accns


def test_judge_flags_a_week_old_report_sec_lacks():
    lag = fl.judge("1", [_Q2, _Q1], _carried("q1"), asof=ASOF, fetched_at=_FETCH)
    assert lag is not None and lag.last_period == date(2026, 3, 31)


def test_judge_waits_a_week_and_a_day_of_grace():
    fresh = _f("10-Q", "q2", "2026-10-20", "2026-09-30")
    assert fl.judge("1", [fresh, _Q1], _carried("q1"), asof=ASOF, fetched_at=_FETCH) is None
    # our copy fetched less than a day after the acceptance: it may simply predate SEC's processing
    soon = datetime(2026, 8, 2, 4, tzinfo=timezone.utc)
    assert fl.judge("1", [_Q2, _Q1], _carried("q1"), asof=ASOF, fetched_at=soon) is None


def test_judge_an_amendment_is_never_the_latest_report_and_other_forms_are_ignored():
    amend = _f("10-K/A", "ka", "2026-09-01", "2025-12-31")
    assert fl.judge("1", [amend, _Q2], _carried("q2"), asof=ASOF, fetched_at=_FETCH) is None
    assert fl.judge("1", [amend], _carried(), asof=ASOF, fetched_at=_FETCH) is None


def test_periodic_filings_reads_the_index_columns():
    subs = {
        "filings": {
            "recent": {
                "form": ["8-K", "10-Q", "10-Q"],
                "accessionNumber": ["a", "b", "c"],
                "filingDate": ["2026-08-02", "2026-08-01", "2026-05-01"],
                "reportDate": ["2026-08-01", "2026-06-30", ""],
                "acceptanceDateTime": ["", "2026-08-01T16:05:00.000Z", ""],
                "primaryDocument": ["x.htm", "q.htm", "r.htm"],
            }
        }
    }
    (only,) = fl.periodic_filings(subs)  # the 8-K is not periodic; the undated period is unreadable
    assert (only.form, only.accession, only.primary_doc) == ("10-Q", "b", "q.htm")
    assert only.accepted == datetime(2026, 8, 1, 16, 5, tzinfo=timezone.utc)
    assert fl.periodic_filings(None) == []


# --- the reader over the master (DB): who is judged -------------------------------------------------------


def _master(db, ticker: str, cik: str | None, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2020-01-01"),
        )
    return sid


def test_a_name_still_onboarding_a_fund_and_a_cikless_name_are_not_judged(db, cache):
    pypl = _master(db, "PYPL", _CIK["PYPL"])
    gpja = _master(db, "GPJA", _CIK["GPJA"])
    fund = _master(db, "FUND", _CIK["PYPL"], kind="etf")
    bare = _master(db, "BARE", None)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_onboarding (tenant_id, security_id, status, source) "
            "VALUES (%s, %s, 'queued', 'cli'), (%s, %s, %s, 'cli')",
            (DEFAULT_TENANT_ID, gpja, DEFAULT_TENANT_ID, pypl, ONBOARDING_DONE),
        )
    db.commit()
    out = fundamentals_lags(
        db, tenant_id=DEFAULT_TENANT_ID, ids=[pypl, gpja, fund, bare], asof=ASOF, cache_dir=cache
    )
    assert set(out) == {pypl}  # GPJA is queued, FUND is a fund, BARE has no CIK


def test_the_local_constants_mirror_their_sources():
    from ingest.edgar.client import _DEFAULT_CACHE
    from pipeline.onboarding import STATUS_DONE

    assert fl.DEFAULT_EDGAR_CACHE == _DEFAULT_CACHE
    assert ONBOARDING_DONE == STATUS_DONE
