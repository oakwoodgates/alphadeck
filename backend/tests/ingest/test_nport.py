from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest.edgar.nport import (
    fetch_nport,
    latest_nport_accession,
    nport_doc_url,
    parse_nport_holdings,
)

# REAL SEC captures (2026-07-26), laid out as an EdgarClient cache dir so the same files serve the
# unit tests here AND the end-to-end endpoint test: the browse-edgar ATOM for LIT's series
# (S000029441 — 10 entries, 2024-12-30 → 2026-06-29, incl. a same-day NPORT-P + NPORT-P/A pair) and
# the primary docs for LIT's /A and ARKK's newest N-PORT.
_CACHE = Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "nport_cache"
_LIT_ATOM = (_CACHE / "browse-nport" / "S000029441.atom").read_text(encoding="utf-8")
_LIT_DOC = (_CACHE / "forms" / "0002048251-26-005686" / "primary_doc.xml").read_text(
    encoding="utf-8"
)
_ARKK_DOC = (_CACHE / "forms" / "0000940400-26-025084" / "primary_doc.xml").read_text(
    encoding="utf-8"
)


class _FakeClient:
    """Records (url, cache_key) and serves the fixture ATOM — the ``test_submissions`` fake-client
    idiom (no network, no cache dir)."""

    def __init__(self, text: str = _LIT_ATOM) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def get_text(self, url: str, cache_key: str) -> str:
        self.calls.append((url, cache_key))
        return self.text


# --- the series locator ------------------------------------------------------------------------------


def test_locator_picks_the_same_day_amendment_over_its_original():
    """The REAL LIT shape: NPORT-P 0002048251-26-005515 and NPORT-P/A …-005686 both filed 2026-06-29,
    both covering period 2026-04-30 (verified live) — the /A is the CORRECTION, so it wins the tie.
    """
    ref = latest_nport_accession(_FakeClient(), "S000029441")  # type: ignore[arg-type]
    assert ref is not None
    assert ref.accession == "0002048251-26-005686"
    assert ref.is_amendment is True
    assert ref.filed == date(2026, 6, 29)
    assert ref.trust_cik == "0001432353"  # parsed from the entry href — the TRUST, never the agent
    assert ref.index_url.endswith("0002048251-26-005686-index.htm")


def test_locator_asof_keeps_only_filings_knowable_then():
    """#1 no-lookahead: ``asof`` admits only ``filed <= asof`` (the filing EXISTED at the simulated
    time — the ATOM carries no report period; filed ≤ asof implies the period predates asof too)."""
    ref = latest_nport_accession(_FakeClient(), "S000029441", asof=date(2026, 1, 15))  # type: ignore[arg-type]
    assert ref is not None
    assert ref.accession == "0002048251-25-003776"
    assert ref.filed == date(2025, 12, 29)
    # asof ON the filing date is knowable (<=, not <)
    on_day = latest_nport_accession(_FakeClient(), "S000029441", asof=date(2025, 12, 29))  # type: ignore[arg-type]
    assert on_day is not None and on_day.accession == "0002048251-25-003776"


def test_locator_asof_before_any_filing_is_none():
    assert (
        latest_nport_accession(_FakeClient(), "S000029441", asof=date(2020, 1, 1))  # type: ignore[arg-type]
        is None
    )


def test_locator_cache_key_is_mutable_class_not_forms():
    """The series→filings ATOM is MUTABLE (a new quarter appends) — its key must NOT ride the
    immutable ``forms/`` prefix, so the 12h default-refresh TTL applies (the #196 freshness model).
    """
    client = _FakeClient()
    latest_nport_accession(client, "S000029441")  # type: ignore[arg-type]
    [(url, key)] = client.calls
    assert key == "browse-nport/S000029441.atom"
    assert not key.startswith("forms/")
    assert "CIK=S000029441" in url and "type=NPORT-P" in url and "output=atom" in url


def test_locator_tolerates_an_empty_or_alien_atom():
    empty = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert latest_nport_accession(_FakeClient(empty), "S000029441") is None  # type: ignore[arg-type]


# --- the doc fetch -----------------------------------------------------------------------------------


