"""Slice 2 — base URLs + throttle + the UA/OpenFIGI env moved to Settings (behavior-preserving).

The byte-identity gate (the test_yahoo_adapter_matches_fetch_eod_exactly pattern): every URL builder's output
for a fixed input is the EXACT pre-refactor literal — captured by evaluating the old code BEFORE the refactor,
so a slash-join slip or a wrong host→builder mapping flips a test. Plus an override smoke (a base override
reaches the builder — a config edit, not a code edit) and the env reroute (UA / OpenFIGI key via cached
Settings, exact legacy names, prefix-does-not-capture). No network, no key.
"""

from __future__ import annotations

import pytest

from app.schemas_api import edgar_url
from domain.settings import Settings, get_settings
from ingest.doe.feed import usaspending_award_url
from ingest.edgar.converts import _filing_doc_url
from ingest.edgar.extract import _doc_url, companyfacts_url
from ingest.edgar.submissions import form4_doc_url, submissions_url
from ingest.prices.eod_loader import stooq_url, yahoo_chart_url

# Every ALPHADECK_* override this slice introduces + the two legacy-named secrets — delenv'd before each test
# so the defaults/byte-identity assertions are hermetic regardless of ambient env (incl. a wrong prefixed one).
_OVERRIDE_ENV = (
    "ALPHADECK_SEC_DATA_BASE",
    "ALPHADECK_SEC_ARCHIVES_BASE",
    "ALPHADECK_SEC_COMPANY_TICKERS_URL",
    "ALPHADECK_STOOQ_BASE",
    "ALPHADECK_YAHOO_CHART_BASE",
    "ALPHADECK_OPENFIGI_URL",
    "ALPHADECK_USASPENDING_API_BASE",
    "ALPHADECK_USASPENDING_AWARD_URL_BASE",
    "ALPHADECK_EDGAR_RATE_PER_SEC",
    "ALPHADECK_USASPENDING_RATE_PER_SEC",
    "ALPHADECK_HTTP_TIMEOUT_S",
    "ALPHADECK_USASPENDING_TIMEOUT_S",
    "ALPHADECK_USER_AGENT",
    "OPENFIGI_API_KEY",
    "ALPHADECK_OPENFIGI_API_KEY",
)

_CIK = 320193
_ACC = "0000320193-26-000001"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    for name in _OVERRIDE_ENV:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- byte-identity: each builder's output for a fixed input == the exact pre-refactor literal ---


def test_url_builders_are_byte_identical_to_pre_refactor():
    assert stooq_url("HIMS") == "https://stooq.com/q/d/l/?s=hims.us&i=d"
    assert (
        yahoo_chart_url("hims")
        == "https://query1.finance.yahoo.com/v8/finance/chart/HIMS?interval=1d&range=1y"
    )
    assert (
        yahoo_chart_url("hims", "5d")
        == "https://query1.finance.yahoo.com/v8/finance/chart/HIMS?interval=1d&range=5d"
    )
    assert submissions_url(_CIK) == "https://data.sec.gov/submissions/CIK0000320193.json"
    assert companyfacts_url(_CIK) == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    assert (
        form4_doc_url(_CIK, _ACC, "xslF345X06/wk-form4.xml")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/wk-form4.xml"
    )
    assert (
        _doc_url(_CIK, _ACC, "form10q.htm")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/form10q.htm"
    )
    assert (
        _filing_doc_url(_CIK, _ACC, "8k.htm")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/8k.htm"
    )
    assert (
        edgar_url("form4", _ACC, "320193")
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm"
    )
    assert usaspending_award_url("CONT_AWD_X") == "https://www.usaspending.gov/award/CONT_AWD_X"


