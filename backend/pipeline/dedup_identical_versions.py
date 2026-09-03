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

THE SUPERSEDES EXCEPTION: 8 of the 12 whitelisted tables (cash_burn, catalyst, dilution, fundamentals,
insider_txn, revenue_mix, shares_outstanding, theme_conviction) carry a self-referencing ``supersedes``
FK — a correction row points ``supersedes`` at the exact version it replaces, and that replaced row can
itself be one of this tool's identical-copy candidates. Deleting it would raise a real
``ForeignKeyViolation`` (hit on dev 2026-09-03 mid-run against ``fact_insider_txn``, which has 278,624
rows referenced by some ``supersedes``). ``self_referencing_fk_columns`` discovers every such FK column
per table from ``information_schema`` at runtime — never hardcodes ``supersedes`` — and every candidate
query EXCLUDES a row referenced through one: a referenced identical copy is KEPT, not deleted. Keeping
a row is always unobservable (the same argument as EARLIEST-SURVIVES below — a read can only ever
resolve to a row that's still there), so this is a pure precision loss on a handful of rows, never a
correctness risk. It also fails loud if any OTHER table is ever found to hold an FK onto a fact table
(today none do) rather than silently mis-deleting.

THE HASH-JOIN FIX: the referenced-id check is a ``LEFT JOIN`` against the DISTINCT set of referenced
ids, never an ``id IN (SELECT ...)``. Postgres hash/merge-joins a ``LEFT JOIN`` regardless of size
(spilling to disk past ``work_mem`` if needed), but an ``IN`` subquery too large to fit a hashed subplan
in ``work_mem`` falls back to re-scanning the whole materialized list PER ROW — an
``O(rows x references)`` plan. Measured on dev (2026-09-04): 605k ``fact_insider_txn`` rows x 278,585
non-null ``supersedes`` under the ``IN`` form planned at cost ~7.8 BILLION and hung for 10+ minutes
before being terminated (nothing written — the query never finished computing candidates). The
candidate set is also computed EXACTLY ONCE per table per run (``fetch_candidates``, returning a
``Candidates`` with both the deletable and referenced id lists already partitioned) — the report and
``apply_table`` both reuse it, so a large table's window query is never paid for twice.

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


def self_referencing_fk_columns(conn: psycopg.Connection, table: str) -> list[str]:
    """FK columns on ``table`` whose constraint points back at ``table`` itself — e.g. ``supersedes``
    (8 of the 12 whitelisted fact tables carry one: cash_burn, catalyst, dilution, fundamentals,
    insider_txn, revenue_mix, shares_outstanding, theme_conviction). A correction row's ``supersedes``
    points at the exact version it replaces; when that replaced row is ALSO one of this tool's
    identical-copy candidates, deleting it would orphan the pointer (a real ``ForeignKeyViolation`` —
    hit on dev 2026-09-03 deleting ``fact_insider_txn``, where 278,624 rows are referenced by some
    ``supersedes``). Keeping a referenced row instead is always safe: the rule's unobservability
    argument doesn't care WHICH later-or-equal row survives, only that no row a read could ever
    resolve to gets removed — so this is a pure precision loss on a handful of rows, never a
    correctness risk.

    DERIVED from ``information_schema`` at runtime — never hardcodes ``supersedes`` — so a renamed or
    newly-added self-referencing FK is picked up automatically. Also FAILS LOUD if any OTHER table is
    ever found to hold an FK onto ``table`` (today none do: no FK anywhere targets a fact table except
    a fact table's own self-referencing one) — that case needs the same exclusion treatment and must
    never be silently ignored.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tc.table_name AS referencing_table, kcu.column_name AS referencing_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema "
            "WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public' "
            "  AND ccu.table_name = %s",
            (table,),
        )
        rows = cur.fetchall()
    self_cols = sorted({r["referencing_column"] for r in rows if r["referencing_table"] == table})
    external = sorted(
        {
            f"{r['referencing_table']}.{r['referencing_column']}"
            for r in rows
            if r["referencing_table"] != table
        }
    )
    if external:
        raise ValueError(
            f"{table!r} is referenced by FK(s) from another table {external} that this tool does not "
            "account for — deleting a row could orphan them. Refusing until dedup_identical_versions "
            "is updated to exclude rows referenced from outside the table too."
        )
    return self_cols


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


def _candidates_select(
    conn: psycopg.Connection, table: str, tenant_id: UUID | None
) -> tuple[sql.Composed, dict, list[str]]:
    """The shared per-row flags every query below is built on: ``is_dup`` (payload matches the
    immediate predecessor in its partition) and ``is_referenced`` (some OTHER row's self-FK column —
    e.g. ``supersedes`` — points at this row's id, so deleting it would orphan that pointer). Computing
    both flags in ONE place keeps every reporting/delete query in agreement. Returns
    ``(select, params, partition_cols)``.

    ``is_referenced`` is a ``LEFT JOIN`` against the DISTINCT set of referenced ids, never an
    ``IN (SELECT ...)`` — Postgres hash/merge-joins a ``LEFT JOIN`` (spilling to disk past
    ``work_mem`` if the referenced set is large) but, for an ``IN`` subquery too big to fit a hashed
    subplan in ``work_mem``, falls back to re-scanning the whole materialized list PER ROW (an
    ``O(rows x references)`` plan). Measured on dev (2026-09-04): 605k ``fact_insider_txn`` rows x
    278,585 non-null ``supersedes`` under the ``IN`` form planned at cost ~7.8 BILLION and hung for
    10+ minutes; the ``LEFT JOIN`` form is the fix.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    payload_cols = payload_columns(conn, table)
    scope_col = scope_column(conn, table)
    partition_cols = partition_columns(scope_col, table)
    where, params = _tenant_where(tenant_id)
    inner = _is_dup_select(table, payload_cols, partition_cols, where)
    partition = sql.SQL(", ").join(sql.Identifier(c) for c in partition_cols)
    fk_cols = self_referencing_fk_columns(conn, table)
    if fk_cols:
        referenced = sql.SQL(" UNION ").join(
            sql.SQL("SELECT DISTINCT {c} AS rid FROM {t} WHERE {c} IS NOT NULL").format(
                c=sql.Identifier(c), t=sql.Identifier(table)
            )
            for c in fk_cols
        )
        select = sql.SQL(
            "SELECT y.id, {partition}, y.is_dup, (r.rid IS NOT NULL) AS is_referenced "
            "FROM ({inner}) y LEFT JOIN ({referenced}) r ON r.rid = y.id"
        ).format(partition=partition, inner=inner, referenced=referenced)
    else:
        select = sql.SQL(
            "SELECT y.id, {partition}, y.is_dup, false AS is_referenced FROM ({inner}) y"
        ).format(partition=partition, inner=inner)
    return select, params, partition_cols


@dataclass
class Candidates:
    """The result of ONE execution of the shared candidates query, already partitioned into the two
    outcomes every caller needs — computed once per table per run, never re-queried, because the
    window query itself (a ``lag() OVER (PARTITION BY ...)`` scan of the whole table) is the expensive
    part on a large table: paying for it twice (once for the report, once for the apply) doubles the
    cost for nothing."""

    table: str
    deletable_ids: list[UUID]  # payload-duplicate AND unreferenced — safe to delete
    referenced_ids: list[UUID]  # payload-duplicate but referenced by a self-FK pointer — KEPT

    @property
    def identical_copies(self) -> int:
        return len(self.deletable_ids) + len(self.referenced_ids)


def fetch_candidates(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> Candidates:
    """Run the shared is_dup/is_referenced query EXACTLY ONCE and partition the flagged rows in
    Python. ``build_report`` and ``apply_table`` both call this (and only this) for the candidate set
    — never ``duplicate_ids``/``referenced_duplicate_ids`` separately — so a table's window query is
    never paid for twice in the same run. Ordered by id for deterministic test/report output."""
    select, params, _ = _candidates_select(conn, table, tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, is_referenced FROM ({s}) z WHERE is_dup ORDER BY id").format(
                s=select
            ),
            params,
        )
        rows = cur.fetchall()
    deletable = [r["id"] for r in rows if not r["is_referenced"]]
    referenced = [r["id"] for r in rows if r["is_referenced"]]
    return Candidates(table=table, deletable_ids=deletable, referenced_ids=referenced)


def duplicate_ids(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> list[UUID]:
    """Convenience wrapper over ``fetch_candidates`` for callers (tests, ad-hoc use) that only need the
    DELETABLE ids. Runs the full candidates query on its own — prefer ``fetch_candidates`` directly
    when you also need ``referenced_ids``, to avoid paying for the query twice."""
    return fetch_candidates(conn, table, tenant_id=tenant_id).deletable_ids


def referenced_duplicate_ids(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> list[UUID]:
    """Convenience wrapper over ``fetch_candidates`` for callers that only need the REFERENCED (kept)
    ids. Same caveat as ``duplicate_ids`` — prefer ``fetch_candidates`` when you need both."""
    return fetch_candidates(conn, table, tenant_id=tenant_id).referenced_ids


def top_duplicate_partitions(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None, limit: int = 5
) -> list[dict]:
    """The ``limit`` partitions with the most DELETABLE identical-copy rows — the "which name/date is
    the pile-up concentrated on" breakdown for the two big tables (a referenced-and-kept row never
    actually shrinks the table, so it's excluded from this concentration view too)."""
    select, params, partition_cols = _candidates_select(conn, table, tenant_id)
    part_list = sql.SQL(", ").join(sql.Identifier(c) for c in partition_cols)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT {p}, count(*) AS copies FROM ({s}) z WHERE is_dup AND NOT is_referenced "
                "GROUP BY {p} ORDER BY copies DESC LIMIT %(limit)s"
            ).format(p=part_list, s=select),
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
    identical_copies: int  # payload-duplicate rows, total (referenced_kept + deletable)
    referenced_kept: int  # of those, pinned by a supersedes-style self-FK pointer — KEPT
    deletable: int  # of those, safe to delete
    top_partitions: list[dict] = field(default_factory=list)
    size_bytes: int = 0


def build_report(
    conn: psycopg.Connection, table: str, *, tenant_id: UUID | None = None
) -> TableReport:
    # ONE candidates query (fetch_candidates), never duplicate_ids + referenced_duplicate_ids
    # separately — see Candidates' docstring for why paying for the window query twice matters.
    candidates = fetch_candidates(conn, table, tenant_id=tenant_id)
    top = top_duplicate_partitions(conn, table, tenant_id=tenant_id) if table in _BIG_TABLES else []
    return TableReport(
        table=table,
        rows=row_count(conn, table, tenant_id=tenant_id),
        distinct_facts=distinct_fact_count(conn, table, tenant_id=tenant_id),
        identical_copies=candidates.identical_copies,
        referenced_kept=len(candidates.referenced_ids),
        deletable=len(candidates.deletable_ids),
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
    print(f"  identical later copies  : {rep.identical_copies}")
    print(f"    referenced by a supersedes-style FK (kept) : {rep.referenced_kept}")
    print(f"    deletable                                  : {rep.deletable}")
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
    identical_copies: int
    referenced_kept: int
    deleted: int


def _print_apply(res: ApplyResult) -> None:
    print(f"\n-- {res.table} — APPLIED --")
    print(f"  rows before : {res.rows_before}")
    print(f"  rows after  : {res.rows_after}")
    print(
        f"  identical later copies : {res.identical_copies}  "
        f"(referenced by a supersedes-style FK, kept: {res.referenced_kept})"
    )
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
) -> Candidates:
    """Delete exactly the DELETABLE duplicate ids for ``table`` (a row referenced by a self-FK pointer
    is excluded — uncommitted; the caller owns the transaction). Runs ``fetch_candidates`` ONCE and
    returns the full ``Candidates`` — ``deletable_ids`` is what was just removed, ``referenced_ids`` is
    what was kept — so a caller (``main``'s apply loop) never re-queries just to report the
    referenced-kept count."""
    candidates = fetch_candidates(conn, table, tenant_id=tenant_id)
    if candidates.deletable_ids:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DELETE FROM {t} WHERE id = ANY(%(ids)s)").format(t=sql.Identifier(table)),
                {"ids": candidates.deletable_ids},
            )
    return candidates


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
            # ONE candidates query + the delete — apply_table returns the full Candidates, so the
            # referenced-kept count below never re-queries (previously a separate
            # referenced_duplicate_ids call before apply_table, paying for the window query twice).
            candidates = apply_table(conn, table, tenant_id=args.tenant)  # uncommitted

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
                identical_copies=candidates.identical_copies,
                referenced_kept=len(candidates.referenced_ids),
                deleted=len(candidates.deletable_ids),
            )
            results.append(res)
            _print_apply(res)

        total_deleted = sum(r.deleted for r in results)
        total_referenced_kept = sum(r.referenced_kept for r in results)
        verify_line = (
            f"verify_asof=PASS(asof={args.verify_asof},known_at={known_at.isoformat()})"
            if before_snapshot is not None
            else "verify_asof=SKIPPED"
        )
        per_table = " ".join(f"{r.table}=-{r.deleted}rows" for r in results)
        print(
            f"\nSUMMARY: {per_table} total_deleted={total_deleted} "
            f"total_referenced_kept={total_referenced_kept} {verify_line}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
