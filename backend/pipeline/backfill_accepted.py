"""Backfill ``fact_insider_txn.accepted`` (the SEC acceptance datetime — the real "disclosed" clock)
from the per-CIK EDGAR submissions JSON.

THE GAP: the acceptance capture landed with the MRVL two-clock fix (migration 0040), and the incremental
Form 4 leg skips already-stored accessions (``existing_accessions``), so every row ingested BEFORE the
capture is frozen at NULL — the value was always in the enumeration (``filings.recent.acceptanceDateTime``,
a parallel array beside ``accessionNumber``); we just discarded it. This CLI repairs that one column.

SOURCE = the per-CIK submissions JSON, NOT the forms cache (the ownership *document* carries no acceptance
datetime — the enumeration is the only source). Cost = O(distinct CIKs with a NULL-accepted row): one
submissions read per security, cache-first (``--live`` refills a stale/absent cache under the 12h TTL). An
accession NOT in the ``recent`` window (>= 1yr / 1,000 filings) stays NULL unless ``--deep`` is given; the
display/metrics fall back to ``recorded_at``/"ingested" for the residue (recall-safe #9).

THE ``--deep`` WALK (P2): ``recent`` is bounded per CIK by FILING COUNT, so the heaviest-filing names lose
acceptance coverage FIRST — and heavy filing volume means many insiders transacting, which is exactly what
the cluster detector keys on. ``--deep`` follows ``filings.files[]`` (EDGAR's pagination index), so those
older accessions resolve too. Anything still unresolvable after the walk stays NULL and VISIBLE as such,
counted in ``accessions_unresolved`` — never silently dropped (#9).

MEASURED SCOPE, on the LATEST-VERSION grain — which is the grain that matters and is much smaller than a
raw row count suggests. Coverage counted over ALL rows reads 314,798/595,376 = 52.9%, but that denominator
includes SUPERSEDED versions: an earlier backfill's correction leaves v1 NULL beside a v2 that carries the
value, and the as-of read (and therefore the Scoreboard ledger and the B2 lag metric) only ever sees v2. On
the latest-version grain the dev copy of prod already reads **314,246/316,374 = 99.3%**, with 2,128 keys
across 117 securities (96 of them basket members) still NULL. That is what ``--deep`` is for, and MEASURED
against live EDGAR 2026-09-15 it closes the basket share of it completely: 96 securities targeted, 117 older
pages read, 0 unreadable, 638 accessions resolved, 1,589 rows corrected, **0 residual NULL** — coverage
99.3% -> 99.83%, the remainder being the 21 non-basket securities. ``--deep --basket-only --live`` is the
intended one-shot.

This is an INTERACTIVE one-shot, so the client's default 12h TTL is the right dial and nothing threads a
freshness flag (``docs/DATA_SOURCES.md`` §cache-freshness — freshness is key-classed, never per-call).

MECHANISM — append-only re-version, the table's own correction discipline (the ``backfill_aff10b5_1``
precedent). ``fact_insider_txn`` carries a live ``no_update`` trigger, so the repair appends a NEW VERSION of
each corrected row: every column copied VERBATIM from the row it supersedes, ONLY ``accepted`` set from the
enumeration, ``supersedes`` linking the old row's id, and ``recorded_at`` left to the DB's ``now()`` — NEVER
backdated (transaction-time honesty; the constraint FORCES a new recorded_at, so the corrected row's
"ingested" line reflects the backfill time — consistent with the pre-existing "the ingested line shows the
latest re-ingest" behavior). ``accepted`` is a DISPLAY + metrics column only: once it is set, the Scoreboard
ledger shows the honest ``disclosed`` (public acceptance) date and the B2 disclosure-lag metric
(``scoreboard/provenance.py``) measures against it. It does NOT change the as-of read's visibility — that
transaction-time gate keys on ``recorded_at`` for every fact table (accepted is never the gate).

The 0037 lesson holds: a batch's appends share one transaction-time ``now()``, so the natural-key constraint
must carry ``security_id`` — ``--execute`` FAILS LOUD up front on an unmigrated schema.

RULES:
- **NULL-only.** Targets natural keys whose LATEST version has ``accepted IS NULL``. A captured (non-NULL)
  value is NEVER rewritten — a re-parse disagreement is ``--verify``'s job to REPORT, never this tool's to fix.
- **Unresolved stays NULL (#9).** An accession absent from ``recent`` (older than the window), a security with
  no CIK, or an uncached submissions (``--no-live``) leaves its rows NULL — counted, never dropped.
- **Idempotent (count-the-table).** A corrected key's latest version is non-NULL, so a re-run targets nothing
  and appends ZERO rows — assert ``count(*)`` before/after, not just the read.
- **Cache-first.** Default reads the submissions cache only (``CacheMiss`` on a miss -> scope left NULL);
  ``--live`` (needs ALPHADECK_USER_AGENT) refills stale/absent caches under the client's 12h TTL.
- **Dry-run by default.** ``--execute`` writes; without it the full read+resolve runs and reports what WOULD
  change, touching nothing.

    python -m pipeline.backfill_accepted                       # dry-run against $DATABASE_URL (cache-only)
    python -m pipeline.backfill_accepted --live                # dry-run, refilling stale submissions caches
    python -m pipeline.backfill_accepted --execute --live      # write the corrections
    python -m pipeline.backfill_accepted --verify --live       # independent cross-check
    python -m pipeline.backfill_accepted --deep --basket-only --live            # the P2 dry-run
    python -m pipeline.backfill_accepted --deep --basket-only --live --execute  # ...and the write
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from db.bitemporal import append_fact
from db.session import database_url
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.edgar.submissions import (
    acceptance_times,
    acceptance_times_deep,
    fetch_submissions,
    parse_acceptance,
)

# The latest-version view of the table — the SAME grain the shared as-of read dedups on
# (db.bitemporal._FACT_IDENTITY['fact_insider_txn'] within its (tenant, security) scope), so "latest is
# NULL" here == "the as-of read would return NULL" there. Ties on recorded_at break by id DESC, mirroring
# _as_of exactly. Since 0037 the natural-key constraint carries this same per-security grain.
_LATEST = (
    "SELECT DISTINCT ON (tenant_id, security_id, accession, insider_name, valid_from, txn_seq) * "
    "FROM fact_insider_txn {where} "
    "ORDER BY tenant_id, security_id, accession, insider_name, valid_from, txn_seq, "
    "recorded_at DESC, id DESC"
)

# Columns NOT copied onto a correction row: id (new version -> new id), recorded_at (DB now(), never
# backdated — the constraint forces a distinct one), supersedes (set to the corrected row's id), accepted
# (the correction itself). Everything else — including any column added later — copies verbatim, so the ONLY
# delta between a row and its correction is the acceptance datetime.
_NOT_COPIED = frozenset({"id", "recorded_at", "supersedes", "accepted"})


@dataclass
class BackfillResult:
    """One run's full accounting — printed verbatim so the operator (and the verification gates) can count
    the table, not just trust the read."""

    executed: bool
    table_rows_before: int = 0
    table_rows_after: int = 0
    scopes_targeted: int = 0  # distinct (tenant, security) with >= 1 latest-version-NULL row
    scopes_no_cik: int = 0  # security has no CIK -> its rows stay NULL
    scopes_no_submissions: int = 0  # uncached (--no-live) or fetch failed -> rows stay NULL
    accessions_resolved: int = (
        0  # distinct accessions found in submissions with a parseable acceptance
    )
    accessions_unresolved: int = (
        0  # distinct accessions NOT in the recent window (deferred --deep) -> NULL
    )
    rows_corrected: int = 0  # rows NULL -> accepted set
    rows_residual_null: int = (
        0  # rows left NULL (no CIK / no submissions / accession out of window)
    )
    # --- the --deep walk (P2) ---------------------------------------------------------------------
    deep: bool = False  # whether filings.files[] was paginated at all
    pages_read: int = 0  # older submissions pages successfully read
    pages_failed: int = (
        0  # older pages unreadable (uncached / fetch failed) -> their rows stay NULL
    )
    # --- coverage, the operator-facing number this slice moves -------------------------------------
    # Latest-version rows carrying a non-NULL `accepted`, over the whole table, measured before and after
    # the run. The Scoreboard ledger's "disclosed" date and the B2 disclosure-lag metric read that column
    # (`scoreboard/provenance.py`: COALESCE(accepted, recorded_at)), so this IS the shipped surface.
    coverage_keys_before: int = 0
    coverage_nonnull_before: int = 0
    coverage_keys_after: int = 0
    coverage_nonnull_after: int = 0


@dataclass
class VerifyResult:
    """The independent cross-check: re-read each security's submissions and compare the stored ``accepted``
    against the enumeration. ``mismatches`` non-empty is the STOP signal (exit 1)."""

    keys_total: int = 0  # latest-version natural keys seen
    keys_stored_nonnull: int = 0  # of those, carrying a stored acceptance
    keys_compared: int = 0  # stored non-NULL AND resolvable in submissions -> actually compared
    keys_no_submissions: int = 0  # security's submissions uncached / unfetchable
    keys_null_consistent: int = (
        0  # stored NULL, accession not in the recent window (consistent unknown)
    )
    keys_null_but_resolvable: int = (
        0  # stored NULL, submissions HAS it (0 after a clean --execute run)
    )
    mismatches: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (accession, insider_name, stored, resolved) — capped at print time, full count always reported


def _redact(url: str) -> str:
    """The DSN with its password masked — printed on every run so the target is explicit."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


