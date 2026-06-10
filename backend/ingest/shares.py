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
    ratified_by: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append a shares-outstanding fact (append-only; the caller owns the txn — no commit here).

    ``source_ref`` is the 10-Q cover / XBRL fact (provenance + identity); a restatement is a NEW row with a
    later ``recorded_at`` (latest-version-wins on the as-of read). Returns the new fact id.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "shares": shares,
        "source": source,
        "source_ref": source_ref,
        "ratified_by": ratified_by,
        "valid_from": event_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_shares_outstanding", values)
