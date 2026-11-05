"""The fund-header parser and the public-date rule, on REAL SEC headers (public documents, copied from the
measured week 2026-10-29..11-04 into tests/fixtures/edgar/fund_headers/). Pure — no DB, no network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from radar.fund_header import HeaderParseError, parse_header
from radar.funds import public_date_of

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "fund_headers"


def _h(accession: str):
    return parse_header((FIX / f"{accession}.hdr.sgml").read_text(encoding="utf-8"))


def test_live_485apos_with_new_series_and_a_raw_ampersand():
    h = _h("0000088053-26-000835")
    assert (h.form, h.private_to_public) == ("485APOS", False)
    assert h.accepted_at == datetime(2026, 11, 2, 21, 31, 16, tzinfo=timezone.utc)  # 16:31:16 ET
    assert h.filing_date == date(2026, 11, 2) and h.released_on == date(2026, 11, 2)
    assert [(f.cik, f.name) for f in h.filers] == [("0000095603", "DEUTSCHE DWS MARKET TRUST")]
    assert [s.kind for s in h.series] == ["new"] * 4
    # the raw "&" survives untouched ("&Technology" is not an HTML entity here)
    assert h.series[1].series_id == "S000114369"
    assert h.series[1].name == "Xtrackers Active Science &Technology Select ETF"
    assert h.series[1].classes[0].class_id == "C000286053"
    assert h.series[1].classes[0].ticker is None


def test_many_new_series_in_one_filing():
    h = _h("0002071844-26-001726")
    assert len(h.series) == 52 and {s.kind for s in h.series} == {"new"}
    assert len({s.series_id for s in h.series}) == 52
    assert len(h.filers) == 1  # the two <FILER> blocks (1933 + 1940 Act) are one co-registrant


def test_two_new_series_with_sp500_ampersand():
    h = _h("0000894189-26-029923")
    assert [s.name for s in h.series] == [
        "Defiance Ai Funds Nasdaq Autonomous Income ETF",
        "Defiance Ai Funds S&P 500 Autonomous Income ETF",
    ]


def test_existing_series_only():
    h = _h("0001162044-26-000924")
    assert [(s.series_id, s.name, s.kind) for s in h.series] == [
        ("S000000502", "Sparrow Growth Fund", "existing")
    ]
    assert [c.ticker for c in h.series[0].classes] == ["SGFFX", "SGNFX", "SGFCX"]


def test_new_classes_contracts_and_private_to_public_release_date():
    h = _h("0001580642-22-002480")
    assert h.private_to_public is True
    assert h.filing_date == date(2022, 5, 4)  # the OLD filing date...
    assert h.released_on == date(2026, 11, 4)  # ...but the first line carries the release day
    # the header lists each of the two series twice: new classes, then again under the existing contracts
    assert [s.kind for s in h.series] == ["new_classes", "new_classes", "existing", "existing"]
    assert [c.name for c in h.series[0].classes] == ["C Class", "A Class"]


def test_485bpos_class_tickers_and_effectiveness_date():
    h = _h("0000051931-26-001081")
    assert h.form == "485BPOS" and h.effectiveness_date == date(2026, 11, 1)
    s = h.series[0]
    assert (s.series_id, s.kind) == ("S000093531", "existing")
    assert s.classes[0].ticker == "AFPSX" and s.classes[0].class_id == "C000261842"


def test_n1a_amendment():
    h = _h("0001213900-26-114442")
    assert h.form == "N-1A/A" and len(h.series) == 5


def test_s1_header_has_no_series_block():
    h = _h("0001999371-26-024117")
    assert h.form == "S-1/A" and h.series == ()
    assert [(f.cik, f.name) for f in h.filers] == [("0002073616", "Canary Staked INJ ETF")]


def test_not_a_header_raises():
    with pytest.raises(HeaderParseError):
        parse_header("<html>Request Rate Threshold Exceeded</html>")


# --- the public date (#1): the public clock, never SEC's next-business-day filing date ---------------


def test_evening_acceptance_counts_that_evening():
    h = _h("0001171520-26-000326")  # accepted Tue 2026-11-03 18:37 ET; SEC dates it 2026-11-04
    assert h.filing_date == date(2026, 11, 4)
    assert public_date_of(h) == date(2026, 11, 3)


def test_private_to_public_counts_from_its_release_day():
    assert public_date_of(_h("0001580642-22-002480")) == date(2026, 11, 4)  # not 2022-05-04


def test_a_daytime_acceptance_is_its_own_date():
    assert public_date_of(_h("0000088053-26-000835")) == date(2026, 11, 2)


def test_no_acceptance_falls_back_to_the_filing_date():
    text = (FIX / "0000088053-26-000835.hdr.sgml").read_text(encoding="utf-8")
    text = "\n".join(ln for ln in text.splitlines() if "ACCEPTANCE-DATETIME" not in ln)
    assert parse_header(text).accepted_at is None
    assert public_date_of(parse_header(text)) == date(2026, 11, 2)
