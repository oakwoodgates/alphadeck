"""First-class master IDENTITY enrichment — the standing backfill for the submissions-sourced fields.

The submissions-sourced identity columns (``sector`` / ``status`` / ``category`` + the 0028 origin
ingredients; ``exchange`` fill-if-null) are written only by ``master.enrich``, whose one production caller
is the draft path — so a row placed before a field existed, or a thesis never re-drafted, never gets the
field. This CLI productizes the remedy: resolve a scope to per-tenant ``{cik: security_id}`` maps and run
the EXISTING ``workbench.enrichment.enrich_for_ciks`` over them (per-CIK isolated, commit-per-CIK resumable,
idempotent UPDATE-in-place). Because ``master.enrich`` writes EVERY submissions-sourced column every run,
any future field added to ``parse_identity`` + the ``enrich`` UPDATE is backfilled by re-running this same
command — no bespoke script again (the field-generic checklist, ``docs/WORKBENCH_ENRICHMENT.md``).

Scopes (bare invocation = ``--baskets``); a full re-enrich of the scope every run — deliberately no
``--missing-only`` (a per-field "missing" predicate goes stale every time a field is added; a basket-wide
re-run is ~a minute at the polite rate):

- ``--thesis <id>`` — one thesis's resolved basket.
- ``--baskets`` — every (non-archived) thesis's resolved members, each under its OWN tenant (theses are
  tenant-intrinsic — the daily cron's pattern).
- ``--universe`` — every CIK in the master (the canonical ``is_primary`` row per CIK — identity is
  company-level, one submissions doc per CIK); takes ``--tenant-id``, defaulting to the deployment tenant.
  ~10.6k docs ≈ 20–30 min at the polite rate — an explicit, rare run, never ambient.

Freshness needs NO flag here: ``submissions/*`` is a mutable cache class on the R1 key-classed 12h TTL, so
under ``--live`` a doc older than 12h re-fetches and a newer one serves (``ingest/edgar/client.py``).
``--live`` is opt-in (cache-first default, the ``populate_master`` convention); offline, an uncached CIK
raises ``CacheMiss`` inside the per-CIK try -> counted ``skipped``, visible on the receipt, never a false
write. Machine-parsed identity only: no fact row, no number, no LLM, never promoted onto a member (#2/#3).

Exit codes: 0 on a normal run; 1 when a ``--live`` run enriched NOTHING while skipping names (the
``audit_identity`` scriptable-health precedent — that shape is a network/UA fault, not a quiet no-op).

    python -m pipeline.enrich_identity                       # bare = --baskets, cache-first
    python -m pipeline.enrich_identity --live                # the real backfill (needs ALPHADECK_USER_AGENT)
    python -m pipeline.enrich_identity --thesis <uuid> --live
    python -m pipeline.enrich_identity --universe --live [--tenant-id <uuid>]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from uuid import UUID

import psycopg

from db.session import connect, current_tenant_id
from ingest.edgar.client import EdgarClient
from repositories import thesis_repo
from securities import master
from workbench.enrichment import enrich_for_ciks


@dataclass
class TenantScope:
    """One tenant's resolved enrichment scope: the ``{cik: security_id}`` map ``enrich_for_ciks`` takes,
    plus the resolved members that COULDN'T key into it (no CIK-bearing master row -> no submissions doc
    to parse — reported on the receipt, never guessed)."""

    cik_to_sid: dict[str, UUID] = field(default_factory=dict)
    unenrichable: int = 0


@dataclass
class TenantReceipt:
    """The per-tenant receipt: scope size + ``enrich_for_ciks``'s outcome counts. The command's whole
    point is that "did the enrich run, and what did it do" is answerable from this — an operator-visible
    action, not an ambient side effect."""

    tenant_id: UUID
    ciks: int
    enriched: int
    skipped: int
    unenrichable: int = 0


def resolve_baskets_scope(
    conn: psycopg.Connection, *, thesis_id: UUID | None = None
) -> dict[UUID, TenantScope]:
    """Resolve ``--thesis`` / ``--baskets`` to per-tenant scopes.

    Walks each thesis's RESOLVED basket members (``security_id`` set — an unresolved placement has no
    exact member to enrich; the ``ingest_thesis`` skip), reads each id through the tenant-scoped master
    accessor (#2/#5 — ``master.get_many``, never a raw query, never a fresh fuzzy resolve), and keys the
    map on the row's CIK. A member with no CIK-bearing row under its tenant (an OpenFIGI-era ``resolve()``
    row, or an ETF sleeve — a fund-trust series has no operating-company CIK) is counted ``unenrichable``:
    there is no submissions doc to parse, and inventing one is exactly what this layer never does.
    Archived theses are excluded (the ``list_all`` default — the daily cron's walk); tenants are
    thesis-intrinsic, so each thesis contributes to its OWN tenant's map."""
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    scopes: dict[UUID, TenantScope] = {}
    for thesis in theses:
        scope = scopes.setdefault(thesis.tenant_id, TenantScope())
        sids = [m.security_id for m in thesis.basket if m.security_id is not None]
        if not sids:
            continue
        secs = master.get_many(conn, sids, tenant_id=thesis.tenant_id)
        for sid in set(sids):
            sec = secs.get(sid)
            if sec is not None and sec.cik:
                scope.cik_to_sid[sec.cik] = sec.id
            else:
                scope.unenrichable += 1
    return scopes


def resolve_universe_scope(conn: psycopg.Connection, *, tenant_id: UUID) -> dict[UUID, TenantScope]:
    """Resolve ``--universe``: every CIK in ONE tenant's master -> its canonical primary row
    (``master.all_cik_primary_ids`` — identity is company-level, so one submissions doc enriches one row
    per CIK, the row every CIK->id read resolves to). CIK-less rows (funds, OpenFIGI-era inserts) simply
    aren't in the map — nothing to parse, nothing skipped."""
    return {
        tenant_id: TenantScope(cik_to_sid=master.all_cik_primary_ids(conn, tenant_id=tenant_id))
    }


def run_enrich(
    conn: psycopg.Connection, edgar, scopes: dict[UUID, TenantScope]
) -> list[TenantReceipt]:
    """Run ``enrich_for_ciks`` per tenant over the resolved scopes; return the receipts. All semantics —
    per-CIK isolation, commit-per-CIK (resumable: a crashed run keeps its progress), the genuine-doc
    guard, idempotent UPDATE-in-place — live in ``enrich_for_ciks`` unchanged; this is a scope-resolver
    + receipt around it."""
    receipts: list[TenantReceipt] = []
    for tenant_id, scope in scopes.items():
        counts = enrich_for_ciks(conn, edgar, scope.cik_to_sid, tenant_id=tenant_id)
        receipts.append(
            TenantReceipt(
                tenant_id=tenant_id,
                ciks=len(scope.cik_to_sid),
                enriched=counts["enriched"],
                skipped=counts["skipped"],
                unenrichable=scope.unenrichable,
            )
        )
    return receipts


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Re-enrich master identity (sector/status/category + origin ingredients) from EDGAR "
        "submissions for a scope of names. Bare invocation = --baskets."
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--thesis", default=None, help="one thesis id (uuid): its resolved basket")
    scope.add_argument(
        "--baskets",
        action="store_true",
        help="every thesis's resolved members (the DEFAULT when no scope is given)",
    )
    scope.add_argument(
        "--universe",
        action="store_true",
        help="every master CIK (the canonical row per CIK; ~10k submissions docs under --live)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="allow live EDGAR fetches (submissions/* re-pull on the 12h TTL; needs "
        "ALPHADECK_USER_AGENT); else cache-only (a missing doc counts skipped)",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="--universe only: target tenant UUID; defaults to the deployment tenant "
        "($ALPHADECK_TENANT_ID / demo). Basket scopes take each thesis's own tenant.",
    )
    a = p.parse_args(argv)
    if a.tenant_id and not a.universe:
        # loud, never silently ignored — a flag that does nothing is the invisible-failure class
        p.error(
            "--tenant-id only applies to --universe (basket scopes take each thesis's own tenant)"
        )

    conn = connect()
    try:
        if a.universe:
            tenant = UUID(a.tenant_id) if a.tenant_id else current_tenant_id()
            scopes = resolve_universe_scope(conn, tenant_id=tenant)
        else:
            scopes = resolve_baskets_scope(conn, thesis_id=UUID(a.thesis) if a.thesis else None)
        edgar = EdgarClient(allow_live=a.live)
        receipts = run_enrich(conn, edgar, scopes)
    finally:
        conn.close()

    for r in receipts:
        tail = (
            f" ({r.unenrichable} resolved member(s) without a CIK-bearing master row — no "
            "submissions doc to parse)"
            if r.unenrichable
            else ""
        )
        print(
            f"tenant {r.tenant_id}: {r.ciks} CIK(s) -> {r.enriched} enriched, {r.skipped} skipped{tail}"
        )
    total_enriched = sum(r.enriched for r in receipts)
    total_skipped = sum(r.skipped for r in receipts)
    print(f"TOTAL: {total_enriched} enriched, {total_skipped} skipped")
    # The scriptable health gate (the audit_identity exit-code precedent): a LIVE run that enriched
    # NOTHING while skipping names is a network/UA/parse fault wearing a clean exit — surface it to a
    # wrapper. A cache-first run skipping uncached CIKs is the expected offline shape and stays 0.
    if a.live and total_enriched == 0 and total_skipped > 0:
        print(
            "ENRICH: --live run enriched nothing while skipping names — investigate before trusting"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
