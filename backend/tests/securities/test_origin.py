"""``resolve_origin`` — the pure derive-on-read origin ladder (the Workbench origin chip).

Pins the full ladder over the raw 0028 ingredients: business country (a US-state abbreviation reads "US" —
the measured SEC quirk; a stated country reads as itself) → business city (the China-ADR fallback, measured
on NIO) → incorporation → None (the honest abstain — the chip renders nothing, never a guessed origin).
Pure and inference-free: SEC-stated strings only, display-normalized (ALL-CAPS → title case), no
city→region map, no call-path import.
"""

from __future__ import annotations

from securities.origin import resolve_origin


def _resolve(country=None, city=None, incorp=None):
    return resolve_origin(business_country=country, business_city=city, incorporation=incorp)


def test_us_state_business_country_reads_us():
    """Rung 1, the SEC quirk (measured live): a US entity's stateOrCountryDescription holds the STATE
    abbreviation ("CA"), not "United States" — the chip reads a quiet "US"."""
    assert _resolve(country="CA", city="Cupertino", incorp="CA") == "US"
    assert _resolve(country="DE") == "US"
    assert _resolve(country="PR") == "US"  # territories are US too
    assert _resolve(country="dc") == "US"  # case-insensitive state test


def test_foreign_stated_country_reads_as_itself():
    """Rung 1, the stated-country arm: present and not a US state → the country, as said (title-cased for
    display when the SEC upcased it — normalization, never inference)."""
    assert _resolve(country="JAPAN", city="TOYOTA CITY", incorp=None) == "Japan"
    assert _resolve(country="CHINA") == "China"
    assert _resolve(country="Ireland") == "Ireland"  # mixed case passes through verbatim


def test_null_country_falls_back_to_city_title_cased():
    """Rung 2, the China-ADR case (measured: NIO's business country is NULL) → the city, title-cased."""
    assert _resolve(country=None, city="SHANGHAI", incorp="Cayman Islands") == "Shanghai"
    assert _resolve(city="HONG KONG") == "Hong Kong"


def test_null_country_and_city_fall_back_to_incorporation():
    """Rung 3: no address locator at all → incorporation (domicile — the last rung, as said)."""
    assert _resolve(incorp="Cayman Islands") == "Cayman Islands"
    assert _resolve(incorp="DE") == "US"  # a US-state incorporation abbreviation reads "US"


def test_all_empty_abstains_none():
    """Rung 4, the honest abstain: nothing stored → None; blanks/whitespace count as nothing."""
    assert _resolve() is None
    assert _resolve(country="  ", city="", incorp="   ") is None


def test_spelled_out_us_normalizes_to_us():
    """A spelled-out US self-identification (EDGAR's X1 describes as "UNITED STATES") reads the same quiet
    "US" — an alias, never inference."""
    assert _resolve(country="UNITED STATES") == "US"
    assert _resolve(incorp="United States") == "US"


def test_military_state_codes_are_not_swallowed_as_us():
    """AA/AE/AP are deliberately NOT in the US set ("AE" collides with a country reading) — the raw string
    surfaces, honest over convenient."""
    assert _resolve(country="AE") == "AE"
