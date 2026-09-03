"""Repair CLI (B2): delete byte-identical bitemporal re-versions — the seed pile-up.

THE GAP: before #314, the demo seed re-ran at every backend boot and re-appended every fixture fact
(``pipeline.seed`` had no "already stored" guard). Each boot's re-append is a new VERSION of the same
logical fact whose PAYLOAD is byte-identical to the version immediately before it — the as-of read
(``db.bitemporal._as_of``) already dedups to the latest version per natural key, so the duplicates are
invisible to every read, but the table keeps growing underneath: ``fact_price_eod`` measured at 516,828
rows for ~193k distinct bars on prod (2026-09-03), ~304k of them identical later copies (UNH alone:
87,297 rows for 561 bars). This tool removes exactly those redundant copies, nothing else.

THE RULE (non-negotiable — what makes the delete unobservable): within each logical fact — partition by
``(tenant_id, <scope column>, <natural key columns>)``, ordered by ``(recorded_at, id)`` — a row whose
PAYLOAD (every column except ``id`` and ``recorded_at``) is identical to the row IMMEDIATELY BEFORE it is
a redundant copy. **Keep the EARLIEST version of every identical run; delete the later copies.** A version
that differs from its predecessor in ANY payload column (a genuine restatement — the 0037/0040 insider
backfills, an EDGAR correction) is NEVER touched, because it is never flagged as a duplicate of its
predecessor in the first place.

WHY EARLIEST-SURVIVES, NOT LATEST: an as-of read pinned at ANY ``known_at`` returns the LATEST version
whose ``recorded_at <= known_at``. If two adjacent versions carry the same payload, a read pinned between
them already saw the earlier one's values — deleting the LATER duplicate changes nothing any read could
ever observe. Deleting the EARLIER one instead would make a read pinned strictly between the two versions
(``recorded_at`` of v1 <= known_at < recorded_at of v2) return NOTHING where it used to return v1's
values — an observable regression. So earliest survives, always.

SCOPE: only tables in ``db.bitemporal._FACT_IDENTITY`` (the SAME whitelist ``_as_of`` / ``fact_exists``
render from — injection-safe, and the only tables this bitemporal discipline applies to). The scope
column (``security_id`` vs ``thesis_id``) and the payload column list are both DERIVED per table from
``information_schema.columns`` at runtime, never hardcoded — so a schema change (a new column, a new
scoped fact table) is picked up automatically rather than silently going stale.

MECHANISM: ``ROW(payload_cols) IS NOT DISTINCT FROM lag(ROW(payload_cols)) OVER (PARTITION BY ... ORDER
BY recorded_at, id)`` — Postgres composite (record) comparison is NULL-safe field-by-field (unlike plain
``=`` over rows, which is NULL if any field is NULL), so this is the direct NULL-safe compare the rule
needs, no cast-to-text/md5 workaround. One DELETE per table, in its own transaction; a non-FULL
``VACUUM (ANALYZE)`` runs afterward in autocommit mode so planner statistics stay current — it does NOT
shrink the file on disk (that needs ``VACUUM FULL`` during a maintenance window; only the row count and
future free-space reuse improve here).

``--verify-asof YYYY-MM-DD`` is the safety net: before touching any table, compute the canonical CallCard
(``calls_repo._canonical(call_for_thesis(..., record=False))``) for every non-archived thesis at that
as-of, with ONE ``known_at`` pinned for the whole run. After each table's DELETE (still uncommitted —
read-your-own-writes on the same connection sees it), recompute the same snapshot and compare; any
difference rolls back that table's transaction and aborts the run before it can touch anything else.

    python -m pipeline.dedup_identical_versions                                  # dry-run (default)
    python -m pipeline.dedup_identical_versions --apply --verify-asof 2026-09-03 # write + safety net
    python -m pipeline.dedup_identical_versions --tables fact_price_eod --apply  # restrict to one table
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from db.bitemporal import _FACT_IDENTITY
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, thesis_repo

# The two tables the seed pile-up actually hit hard (§3.3 of the perf diagnosis) — the only ones worth
# the extra top-5-partitions query in the report; the small tables' whole duplicate set is already
# printable directly.
_BIG_TABLES = frozenset({"fact_price_eod", "fact_insider_txn"})

# Columns never part of the payload compare: a NEW version always gets a new id, and recorded_at IS the
# version axis (the transaction-time clock) — comparing on it would make every row unique by definition.
_NOT_PAYLOAD = frozenset({"id", "recorded_at"})


# --------------------------------------------------------------------------------------------------
# Schema introspection — derives scope column + payload columns per table from information_schema,
# never hardcoded (CLAUDE.md: "verify the scope column per table via information_schema rather than
# assuming").
# --------------------------------------------------------------------------------------------------


def _table_columns(conn: psycopg.Connection, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return [r["column_name"] for r in cur.fetchall()]


def payload_columns(conn: psycopg.Connection, table: str) -> list[str]:
    """Every column except ``id`` / ``recorded_at`` — the compare basis for "is this a redundant copy"."""
    cols = _table_columns(conn, table)
    if not cols:
        raise ValueError(f"no columns found for table {table!r} — wrong DB / schema?")
    return [c for c in cols if c not in _NOT_PAYLOAD]


def scope_column(conn: psycopg.Connection, table: str) -> str:
    """``thesis_id`` for the one thesis-scoped fact table, else ``security_id`` — DERIVED, not assumed."""
    cols = set(_table_columns(conn, table))
    if "thesis_id" in cols:
        return "thesis_id"
    if "security_id" in cols:
        return "security_id"
    raise ValueError(
        f"{table!r} has neither thesis_id nor security_id — cannot derive its scope column"
    )


def partition_columns(scope_col: str, table: str) -> list[str]:
    """``(tenant_id, scope_col, *natural_key_cols)`` — the SAME de-dup-the-column-list shape
    ``db.bitemporal.as_of_many`` already uses for its ``DISTINCT ON`` partition. ``_FACT_IDENTITY[table]``
    is the natural key WITHIN one scope (mirrors how ``_as_of`` reads it under a ``WHERE scope = ...``
    filter); this tool scans the WHOLE table, so the scope column is prefixed explicitly."""
    return ["tenant_id", scope_col] + [c for c in _FACT_IDENTITY[table] if c != scope_col]


# --------------------------------------------------------------------------------------------------
# The shared "is this row a redundant copy of its immediate predecessor" query — one builder, reused by
# the dry-run report, the top-5-partitions breakdown, and the delete.
# --------------------------------------------------------------------------------------------------


def _tenant_where(tenant_id: UUID | None) -> tuple[sql.Composable, dict]:
    if tenant_id is None:
        return sql.SQL(""), {}
    return sql.SQL(" WHERE tenant_id = %(tenant_id)s"), {"tenant_id": tenant_id}


def _is_dup_select(
    table: str, payload_cols: list[str], partition_cols: list[str], where: sql.Composable
) -> sql.Composed:
    payload = sql.SQL(", ").join(sql.Identifier(c) for c in payload_cols)
    partition = sql.SQL(", ").join(sql.Identifier(c) for c in partition_cols)
    return sql.SQL(
        "SELECT id, {partition}, "
        "ROW({payload}) IS NOT DISTINCT FROM "
        "lag(ROW({payload})) OVER (PARTITION BY {partition} ORDER BY recorded_at, id) AS is_dup "
        "FROM {table}{where}"
    ).format(partition=partition, payload=payload, table=sql.Identifier(table), where=where)


def duplicate_ids(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> list[UUID]:
    """The ids of every row whose payload matches the row immediately before it in its partition — the
    exact set this tool is permitted to delete. Ordered by id for deterministic test/report output.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    payload_cols = payload_columns(conn, table)
    scope_col = scope_column(conn, table)
    partition_cols = partition_columns(scope_col, table)
    where, params = _tenant_where(tenant_id)
    inner = _is_dup_select(table, payload_cols, partition_cols, where)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id FROM ({inner}) x WHERE is_dup ORDER BY id").format(inner=inner),
            params,
        )
        return [r["id"] for r in cur.fetchall()]


