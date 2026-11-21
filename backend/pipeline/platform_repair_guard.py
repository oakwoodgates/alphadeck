"""What a REPAIR may touch in the two platform value tables (V2 W2d), stated once for both repairs.

``fact_shares_outstanding`` and ``fact_cash_burn`` hold PLATFORM VALUES (``docs/PLATFORM_VALUES.md``): one value per
company for everyone, in the data tenant, set by the machine from a clean filing or by the steward. A repair CLI
(``pipeline.repair_shares_cover_dates``, ``pipeline.repair_filing_dates``) appends versions to fix a stored row's
date or dating. Three rules keep it from undoing V2:

1. **The data tenant only.** A row under any other tenant is read by nobody (every read of a DATA table names the
   data tenant), so the repair lists it as ``outside_data_tenant`` and never judges or writes it. Each append
   calls ``domain.scopes.require_write_tenant`` first.
2. **The steward's decisions stand.** A fact (one ``source_ref``) any of whose versions a ``platform_value_decision``
   names (an approve or correct, standing or undone), or whose versions include one set aside
   (``db.set_aside``), is the steward's: listed as ``steward_decision``, never judged, never written. Restating an
   undone version would read again the value the steward took back (an appended version is never set aside);
   restating a standing one would detach the version the decision names from the value shown.
3. **The change stamp.** Every company an ``--apply`` appends to is stamped changed (``db.change_stamp.mark_changed``)
   inside the repair's own transaction, before its commit: a repair holds the repair lock, not the company write
   lock whose release stamps.

Reads only. The SQL is module constants plus ``db.set_aside``'s registered literal (never caller input).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from db.set_aside import active_ids_sql, has_set_aside
from domain.platform_values import TABLE_OF
from domain.scopes import DATA_TENANT_ID

OUTSIDE = "outside_data_tenant"
STEWARD = "steward_decision"

OUTSIDE_WHY = (
    "not judged: outside the data tenant (tenant {tenant}) — no read shows this row; the repair writes the data "
    "tenant only"
)
STEWARD_WHY = (
    "the steward's decision: a platform_value_decision names a version of this fact (or a version is set aside) "
    "— never judged, never written by a repair"
)

_TYPE_OF: dict[str, str] = {table: fact_type for fact_type, table in TABLE_OF.items()}


def outside_data_tenant(tenant_id: UUID) -> bool:
    """Is a stored row outside the data tenant (listed, never judged, never written)?"""
    return tenant_id != DATA_TENANT_ID


def steward_named_ids(conn: psycopg.Connection, table: str) -> set[UUID]:
    """The ids of ``table``'s versions that are the steward's: every version a ``platform_value_decision`` names
    (standing or undone), and every version set aside now. ``table`` is one of the two platform value tables.
    """
    fact_type = _TYPE_OF[table]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fact_id FROM platform_value_decision WHERE fact_type = %s AND fact_id IS NOT NULL",
            (fact_type,),
        )
        ids = {_first(r) for r in cur.fetchall()}
        if has_set_aside(table):
            cur.execute(active_ids_sql(table))
            ids |= {_first(r) for r in cur.fetchall()}
    return ids


def is_stewards(versions: list[dict[str, Any]], named: set[UUID]) -> bool:
    """Is a fact (its stored versions) the steward's: does any version appear in ``named``?"""
    return any(v["id"] in named for v in versions)


def _first(row: Any) -> UUID:
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


__all__ = [
    "OUTSIDE",
    "OUTSIDE_WHY",
    "STEWARD",
    "STEWARD_WHY",
    "is_stewards",
    "outside_data_tenant",
    "steward_named_ids",
]
