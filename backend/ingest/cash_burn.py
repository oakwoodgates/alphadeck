"""Ingest a cash + quarterly-burn fact — the basis for the Workbench's cash-runway meter.

runway months = cash / (quarterly_burn / 3), derived on read (Option B); a cash-flow-positive name
(``quarterly_burn_usd <= 0``) reads as max runway. This is the operator-ratified SOURCED fact behind the
runway number (mirroring the catalyst bridge): ``source_ref`` is the 10-Q it came from — NEVER a model
guess. Append-only; the caller owns the txn.

(The RUNWAY meter is built real from this fact — it is NOT the dilution clock. The dilution meter reuses
the existing dilution clock / ``fact_dilution`` separately.)
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID


def ingest_cash_burn(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    cash_usd: float,
    quarterly_burn_usd: float,
    source: str,
    source_ref: str,
    event_date: date,
    note: str | None = None,
    ratified_by: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append a cash + quarterly-burn fact (append-only; the caller owns the txn — no commit here).

    ``cash_usd`` is cash + equivalents on hand; ``quarterly_burn_usd`` is net cash used in operations per
    quarter (``<= 0`` = cash-positive). ``source_ref`` is the 10-Q (provenance + identity); a restatement is
    a NEW row with a later ``recorded_at`` (latest-version-wins on the as-of read). Returns the new fact id.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "cash_usd": cash_usd,
        "quarterly_burn_usd": quarterly_burn_usd,
        "source": source,
        "source_ref": source_ref,
        "note": note,
        "ratified_by": ratified_by,
        "valid_from": event_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_cash_burn", values)
