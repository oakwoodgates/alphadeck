"""The V2 cutover's APPLY path (W10a): ``python -m pipeline.v2_cutover run ...``.

THIS MODULE DELETES A DATABASE'S HISTORY ON PURPOSE (operator decision D1: V2's record starts at cutover). Its
guards, its backup precondition and its one all-or-nothing transaction are the product; every refusal below is a
feature, and each one writes nothing.

::

    python -m pipeline.v2_cutover run --database NAME --confirm "reset NAME" [--prod] [--apply]
        [--expect-plan PATH] [--plan-out PATH] [--orphan-purity leave|delete] [--reuse-backup FILE]
        [--no-history-archive] [--provenance-dir DIR]

Defaults (each operator decision is a flag whose default is the safe choice): dry run (no ``--apply``);
``--orphan-purity leave``; a NEW verified backup (no ``--reuse-backup``); the V1 history archive taken; the
provenance file under ``data/cutover_runs``.

Exit codes: 0 done, a dry run, or already cut over with nothing left to do; 1 refused (nothing written); 2 the
wrong database or confirmation, or (as ``plan``) an unmapped or missing table; 3 the backup failed or did not
verify (nothing written); 4 the transaction failed and rolled back (nothing written; the backup is kept);
75 a lock is busy or the nightly window.

**The guard** (``guard_refusal`` / ``assert_cutover_target``, PURE, allow-list). The name is read with
``current_database()`` on the connection that would write, never from a URL, before anything else is read.
``--database`` must equal it; ``--confirm`` must equal ``reset <name>`` exactly; the production name
``alphadeck`` is allowed only with ``--prod``, and ``--prod`` with any other name refuses; any other name must be
a rehearsal database (``v2_move.assert_rehearsal_db``: a fork ``alphadeck_<name>`` or a test database);
``alphadeck_dev``, ``alphadeck_sig`` and ``alphadeck_prod`` always refuse. ``--prod --apply`` needs
``--expect-plan``. Exit 2, nothing else read.

**Preflight** (dry run and apply; read-only; one ``ok:`` or ``REFUSE:`` line each):
P1 every ``db/migrations/*.sql`` is applied; P2 the data tenant and the founding workspace exist; P3 not inside
the nightly window (exit 75); P4 (apply) the repair lock (call-of-record + fact gate) and job locks 3-7, held for
the whole run (exit 75, naming the holder); P5 the dry run's surprise rules (rules 1-2 exit 2; rules 3-9 refuse,
except rule 8, the unratified data-tenant purity, which ``--orphan-purity`` acknowledges); P6 a workspace
company catalyst refuses; P7 no share-count or cash/burn row would fail migration 0206's vocabulary; P8 the
move's own refusals; P9 counts printed (decisions, positions, theme conviction: all kept); P10 already cut over
(a ``data_repair`` row of tool ``v2_cutover``): nothing left to do -> exit 0, no backup, no journal; any history
row present -> REFUSE ("a second reset would delete V2 nights"); P11 every foreign key INTO the reset tables comes
from a reset table (so no cascade can reach a table the plan does not list). ``--expect-plan`` refuses (exit 1)
on any difference from the file; ``--plan-out`` writes the plan.

**The backup** (apply, before any write): ``pipeline.backup.run_backup(label="pre-v2-reset")`` of the SAME
database (the connection string's database name must equal the guarded name), or ``--reuse-backup FILE``
(newer than the newest ``recorded_at`` in ``calls`` and the moved tables and the newest
``data_repair.applied_at``). Verified by ``verify_dump``: in the backups directory, a backup name, not empty, and
the ``pg_dump`` completion trailer (``DUMP_COMPLETE_MARKER``) in its last ``TAIL_BYTES``. Then the history
archive (``-v1-history``): the eight reset tables only, verified the same way. Exit 3 on any failure.

**The transaction** (ONE, on the guarded connection): re-plan and compare with the printed plan (any difference
refuses, exit 1); read the baseline; the three move legs (``v2_move.relabel_legs``); the orphan purity rows when
``--orphan-purity delete``; ``DELETE FROM`` each table of ``RESET_ORDER`` (children first; never TRUNCATE, never
CASCADE, never DROP), each row count equal to the plan; ``mark_changed`` for every company touched; VALIDATE
migration 0206's two constraints; the ``data_repair`` journal row (tool ``v2_cutover``, ``deletes`` true) -
INSIDE the transaction, so the "already cut over" marker can never be missing after a committed reset; commit.
Any failure: rollback, every disabled trigger checked back on (``v2_move._restore_triggers``), exit 4.

**The provenance file** ``<provenance-dir>/<UTC ts>-<db>.json`` (written as ``.pending`` before the commit and
renamed after it; W10b's ``verify`` reads it), one JSON object:

- ``tool``, ``database``, ``started_at``, ``committed_at``, ``journal_id``, ``backup``, ``archive``;
- ``plan``: the plan document (``plan_document``: ``moves`` by table with ``rows`` and ``by_ratified``,
  ``resets`` by table, ``orphan_purity`` ``{rows, action}``);
- ``preflight``: the printed preflight lines;
- ``counts``: the journal's counts (``moved``, ``by_ratified``, ``reset``, ``orphan_purity``, ``validated``,
  ``companies``);
- ``baseline`` (read in the transaction, before the first write): ``theses`` [``{id, tenant_id, created_at,
  archived_at}``]; ``basket_members`` {thesis id: [``{id, security_id, ticker, segment, role}``]};
  ``children`` {thesis id: {table: rows}} for ``kill_criterion``, ``catalyst``, ``evidence``,
  ``thesis_exclusion``, ``basket_snapshot``, ``fact_term_mentions``, ``fact_spac_match``,
  ``fact_theme_conviction``; ``operator_decision`` (rows); ``share_security_ids`` and ``cash_security_ids``
  (companies with a row, any tenant); ``moved_tables_by_tenant`` {table: {tenant: rows}} before the move;
  ``app_users`` [``{id, role, workspace_id, disabled_at}``]; ``plan_json`` (the ``plan --json`` document).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Json

import domain.scopes as scopes
from db import session
from db.change_stamp import mark_changed
from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID, database_url
from domain import market_time
from domain.settings import get_settings
from pipeline import backup, v2_move
from pipeline import v2_cutover as cutover
from pipeline.onboarding import ONBOARD_LOCK_ID, window_bounds
from pipeline.run_lock import (
    LOCK_ID_EDITIONS,
    LOCK_ID_EXTRACTION,
    LOCK_ID_MASTER_REFRESH,
    LOCK_ID_TERM_MENTIONS,
    JobAlreadyRunning,
    PassAlreadyRunning,
    acquire_job_lock,
    acquire_repair_lock,
)
from pipeline.schedule import parse_run_at

TOOL = "v2_cutover"
PROD_DATABASE = "alphadeck"

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_GUARD = 2
EXIT_BACKUP = 3
EXIT_ROLLED_BACK = 4
EXIT_BUSY = 75

# The tables the reset empties: EXACTLY the ``reset`` entries of ``v2_cutover.CUTOVER_ACTION``, children before
# parents (every edition child references ``edition``; ``call_annotation`` references ``calls``).
RESET_ORDER: tuple[str, ...] = (
    "edition_cause",
    "edition_outcome",
    "edition_call",
    "edition_entry",
    "edition_event",
    "edition",
    "call_annotation",
    "calls",
)
# migration 0206's two NOT VALID constraints, validated in the transaction
VALIDATED_CONSTRAINTS: dict[str, str] = {
    "fact_shares_outstanding": "fact_shares_outstanding_ratified_by_vocabulary",
    "fact_cash_burn": "fact_cash_burn_ratified_by_vocabulary",
}
# the same rule as 0206's CHECK, negated: a row this matches would fail the VALIDATE
_VOCABULARY_VIOLATION = (
    "ratified_by IS NOT NULL AND ratified_by NOT IN ('auto', 'operator') "
    "AND ratified_by !~ '^[^@[:space:]]+@[^@[:space:]]+$'"
)
JOB_LOCKS: tuple[tuple[int, str], ...] = (
    (ONBOARD_LOCK_ID, "onboarding job"),
    (LOCK_ID_EDITIONS, "editions job"),
    (LOCK_ID_MASTER_REFRESH, "master refresh"),
    (LOCK_ID_TERM_MENTIONS, "term mentions job"),
    (LOCK_ID_EXTRACTION, "extraction job"),
)

# The line plain-text ``pg_dump`` writes at the very end of a COMPLETE dump (pg_backup_archiver.c, every release
# the repo's images use; newer releases follow it with a ``\\unrestrict`` line, still inside the tail read here).
# NOT confirmed against a real dump in this repo: no test may run pg_dump. Rehearsal 1 confirms it on the fork.
DUMP_COMPLETE_MARKER = "-- PostgreSQL database dump complete"
TAIL_BYTES = 4096
BACKUP_LABEL = "pre-v2-reset"
ARCHIVE_LABEL = "v1-history"
_ARCHIVE_SLUG = "-" + ARCHIVE_LABEL + ".sql"

_DEFAULT_PROVENANCE = Path(__file__).resolve().parents[2] / "data" / "cutover_runs"
_CHILD_TABLES = (
    "kill_criterion",
    "catalyst",
    "evidence",
    "thesis_exclusion",
    "basket_snapshot",
    "fact_term_mentions",
    "fact_spac_match",
    "fact_theme_conviction",
)

# read at call time through this module, so a test can inject a name (never by connecting to a prod-named DB)
database_name = v2_move.database_name


# --- the guard (PURE) ----------------------------------------------------------------------------------------


class NotACutoverTarget(RuntimeError):
    """The database is not one the cutover may run against with these flags."""


def assert_cutover_target(name: str, *, prod: bool) -> None:
    """Return quietly when the cutover may target ``name`` with ``prod``; raise ``NotACutoverTarget`` otherwise.
    Allow-list: the production name only with ``--prod``; ``--prod`` only with the production name; otherwise a
    rehearsal database (a fork or a test database). The reserved tiers always refuse."""
    if not isinstance(name, str):
        raise NotACutoverTarget(f"database {name!r} is not a name")
    if name == PROD_DATABASE:
        if not prod:
            raise NotACutoverTarget(
                f"database {name!r} is the production database: the cutover needs --prod for it"
            )
        return
    if prod:
        raise NotACutoverTarget(
            f"--prod is only for the production database {PROD_DATABASE!r}; {name!r} is not it"
        )
    try:
        v2_move.assert_rehearsal_db(name)
    except v2_move.NotARehearsalDatabase:
        raise NotACutoverTarget(
            f"database {name!r} is not a cutover target: only {PROD_DATABASE!r} with --prod, a fork "
            "(alphadeck_<name>) or a test database (alphadeck_test...); alphadeck_dev, alphadeck_sig and "
            "alphadeck_prod never"
        ) from None


def guard_refusal(
    *, current: str, database: str, confirm: str, prod: bool, apply: bool, expect_plan: bool
) -> str | None:
    """Every reason the guard refuses, as one sentence, or ``None``. ``current`` is ``current_database()`` read on
    the writing connection; the rest are the flags. PURE."""
    if database != current:
        return f"--database {database!r} is not the database this connection is on ({current!r})"
    if confirm != f"reset {database}":
        return f"--confirm must be exactly 'reset {database}' (got {confirm!r})"
    try:
        assert_cutover_target(current, prod=prod)
    except NotACutoverTarget as exc:
        return str(exc)
    if prod and apply and not expect_plan:
        return "--prod --apply needs --expect-plan PATH (the file a dry run wrote with --plan-out)"
    return None


# --- the backup check (PURE over a file's tail) -----------------------------------------------------------------


def dump_is_complete(tail: bytes) -> bool:
    """Does this tail of a plain-SQL dump carry ``pg_dump``'s completion trailer? PURE."""
    return DUMP_COMPLETE_MARKER.encode("ascii") in tail


