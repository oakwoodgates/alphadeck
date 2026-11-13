"""``dashboards.filings.source_url`` and the split row's ``sec_url`` use the ONE wire rule (security hardening PR
4a): ``domain.links.safe_http_url``. A stored http(s) reference that fails the rule is no link (never the raw
value); an accession-shaped reference still resolves to the EDGAR index."""

from __future__ import annotations

import pytest

from dashboards import filings
from dashboards.filings import source_url
from tests.domain.test_links import HOSTILE_URLS

CIK = "0000320193"


def test_a_good_stored_url_is_the_link() -> None:
    url = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/x-index.htm"
    assert source_url(url, CIK) == url
    assert source_url(f"  {url}  ", CIK) == url  # the stored value's padding is trimmed, as before


def test_an_accession_still_resolves_through_the_index() -> None:
    assert (
        source_url("0000320193-24-000123", CIK)
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/"
    )


# the trailing-space case is left out: ``source_url`` trims the stored reference first (unchanged behavior), so
# it is the trimmed, well-formed URL; ``HttpUrlOut`` on the response field refuses an untrimmed one
@pytest.mark.parametrize(
    "value",
    [v for v in HOSTILE_URLS if v == v.strip()] + ["https://user@evil.example/x"],
)
def test_a_hostile_stored_reference_is_no_link(value: str) -> None:
    assert source_url(value, CIK) is None


def test_a_credentialed_url_is_no_longer_passed_through() -> None:
    # the old ``https?://\\S+`` regex let this through as a link
    assert source_url("https://user:pass@evil.example/", None) is None


def test_the_old_regex_is_gone() -> None:
    assert not hasattr(filings, "_HTTP")
