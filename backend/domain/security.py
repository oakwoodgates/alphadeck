from __future__ import annotations

from uuid import UUID

from domain.base import DomainModel
from domain.enums import InstrumentKind


class Security(DomainModel):
    """A resolved security in the canonical master (CIK <-> ticker <-> CUSIP <-> FIGI).

    Everything resolves to the master at ingest; ``id`` is the security_master row id that facts
    reference. ``cusip`` stays optional (OpenFIGI ticker-mapping doesn't return it).

    ``sector`` / ``exchange`` / ``status`` / ``category`` are machine-parsed IDENTITY (from EDGAR submissions),
    enriched onto the master — descriptive, NEVER a fact: they never enter a fact_* table or feed a number on a
    call card (#1/#3 govern NUMBERS, not identity strings). ``status`` is a listing-presence heuristic, not a
    delisting feed; ``category`` is EDGAR's filer-status string (a maturity/size tell, e.g. "Large accelerated
    filer" vs "Smaller reporting company"). All optional — an un-enriched row reads ``None`` (the honest fallback).

    ``instrument_kind`` (ETF Sleeve, Slice 1) is what the instrument IS — ``equity`` (the default) vs ``etf``
    (a surfaced fund sleeve). Identity like the above, never a fact; NOT NULL with a meaningful default, so it
    is required here (never ``None``) and defaults to ``equity``.
    """

    id: UUID
    # None = a resolved SEC filer with NO listed line (a sub / holdco / debt issuer — the master keeps
    # them; the "No listed ticker" bucket). The column is nullable and such rows exist live; a required
    # str here made ``master.get`` RAISE on any of them (latent: one ticker-less basket member would have
    # aborted a whole back-half ingest run). Consumers already guard ``if not sec.ticker``.
    ticker: str | None = None
    tenant_id: UUID
    name: str | None = None
    cik: str | None = None
    cusip: str | None = None
    figi: str | None = None
    sector: str | None = None
    exchange: str | None = None
    status: str | None = (
        None  # 'active' | 'inactive' — a listing-presence heuristic, never "delisted"
    )
    category: str | None = (
        None  # EDGAR filer category (maturity/size tell) — identity, never a number
    )
    instrument_kind: InstrumentKind = (
        InstrumentKind.EQUITY  # 'equity' | 'etf' — what it IS; identity, never a call input (#4/#6)
    )
    # ORIGIN ingredients (migration 0028) — raw locators from EDGAR submissions, stored so the origin chip is
    # DERIVED ON READ (securities/origin.py); no computed label/bool is stored. Identity like the above, never
    # a fact / call input. NULL = un-enriched (the chip abstains). `business_country` is the SEC's
    # stateOrCountryDescription — a US STATE abbreviation ("CA") for US entities, often null for foreign ADRs
    # (where `business_city`, e.g. "SHANGHAI", is the only populated locator). `files_foreign_forms` (20-F/40-F
    # in recent filings) is a stored ingredient now SUBSUMED by `recent_foreign_form` (0031, carries the form).
    incorporation: str | None = None
    business_city: str | None = None
    business_country: str | None = None
    files_foreign_forms: bool | None = None
    # FILER-FORM ingredients (migration 0031) — for the foreign-filer explainability tell, DERIVED ON READ
    # (securities/filer_coverage.foreign_filer_form); no computed label is stored. Identity like origin, never
    # a fact / call input (#3). NULL = un-enriched (the tell abstains). `recent_foreign_form` is the newer of
    # 20-F/40-F in recent filings ("20-F" FPI · "40-F" Canadian-MJDS); `files_domestic_forms` (10-K/10-Q
    # present) is the domestic veto that kills the legacy-foreign-form false positive (the UUUU case).
    files_domestic_forms: bool | None = None
    recent_foreign_form: str | None = None
    # RESOLVED PRICE SYMBOL (migration 0032) — the vendor symbol to PRICE under when it DIFFERS from the SEC
    # ``ticker`` (FDCT priced under FDCTD: 251 bars vs 16). Identity like the above, never a fact / call input
    # (#1/#3). NULL is the healthy common case ("priced under the canonical ticker"); a non-null value is the
    # EXCEPTION marker. Consumers price under ``price_symbol or ticker``; the SEC ``ticker`` is NEVER overwritten.
    # ``price_symbol_basis`` is the provenance of the resolution (resolver:auto / operator:adopt), stored as a
    # basis (like ``enriched_source``), never a fact's ``ratified_by``. NULL = un-resolved.
    price_symbol: str | None = None
    price_symbol_basis: str | None = None


class SecurityIdentity(DomainModel):
    """Identity parsed from an EDGAR submissions JSON — the input to ``master.enrich``.

    Machine-parsed descriptive identity (sector/exchange/status + rebrand history), NOT a fact: it never
    enters a fact_* table and never feeds a number on a call card (#1/#3 govern NUMBERS, not identity
    strings). ``status`` is a LISTING-PRESENCE heuristic (a current ticker AND exchange -> 'active', else
    'inactive'), never a formal delisting verdict — the operator-facing label stays a hedged guess.
    ``former_names`` is parsed now so the later identity-bridge slice's data shape is ready; it is UNUSED
    today (``master.enrich`` does not persist it).
    """

    sector: str | None = None
    exchange: str | None = None
    status: str = "active"
    category: str | None = (
        None  # EDGAR filer category (e.g. "Large accelerated filer") — identity, not a number
    )
    former_names: list[dict[str, str]] = []  # [{name, from, to}] from submissions.formerNames
    # ORIGIN ingredients (raw locators; see Security) — parsed by ``parse_identity``, persisted by
    # ``master.enrich``, derived on read by ``securities/origin.py``. All default None so a hand-built
    # identity (tests, partial writers) writes NULL — "un-parsed", never a false "no foreign forms".
    incorporation: str | None = (
        None  # stateOfIncorporationDescription ("Cayman Islands"; "CA" for US)
    )
    business_city: str | None = None  # addresses.business.city ("SHANGHAI")
    business_country: str | None = (
        None  # addresses.business.stateOrCountryDescription (US state abbrev / country / null)
    )
    files_foreign_forms: bool | None = (
        None  # 20-F/40-F in filings.recent.form (subsumed by recent_foreign_form, 0031)
    )
    # FILER-FORM ingredients (0031) — parsed by ``parse_identity``, persisted by ``master.enrich``, derived on
    # read by ``securities/filer_coverage.py``. Default None so a hand-built identity (tests, partial writers)
    # writes NULL — "un-parsed", never a false "files no foreign form" / "files domestic forms".
    files_domestic_forms: bool | None = None  # 10-K/10-Q in filings.recent.form (the domestic veto)
    recent_foreign_form: str | None = None  # the newer of 20-F/40-F in filings.recent.form
