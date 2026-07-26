from __future__ import annotations

from pathlib import Path

import pytest

from ingest import CacheMiss
from securities import fund_tickers

# The REAL SEC company_tickers_mf.json rows (trimmed to four funds, structure intact) — captured
# 2026-07-26. LIT + URA share ONE trust CIK (1432353) with DIFFERENT seriesIds: the series id, not the
# trust, is the fund's identity — the load-bearing fact the resolver exists to carry.
_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sec"


def test_resolves_lit_to_trust_cik_and_series():
    ident = fund_tickers.resolve("LIT", cache_dir=_FIXTURES)
    assert ident is not None
    assert ident.trust_cik == "0001432353"  # zero-padded, the master/sec_tickers convention
    assert ident.series_id == "S000029441"
    assert ident.class_id == "C000090392"


def test_lowercase_ticker_resolves_the_same():
    assert fund_tickers.resolve("lit", cache_dir=_FIXTURES) == fund_tickers.resolve(
        "LIT", cache_dir=_FIXTURES
    )


def test_same_trust_different_series_lit_vs_ura():
    """LIT and URA are BOTH Global X Funds (trust CIK 1432353) — only the seriesId tells their N-PORTs
    apart. A trust-level resolution would conflate every Global X fund."""
    lit = fund_tickers.resolve("LIT", cache_dir=_FIXTURES)
    ura = fund_tickers.resolve("URA", cache_dir=_FIXTURES)
    assert lit is not None and ura is not None
    assert lit.trust_cik == ura.trust_cik == "0001432353"
    assert lit.series_id != ura.series_id
    assert ura.series_id == "S000029442"


def test_unknown_symbol_is_none_never_a_guess():
    assert fund_tickers.resolve("NOTAFUND", cache_dir=_FIXTURES) is None


def test_uncached_with_live_disabled_raises_cachemiss(tmp_path):
    with pytest.raises(CacheMiss):
        fund_tickers.resolve("LIT", cache_dir=tmp_path, allow_live=False)