def test_fetch_nport_uses_trust_cik_url_and_immutable_forms_key():
    client = _FakeClient(_LIT_DOC)
    fetch_nport(client, "0001432353", "0002048251-26-005686")  # type: ignore[arg-type]
    [(url, key)] = client.calls
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1432353/000204825126005686/primary_doc.xml"
    )
    assert key == "forms/0002048251-26-005686/primary_doc.xml"  # cache-forever class


def test_nport_doc_url_strips_padding_and_dashes():
    assert nport_doc_url("0001432353", "0002048251-26-005686") == (
        "https://www.sec.gov/Archives/edgar/data/1432353/000204825126005686/primary_doc.xml"
    )


# --- the parser (pure, real docs) --------------------------------------------------------------------


def test_parse_lit_reads_series_vintage_and_45_holdings():
    r = parse_nport_holdings(_LIT_DOC)
    assert r.series_id == "S000029441"
    assert r.series_name == "Global X Lithium and Battery Tech ETF"
    assert r.report_date == date(2026, 4, 30)  # the vintage LABEL (~quarter-end, ~60d lagged)
    assert len(r.holdings) == 45


def test_parse_lit_holding_fields_and_na_normalization():
    r = parse_nport_holdings(_LIT_DOC)
    tsla = next(h for h in r.holdings if h.name == "TESLA, INC.")
    assert tsla.cusip == "88160R101"
    assert tsla.isin == "US88160R1014"
    assert tsla.ticker is None  # Global X puts NO ticker identifier on equity holdings (measured)
    assert tsla.val_usd == pytest.approx(48873445.95)
    assert tsla.pct_val == pytest.approx(2.29, abs=0.01)
    # the SEC's explicit "N/A" is normalized to None — never matched as a real identifier
    repo = next(h for h in r.holdings if h.ticker == "BNYREPOS")
    assert repo.cusip is None and repo.isin is None
    # the measured Global X shape: the ONLY ticker identifier in the whole doc is the repo line —
    # zero equity tickers, so LIT's overlap is honestly unresolved-dominant until 2b
    assert [h.ticker for h in r.holdings if h.ticker] == ["BNYREPOS"]


def test_parse_arkk_carries_tickers_on_most_holdings():
    """The OTHER measured filer shape (ARK): tickers on 44/46 holdings — the ticker-keyed overlap has
    real coverage on such funds today; the LIT shape waits on 2b's CUSIP→ticker."""
    r = parse_nport_holdings(_ARKK_DOC)
    assert r.series_id == "S000042977"
    assert r.report_date == date(2026, 4, 30)
    assert len(r.holdings) == 46
    assert sum(1 for h in r.holdings if h.ticker) == 44
    ktos = next(h for h in r.holdings if h.ticker == "KTOS")
    assert ktos.cusip == "50077B207"


def test_parse_zero_holdings_doc_is_empty_not_a_crash():
    bare = (
        '<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport">'
        "<formData><genInfo><seriesId>S000000001</seriesId><repPdDate>2026-01-31</repPdDate>"
        "</genInfo></formData></edgarSubmission>"
    )
    r = parse_nport_holdings(bare)
    assert r.series_id == "S000000001"
    assert r.holdings == []


def test_parse_sparse_holding_and_malformed_numbers_are_none_never_a_crash():
    xml = (
        '<?xml version="1.0"?><edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>'
        "<invstsOrSecs>"
        "<invstOrSec><name>SPARSE CO</name></invstOrSec>"
        "<invstOrSec><name>ODD CO</name><cusip>N/A</cusip><valUSD>not-a-number</valUSD>"
        "<pctVal></pctVal></invstOrSec>"
        "</invstsOrSecs></formData></edgarSubmission>"
    )
    r = parse_nport_holdings(xml)
    assert (
        len(r.holdings) == 2
    )  # both SHOWN (#9) — sparse fields are None, the walk never drops a row
    sparse, odd = r.holdings
    assert sparse.name == "SPARSE CO" and sparse.cusip is None and sparse.pct_val is None
    assert odd.cusip is None and odd.val_usd is None and odd.pct_val is None
