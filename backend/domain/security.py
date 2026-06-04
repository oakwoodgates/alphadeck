from __future__ import annotations

from uuid import UUID

from domain.base import DomainModel


class Security(DomainModel):
    """A resolved security in the canonical master (CIK <-> ticker <-> CUSIP <-> FIGI).

    Everything resolves to the master at ingest; ``id`` is the security_master row id that facts
    reference. ``cusip`` stays optional (OpenFIGI ticker-mapping doesn't return it).
    """

    id: UUID
    ticker: str
    tenant_id: UUID
    name: str | None = None
    cik: str | None = None
    cusip: str | None = None
    figi: str | None = None