def _count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_insider_txn")
        return cur.fetchone()["n"]


def _cik_for(conn: psycopg.Connection, tenant_id: UUID, security_id: UUID) -> str | None:
    """The security's canonical CIK from the master — the submissions JSON is keyed by it (the same CIK
    the Form 4 leg fetches). NULL/missing -> None (the scope's rows stay NULL, #9)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cik FROM security_master WHERE tenant_id = %s AND id = %s",
            (tenant_id, security_id),
        )
        row = cur.fetchone()
    return (row["cik"] if row else None) or None


def _coverage(conn: psycopg.Connection) -> tuple[int, int]:
    """``(latest-version keys, of those carrying a non-NULL accepted)`` across the whole table — the
    operator-facing coverage number, measured on the SAME latest-version grain the as-of read dedups on
    (so it is what the Scoreboard ledger and the B2 lag metric actually see)."""
    q = (
        "SELECT count(*) AS keys, count(accepted) AS nonnull FROM ("
        + _LATEST.format(where="")
        + ") latest"
    )
    with conn.cursor() as cur:
        cur.execute(q)
        r = cur.fetchone()
        return r["keys"], r["nonnull"]


def _target_scopes(
    conn: psycopg.Connection, *, basket_only: bool = False
) -> list[tuple[UUID, UUID]]:
    """Distinct (tenant, security) with at least one latest-version-NULL ``accepted`` row — the worklist,
    scoped per security because the source (submissions JSON) is per-CIK.

    ``basket_only`` narrows it to securities placed in some thesis's basket (archived theses included — an
    archived thesis's history is still read by the Scoreboard). MEASURED: 117 securities have a
    latest-version NULL ``accepted`` and 96 are basket members, so this is the cost thread applied to the
    deep walk — the expensive pagination is spent on the names the operator's surfaces actually show.
    NARROWING ONLY: an excluded security's rows are untouched and stay NULL and visible, never dropped (#9).
    """
    where = ""
    if basket_only:
        where = "WHERE security_id IN (SELECT security_id FROM basket_member WHERE security_id IS NOT NULL)"
    q = (
        "SELECT DISTINCT tenant_id, security_id FROM (" + _LATEST.format(where=where) + ") latest "
        "WHERE accepted IS NULL ORDER BY tenant_id, security_id"
    )
    with conn.cursor() as cur:
        cur.execute(q)
        return [(r["tenant_id"], r["security_id"]) for r in cur.fetchall()]


def _scope_latest_rows(
    conn: psycopg.Connection, tenant_id: UUID, security_id: UUID, *, null_only: bool
) -> list[dict]:
    """The latest-version rows for one (tenant, security) scope — ``null_only`` filters to the NULL-accepted
    correction targets (backfill) or returns all (verify)."""
    where = "WHERE tenant_id = %s AND security_id = %s"
    q = "SELECT * FROM (" + _LATEST.format(where=where) + ") latest"
    if null_only:
        q += " WHERE accepted IS NULL"
    with conn.cursor() as cur:
        cur.execute(q, (tenant_id, security_id))
        return cur.fetchall()


def _require_security_scoped_natural_key(conn: psycopg.Connection) -> None:
    """FAIL LOUD, before the first write, unless ``fact_insider_txn_natural_key`` carries ``security_id``
    (migration 0037). One batch's corrections share a constant ``now()``; two same-instant corrections of one
    filing held under two securities collided on the pre-0037 constraint and aborted the prod run mid-batch
    (2026-08-17). Refusing up front turns that crash into an instruction."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_constraint c "
            "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
            "WHERE c.conname = 'fact_insider_txn_natural_key' "
            "AND c.conrelid = 'fact_insider_txn'::regclass"
        )
        cols = {r["attname"] for r in cur.fetchall()}
    if "security_id" not in cols:
        raise SystemExit(
            "fact_insider_txn_natural_key lacks security_id (migration 0037 not applied) — two "
            "same-instant corrections of one filing under two securities would collide mid-batch "
            "(the 2026-08-17 prod abort). Run `python -m db.migrate` first; refusing."
        )


