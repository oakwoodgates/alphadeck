"""Lazy master IDENTITY enrichment (Workbench enrichment, Slice 2) — fill sector / exchange / status onto the
master rows for a set of discovered CIKs, from each CIK's EDGAR submissions JSON.

Run on the draft path BEFORE resolution (``execute_draft``: discovery → ENRICH → resolve), so the chain
reconciler's status-gate reads a fresh listing status. Machine-parsed identity, never a fact (#1/#3): it writes
only the master's descriptive columns (with an enrichment basis), never a fact_* row, never a number on a call
card. FAIL-VISIBLE per CIK — a fetch / parse / write fault logs and skips that name (its row stays un-enriched
→ abstains, the honest fallback), NEVER aborting the draft (#9). The network stays OUT of the pure resolver.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.submissions import fetch_submissions, parse_identity
from securities import master

_log = logging.getLogger("alphadeck.enrichment")


def enrich_for_ciks(
    conn: psycopg.Connection,
    edgar,
    cik_to_sid: Mapping[str, UUID],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, int]:
    """Enrich the master rows for ``cik_to_sid`` (the discovered placeable CIKs → their master ids) with
    machine-parsed identity from each CIK's submissions JSON. Per-CIK isolated (commit on success, rollback on
    fault) so one bad name never poisons the rest; idempotent (``master.enrich`` UPDATEs in place). Returns
    ``{"enriched", "skipped"}``.

    Only a GENUINE submissions doc enriches: the response must echo a top-level ``cik`` (a real submissions
    JSON always does). A missing / broken / non-submissions response is SKIPPED, never written — so a bad fetch
    can NEVER harden into a false ``inactive`` (the operator-note honesty bound; #9). ``edgar`` needs a
    ``get_json(url, cache_key)`` (the real ``EdgarClient`` or a fake).
    """
    enriched = skipped = 0
    for cik, sid in cik_to_sid.items():
        # network / cache miss — fail-visible, leave the row un-enriched (abstain), never abort the draft
        try:
            subs = fetch_submissions(edgar, cik)
        except Exception:
            _log.warning(
                "enrich: submissions fetch failed for CIK %s; left un-enriched", cik, exc_info=True
            )
            skipped += 1
            continue
        # not a real submissions doc (no top-level cik) — abstain, never write a false 'inactive'
        if not subs.get("cik"):
            skipped += 1
            continue
        # a DB fault on one row must not abort the draft — roll back just this one and skip it
        try:
            master.enrich(
                conn, sid, parse_identity(subs), source=f"submissions:CIK{cik}", tenant_id=tenant_id
            )
            conn.commit()
            enriched += 1
        except Exception:
            conn.rollback()
            _log.warning(
                "enrich: write failed for CIK %s (sid %s); left un-enriched",
                cik,
                sid,
                exc_info=True,
            )
            skipped += 1
    return {"enriched": enriched, "skipped": skipped}
