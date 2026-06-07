from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import CatalystType, Grade


def ingest_catalyst(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    catalyst_type: CatalystType,
    grade: Grade,
    label: str,
    source: str,
    source_ref: str,
    event_date: date,
    horizon_end: date | None = None,
    ratified_by: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append a catalyst-conviction fact (append-only; the caller owns the txn — no commit here).

    The bridge that turns a verifiable catalyst into a Key-1 conviction fact with provenance: the
    operator-ratified path now (``source='ratified'``), the deterministic feeds later
    (``'8-k'`` / ``'doe_award'`` / ``'nrc'``). NEVER a model guess — ``source`` / ``source_ref`` carry
    the real source, and ``grade`` is set by the ratifier (or the deterministic rule), not the LLM.
    ``horizon_end`` is the agreement's relevance horizon (its period-of-performance end, where the
    structured record carries one) — it drives liveness, decoupled from grade; ``None`` -> the default.
    Returns the new fact id.
    """
    values = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "catalyst_type": CatalystType(catalyst_type).value,
        "grade": Grade(grade).value,
        "label": label,
        "source": source,
        "source_ref": source_ref,
        "horizon_end": horizon_end,
        "ratified_by": ratified_by,
        "valid_from": event_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_catalyst", values)
