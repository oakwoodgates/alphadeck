from __future__ import annotations

from uuid import UUID

from domain.base import DomainModel


class Security(DomainModel):
    """A resolved security in the canonical master (CIK <-> ticker <-> CUSIP <-> FIGI).

    Everything resolves to the master at ingest; ``id`` is the security_master row id that facts
    reference. ``cusip`` stays optional (OpenFIGI ticker-mapping doesn't return it).

    ``sector`` / ``exchange`` / ``status`` / ``category`` are machine-parsed IDENTITY (from EDGAR submissions),
    enriched onto the master — descriptive, NEVER a fact: they never enter a fact_* table or feed a number on a
    call card (#1/#3 govern NUMBERS, not identity strings). ``status`` is a listing-presence heuristic, not a
    delisting feed; ``category`` is EDGAR's filer-status string (a maturity/size tell, e.g. "Large accelerated
    filer" vs "Smaller reporting company"). All optional — an un-enriched row reads ``None`` (the honest fallback).
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
