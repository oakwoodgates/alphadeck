"""Ingest a shares-outstanding fact — the basis for the Workbench's market-cap figure.

market cap = latest as-of close × latest as-of shares outstanding (derived on read, Option B). This is the
operator-ratified SOURCED fact behind the shares number (mirroring the catalyst bridge): ``source_ref`` is
the 10-Q cover / XBRL fact it came from — NEVER a model guess. Append-only; the caller owns the txn.

(Distinct from ``fact_dilution.shares_outstanding``, which the dilution clock reads on its own as-of/identity;
this is a general per-name source so a name without a convert still carries shares.)
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID


def ingest_shares_outstanding(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    shares: float,
    source: str,
    source_ref: str,
    event_date: date,
    note: str | None = None,
    ratified_by: str | None = None,
    vouched: str | None = None,
    ads_ratio: int | None = None,
    ads_ratio_status: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append a shares-outstanding fact (append-only; the caller owns the txn — no commit here).

    ``source_ref`` is the 10-Q cover / XBRL fact (provenance + identity); a restatement is a NEW row with a
    later ``recorded_at`` (latest-version-wins on the as-of read). ``vouched`` is confirm/override PROVENANCE
    ('confirmed' | 'overridden' | None) — never a scoring input. Returns the new fact id.

    ``ads_ratio`` / ``ads_ratio_status`` (annual-cover names, spec §10): derivation metadata for the
    market-cap scorer — ``shares`` stays the TRUE ordinary count from the cover; the ratio modulates the
    derivation, never the fact. None/None (every 10-Q name, every legacy row) = not applicable -> 1:1.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "shares": shares,
        "source": source,
        "source_ref": source_ref,
        "note": note,
        "ratified_by": ratified_by,
        "vouched": vouched,
        "ads_ratio": ads_ratio,
        "ads_ratio_status": ads_ratio_status,
        "valid_from": event_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_shares_outstanding", values)
