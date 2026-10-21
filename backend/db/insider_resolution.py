"""The Postgres side of the insider-amendment RESOLUTION (R8-A): load what an as-of read of ``fact_insider_txn``
can see of the 4/A tables and hand it to the ONE pure resolver (``domain.insider_amendments``) — the twin of
``replay.pit``'s DuckDB loader, so the two engines resolve identically (the D3a precedent).

Every input is gated on its OWN transaction time, exactly like the fact read it resolves: an amendment, a link
or an operator decision counts iff ``recorded_at <= known_at`` — so a read pinned before tonight's link sees
the original exactly as it was stored, and a replay pinned before a ratification sees the pending state the
system actually held (#1). The CIK set (the other-issuer screen) is IDENTITY — ``security_master.cik`` plus
the T7 CIK-change log, never as-of (the ``security_cik`` precedent: the master is identity, a CIK is stable).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

import psycopg

from domain.insider_amendments import ResolutionInputs, group_inputs

RESOLVED_TABLE = "fact_insider_txn"


def load_inputs(
    conn: psycopg.Connection,
    security_ids: Collection[UUID],
    *,
    known_at: datetime,
    tenant_id: UUID,
) -> ResolutionInputs:
    """Every 4/A record, link and decision of ``security_ids`` recorded at/before ``known_at``, plus every CIK
    each security has been known by. Five small reads (the tables hold ~one row per amendment)."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return ResolutionInputs()
    p = {"t": tenant_id, "ids": ids, "k": known_at}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tenant_id, security_id, accession, rows, accepted, recorded_at "
            "FROM insider_amendment "
            "WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) AND recorded_at <= %(k)s",
            p,
        )
        amendments = cur.fetchall()
        cur.execute(
            "SELECT id, security_id, amendment_accession, original_accessions, voided_accessions, "
            "candidate_accessions, method, disposition, status, accepted, recorded_at "
            "FROM insider_amendment_link "
            "WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) AND recorded_at <= %(k)s",
            p,
        )
        links = cur.fetchall()
        cur.execute(
            "SELECT id, security_id, amendment_accession, action, disposition, original_accessions, replaced_rows, "
            "voided_accessions, revokes, recorded_at "
            "FROM insider_amendment_decision "
            "WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) AND recorded_at <= %(k)s",
            p,
        )
        decisions = cur.fetchall()
        cur.execute(
            "SELECT id AS security_id, cik FROM security_master "
            "WHERE tenant_id = %(t)s AND id = ANY(%(ids)s) AND cik IS NOT NULL "
            "UNION SELECT security_id, prior_cik FROM security_cik_change "
            "WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) AND prior_cik IS NOT NULL "
            "UNION SELECT security_id, new_cik FROM security_cik_change "
            "WHERE tenant_id = %(t)s AND security_id = ANY(%(ids)s) AND new_cik IS NOT NULL",
            p,
        )
        ciks = [(r["security_id"], r["cik"]) for r in cur.fetchall()]
    return group_inputs(amendments, links, decisions, ciks)