def _resolved_acceptances(
    client: EdgarClient, cik: str, *, deep: bool = False
) -> tuple[dict[str, datetime], int, int] | None:
    """The security's ``{accession: accepted datetime}`` from its submissions JSON, or ``None`` if the
    submissions is uncached / unfetchable (the scope is then left NULL — #9). Only parseable acceptance
    values survive (an unparseable one is dropped -> the accession reads unresolved).

    ``deep`` also paginates ``filings.files[]`` (``acceptance_times_deep``), reaching accessions older than
    the ``recent`` window. Returns ``(map, pages_read, pages_failed)``; the page counts are 0 without
    ``deep`` and for a company whose whole tape fits in ``recent``.
    """
    try:
        subs = fetch_submissions(client, cik)
        if deep:
            raw_map, pages_read, pages_failed = acceptance_times_deep(client, subs)
        else:
            raw_map, pages_read, pages_failed = acceptance_times(subs), 0, 0
    except CacheMiss:
        return None
    except (
        Exception
    ) as e:  # noqa: BLE001 — a live fetch failure (httpx / transient) leaves the scope NULL
        if _is_http_error(e):
            return None
        raise
    resolved = {
        acc: dt for acc, raw in raw_map.items() if (dt := parse_acceptance(raw)) is not None
    }
    return resolved, pages_read, pages_failed


