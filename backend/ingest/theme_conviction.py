from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import Grade


def ingest_theme_conviction(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
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
    """Append a THEME-conviction fact (append-only; the caller owns the txn — no commit here).

    The bridge that turns an operator's thesis-level theme conviction into a Key-1 FALLBACK fact with
    provenance: a basket-level belief that arms an otherwise-confirmed member as a disciplined STARTER
    (M5b). NEVER a model guess — ``source`` / ``source_ref`` carry the real basis, and ``grade`` is set
    by the ratifier (ratified at ``flip`` — capped at starter; belief can never mint a core, rule 2; the
    broadcast also emits flip). ``horizon_end`` is the operator-set horizon (the conviction expires past
    it unless re-ratified); ``None`` -> the configured default. Returns the new fact id.
    """
    values = {
        "tenant_id": tenant_id,
        "thesis_id": thesis_id,
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
    return append_fact(conn, "fact_theme_conviction", values)