def verify_dump(path: Path, *, backups_dir: Path) -> str | None:
    """Why ``path`` is not a verified backup, or ``None``: it must exist in ``backups_dir``, carry a backup name
    (``pipeline.backup._parse_backup_name``), be non-empty, and end with the completion trailer (its last
    ``TAIL_BYTES``). Reads only the file's size and tail."""
    path = Path(path)
    if not path.is_file():
        return f"backup {path.name} is missing"
    if path.resolve().parent != Path(backups_dir).resolve():
        return f"backup {path.name} is not in the backups directory ({backups_dir})"
    if backup._parse_backup_name(path.name) is None:
        return f"{path.name} does not carry a backup name (alphadeck-<UTC ts>[-<label>].sql)"
    size = path.stat().st_size
    if size == 0:
        return f"backup {path.name} is empty"
    with path.open("rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        tail = f.read()
    if not dump_is_complete(tail):
        return (
            f"backup {path.name} has no '{DUMP_COMPLETE_MARKER}' trailer in its last {TAIL_BYTES} bytes "
            "(an incomplete dump)"
        )
    return None


_ANY_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")


def _scrub(text: str, url: str) -> str:
    """``text`` with the connection string (whole, or without its password), any other URI, and every password
    replaced: what a failure message may print. PURE."""
    safe, password = backup._dump_target(url)
    for s in (url, safe):
        if s:
            text = text.replace(s, "<connection string>")
    text = _ANY_URI.sub("<connection string>", text)
    return backup._redact(text, [password] if password else [])


def _url_dbname(url: str) -> str | None:
    """The database a connection string names (``None`` when it names none or cannot be parsed). The string
    itself is never printed."""
    try:
        return conninfo_to_dict(url).get("dbname") or None
    except Exception:  # noqa: BLE001 - an unparseable string names no database: refused
        return None


# --- options -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    database: str
    confirm: str
    prod: bool = False
    apply: bool = False
    expect_plan: Path | None = None
    plan_out: Path | None = None
    orphan_purity: str = "leave"  # leave | delete (operator decision N1; default leave)
    reuse_backup: str | None = None
    history_archive: bool = True  # operator decision N9; default archive
    provenance_dir: Path | None = None


def options_from_args(args: argparse.Namespace) -> Options:
    return Options(
        database=args.database,
        confirm=args.confirm,
        prod=bool(args.prod),
        apply=bool(args.apply),
        expect_plan=Path(args.expect_plan) if args.expect_plan else None,
        plan_out=Path(args.plan_out) if args.plan_out else None,
        orphan_purity=args.orphan_purity,
        reuse_backup=args.reuse_backup,
        history_archive=not args.no_history_archive,
        provenance_dir=Path(args.provenance_dir) if args.provenance_dir else None,
    )


# --- the reads ----------------------------------------------------------------------------------------------


def _scalar(cur: Any, query: Any, params: Sequence[Any] = ()) -> Any:
    cur.execute(query, params)
    row = cur.fetchone()
    return None if row is None else next(iter(row.values()))


def _reset_counts(cur: Any) -> dict[str, int]:
    return {
        t: int(_scalar(cur, sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))))
        for t in RESET_ORDER
    }