def top_duplicate_partitions(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None, limit: int = 5
) -> list[dict]:
    """The ``limit`` partitions with the most identical-copy rows — the "which name/date is the pile-up
    concentrated on" breakdown for the two big tables."""
    payload_cols = payload_columns(conn, table)
    scope_col = scope_column(conn, table)
    partition_cols = partition_columns(scope_col, table)
    where, params = _tenant_where(tenant_id)
    inner = _is_dup_select(table, payload_cols, partition_cols, where)
    part_list = sql.SQL(", ").join(sql.Identifier(c) for c in partition_cols)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT {p}, count(*) AS copies FROM ({inner}) x WHERE is_dup "
                "GROUP BY {p} ORDER BY copies DESC LIMIT %(limit)s"
            ).format(p=part_list, inner=inner),
            {**params, "limit": limit},
        )
        return [dict(r) for r in cur.fetchall()]


def row_count(conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None) -> int:
    """COUNT THE TABLE — the CLAUDE.md convention: idempotency/impact must be proven against the raw
    row count, never the as-of read (which already dedups and would hide a stuck duplicate append).
    """
    where, params = _tenant_where(tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT count(*) AS n FROM {t}{w}").format(t=sql.Identifier(table), w=where),
            params,
        )
        return cur.fetchone()["n"]