def _is_http_error(e: Exception) -> bool:
    """Is ``e`` a network fetch failure (tolerate: the scope is left NULL) rather than a systemic bug?"""
    try:
        import httpx  # lazy, mirroring the clients — the package imports without it
    except ImportError:  # pragma: no cover — with httpx absent no httpx error can have been raised
        return False
    return isinstance(e, httpx.HTTPError)


def run_backfill(
    conn: psycopg.Connection,
    *,
    client: EdgarClient,
    execute: bool,
    limit: int | None = None,
    deep: bool = False,
    basket_only: bool = False,
    log=print,
) -> BackfillResult:
    """The repair pass (see the module docstring for the rules). ``execute=False`` performs the full read +
    resolve and reports what WOULD change, appending nothing. ``deep`` paginates ``filings.files[]`` so
    accessions older than the ``recent`` window resolve too; ``basket_only`` narrows the worklist to
    securities that are placed in a basket (the cost thread — see ``_target_scopes``)."""
    if execute:  # write-path precondition (0037); a dry-run reads fine on any schema
        _require_security_scoped_natural_key(conn)
    res = BackfillResult(executed=execute, deep=deep)
    res.table_rows_before = _count(conn)
    res.coverage_keys_before, res.coverage_nonnull_before = _coverage(conn)
    scopes = _target_scopes(conn, basket_only=basket_only)
    if limit is not None:
        scopes = scopes[:limit]
    res.scopes_targeted = len(scopes)
    scope_note = " (basket members only)" if basket_only else ""
    log(f"targets: {len(scopes)} securities with a latest-version NULL accepted{scope_note}")
    if deep:
        log("mode   : DEEP — paginating filings.files[] beyond the recent window")

    resolved_accessions: set[str] = set()
    unresolved_accessions: set[str] = set()
    for i, (tenant_id, security_id) in enumerate(scopes, start=1):
        rows = _scope_latest_rows(conn, tenant_id, security_id, null_only=True)
        if not rows:  # corrected by an earlier scope/run in the meantime — nothing to do
            continue
        cik = _cik_for(conn, tenant_id, security_id)
        if not cik:
            res.scopes_no_cik += 1
            res.rows_residual_null += len(rows)
            continue
        resolution = _resolved_acceptances(client, cik, deep=deep)
        if resolution is None:
            res.scopes_no_submissions += 1
            res.rows_residual_null += len(rows)
            log(f"  warn: security {security_id} (CIK {cik}) submissions unavailable — left NULL")
            continue
        amap, pages_read, pages_failed = resolution
        res.pages_read += pages_read
        res.pages_failed += pages_failed
        if pages_failed:  # loud: an unread page is coverage this run could not reach (#9)
            log(
                f"  warn: security {security_id} (CIK {cik}) {pages_failed} older submissions "
                "page(s) unreadable — their accessions stay NULL"
            )
        for row in rows:
            dt = amap.get(row["accession"])
            if dt is None:  # accession out of the recent window (deferred --deep) — stays NULL (#9)
                unresolved_accessions.add(row["accession"])
                res.rows_residual_null += 1
                continue
            resolved_accessions.add(row["accession"])
            res.rows_corrected += 1
            if execute:
                values = {k: v for k, v in row.items() if k not in _NOT_COPIED}
                values["accepted"] = dt
                values["supersedes"] = row["id"]  # provenance: which version this corrects
                append_fact(conn, "fact_insider_txn", values)
        if execute:
            conn.commit()  # per-scope: a crash keeps progress; the re-run resumes at remaining NULLs
        if i % 200 == 0 or i == len(scopes):
            log(f"  ... {i}/{len(scopes)} securities checked")
    res.accessions_resolved = len(resolved_accessions)
    res.accessions_unresolved = len(unresolved_accessions - resolved_accessions)
    res.table_rows_after = _count(conn)
    res.coverage_keys_after, res.coverage_nonnull_after = _coverage(conn)
    return res


