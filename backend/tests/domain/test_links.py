"""The one rule for a URL on the wire (security hardening PR 4a): ``domain.links.safe_http_url`` and the
``HttpUrlOut`` annotated type that applies it on response models."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from domain.links import MAX_URL_LENGTH, HttpUrlOut, safe_http_url

# The hostile-output cases (spec item 6). Kept here, not in the shared hostile-input fixture another PR adds.
HOSTILE_URLS: list[str] = [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "https://user:pass@evil.example/",
    "https://evil.example/ ",
    "https://evil.example/" + "a" * 5000,
]


@pytest.mark.parametrize(
    "value",
    [
        "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/",
        "http://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193",
        "HTTPS://WWW.SEC.GOV/Archives/edgar/data/1/000000000124000001/x-index.htm",
        "https://investor.example.com/press/2026/q3.pdf#page=4",
    ],
)
def test_a_well_formed_http_url_passes_unchanged(value: str) -> None:
    assert safe_http_url(value) == value


@pytest.mark.parametrize("value", HOSTILE_URLS)
def test_a_hostile_url_is_none(value: str) -> None:
    assert safe_http_url(value) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        42,
        "0000320193-24-000123",  # an accession, not a URL
        "ftp://www.sec.gov/x",
        "vbscript:msgbox(1)",
        "https://",  # no host
        "https:///path-only",
        "//evil.example/protocol-relative",
        "/relative/path",
        "https://user@evil.example/",  # a user name alone is a credential too
        "https://evil.example/a b",
        "https://evil.example/\ttab",
        "https://evil.example/\nnewline",
        "https://evil.example/\x00nul",
        " https://evil.example/",  # leading whitespace
        "https://evil.example/\u2028",  # a Unicode line separator
        "https://evil.example/\u00a0nbsp",  # a no-break space
    ],
)
def test_anything_else_is_none(value: object) -> None:
    assert safe_http_url(value) is None


def test_the_length_cap_is_inclusive() -> None:
    base = "https://www.sec.gov/"
    at_cap = base + "a" * (MAX_URL_LENGTH - len(base))
    assert len(at_cap) == MAX_URL_LENGTH == 2000
    assert safe_http_url(at_cap) == at_cap
    assert safe_http_url(at_cap + "a") is None


class _Out(BaseModel):
    url: HttpUrlOut
    maybe: HttpUrlOut = None


def test_http_url_out_passes_a_good_url_through() -> None:
    url = "https://www.sec.gov/Archives/edgar/data/1/000000000124000001/"
    assert _Out(url=url).model_dump(mode="json") == {"url": url, "maybe": None}


@pytest.mark.parametrize("value", HOSTILE_URLS)
def test_http_url_out_turns_a_hostile_url_into_null_on_the_wire(value: str) -> None:
    out = _Out(url=value, maybe=value)
    assert out.model_dump(mode="json") == {"url": None, "maybe": None}
    assert '"url":null' in out.model_dump_json()


def test_http_url_out_never_raises_on_a_bad_value() -> None:
    # an invalid stored URL degrades to "no link" — the response is never refused because of it
    assert _Out(url="not a url").url is None
    assert _Out.model_validate({"url": 17}).url is None