def _orphans(cur: Any) -> list[dict[str, Any]]:
    """The unratified data-tenant purity rows (dry-run rule 8)."""
    cur.execute(
        "SELECT id, security_id FROM fact_revenue_mix WHERE tenant_id = %s AND ratified_by IS NULL ORDER BY id",
        (scopes.DATA_TENANT_ID,),
    )
    return [dict(r) for r in cur.fetchall()]


def _references_into(cur: Any) -> list[tuple[str, str, str]]:
    """Every foreign key INTO a reset table (schema ``public``): ``(from table, to table, on-delete action)``. A
    referencing table outside ``public`` is named ``schema.table``, so it can never pass for a reset table.
    """
    cur.execute(
        "SELECT CASE WHEN fn.nspname = 'public' THEN fc.relname ELSE fn.nspname || '.' || fc.relname END AS f, "
        "tc.relname AS t, con.confdeltype AS d "
        "FROM pg_constraint con "
        "JOIN pg_class tc ON tc.oid = con.confrelid JOIN pg_namespace tn ON tn.oid = tc.relnamespace "
        "JOIN pg_class fc ON fc.oid = con.conrelid JOIN pg_namespace fn ON fn.oid = fc.relnamespace "
        "WHERE con.contype = 'f' AND tn.nspname = 'public' AND tc.relname = ANY(%s) ORDER BY 1, 2",
        (list(RESET_ORDER),),
    )
    return [(r["f"], r["t"], str(r["d"])) for r in cur.fetchall()]