def run_verify(
    conn: psycopg.Connection,
    *,
    client: EdgarClient,
    deep: bool = False,
    log=print,
) -> VerifyResult:
    """The independent cross-check: for EVERY latest-version key, re-resolve the acceptance from the
    security's submissions and compare the stored value against it. Reads only — writes nothing. A stored
    non-NULL value that disagrees with the enumeration is a MISMATCH.

    ``deep`` must MATCH the backfill run being verified. Verifying a ``--deep`` write WITHOUT it would
    re-classify every deep-resolved key as ``<unresolved>`` and report a wall of false mismatches; the CLI
    threads the same flag to both paths so they cannot diverge by accident."""
    res = VerifyResult()
    # every distinct scope (not just NULL ones) — verify covers already-captured rows too
    q = "SELECT DISTINCT tenant_id, security_id FROM fact_insider_txn ORDER BY tenant_id, security_id"
    with conn.cursor() as cur:
        cur.execute(q)
        scopes = [(r["tenant_id"], r["security_id"]) for r in cur.fetchall()]
    log(f"verify: {len(scopes)} securities")
    for i, (tenant_id, security_id) in enumerate(scopes, start=1):
        rows = _scope_latest_rows(conn, tenant_id, security_id, null_only=False)
        res.keys_total += len(rows)
        cik = _cik_for(conn, tenant_id, security_id)
        resolution = _resolved_acceptances(client, cik, deep=deep) if cik else None
        if resolution is None:
            res.keys_no_submissions += len(rows)
            continue
        amap = resolution[0]
        for r in rows:
            resolved = amap.get(r["accession"])
            stored = r["accepted"]
            if stored is None:
                if resolved is None:
                    res.keys_null_consistent += 1
                else:
                    res.keys_null_but_resolvable += 1
                continue
            res.keys_stored_nonnull += 1
            if (
                resolved is None
            ):  # stored a value the current enumeration no longer resolves — report it
                res.mismatches.append(
                    (r["accession"], r["insider_name"] or "?", str(stored), "<unresolved>")
                )
                continue
            res.keys_compared += 1
            if stored != resolved:
                res.mismatches.append(
                    (r["accession"], r["insider_name"] or "?", str(stored), str(resolved))
                )
        if i % 200 == 0 or i == len(scopes):
            log(f"  ... {i}/{len(scopes)} securities verified")
    return res


def _pct(nonnull: int, keys: int) -> str:
    return f"{nonnull}/{keys} ({100.0 * nonnull / keys:.1f}%)" if keys else "0/0 (n/a)"


