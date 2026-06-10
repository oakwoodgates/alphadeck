"""Ingest an exposure-purity fact — the % of a name's revenue from a named business line.

The Workbench's purity meter re-derives from this fact on read (Option B). It is the operator-ratified
SOURCED fact behind the number (mirroring the catalyst bridge): ``source``/``source_ref`` carry the real
10-K segment it came from — NEVER a model guess or a typed estimate. Append-only; the caller owns the txn.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID


def ingest_revenue_mix(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    segment_label: str,
    mix_pct: float,
    source: str,
    source_ref: str,
    event_date: date,
    note: str | None = None,
    ratified_by: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append a revenue-mix fact (append-only; the caller owns the txn — no commit here).

    ``segment_label`` is the revenue line (e.g. "nuclear"); ``mix_pct`` is its share of revenue (0..100).
    ``source_ref`` is the 10-K segment (URL/accession) — provenance and the fact's natural identity, so a
    later restatement is a NEW row with a later ``recorded_at`` (latest-version-wins on the as-of read).
    Returns the new fact id.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "segment_label": segment_label,
        "mix_pct": mix_pct,
        "source": source,
        "source_ref": source_ref,
        "note": note,
        "ratified_by": ratified_by,
        "valid_from": event_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_revenue_mix", values)