def _unapplied_migrations(cur: Any) -> list[str]:
    cur.execute("SELECT filename FROM schema_migrations")
    applied = {r["filename"] for r in cur.fetchall()}
    return sorted(p.name for p in Path(MIGRATIONS_DIR).glob("*.sql") if p.name not in applied)


def _journal_marker(cur: Any) -> datetime | None:
    """When the cutover was journaled (the newest ``data_repair`` row of tool ``v2_cutover``), or ``None``."""
    return _scalar(cur, "SELECT max(applied_at) AS at FROM data_repair WHERE tool = %s", (TOOL,))


def _newest_data_time(cur: Any) -> datetime | None:
    """The newest ``recorded_at`` in ``calls`` and the moved tables, and the newest ``data_repair.applied_at``: a
    reused backup must be newer than all of them."""
    parts = [
        sql.SQL("SELECT max(recorded_at) AS at FROM {}").format(sql.Identifier(t))
        for t in ("calls", *v2_move.MOVED_TABLES)
    ]
    parts.append(sql.SQL("SELECT max(applied_at) AS at FROM data_repair"))
    query = sql.SQL("SELECT max(at) AS at FROM ({}) AS x").format(
        sql.SQL(" UNION ALL ").join(parts)
    )
    return _scalar(cur, query)


def plan_document(
    database: str,
    move: v2_move.MovePlan,
    resets: dict[str, int],
    orphan_rows: int,
    orphan_action: str,
) -> dict[str, Any]:
    """The planned counts, as plain JSON: what ``--plan-out`` writes and ``--expect-plan`` compares."""
    return {
        "database": database,
        "moves": {
            p.table: {"rows": p.to_move, "by_ratified": dict(sorted(p.by_ratified.items()))}
            for p in move.legs
        },
        "resets": {t: int(resets[t]) for t in RESET_ORDER},
        "orphan_purity": {"rows": int(orphan_rows), "action": orphan_action},
    }


def plan_differences(expected: dict[str, Any], found: dict[str, Any]) -> list[str]:
    """Every planned count that differs, one line each (empty = the same plan). PURE."""
    out: list[str] = []
    if expected.get("database") != found.get("database"):
        out.append(
            f"database: expected {expected.get('database')!r}, found {found.get('database')!r}"
        )
    for section in ("moves", "resets"):
        a, b = expected.get(section) or {}, found.get(section) or {}
        for table in sorted(set(a) | set(b)):
            if a.get(table) != b.get(table):
                out.append(f"{section}.{table}: expected {a.get(table)}, found {b.get(table)}")
    if expected.get("orphan_purity") != found.get("orphan_purity"):
        out.append(
            f"orphan_purity: expected {expected.get('orphan_purity')}, found {found.get('orphan_purity')}"
        )
    return out


def _baseline(cur: Any, plan_json: dict[str, Any]) -> dict[str, Any]:
    """The recall baseline (module docstring), read before the first write."""
    cur.execute("SELECT id, tenant_id, created_at, archived_at FROM thesis ORDER BY id")
    theses = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT thesis_id, id, security_id, ticker, segment, role FROM basket_member ORDER BY thesis_id, id"
    )
    members: dict[str, list[dict[str, Any]]] = {}
    for r in cur.fetchall():
        row = dict(r)
        members.setdefault(str(row.pop("thesis_id")), []).append(row)
    children: dict[str, dict[str, int]] = {}
    for table in _CHILD_TABLES:
        cur.execute(
            sql.SQL("SELECT thesis_id, count(*) AS n FROM {} GROUP BY 1").format(
                sql.Identifier(table)
            )
        )
        for r in cur.fetchall():
            children.setdefault(str(r["thesis_id"]), {})[table] = int(r["n"])
    by_tenant: dict[str, dict[str, int]] = {}
    for table in v2_move.MOVED_TABLES:
        cur.execute(
            sql.SQL("SELECT tenant_id, count(*) AS n FROM {} GROUP BY 1 ORDER BY 1").format(
                sql.Identifier(table)
            )
        )
        by_tenant[table] = {str(r["tenant_id"]): int(r["n"]) for r in cur.fetchall()}
    ids: dict[str, list[str]] = {}
    for key, table in (
        ("share_security_ids", "fact_shares_outstanding"),
        ("cash_security_ids", "fact_cash_burn"),
    ):
        cur.execute(sql.SQL("SELECT DISTINCT security_id FROM {}").format(sql.Identifier(table)))
        ids[key] = sorted(str(r["security_id"]) for r in cur.fetchall())
    cur.execute("SELECT id, role, workspace_id, disabled_at FROM app_user ORDER BY id")
    users = [dict(r) for r in cur.fetchall()]
    return {
        "theses": theses,
        "basket_members": members,
        "children": children,
        "operator_decision": int(_scalar(cur, "SELECT count(*) AS n FROM operator_decision")),
        **ids,
        "moved_tables_by_tenant": by_tenant,
        "app_users": users,
        "plan_json": plan_json,
    }


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str))