def _print_backfill(res: BackfillResult) -> None:
    mode = "EXECUTE" if res.executed else "DRY-RUN (nothing written)"
    depth = "DEEP (filings.files[] paginated)" if res.deep else "recent window only"
    print(f"\n=== backfill accepted — {mode} · {depth} ===")
    print(f"  table rows before : {res.table_rows_before}")
    print(f"  table rows after  : {res.table_rows_after}")
    print(f"  rows appended     : {res.table_rows_after - res.table_rows_before}")
    print(f"  securities targeted        : {res.scopes_targeted}")
    print(f"  securities without a CIK   : {res.scopes_no_cik} (rows stay NULL)")
    print(
        f"  securities no submissions  : {res.scopes_no_submissions} (uncached/unfetchable, stay NULL)"
    )
    if res.deep:
        print(f"  older pages read           : {res.pages_read}")
        print(f"  older pages unreadable     : {res.pages_failed} (their accessions stay NULL)")
    print(f"  accessions resolved        : {res.accessions_resolved}")
    residual_note = (
        "still unresolved after the deep walk — NULL and visible, never dropped (#9)"
        if res.deep
        else "out of the recent window — rerun with --deep"
    )
    print(f"  accessions unresolved      : {res.accessions_unresolved} ({residual_note})")
    print(f"  rows NULL -> accepted      : {res.rows_corrected}")
    print(f"  rows residual NULL         : {res.rows_residual_null}")
    # COVERAGE — the shipped-surface number this slice moves (the Scoreboard ledger's "disclosed" date and
    # the B2 disclosure-lag metric read `accepted`). On a dry-run before == after by construction.
    print(
        f"  coverage before            : {_pct(res.coverage_nonnull_before, res.coverage_keys_before)}"
    )
    print(
        f"  coverage after             : {_pct(res.coverage_nonnull_after, res.coverage_keys_after)}"
    )


def _print_verify(res: VerifyResult) -> None:
    print("\n=== verify accepted — independent submissions cross-check (read-only) ===")
    print(f"  latest-version keys        : {res.keys_total}")
    print(f"  stored non-NULL            : {res.keys_stored_nonnull}")
    print(f"  compared (resolved)        : {res.keys_compared}")
    print(f"  no submissions             : {res.keys_no_submissions}")
    print(f"  NULL, not in window        : {res.keys_null_consistent} (consistent unknown)")
    print(f"  NULL but resolvable        : {res.keys_null_but_resolvable} (0 expected after a run)")
    print(f"  MISMATCHES                 : {len(res.mismatches)}")
    for acc, who, stored, resolved in res.mismatches[:20]:
        print(f"    MISMATCH {acc} ({who}): stored={stored} resolved={resolved}")
    if len(res.mismatches) > 20:
        print(f"    ... and {len(res.mismatches) - 20} more")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill fact_insider_txn.accepted from the per-CIK EDGAR submissions JSON "
        "(append-only re-version; NULL-only; dry-run by default)."
    )
    p.add_argument("--execute", action="store_true", help="write corrections (default: dry-run)")
    p.add_argument(
        "--verify",
        action="store_true",
        help="independent submissions cross-check instead of the backfill (read-only; exit 1 on any "
        "mismatch)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="refill stale/absent submissions caches from EDGAR (needs ALPHADECK_USER_AGENT); default "
        "is cache-only (an uncached security is left NULL)",
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="explicit target DSN (default: $DATABASE_URL, else the dev default)",
    )
    p.add_argument(
        "--limit", type=int, default=None, help="cap the securities processed (spot runs)"
    )
    p.add_argument(
        "--deep",
        action="store_true",
        help="also paginate filings.files[] — the accessions older than EDGAR's recent window "
        "(>= 1yr / 1,000 filings). Costs one extra cached fetch per older page; use with --live",
    )
    p.add_argument(
        "--basket-only",
        action="store_true",
        help="restrict the worklist to securities placed in some thesis's basket (the deep walk's "
        "intended scope: 96 of the 117 securities with a latest-version NULL accepted are basket members)",
    )
    args = p.parse_args(argv)

    url = args.database_url or database_url()
    print(f"target DB : {_redact(url)}")
    print(f"mode      : {'LIVE (refill stale caches)' if args.live else 'cache-only'}")
    print(
        f"depth     : {'DEEP (filings.files[] paginated)' if args.deep else 'recent window only'}"
    )
    client = EdgarClient(allow_live=args.live)
    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")
        if args.verify:
            # the SAME depth as the backfill it verifies — see run_verify's docstring
            vres = run_verify(conn, client=client, deep=args.deep)
            _print_verify(vres)
            if vres.mismatches:
                raise SystemExit(
                    1
                )  # the STOP signal — a stored value the enumeration disagrees with
        else:
            bres = run_backfill(
                conn,
                client=client,
                execute=args.execute,
                limit=args.limit,
                deep=args.deep,
                basket_only=args.basket_only,
            )
            _print_backfill(bres)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