def test_usaspending_client_builds_exact_api_urls(monkeypatch):
    """The two USASpending API URLs are built inside the client methods (not standalone builders) — capture
    them to prove the api_base + the unchanged path are byte-identical."""
    from ingest.doe.client import UsaSpendingClient

    seen: list[str] = []

    def fake_get_json(self, url, *, cache_key, body=None):
        seen.append(url)
        return {}

    monkeypatch.setattr(UsaSpendingClient, "_get_json", fake_get_json)
    client = UsaSpendingClient()
    client.search_awards({"q": 1})
    client.award_detail("CONT_AWD_X")
    assert seen == [
        "https://api.usaspending.gov/api/v2/search/spending_by_award/",
        "https://api.usaspending.gov/api/v2/awards/CONT_AWD_X/",
    ]


def test_fixed_url_and_base_fields_match_pre_refactor_literals():
    s = Settings()
    assert s.sec_data_base == "https://data.sec.gov"
    assert s.sec_archives_base == "https://www.sec.gov/Archives/edgar/data"
    # the ONE deliberate post-refactor default change: the canonical-primary slice moved the universe to the
    # EXCHANGE variant (per-instrument venue — the rank's discriminator); the plain file has no exchange
    assert s.sec_company_tickers_url == "https://www.sec.gov/files/company_tickers_exchange.json"
    assert s.stooq_base == "https://stooq.com"
    assert s.yahoo_chart_base == "https://query1.finance.yahoo.com"
    assert s.openfigi_url == "https://api.openfigi.com/v3/mapping"
    assert s.usaspending_api_base == "https://api.usaspending.gov/api/v2"
    assert s.usaspending_award_url_base == "https://www.usaspending.gov/award"


def test_throttle_defaults_match_pre_refactor_values_and_types():
    s = Settings()
    assert s.edgar_rate_per_sec == 8.0 and isinstance(s.edgar_rate_per_sec, float)
    assert s.usaspending_rate_per_sec == 5.0 and isinstance(s.usaspending_rate_per_sec, float)
    assert s.http_timeout_s == 30.0 and isinstance(s.http_timeout_s, float)
    assert s.usaspending_timeout_s == 60.0 and isinstance(s.usaspending_timeout_s, float)


# --- override smoke: a base override reaches the builder (config edit, not code edit) ---


def test_env_override_of_a_base_reaches_the_builder(monkeypatch):
    monkeypatch.setenv("ALPHADECK_SEC_DATA_BASE", "https://sec.example.test")
    get_settings.cache_clear()
    assert submissions_url(_CIK) == "https://sec.example.test/submissions/CIK0000320193.json"


# --- env reroute: UA + OpenFIGI key via Settings (exact legacy names, prefix-does-not-capture) ---


def test_user_agent_and_openfigi_key_read_their_exact_legacy_names(monkeypatch):
    monkeypatch.setenv("ALPHADECK_USER_AGENT", "AlphaDeck test you@example.com")
    monkeypatch.setenv("OPENFIGI_API_KEY", "figi-key-xyz")
    s = Settings()
    assert s.user_agent == "AlphaDeck test you@example.com"
    assert s.openfigi_api_key == "figi-key-xyz"
    # the prefix must NOT capture the unprefixed OpenFIGI key
    monkeypatch.delenv("OPENFIGI_API_KEY", raising=False)
    monkeypatch.setenv("ALPHADECK_OPENFIGI_API_KEY", "wrong-prefixed-var")
    assert Settings().openfigi_api_key is None


def test_edgar_client_reads_user_agent_and_rate_from_settings(monkeypatch):
    """The clients reroute through cached Settings — a UA set in the env reaches a freshly-constructed client,
    and the default rate matches the pre-refactor 8.0/s."""
    from ingest.edgar.client import EdgarClient

    monkeypatch.setenv("ALPHADECK_USER_AGENT", "UA via settings")
    get_settings.cache_clear()
    client = EdgarClient(allow_live=True)
    assert client.user_agent == "UA via settings"
    assert client._rate._min_interval == pytest.approx(1.0 / 8.0)  # default edgar_rate_per_sec
