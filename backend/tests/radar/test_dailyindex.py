"""The daily master-index parser (ingest/edgar/dailyindex.py) — guard-based parsing: header,
banner, and malformed lines fail the field guards naturally; no header-state to desync."""

from __future__ import annotations

from datetime import date

from ingest.edgar.dailyindex import daily_index_url, parse_master_idx

SAMPLE = """Description:           Daily Index of EDGAR Dissemination Feed
Last Data Received:    August 3, 2026
Anonymous FTP:         ftp://ftp.sec.gov/edgar/

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
1111|KNOWN SHELL CORP|8-K|2026-08-03|edgar/data/1111/0001111111-26-000001.txt
2222|New Shell Acquisition Corp|425|2026-08-03|edgar/data/2222/0002222222-26-000002.txt
4444|Ordinary Opco Inc|10-K|2026-08-03|edgar/data/4444/0004444444-26-000004.txt
not-a-cik|Junk Row|8-K|2026-08-03|edgar/data/x/y.txt
5555|Bad Date Co|8-K|yesterday|edgar/data/5555/0005555555-26-000005.txt
"""


def test_parse_master_idx_keeps_only_well_formed_rows():
    rows = parse_master_idx(SAMPLE)
    assert [r.cik for r in rows] == ["1111", "2222", "4444"]
    r = rows[1]
    assert r.company == "New Shell Acquisition Corp"
    assert r.form == "425"
    assert r.filed == date(2026, 8, 3)
    assert r.accession == "0002222222-26-000002"


def test_daily_index_url_quarters():
    assert daily_index_url(date(2026, 8, 3)).endswith("/2026/QTR3/master.20260803.idx")
    assert daily_index_url(date(2026, 1, 2)).endswith("/2026/QTR1/master.20260102.idx")