# --- the preflight ---------------------------------------------------------------------------------------------


@dataclass
class Preflight:
    lines: list[str]
    refusals: list[str]
    fatal: bool  # a rule 1-2 table problem: exit 2, as ``plan``
    already_cut: datetime | None  # set when cut over and nothing is left to do
    doc: dict[str, Any] | None
    move: v2_move.MovePlan | None
    plan_json: dict[str, Any] | None


def preflight(conn: Any, opts: Options, name: str) -> Preflight:
    """P1, P2, P5-P11 (P3 and P4 run before it). Reads only, in the caller's transaction."""
    lines: list[str] = []
    refusals: list[str] = []

    def ok(text: str) -> None:
        lines.append(f"ok: {text}")

    def refuse(text: str) -> None:
        lines.append(f"REFUSE: {text}")
        refusals.append(text)

    with conn.cursor() as cur:
        missing = _unapplied_migrations(cur)
        if missing:
            refuse(
                f"P1 {len(missing)} migration(s) not applied ({', '.join(missing)}): deploy or migrate first"
            )
        else:
            ok("P1 every migration file is applied")
        try:
            scopes.assert_data_tenant(conn)
            kind = _scalar(
                cur, "SELECT kind FROM tenant WHERE id = %s", (scopes.FOUNDING_WORKSPACE_ID,)
            )
            if kind != "workspace":
                raise scopes.DataTenantMissing(
                    f"the founding workspace {scopes.FOUNDING_WORKSPACE_ID} is missing (kind {kind!r})"
                )
            ok("P2 the data tenant and the founding workspace exist")
        except scopes.DataTenantMissing as exc:
            conn.rollback()
            refuse(f"P2 {exc}")
            return Preflight(lines, refusals, False, None, None, None, None)

    inv = cutover.inventory(conn)
    found = cutover.surprises(inv)
    fatal = any(s.fatal for s in found)
    for s in found:
        if s.fatal:
            refuse(f"P5 rule {s.rule}: {s.text}")
        elif s.rule == 8:
            ok(
                f"P5 rule 8 acknowledged by --orphan-purity {opts.orphan_purity}: {s.count} unratified "
                "data-tenant purity row(s)"
            )
        elif s.rule == 6:
            refuse(f"P6 {s.text.split(' - ')[0]} - W6 converts these; none were expected")
        else:
            refuse(f"P5 rule {s.rule}: {s.text}")
    if not found:
        ok("P5 no surprises")
    if fatal:
        return Preflight(lines, refusals, True, None, None, None, None)
    if not any(s.rule == 6 for s in found):
        ok("P6 no workspace company catalysts")

    move = v2_move.plan_move(conn)
    with conn.cursor() as cur:
        bad = {
            t: int(
                _scalar(
                    cur,
                    sql.SQL("SELECT count(*) AS n FROM {} WHERE " + _VOCABULARY_VIOLATION).format(
                        sql.Identifier(t)
                    ),
                )
            )
            for t in VALIDATED_CONSTRAINTS
        }
        if any(bad.values()):
            refuse(
                "P7 rows that would fail migration 0206's ratified_by vocabulary: "
                + ", ".join(f"{t} {n}" for t, n in bad.items())
            )
        else:
            ok("P7 every share-count and cash/burn ratified_by is in 0206's vocabulary")
        for r in move.refusals():
            refuse(f"P8 {r}")
        if not move.refusals():
            ok("P8 the move has no refusals")
        decisions = int(_scalar(cur, "SELECT count(*) AS n FROM operator_decision"))
        positions = int(
            _scalar(
                cur,
                "SELECT count(*) AS n FROM thesis WHERE position_entry_price IS NOT NULL OR "
                "position_current_price IS NOT NULL OR position_opened_on IS NOT NULL",
            )
        )
        conviction = int(_scalar(cur, "SELECT count(*) AS n FROM fact_theme_conviction"))
        lines.append(
            f"count: P9 kept: operator_decision {decisions}, theses with position fields {positions}, "
            f"fact_theme_conviction {conviction}"
        )
        resets = _reset_counts(cur)
        orphans = _orphans(cur)
        outside = [r for r in _references_into(cur) if r[0] not in RESET_ORDER]
        if outside:
            refuse(
                "P11 foreign key(s) into the reset tables from other tables (a delete could reach rows the "
                "plan does not list): "
                + ", ".join(f"{f} -> {t} (on delete {d})" for f, t, d in outside)
            )
        else:
            ok("P11 only the reset tables reference the reset tables")
        cut_at = _journal_marker(cur)
    doc = plan_document(name, move, resets, len(orphans), opts.orphan_purity)
    already = None
    if cut_at is not None:
        history = sum(resets.values())
        pending = move.total + (len(orphans) if opts.orphan_purity == "delete" else 0)
        when = cut_at.astimezone(timezone.utc).date().isoformat()
        if resets["calls"]:
            refuse(
                f"P10 already cut over on {when}, and calls holds {resets['calls']} row(s): a second reset "
                "would delete V2 nights"
            )
        elif history or pending:
            refuse(
                f"P10 already cut over on {when}, but {history} history row(s) and {pending} row(s) to move "
                "or delete are present: a second cutover is refused (investigate by hand)"
            )
        else:
            already = cut_at
            ok(f"P10 already cut over on {when}; nothing to do")
    else:
        ok("P10 not cut over yet")
    return Preflight(lines, refusals, False, already, doc, move, cutover.to_json(inv, found))