def distinct_fact_count(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> int:
    scope_col = scope_column(conn, table)
    partition_cols = partition_columns(scope_col, table)
    where, params = _tenant_where(tenant_id)
    part_list = sql.SQL(", ").join(sql.Identifier(c) for c in partition_cols)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT count(*) AS n FROM (SELECT DISTINCT {p} FROM {t}{w}) s").format(
                p=part_list, t=sql.Identifier(table), w=where
            ),
            params,
        )
        return cur.fetchone()["n"]


def table_size(conn: psycopg.Connection, table: str) -> int:
    """``pg_total_relation_size`` (table + indexes + TOAST) in bytes — reported before/after so the
    operator sees size does NOT shrink from the DELETE alone (only a non-locking ``VACUUM (ANALYZE)``
    runs here; reclaiming the file needs ``VACUUM FULL`` in a maintenance window)."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_total_relation_size(%s::regclass) AS n", (table,))
        return cur.fetchone()["n"]


# --------------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------------


@dataclass
class TableReport:
    table: str
    rows: int
    distinct_facts: int
    identical_copies: int
    top_partitions: list[dict] = field(default_factory=list)
    size_bytes: int = 0


def build_report(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> TableReport:
    ids = duplicate_ids(conn, table, tenant_id=tenant_id)
    top = top_duplicate_partitions(conn, table, tenant_id=tenant_id) if table in _BIG_TABLES else []
    return TableReport(
        table=table,
        rows=row_count(conn, table, tenant_id=tenant_id),
        distinct_facts=distinct_fact_count(conn, table, tenant_id=tenant_id),
        identical_copies=len(ids),
        top_partitions=top,
        size_bytes=table_size(conn, table),
    )


def _human(nbytes: int) -> str:
    v = float(nbytes)
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.1f}{unit}"
        v /= 1024
    return f"{v:.1f}GB"  # pragma: no cover


def _print_report(rep: TableReport) -> None:
    print(f"\n-- {rep.table} --")
    print(f"  rows                    : {rep.rows}")
    print(f"  distinct facts          : {rep.distinct_facts}")
    print(f"  identical later copies  : {rep.identical_copies}  (candidates for deletion)")
    print(f"  on-disk size            : {_human(rep.size_bytes)} ({rep.size_bytes} bytes)")
    for p in rep.top_partitions:
        copies = p["copies"]
        key = {k: v for k, v in p.items() if k != "copies"}
        print(f"    {copies:>6} copies  {key}")


@dataclass
class ApplyResult:
    table: str
    rows_before: int
    rows_after: int
    size_before: int
    size_after: int
    deleted: int


def _print_apply(res: ApplyResult) -> None:
    print(f"\n-- {res.table} — APPLIED --")
    print(f"  rows before : {res.rows_before}")
    print(f"  rows after  : {res.rows_after}")
    print(f"  deleted     : {res.deleted}")
    print(f"  size before : {_human(res.size_before)} ({res.size_before} bytes)")
    print(
        f"  size after  : {_human(res.size_after)} ({res.size_after} bytes)  "
        f"[VACUUM (ANALYZE) only — file size does NOT shrink until a VACUUM FULL / maintenance window]"
    )


# --------------------------------------------------------------------------------------------------
# --verify-asof — the canonical-call safety net
# --------------------------------------------------------------------------------------------------


def snapshot_canonical_calls(
    conn: psycopg.Connection, asof: date, known_at: datetime
) -> dict[UUID, str]:
    """``calls_repo._canonical`` of every non-archived thesis's CallCard at ``asof``/``known_at``,
    read-only (``record=False``) — the "did this DELETE change what any call reads" snapshot."""
    out: dict[UUID, str] = {}
    for thesis in thesis_repo.list_all(conn, include_archived=False):
        card = call_for_thesis(conn, thesis.id, asof, known_at=known_at, record=False)
        out[thesis.id] = calls_repo._canonical(card)
    return out


def diff_snapshots(before: dict[UUID, str], after: dict[UUID, str]) -> list[UUID]:
    ids = set(before) | set(after)
    return sorted(tid for tid in ids if before.get(tid) != after.get(tid))


# --------------------------------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------------------------------


def apply_table(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> list[UUID]:
    """Delete exactly the flagged duplicate ids for ``table`` (uncommitted — the caller owns the
    transaction). Returns the deleted ids."""
    ids = duplicate_ids(conn, table, tenant_id=tenant_id)
    if ids:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DELETE FROM {t} WHERE id = ANY(%(ids)s)").format(t=sql.Identifier(table)),
                {"ids": ids},
            )
    return ids


def vacuum_analyze(conn: psycopg.Connection, table: str) -> None:
    """Non-locking ``VACUUM (ANALYZE)`` — must run outside a transaction block (autocommit). Refreshes
    planner statistics and frees space FOR REUSE; never shrinks the on-disk file (that needs
    ``VACUUM FULL``, which takes an exclusive lock — deliberately not run here)."""
    prior = conn.autocommit
    conn.autocommit = True
    try:
        conn.execute(sql.SQL("VACUUM (ANALYZE) {t}").format(t=sql.Identifier(table)))
    finally:
        conn.autocommit = prior


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------


def require_database_url() -> str:
    """``DATABASE_URL`` MUST be set explicitly — no silent dev-default (unlike ``db.session.database_url``,
    which falls back to the local dev DSN). This is a destructive repair tool; the caller states the
    target explicitly, every time."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. This is a DESTRUCTIVE repair tool (it deletes rows) — refusing "
            "to fall back to any default. Set DATABASE_URL to the exact target DB and rerun."
        )
    return url


