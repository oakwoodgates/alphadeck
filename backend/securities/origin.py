"""Origin resolution — WHERE a name is from, derived on read from the master's stored raw ingredients.

The Workbench origin chip: the operator surfaces many foreign (esp. Chinese) companies during discovery and
wants to spot-and-skip them by sight. ``resolve_origin`` turns the raw locator ingredients (migration 0028 —
parsed by ``parse_identity``, persisted by ``master.enrich``) into one display string, or ``None`` (no chip).

DERIVE-ON-READ by design: no computed label/bool is stored, so the ladder can be tuned in code with zero
re-enrich, and the stored row always carries the raw evidence (#6). This is machine-parsed SURFACE identity —
a raw-fallback ladder over SEC-stated strings, NO inference (no curated city→region map): never a fact, never
a number, never a call input, and NOT a display signal (those live in ``signals/display/``; this module is
pure and imports nothing from the call path). It TAGS, never filters (#9 — a foreign name still surfaces; the
operator's existing prune is the decision, #10). Unknown → ``None`` — the chip renders NOTHING, never a
guessed or defaulted origin (the same hedged honesty as the listing-``status`` heuristic).

The ladder (all inputs are the SEC's own strings, taken as-said):

1. **Business-address country** (``addresses.business.stateOrCountryDescription``) — the primary locator.
   SEC quirk (measured live): for US entities this field holds the US STATE abbreviation ("CA", "DE"), not
   "United States" — so a US-state/territory value reads **"US"**; any other present value is a real stated
   country and reads as itself.
2. **Business city** — the China-ADR case (measured: NIO has country null, city "SHANGHAI") → the city.
3. **Incorporation** (``stateOfIncorporationDescription``) — US-state abbrev → "US"; anything else present
   ("Cayman Islands") reads as itself. Incorporation is DOMICILE, not operations — it is the last rung.
4. Nothing present → ``None`` (no chip).

Display normalization only (never inference): the SEC upcases addresses ("SHANGHAI", "CHINA"), so an
ALL-CAPS value is title-cased for the chip ("Shanghai"); mixed-case values ("Cayman Islands") pass through
verbatim.
"""

from __future__ import annotations

# The 50 US states + DC + the inhabited territories, as the SEC's address/incorporation abbreviations.
# Deliberately EXCLUDES the military mail codes (AA/AE/AP): "AE" collides with a plausible country reading
# and a rare raw "AE" chip is honest, while a false "US" would mislead — abstain toward the raw string.
_US_STATE_ABBREVS = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD "
    "MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC "
    "SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP".split()
)

# Spelled-out US self-identifications (EDGAR's "X1" code describes as "UNITED STATES") — normalized to the
# same quiet "US" chip. Aliases only, never inference.
_US_ALIASES = frozenset(
    {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA"}
)


def _is_us(value: str) -> bool:
    return value.upper() in _US_STATE_ABBREVS or value.upper() in _US_ALIASES


def _display(value: str) -> str:
    """Title-case an ALL-CAPS SEC string for the chip ("SHANGHAI" → "Shanghai"); keep mixed case verbatim
    ("Cayman Islands") and short all-caps tokens ("AE") as-is (a code shouldn't read "Ae"). Word-wise
    ``capitalize`` (not ``str.title``) so "ST. JOHN'S" → "St. John's"."""
    if value.isupper() and len(value) > 3:
        return " ".join(w.capitalize() for w in value.split())
    return value


def resolve_origin(
    *,
    business_country: str | None,
    business_city: str | None,
    incorporation: str | None,
) -> str | None:
    """The pure origin ladder: business country (US-state abbrev → "US") → business city → incorporation →
    ``None``. No I/O, no inference — display-normalizes the SEC's own strings, or abstains."""
    country = (business_country or "").strip()
    if country:
        return "US" if _is_us(country) else _display(country)
    city = (business_city or "").strip()
    if city:
        return _display(city)
    incorp = (incorporation or "").strip()
    if incorp:
        return "US" if _is_us(incorp) else _display(incorp)
    return None
