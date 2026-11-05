"""The fund radar scan end-to-end over a FIXTURE cache (allow_live=False — the suite never touches the
network): daily index -> header per accession -> filing + series facts -> the two symbol legs. Headers are
REAL SEC headers (tests/fixtures/edgar/fund_headers); the index rows and the symbol files are synthetic
(marked below). Idempotency assertions COUNT THE TABLES."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from radar import fund_repo, fund_stage
from radar.funds import FundRadarResult, run_fund_radar, run_symbol_legs

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "fund_headers"
D = date(2026, 11, 3)
D2 = date(2026, 11, 4)

# SYNTHETIC index rows (the accessions are the real fixtures'); the Founder Funds row sits on the 11-04
# index because SEC dated that filing 11-04 — its public date is still the 11-03 evening.
INDEX_D = """CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
1844255|Defiance ETFs Trust I|485APOS|2026-11-03|edgar/data/1844255/0002071844-26-001726.txt
1844255|Defiance ETFs Trust I|485APOS|2026-11-03|edgar/data/1844255/0002071844-26-001726.txt
1540305|ETF Series Solutions|485APOS|2026-11-03|edgar/data/1540305/0000894189-26-029923.txt
2073616|Canary Staked INJ ETF|S-1/A|2026-11-03|edgar/data/2073616/0001999371-26-024117.txt
9999|Acme Software Corp|S-1|2026-11-03|edgar/data/9999/0009999999-26-000001.txt
4444|Noise Filer Inc|10-K|2026-11-03|edgar/data/4444/0004444444-26-000004.txt
1111|Broken Trust|485APOS|2026-11-03|edgar/data/1111/0001111111-26-000009.txt
"""
INDEX_D2 = """CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
2077057|Founder Funds Trust|485APOS|2026-11-04|edgar/data/2077057/0001171520-26-000326.txt
1545440|Ultimus Managers Trust|485APOS|2026-11-04|edgar/data/1545440/0001580642-22-002480.txt
"""
HEADERS = (
    "0002071844-26-001726",
    "0000894189-26-029923",
    "0001999371-26-024117",
    "0001171520-26-000326",
    "0001580642-22-002480",
)

# SYNTHETIC symbol files: one tracked series (a Defiance fund from the 52-fund filing) and one that no
# filing announced as new.
SEC_TICKERS = {
    "fields": ["cik", "seriesId", "classId", "symbol"],
    "data": [
        [1844255, "S000114440", "C000286124", "tstx"],
        [1, "S000999999", "C000999999", "ZZZZ"],
    ],
}
NASDAQ_TRADED = (
    "Nasdaq Traded|Symbol|Security Name|Listing Exchange|Market Category|ETF|Round Lot Size|Test Issue|"
    "Financial Status|CQS Symbol|NASDAQ Symbol|NextShares\n"
    "Y|TSTX|Test Wrapper ETF|Q| |Y|100|N|N|TSTX|TSTX|N\n"
    "Y|ZZZZ|Other ETF|Q| |Y|100|Y|N|ZZZZ|ZZZZ|N\n"
    "File Creation Time: 1104202622:00||||||||||\n"
)
NASDAQ_OTHER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "ABC|Some Company|N|ABC|N|100|N|ABC\n"
    "File Creation Time: 1104202622:00|||||||\n"
)


def _nasdaq(url: str) -> str:
    return NASDAQ_TRADED if "nasdaqtraded" in url else NASDAQ_OTHER


@pytest.fixture(autouse=True)
def _clean_fund_tables(db):
    with db.cursor() as cur:
        cur.execute("TRUNCATE fact_fund_filing, fact_fund_series, fact_fund_symbol")
    db.commit()


@pytest.fixture
def cache(tmp_path):
    (tmp_path / "daily-index").mkdir()
    (tmp_path / "daily-index" / "master.20261103.idx").write_text(INDEX_D, encoding="utf-8")
    (tmp_path / "daily-index" / "master.20261104.idx").write_text(INDEX_D2, encoding="utf-8")
    for acc in HEADERS:
        d = tmp_path / "forms" / acc
        d.mkdir(parents=True)
        shutil.copy(FIX / f"{acc}.hdr.sgml", d / "hdr.sgml")
    (tmp_path / "fund-tickers").mkdir()
    (tmp_path / "fund-tickers" / "company_tickers_mf.json").write_text(
        json.dumps(SEC_TICKERS), encoding="utf-8"
    )
    return tmp_path


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — test-only, fixed names
        return cur.fetchone()["n"]


def _run(db, cache, **kw):
    client = EdgarClient(cache_dir=cache, allow_live=False)
    return run_fund_radar(
        db, until=D2, days=3, edgar_client=client, nasdaq_fetch=_nasdaq, symbol_night=D2, **kw
    )


def test_scan_stores_facts_dedupes_coregistrants_and_is_idempotent(db, cache):
    r = _run(db, cache)

    # 11-02 has no cached index (quiet skip); the two cached days are scanned
    assert r.dates_scanned == ["2026-11-03", "2026-11-04"]
    assert any("2026-11-02" in s and "not cached" in s for s in r.dates_skipped)
    # forms kept: the 5 with headers + the broken one; the co-registrant row is deduped; the non-fund S-1 and
    # the 10-K are not read at all
    assert r.filings_seen == 6
    assert r.filings_appended == 5
    assert _count(db, "fact_fund_filing") == 5
    # 52 + 2 + 1 (S-1 name-only) + 1 (existing) + 2 (new classes; the header lists each twice -> one row)
    assert r.series_appended == 58 and _count(db, "fact_fund_series") == 58

    # the header fault is loud, and nothing is stored for it (tomorrow's window retries it)
    assert len(r.errors) == 1 and "0001111111-26-000009" in r.errors[0]
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM fact_fund_filing WHERE accession = '0001111111-26-000009'")
        assert cur.fetchone() is None

    # public dates: the evening acceptance counts that evening; the private-to-public one its release day
    with db.cursor() as cur:
        cur.execute("SELECT accession, public_date, valid_from FROM fact_fund_filing")
        pub = {row["accession"]: (row["public_date"], row["valid_from"]) for row in cur.fetchall()}
    assert pub["0001171520-26-000326"] == (date(2026, 11, 3), date(2026, 11, 3))
    assert pub["0001580642-22-002480"] == (date(2026, 11, 4), date(2026, 11, 4))

    # the S-1's name-only row: no series ID, keyed on the filer
    with db.cursor() as cur:
        cur.execute(
            "SELECT series_key, series_id, series_name, kind FROM fact_fund_series WHERE accession = %s",
            ("0001999371-26-024117",),
        )
        row = cur.fetchone()
    assert (row["series_key"], row["series_id"]) == ("cik:0002073616", None)
    assert (row["series_name"], row["kind"]) == ("Canary Staked INJ ETF", "new")

    # symbols: only the tracked series' SEC row; the test issue is not a listing
    assert _count(db, "fact_fund_symbol") == 2
    syms = {(s.series_id, s.symbol, s.source, s.night) for s in fund_repo.symbol_rows(db)}
    assert syms == {
        ("S000114440", "TSTX", "sec_ticker_file", D2),
        ("S000114440", "TSTX", "nasdaq_directory", D2),
    }

    # IDEMPOTENCY — a re-scan appends nothing; the TABLES do not grow (count, not the read)
    r2 = _run(db, cache)
    assert r2.filings_appended == 0 and r2.filings_unchanged == 5
    assert r2.series_appended == 0 and r2.series_unchanged == 58 and r2.symbols_appended == 0
    assert _count(db, "fact_fund_filing") == 5
    assert _count(db, "fact_fund_series") == 58
    assert _count(db, "fact_fund_symbol") == 2


def test_no_index_day_is_a_quiet_skip_not_an_error(db):
    class _Resp:
        status_code = 403

    class _Forbidden(Exception):
        response = _Resp()

    class _Client:
        live_fetches = 0

        def get_text(self, *a, **k):
            raise _Forbidden()

        def get_json(self, *a, **k):
            raise _Forbidden()

    r = run_fund_radar(
        db, until=D, days=1, edgar_client=_Client(), symbol_files=False  # type: ignore[arg-type]
    )
    assert r.dates_scanned == [] and r.dates_skipped == ["2026-11-03"] and r.errors == []


def test_symbol_files_mark_only_the_night_they_are_read(db, cache):
    """A backfill scans days with the symbol files off; the two files then mark ONE night (tonight)."""
    client = EdgarClient(cache_dir=cache, allow_live=False)
    r = run_fund_radar(db, until=D2, days=3, edgar_client=client, symbol_files=False)
    assert _count(db, "fact_fund_symbol") == 0 and r.symbols_appended == 0

    tail = FundRadarResult()
    run_symbol_legs(
        db, client, night=date(2026, 11, 20), allow_live=False, nasdaq_fetch=_nasdaq, result=tail
    )
    assert {s.night for s in fund_repo.symbol_rows(db)} == {date(2026, 11, 20)}
    assert tail.errors == []


def test_nasdaq_file_down_is_visible_and_the_sec_leg_still_runs(db, cache):
    def _down(url: str) -> str:
        raise RuntimeError("503")

    client = EdgarClient(cache_dir=cache, allow_live=False)
    r = run_fund_radar(
        db, until=D2, days=3, edgar_client=client, nasdaq_fetch=_down, symbol_night=D2
    )
    assert any("Nasdaq directory nasdaqtraded.txt" in e for e in r.errors)
    assert {s.source for s in fund_repo.symbol_rows(db)} == {"sec_ticker_file"}


def test_no_live_without_a_fetcher_skips_the_nasdaq_leg_visibly(db, cache):
    client = EdgarClient(cache_dir=cache, allow_live=False)
    r = run_fund_radar(db, until=D2, days=3, allow_live=False, edgar_client=client, symbol_night=D2)
    assert any("Nasdaq directory" in s for s in r.symbols_skipped)


def test_stage_derived_from_the_stored_facts(db, cache):
    _run(db, cache)
    launches = {
        lc.key: lc
        for lc in fund_stage.derive_launches(fund_repo.series_views(db), fund_repo.symbol_rows(db))
    }
    lc = launches["S000114440"]
    assert lc.stage.filed == date(2026, 11, 3)
    assert lc.stage.effective is None
    assert lc.stage.ticker == D2 and lc.stage.listed == D2
    assert lc.stage.current == "listed" and lc.symbols == ("TSTX",)
    # a new-classes row and an existing row are not launches; the S-1 name-only fund is
    assert "S000039711" not in launches and "cik:0002073616" in launches
    assert launches["cik:0002073616"].stage.current == "filed"


def test_the_recurring_leg_builds_its_client_with_the_recurring_ttl(db, monkeypatch):
    seen = {}

    class _Spy(EdgarClient):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr("radar.funds.EdgarClient", _Spy)
    run_fund_radar(db, until=D, days=1, allow_live=False, symbol_files=False)
    assert seen["cache_ttl_s"] == RECURRING_CACHE_TTL_S


def test_tenant_default_is_used(db, cache):
    _run(db, cache)
    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT tenant_id FROM fact_fund_filing")
        assert [r["tenant_id"] for r in cur.fetchall()] == [DEFAULT_TENANT_ID]