def _redact(url: str) -> str:
    """host:port/dbname only — NEVER the credentials — printed as the first line of every run so the
    operator can see exactly which database is about to be touched."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}{parts.path}"


def _validate_tables(names: list[str]) -> list[str]:
    unknown = [n for n in names if n not in _FACT_IDENTITY]
    if unknown:
        raise SystemExit(
            f"unknown table(s) {unknown!r} — must be one of {sorted(_FACT_IDENTITY)}. Refusing."
        )
    return names


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _parse_uuid(s: str) -> UUID:
    return UUID(s)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Delete byte-identical bitemporal fact re-versions (B2 — the seed pile-up). "
        "Dry-run by default; --apply writes."
    )
    p.add_argument(
        "--apply", action="store_true", help="write the deletes (default: dry-run report only)"
    )
    p.add_argument(
        "--tables",
        default=None,
        help="comma-separated subset of fact tables (default: every table in _FACT_IDENTITY)",
    )
    p.add_argument("--tenant", type=_parse_uuid, default=None, help="restrict to one tenant_id")
    p.add_argument(
        "--verify-asof",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="--apply only: before/after every table's delete, assert every non-archived thesis's "
        "canonical call at this as-of (one pinned known_at) is unchanged; abort+rollback on any diff",
    )
    args = p.parse_args(argv)

    url = require_database_url()
    print(f"target DB : {_redact(url)}")
    tables = _validate_tables(args.tables.split(",")) if args.tables else sorted(_FACT_IDENTITY)

    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")

        if not args.apply:
            print("\n=== dedup_identical_versions — DRY-RUN (nothing written) ===")
            for table in tables:
                _print_report(build_report(conn, table, tenant_id=args.tenant))
            conn.rollback()  # read-only; nothing to commit
            print(
                "\nSUMMARY: dry-run only, nothing written — rerun with --apply to delete "
                "the reported candidates"
            )
            return

        # --verify-asof: pin ONE known_at for the whole run, snapshot BEFORE touching any table.
        known_at = datetime.now(timezone.utc)
        before_snapshot: dict[UUID, str] | None = None
        if args.verify_asof is not None:
            before_snapshot = snapshot_canonical_calls(conn, args.verify_asof, known_at)
            conn.rollback()  # the snapshot is read-only; keep it out of the first table's transaction
            print(
                f"\nverify-asof baseline: {len(before_snapshot)} non-archived thesis call(s) "
                f"@ asof={args.verify_asof} known_at={known_at.isoformat()}"
            )

        print("\n=== dedup_identical_versions — APPLY ===")
        results: list[ApplyResult] = []
        for table in tables:
            rows_before = row_count(conn, table, tenant_id=args.tenant)
            size_before = table_size(conn, table)
            deleted_ids = apply_table(conn, table, tenant_id=args.tenant)  # uncommitted

            if before_snapshot is not None:
                after_snapshot = snapshot_canonical_calls(conn, args.verify_asof, known_at)
                diffs = diff_snapshots(before_snapshot, after_snapshot)
                if diffs:
                    conn.rollback()
                    print(
                        f"\nVERIFY-ASOF FAILED after deleting from {table}: "
                        f"{len(diffs)} thesis call(s) changed — ROLLED BACK, aborting run."
                    )
                    for tid in diffs[:10]:
                        print(f"  changed: {tid}")
                    raise SystemExit(1)

            conn.commit()
            vacuum_analyze(conn, table)
            rows_after = row_count(conn, table, tenant_id=args.tenant)
            size_after = table_size(conn, table)
            res = ApplyResult(
                table=table,
                rows_before=rows_before,
                rows_after=rows_after,
                size_before=size_before,
                size_after=size_after,
                deleted=len(deleted_ids),
            )
            results.append(res)
            _print_apply(res)

        total_deleted = sum(r.deleted for r in results)
        verify_line = (
            f"verify_asof=PASS(asof={args.verify_asof},known_at={known_at.isoformat()})"
            if before_snapshot is not None
            else "verify_asof=SKIPPED"
        )
        per_table = " ".join(f"{r.table}=-{r.deleted}rows" for r in results)
        print(f"\nSUMMARY: {per_table} total_deleted={total_deleted} {verify_line}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