def _plan_lines(doc: dict[str, Any]) -> list[str]:
    out = []
    for table, m in doc["moves"].items():
        by = ", ".join(f"{k} {n}" for k, n in m["by_ratified"].items()) or "none"
        out.append(f"plan: move {table} {m['rows']} row(s) ({by})")
    for table, n in doc["resets"].items():
        out.append(f"plan: reset {table} {n} row(s)")
    o = doc["orphan_purity"]
    out.append(f"plan: orphan purity {o['rows']} row(s) ({o['action']})")
    return out


# --- the run -------------------------------------------------------------------------------------------------------


def _window_now(now: datetime) -> tuple[datetime, datetime] | None:
    s = get_settings()
    return window_bounds(
        now.astimezone(market_time.market_tz()),
        run_at=parse_run_at(s.cron_run_at),
        before_min=s.onboard_window_before_min,
        after_min=s.onboard_window_after_min,
    )


def _delete_all(cur: Any, table: str) -> int:
    """``DELETE FROM`` one reset table (never TRUNCATE, never CASCADE); the deleted row count."""
    cur.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(table)))
    return int(cur.rowcount)


def _result(code: int, text: str) -> int:
    print(f"RESULT: {text}. Exit {code}.")
    return code


def run(
    opts: Options,
    *,
    dump_runner: Callable[..., None] | None = None,
    backups_dir: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    """The CLI (module docstring). ``dump_runner`` and ``backups_dir`` are ``pipeline.backup``'s seams (tests
    inject a stub runner and a tmp dir); ``now`` is the clock for the nightly-window check."""
    print("V2 CUTOVER - RUN - " + ("APPLY" if opts.apply else "DRY RUN"))
    conn = session.connect()
    locks: list[Any] = []
    try:
        name = database_name(conn)
        conn.rollback()
        reason = guard_refusal(
            current=name,
            database=opts.database,
            confirm=opts.confirm,
            prod=opts.prod,
            apply=opts.apply,
            expect_plan=opts.expect_plan is not None,
        )
        if reason:
            print(f"target DB : {name} (REFUSED)")
            print(f"REFUSE: {reason}")
            return _result(EXIT_GUARD, "REFUSED - nothing read or written")
        print(f"target DB : {name} (confirmed{'; production, --prod' if opts.prod else ''})")
        if not opts.apply:
            conn.read_only = True  # a dry run PHYSICALLY cannot write
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")

        w = _window_now((now or market_time.market_now)())
        if w is not None:
            print(
                f"REFUSE: P3 inside the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M} ET); run outside it"
            )
            return _result(EXIT_BUSY, "BUSY - nothing written")
        print("ok: P3 outside the nightly window")

        if opts.apply:
            try:
                locks.append(acquire_repair_lock(holder=TOOL))
                for lock_id, what in JOB_LOCKS:
                    locks.append(acquire_job_lock(lock_id=lock_id, holder=TOOL, what=what))
            except (PassAlreadyRunning, JobAlreadyRunning) as exc:
                print(f"REFUSE: P4 {exc}")
                return _result(EXIT_BUSY, "BUSY - nothing written")
            print("ok: P4 holding the repair lock and job locks 3-7")

        pf = preflight(conn, opts, name)
        conn.rollback()
        for line in pf.lines:
            print(line)
        if pf.fatal:
            return _result(EXIT_GUARD, "REFUSED - an unmapped or missing table; nothing written")
        if pf.refusals:
            return _result(EXIT_REFUSED, "REFUSED - nothing written")
        assert pf.doc is not None and pf.move is not None and pf.plan_json is not None
        if pf.already_cut is not None:
            return _result(EXIT_OK, "ALREADY CUT OVER - nothing to do, no backup, no journal")
        for line in _plan_lines(pf.doc):
            print(line)
        if opts.expect_plan is not None:
            try:
                expected = json.loads(Path(opts.expect_plan).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(
                    f"REFUSE: --expect-plan {opts.expect_plan} cannot be read ({exc.__class__.__name__})"
                )
                return _result(EXIT_REFUSED, "REFUSED - nothing written")
            diffs = plan_differences(expected, pf.doc)
            if diffs:
                for d in diffs:
                    print(f"REFUSE: --expect-plan differs: {d}")
                return _result(EXIT_REFUSED, "REFUSED - nothing written")
            print(f"ok: the plan equals --expect-plan {opts.expect_plan}")
        if opts.plan_out is not None:
            Path(opts.plan_out).parent.mkdir(parents=True, exist_ok=True)
            Path(opts.plan_out).write_text(
                json.dumps(pf.doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"plan-out: {opts.plan_out}")
        if not opts.apply:
            how = (
                f"reuse {opts.reuse_backup}"
                if opts.reuse_backup
                else f"take a new '{BACKUP_LABEL}' backup"
            )
            print(
                f"backup: would {how}"
                + (" and the V1 history archive" if opts.history_archive else "")
            )
            return _result(EXIT_OK, "DRY RUN - nothing written")
        return _apply(conn, opts, name, pf, dump_runner=dump_runner, backups_dir=backups_dir)
    finally:
        try:
            conn.close()
        finally:
            for lock in reversed(locks):
                lock.release()


def _backup_step(
    conn: Any,
    opts: Options,
    name: str,
    *,
    dump_runner: Callable[..., None] | None,
    backups_dir: Path,
) -> tuple[str, str | None] | None:
    """Take (or reuse) and verify the backup, then the history archive. Returns ``(backup name, archive name)``,
    or ``None`` after printing the refusal (the caller exits 3). Never prints a connection string.
    """
    url = database_url()
    target = _url_dbname(url)
    if target != name:
        print(
            f"REFUSE: backup: the connection string names a different database ({target!r}) than the guarded "
            f"one ({name!r}); the backup would not protect it"
        )
        return None
    if opts.reuse_backup:
        path = Path(opts.reuse_backup)
        if not path.is_absolute() and path.parent == Path("."):
            path = backups_dir / path
        problem = verify_dump(path, backups_dir=backups_dir)
        if problem is None and path.name.endswith(_ARCHIVE_SLUG):
            problem = f"{path.name} is a history archive, not a full backup"
        if problem is None:
            created = backup._parse_backup_name(path.name)[0]  # type: ignore[index]
            with conn.cursor() as cur:
                newest = _newest_data_time(cur)
            conn.rollback()
            if newest is not None and created <= newest:
                problem = (
                    f"backup {path.name} ({created:%Y-%m-%dT%H:%M:%SZ}) is older than the newest data "
                    f"({newest.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ})"
                )
        if problem:
            print(f"REFUSE: backup: {problem}")
            return None
        full = path.name
        print(f"backup: {full} reused, verified (complete, {path.stat().st_size} bytes)")
    else:
        try:
            res = backup.run_backup(
                label=BACKUP_LABEL, database_url=url, base_dir=backups_dir, dump_runner=dump_runner
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a failed backup: nothing written
            print(f"REFUSE: backup failed: {_scrub(f'{exc.__class__.__name__}: {exc}', url)}")
            return None
        problem = verify_dump(res.path, backups_dir=backups_dir)
        if problem:
            print(f"REFUSE: backup: {problem}")
            return None
        full = res.name
        print(f"backup: {backups_dir / full} verified (complete, {res.bytes} bytes)")
    archive = None
    if opts.history_archive:
        try:
            res = backup.run_backup(
                label=ARCHIVE_LABEL,
                database_url=url,
                base_dir=backups_dir,
                dump_runner=dump_runner,
                tables=[f"public.{t}" for t in RESET_ORDER],
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"REFUSE: history archive failed: {_scrub(f'{exc.__class__.__name__}: {exc}', url)}"
            )
            return None
        problem = verify_dump(res.path, backups_dir=backups_dir)
        if problem:
            print(f"REFUSE: history archive: {problem}")
            return None
        archive = res.name
        print(f"archive: {backups_dir / archive} verified (complete, {res.bytes} bytes)")
    else:
        print("archive: skipped (--no-history-archive)")
    return full, archive


def _apply(
    conn: Any,
    opts: Options,
    name: str,
    pf: Preflight,
    *,
    dump_runner: Callable[..., None] | None,
    backups_dir: Path | None,
) -> int:
    started = datetime.now(timezone.utc)
    bdir = Path(backups_dir) if backups_dir is not None else backup._DEFAULT_BACKUPS
    got = _backup_step(conn, opts, name, dump_runner=dump_runner, backups_dir=bdir)
    if got is None:
        return _result(EXIT_BACKUP, "BACKUP FAILED - nothing written")
    full, archive = got
    assert pf.doc is not None and pf.plan_json is not None

    prov_dir = Path(opts.provenance_dir) if opts.provenance_dir is not None else _DEFAULT_PROVENANCE
    final = prov_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{name}.json"
    pending = final.with_name(final.name + ".pending")
    disabled: list[str] = []
    try:
        with conn.cursor() as cur:
            move = v2_move.plan_move(conn)
            resets = _reset_counts(cur)
            orphans = _orphans(cur)
            doc = plan_document(name, move, resets, len(orphans), opts.orphan_purity)
            diffs = plan_differences(pf.doc, doc) + move.refusals()
            if diffs:
                raise v2_move.MoveRefused(
                    "the database differs from the plan printed above: " + "; ".join(diffs)
                )
            base = _baseline(cur, pf.plan_json)
            moved = v2_move.relabel_legs(cur, move, disabled)
            deleted = 0
            touched = set(move.security_ids)
            if opts.orphan_purity == "delete" and orphans:
                cur.execute(
                    "DELETE FROM fact_revenue_mix WHERE id = ANY(%s)", ([o["id"] for o in orphans],)
                )
                deleted = int(cur.rowcount)
                if deleted != len(orphans):
                    raise v2_move.MoveRefused(
                        f"fact_revenue_mix: deleted {deleted} orphan row(s) but the plan said {len(orphans)}"
                    )
                touched |= {o["security_id"] for o in orphans}
            reset: dict[str, int] = {}
            for table in RESET_ORDER:
                reset[table] = _delete_all(cur, table)
                if reset[table] != resets[table]:
                    raise v2_move.MoveRefused(
                        f"{table}: deleted {reset[table]} row(s) but the plan said {resets[table]}"
                    )
            left = {t: n for t, n in _reset_counts(cur).items() if n}
            if left:
                raise v2_move.MoveRefused(f"rows left after the reset: {left}")
        stamped = mark_changed(conn, touched)
        with conn.cursor() as cur:
            for table, constraint in VALIDATED_CONSTRAINTS.items():
                cur.execute(
                    sql.SQL("ALTER TABLE {t} VALIDATE CONSTRAINT {c}").format(
                        t=sql.Identifier(table), c=sql.Identifier(constraint)
                    )
                )
            counts = {
                "moved": moved,
                "by_ratified": {t: m["by_ratified"] for t, m in doc["moves"].items()},
                "reset": reset,
                "orphan_purity": {
                    "rows": len(orphans),
                    "action": opts.orphan_purity,
                    "deleted": deleted,
                },
                "validated": list(VALIDATED_CONSTRAINTS.values()),
                "companies": stamped,
                "backup": full,
                "archive": archive,
            }
            total = sum(moved.values()) + sum(reset.values()) + deleted
            cur.execute(
                "INSERT INTO data_repair (tenant_id, tool, action, summary, counts, rows_changed, deletes, "
                "provenance) VALUES (%s, %s, 'apply', %s, %s, %s, true, %s) RETURNING id",
                (
                    DEFAULT_TENANT_ID,
                    TOOL,
                    f"V2 cutover: moved {sum(moved.values())} platform-fact row(s); reset "
                    f"{sum(reset.values())} V1 history row(s) ({reset['calls']} calls); orphan purity "
                    f"{opts.orphan_purity} ({deleted} deleted); backup {full}",
                    Json(_jsonable(counts)),
                    total,
                    str(final),
                ),
            )
            journal_id: UUID = cur.fetchone()["id"]
        prov = {
            "tool": TOOL,
            "database": name,
            "started_at": started.isoformat(),
            "journal_id": str(journal_id),
            "backup": full,
            "archive": archive,
            "plan": doc,
            "preflight": pf.lines,
            "counts": counts,
            "baseline": base,
        }
        prov_dir.mkdir(parents=True, exist_ok=True)
        pending.write_text(
            json.dumps(_jsonable(prov), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        conn.commit()
    except v2_move.MoveRefused as exc:
        _undo(conn, disabled, pending)
        print(f"REFUSE: {exc}")
        return _result(
            EXIT_REFUSED, f"REFUSED - rolled back, nothing written (backup kept: {full})"
        )
    except Exception as exc:  # noqa: BLE001 - every other failure: rolled back, exit 4
        _undo(conn, disabled, pending)
        print(f"FAILED: {_scrub(f'{exc.__class__.__name__}: {exc}', database_url())}")
        return _result(EXIT_ROLLED_BACK, f"ROLLED BACK - nothing written (backup kept: {full})")
    except BaseException:
        _undo(conn, disabled, pending)
        raise

    try:
        doc_done = json.loads(pending.read_text(encoding="utf-8"))
        doc_done["committed_at"] = datetime.now(timezone.utc).isoformat()
        pending.write_text(json.dumps(doc_done, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(pending, final)
    except OSError as exc:
        print(
            f"WARNING: the cutover COMMITTED, but the provenance file could not be finalized "
            f"({exc.__class__.__name__}); it is at {pending}"
        )
    for table, n in moved.items():
        print(f"moved: {table} {n}")
    for table, n in reset.items():
        print(f"reset: {table} {n}")
    print(f"orphan purity: {len(orphans)} row(s), {opts.orphan_purity} ({deleted} deleted)")
    print(f"validated: {len(VALIDATED_CONSTRAINTS)} constraints")
    print(f"companies stamped changed: {stamped}")
    print(f"journal: data_repair {journal_id}")
    print(f"provenance: {final}")
    return _result(EXIT_OK, "CUT OVER")


def _undo(conn: Any, disabled: list[str], pending: Path) -> None:
    """A failed transaction: roll back, check every trigger it disabled is on again, drop the pending file."""
    try:
        conn.rollback()
    except Exception:  # noqa: BLE001 - a dead session's transaction is already gone
        pass
    v2_move._restore_triggers(conn, disabled)
    pending.unlink(missing_ok=True)
