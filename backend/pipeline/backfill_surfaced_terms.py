"""Backfill per-member ``surfaced_terms`` — freeze each basket member's discovery-term provenance.

``basket_member.surfaced_terms`` (migration 0029) records WHICH discovery terms surfaced a name, frozen
when it enters the Basket (the promote freeze pass). Members promoted before the column existed hold the
honest default ``{}`` — this CLI productizes the remedy: per thesis, run the SAME deterministic EFTS
discovery a draft runs (``workbench.discovery.run_discovery`` over the thesis's persisted term set — no
LLM, no number, #3), then freeze each resolvable member's value as ``sorted(universe.filers[cik].keywords)``
when its CIK surfaced (identical to draft-time display), else ``{}`` (ETF sleeves / hand-added / unmatched —
honest, counted). Writes go through the narrow ``thesis_repo.set_surfaced_terms`` (UPDATE-in-place, no
ordinal churn), committed per thesis (resumable).

**TIMING CAVEAT (time-sensitive, run-before-refining):** the only recoverable record of an existing
basket's "original" terms is their match against the CURRENT term set. Run this on **prod while it still
holds the 45-term set**, BEFORE refining prod's terms or running a prod re-scope — once the terms are
refined, the original is unrecoverable (the spec's sequencing note). Dev is already on the 6-term set;
its backfill freezes those, accepted.

Scopes (bare invocation = ``--baskets``):

- ``--thesis <id>`` — one thesis.
- ``--baskets`` — every (non-archived) thesis, each under its OWN tenant (theses are tenant-intrinsic —
  the daily cron's pattern).

**Freeze protection (only-fills-empty):** by default a member with a stored NON-EMPTY value is KEPT
untouched — the stored value is a frozen original, and an unconditional rewrite would destroy it (the
deliberate divergence from ``enrich_identity``'s full re-enrich). ``--overwrite`` is the explicit
re-freeze: every resolvable member is re-frozen to the current computed value (including ``{}`` for a
member whose CIK no longer surfaces) — the ONLY correction path, since the promote freeze pass makes
stored-wins a server property. Idempotent either way: a re-run writes the same values (or keeps), and
the row count never changes (UPDATE-in-place).

**Degraded-coverage refusal (freeze-specific):** if the discovery run's ``coverage.failed_terms`` is
non-empty, the run enumerated LESS than the term set asks — freezing under it would under-match originals
FOREVER, so the thesis is REFUSED (no write, counted, exit 1). ``--force`` overrides for a deliberate
partial freeze. (``DiscoveryDegraded`` — a run that failed past the tolerance — already hard-fails.)

**Cost is the operator's to spend, never ambient:** a thesis with NOTHING eligible to freeze (no term
set produced; no member with a CIK-bearing master row; or every candidate already frozen and no
``--overwrite``) is skipped WITHOUT an EFTS run — visible on the receipt, zero API spend.

**Freshness:** ``--live`` gates ``EdgarClient(allow_live=...)`` (needs ``ALPHADECK_USER_AGENT``);
cache-first is the offline default — an uncached EFTS page fails its term into ``coverage.failed_terms``
(or ``DiscoveryDegraded``), which the refusal above surfaces: never a silent partial freeze.

Exit codes: 0 on a normal run; 1 when any thesis was refused for coverage, or a ``--live`` run froze
NOTHING while eligible members existed (the ``enrich_identity`` scriptable-health precedent — that shape
is a network/UA fault, not a quiet no-op).

    python -m pipeline.backfill_surfaced_terms                    # bare = --baskets, cache-first
    python -m pipeline.backfill_surfaced_terms --live             # the real backfill (needs ALPHADECK_USER_AGENT)
    python -m pipeline.backfill_surfaced_terms --thesis <uuid> --live
    python -m pipeline.backfill_surfaced_terms --thesis <uuid> --live --overwrite   # the explicit re-freeze
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from domain.thesis import Thesis
from ingest.edgar.client import EdgarClient
from repositories import thesis_repo
from securities import master
from workbench.discovery import DiscoveryUnavailable, run_discovery


@dataclass
class ThesisReceipt:
    """One thesis's backfill outcome: the member buckets (frozen / kept / unmatched / no-CIK) or the
    skip/refusal reason. The command's whole point is that "did the freeze run, and what did it write"
    is answerable from this — an operator-visible action, not an ambient side effect."""

    thesis_id: UUID
    tenant_id: UUID
    name: str
    members: int = 0
    frozen: int = 0  # written with the freshly computed non-empty terms
    kept: int = 0  # stored non-empty, no --overwrite -> untouched (the frozen original wins)
    unmatched: int = 0  # CIK resolvable but did not surface this run -> {} (honest)
    no_cik: int = (
        0  # no security_id, or no CIK-bearing master row -> nothing to match, never guessed
    )
    eligible: int = 0  # members a write COULD have frozen (drives the --live exit gate)
    skipped: str | None = (
        None  # "no term set" / "nothing eligible" / "coverage: ..." -> no EFTS run/write
    )
    refused: bool = False  # coverage refusal (drives exit 1)


def _member_ciks(conn: psycopg.Connection, thesis: Thesis) -> dict[UUID, str | None]:
    """Resolve each distinct member ``security_id`` -> its master row's CIK (``None`` = no CIK-bearing row
    under this thesis's tenant — an ETF sleeve / OpenFIGI-era row; nothing to match, never guessed). Reads
    through the tenant-scoped master accessor (#2/#5 — never a raw query, never a fresh fuzzy resolve).
    """
    tenant = thesis.tenant_id or DEFAULT_TENANT_ID
    sids = {m.security_id for m in thesis.basket if m.security_id is not None}
    if not sids:
        return {}
    secs = master.get_many(conn, sids, tenant_id=tenant)
    return {sid: (secs[sid].cik if sid in secs and secs[sid].cik else None) for sid in sids}


def backfill_thesis(
    conn: psycopg.Connection,
    edgar,
    thesis: Thesis,
    *,
    overwrite: bool = False,
    force: bool = False,
) -> ThesisReceipt:
    """Freeze one thesis's members. Runs discovery AT MOST once (and not at all when nothing is eligible —
    cost is the operator's to spend); classifies every member into the receipt buckets; writes only through
    ``set_surfaced_terms``. The caller owns the transaction (commit per thesis for resumability)."""
    r = ThesisReceipt(
        thesis_id=thesis.id,
        tenant_id=thesis.tenant_id or DEFAULT_TENANT_ID,
        name=thesis.name,
        members=len(thesis.basket),
    )
    ciks = _member_ciks(conn, thesis)

    # Classify who COULD be written: a member keys the UPDATE by security_id and matches the universe by
    # CIK, so no sid / no CIK-bearing row -> no_cik (visible, never guessed). A stored NON-EMPTY value is
    # a frozen original: kept unless --overwrite (Q2 — only-fills-empty by default).
    candidates: list[tuple[UUID, list[str], str]] = []  # (security_id, stored terms, CIK)
    for m in thesis.basket:
        cik = ciks.get(m.security_id) if m.security_id else None
        if cik is None or m.security_id is None:
            r.no_cik += 1
        else:
            candidates.append((m.security_id, m.surfaced_terms, cik))
    r.eligible = sum(1 for _, stored, _cik in candidates if overwrite or not stored)

    if not thesis.term_set:
        # Not-ready, not an empty theme (the DiscoveryNoTerms state) — skipped VISIBLY, no EFTS run.
        r.skipped = "no term set"
        r.kept = sum(1 for _, stored, _cik in candidates if stored)
        return r
    if r.eligible == 0:
        # Nothing a write could touch (all frozen already, or nothing resolvable) — zero API spend.
        r.skipped = "nothing eligible"
        r.kept = sum(1 for _, stored, _cik in candidates if stored)
        return r

    try:
        universe = run_discovery(
            conn, edgar, thesis.term_set, tenant_id=thesis.tenant_id or DEFAULT_TENANT_ID
        )
    except DiscoveryUnavailable as exc:
        # Discovery could not enumerate a trustworthy universe (degraded pages / nothing placeable). Freezing
        # UNDER a broken run would record under-matched originals FOREVER, so REFUSE this thesis (no write)
        # and CONTINUE — one bad thesis never crashes the whole backfill (per-thesis commit + refusal, exit 1).
        # On dev this is the copied cache lacking a thesis's terms (cache-first, live off); on prod run --live.
        r.skipped = f"discovery unavailable: {exc}"
        r.refused = True
        return r
    if universe.coverage is not None and universe.coverage.failed_terms and not force:
        # A partial enumeration would freeze UNDER-MATCHED originals forever — refuse the whole thesis
        # (no write), surface it, exit 1. --force is the deliberate override.
        r.skipped = (
            f"coverage: {len(universe.coverage.failed_terms)} failed term(s) "
            f"({', '.join(universe.coverage.failed_terms[:5])}) — refusing to freeze a partial match; "
            "--force overrides"
        )
        r.refused = True
        return r

    writes: dict[UUID, list[str]] = {}
    for sid, stored, cik in candidates:
        filer = universe.filers.get(cik)
        computed = sorted(filer.keywords) if filer is not None else []
        if stored and not overwrite:
            r.kept += 1  # the frozen original wins (Q2)
        elif computed:
            writes[sid] = computed
            r.frozen += 1
        else:
            # CIK resolvable but did not surface under the current terms -> {} (honest). Under
            # --overwrite the {} IS written (the explicit re-freeze); by default the stored {} already
            # says it, so there is nothing to write.
            if overwrite:
                writes[sid] = []
            r.unmatched += 1
    if writes:
        thesis_repo.set_surfaced_terms(conn, thesis.id, writes)
    return r


def run_backfill(
    conn: psycopg.Connection,
    edgar,
    *,
    thesis_id: UUID | None = None,
    overwrite: bool = False,
    force: bool = False,
) -> list[ThesisReceipt]:
    """Resolve the scope (one thesis, or every non-archived thesis — the ``list_all`` default, each under
    its own tenant), backfill each, COMMIT PER THESIS (a crashed run keeps its progress), return the
    receipts."""
    if thesis_id is not None:
        one = thesis_repo.get(conn, thesis_id)
        if one is None:
            raise LookupError(f"thesis {thesis_id} not found")
        theses = [one]
    else:
        theses = thesis_repo.list_all(conn)
    receipts: list[ThesisReceipt] = []
    for thesis in theses:
        r = backfill_thesis(conn, edgar, thesis, overwrite=overwrite, force=force)
        conn.commit()
        receipts.append(r)
    return receipts


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Freeze per-member surfaced_terms (discovery-term provenance) for existing baskets. "
        "Bare invocation = --baskets. TIMING: run on prod BEFORE refining prod's terms — the original "
        "match is unrecoverable once the term set changes."
    )
    scope = p.add_mutually_exclusive_group()
    scope.add_argument("--thesis", default=None, help="one thesis id (uuid)")
    scope.add_argument(
        "--baskets",
        action="store_true",
        help="every non-archived thesis (the DEFAULT when no scope is given)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="allow live EDGAR fetches (needs ALPHADECK_USER_AGENT); else cache-only (an uncached page "
        "fails its term into the coverage refusal — never a silent partial freeze)",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="the explicit re-freeze: rewrite EVERY resolvable member from the current run (default "
        "only-fills-empty — a stored non-empty value is a frozen original and is kept)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="freeze despite degraded coverage (failed terms) — a deliberate partial freeze",
    )
    a = p.parse_args(argv)

    conn = connect()
    try:
        edgar = EdgarClient(allow_live=a.live)
        receipts = run_backfill(
            conn,
            edgar,
            thesis_id=UUID(a.thesis) if a.thesis else None,
            overwrite=a.overwrite,
            force=a.force,
        )
    finally:
        conn.close()

    for r in receipts:
        head = f"thesis {r.name!r} ({r.thesis_id}) [tenant {r.tenant_id}]: {r.members} member(s)"
        if r.refused:
            print(f"{head} -> REFUSED ({r.skipped})")
        elif r.skipped:
            print(
                f"{head} -> skipped ({r.skipped}); {r.kept} kept, "
                f"{r.members - r.kept - r.no_cik} untouched, {r.no_cik} no-CIK"
            )
        else:
            print(
                f"{head} -> {r.frozen} frozen, {r.kept} kept, {r.unmatched} unmatched, "
                f"{r.no_cik} no-CIK"
            )
    total_frozen = sum(r.frozen for r in receipts)
    total_eligible = sum(r.eligible for r in receipts if not r.refused and r.skipped is None)
    refused = sum(1 for r in receipts if r.refused)
    print(f"TOTAL: {total_frozen} frozen across {len(receipts)} thesis(es), {refused} refused")

    if refused:
        # The freeze-specific gate: a refusal means an under-enumerated run tried to become a frozen
        # original — surface it to a wrapper, never a clean exit.
        raise SystemExit(1)
    if a.live and total_frozen == 0 and total_eligible > 0:
        # The enrich_identity scriptable-health precedent: a LIVE run that froze NOTHING while eligible
        # members existed is a network/UA/term fault wearing a clean exit.
        print(
            "BACKFILL: --live run froze nothing while eligible members existed — investigate before trusting"
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
