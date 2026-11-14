"""The V2 cutover dry run: ``python -m pipeline.v2_cutover plan [--json PATH]`` (V2 PR 1 / W0a).

READ-ONLY. It reads the live database's table list and, for every table: who owns it (``domain.scopes.owner_of``),
what the cutover will do with it (``CUTOVER_ACTION``; default ``keep``), its row count, its rows per tenant and
per ``ratified_by`` (found from ``information_schema.columns``, never a hard-coded list). Then it names every
SURPRISE: a row where no plan expects it.

It fails closed. A table the ownership registry (``domain.scopes``: ``SCOPE_OF`` + ``UNSCOPED_TABLES``) does not
name, or a mapped table the database lacks, exits 2; any other surprise exits 1; a clean database exits 0. The
connection is read-only (``conn.read_only`` and ``SET TRANSACTION READ ONLY``) and rolled back: nothing is
written to the database. ``--json PATH`` writes the same data to a file for comparing rehearsals.

The rules name TABLES, never a scope member that V2 deletes, so changing ``SCOPE_OF`` (V2 PR 3) needs no edit
here: the generic rules (3, 4) follow ``owner_of``, and the tables with a dedicated rule (5-9) are skipped by the
generic ones whatever their scope.
"""

from __future__ import annotations

import argparse
import json
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg import sql

import domain.scopes as scopes
from db import session

# What the cutover does with a table. EXCEPTIONS only: every other mapped table is ``keep``.
CUTOVER_ACTION: dict[str, str] = {
    # the founding workspace's share counts and cash/burn are relabeled to the data tenant (V2 PR 2)
    "fact_shares_outstanding": "move→data",
    "fact_cash_burn": "move→data",
    # data-tenant RATIFIED purity rows are relabeled to the founding workspace (operator decision D2 (a))
    "fact_revenue_mix": "move→founding",
    # workspace company catalysts and theme conviction become thesis catalysts (W6 / W10)
    "fact_catalyst": "convert (W6/W10)",
    "fact_theme_conviction": "convert (W6/W10)",
    # the record restarts at cutover
    "calls": "reset",
    "call_annotation": "reset",
    "edition": "reset",
    "edition_event": "reset",
    "edition_entry": "reset",
    "edition_call": "reset",
    "edition_outcome": "reset",
    "edition_cause": "reset",
    # bookkeeping that is pruned, not carried
    "user_action_count": "prune",
    "security_change": "prune",
}

# The tables with a DEDICATED rule (5-9 below): the generic owner rules (3, 4) skip them, so each finding is
# reported once, by its most specific sentence, before and after V2 PR 3 changes their scope.
_SHARE_AND_CASH = ("fact_cash_burn", "fact_shares_outstanding")
_DEDICATED = frozenset({*_SHARE_AND_CASH, "fact_catalyst", "fact_revenue_mix", "thesis"})

_FATAL_RULES = frozenset({1, 2})
_WRAP = 110


@dataclass(frozen=True)
class TenantRow:
    id: UUID
    kind: str
    name: str
    users: int  # app_user rows whose workspace is this tenant


@dataclass(frozen=True)
class TableCount:
    name: str
    rows: int
    # rows per tenant; None when the table has no tenant_id column
    by_tenant: dict[UUID, int] | None = None
    # rows per (tenant, ratified_by); None when the table has no ratified_by column. The tenant is None when the
    # table has ratified_by but no tenant_id.
    by_ratified: dict[tuple[UUID | None, str | None], int] | None = None


@dataclass(frozen=True)
class Inventory:
    database: str
    read_at: datetime  # the database clock
    migrations_applied: int
    newest_migration: str | None
    tenants: tuple[TenantRow, ...]
    tables: dict[str, TableCount]  # every base table in schema public


@dataclass(frozen=True)
class Surprise:
    rule: int
    table: str
    count: int
    text: str

    @property
    def fatal(self) -> bool:
        return self.rule in _FATAL_RULES


# --- naming --------------------------------------------------------------------------------------------------


def short_id(tenant: UUID | None) -> str:
    """A tenant id in the table: ``…`` plus its last four hex digits."""
    return "NULL" if tenant is None else "…" + tenant.hex[-4:]


def mapped_tables() -> set[str]:
    """Every table the ownership registry names (read at call time, so a test can swap the map)."""
    return set(scopes.SCOPE_OF) | set(scopes.UNSCOPED_TABLES)


def plan_of(table: str) -> str:
    return CUTOVER_ACTION.get(table, "keep")


def _owner(table: str) -> str | None:
    try:
        return scopes.owner_of(table)
    except KeyError:
        return None


# --- the read --------------------------------------------------------------------------------------------------


def _one(cur: Any, query: str | sql.Composable, params: Sequence[Any] = ()) -> dict[str, Any]:
    cur.execute(query, params)
    return cur.fetchone()


def inventory(conn: Any) -> Inventory:
    """Read the database: its name and clock, the applied migrations, the tenants, and every base table's counts.
    Reads only. ``conn`` is a psycopg connection with dict rows."""
    with conn.cursor() as cur:
        head = _one(cur, "SELECT current_database() AS db, now() AS at")
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name IN ('tenant_id', 'ratified_by') "
            "ORDER BY table_name, column_name"
        )
        columns: dict[str, set[str]] = {}
        for r in cur.fetchall():
            columns.setdefault(r["table_name"], set()).add(r["column_name"])

        applied, newest = 0, None
        if "schema_migrations" in names:
            m = _one(cur, "SELECT count(*) AS n, max(filename) AS newest FROM schema_migrations")
            applied, newest = int(m["n"]), m["newest"]

        tenants: tuple[TenantRow, ...] = ()
        if "tenant" in names:
            users_join = (
                "LEFT JOIN app_user u ON u.workspace_id = t.id" if "app_user" in names else ""
            )
            users_count = "count(u.id)" if "app_user" in names else "0"
            cur.execute(
                f"SELECT t.id, t.kind, t.name, {users_count} AS users FROM tenant t {users_join} "
                "GROUP BY t.id, t.kind, t.name ORDER BY t.id"
            )
            tenants = tuple(
                TenantRow(r["id"], r["kind"], r["name"], int(r["users"])) for r in cur.fetchall()
            )

        tables: dict[str, TableCount] = {}
        for name in names:
            ident = sql.Identifier(name)
            rows = int(_one(cur, sql.SQL("SELECT count(*) AS n FROM {}").format(ident))["n"])
            cols = columns.get(name, set())
            by_tenant: dict[UUID, int] | None = None
            by_ratified: dict[tuple[UUID | None, str | None], int] | None = None
            if "ratified_by" in cols:
                tenant_expr = sql.SQL("tenant_id" if "tenant_id" in cols else "NULL::uuid")
                cur.execute(
                    sql.SQL(
                        "SELECT {t} AS tenant_id, ratified_by, count(*) AS n FROM {tbl} "
                        "GROUP BY 1, 2 ORDER BY 1, 2"
                    ).format(t=tenant_expr, tbl=ident)
                )
                by_ratified = {
                    (r["tenant_id"], r["ratified_by"]): int(r["n"]) for r in cur.fetchall()
                }
                if "tenant_id" in cols:
                    by_tenant = {}
                    for (tenant, _), n in by_ratified.items():
                        by_tenant[tenant] = by_tenant.get(tenant, 0) + n  # type: ignore[index]
            elif "tenant_id" in cols:
                cur.execute(
                    sql.SQL("SELECT tenant_id, count(*) AS n FROM {} GROUP BY 1 ORDER BY 1").format(
                        ident
                    )
                )
                by_tenant = {r["tenant_id"]: int(r["n"]) for r in cur.fetchall()}
            tables[name] = TableCount(name, rows, by_tenant, by_ratified)

    return Inventory(
        database=head["db"],
        read_at=head["at"],
        migrations_applied=applied,
        newest_migration=newest,
        tenants=tenants,
        tables=tables,
    )


# --- the rules ----------------------------------------------------------------------------------------------


def _ratified(t: TableCount, tenant: UUID, *, is_set: bool) -> int:
    return sum(
        n
        for (tid, by), n in (t.by_ratified or {}).items()
        if tid == tenant and (by is not None) == is_set
    )


def surprises(inv: Inventory) -> list[Surprise]:
    """Every row where no plan expects it, sorted by rule, table, text. Rules 1 and 2 are fatal (exit 2)."""
    data = scopes.DATA_TENANT_ID
    founding = scopes.FOUNDING_WORKSPACE_ID
    out: list[Surprise] = []
    mapped = mapped_tables()

    # 1. a database table nobody owns
    for name in sorted(set(inv.tables) - mapped):
        n = inv.tables[name].rows
        out.append(
            Surprise(
                1,
                name,
                n,
                f"{name}: {n} row(s) - not named in domain.scopes (SCOPE_OF or UNSCOPED_TABLES); name its "
                "owner there before any cutover",
            )
        )
    # 2. a mapped table the database lacks
    for name in sorted(mapped - set(inv.tables)):
        out.append(
            Surprise(
                2,
                name,
                0,
                f"{name}: named in domain.scopes but missing from this database - run "
                "`python -m db.migrate` first",
            )
        )

    for name in sorted(set(inv.tables) & mapped):
        t = inv.tables[name]
        owner = _owner(name)
        by_tenant = t.by_tenant or {}
        if name in _DEDICATED:
            continue
        for tenant, n in sorted(by_tenant.items(), key=lambda kv: str(kv[0])):
            # 3. a platform table's rows under a workspace
            if owner == "platform" and tenant != data and n:
                out.append(
                    Surprise(
                        3,
                        name,
                        n,
                        f"{name}: {n} row(s) under workspace {short_id(tenant)} - a platform table's rows "
                        "belong to the data tenant; V2 reads none of these",
                    )
                )
            # 4. a workspace table's rows under the data tenant
            if owner == "user" and tenant == data and n:
                out.append(
                    Surprise(
                        4,
                        name,
                        n,
                        f"{name}: {n} row(s) under the data tenant - a workspace table's rows belong to a "
                        "user's workspace; V2 reads none of these",
                    )
                )

    # 5. share counts or cash/burn in a workspace other than the founding one (the move carries only founding's)
    for name in _SHARE_AND_CASH:
        t = inv.tables.get(name)
        for tenant, n in sorted(((t and t.by_tenant) or {}).items(), key=lambda kv: str(kv[0])):
            if tenant not in (data, founding) and n:
                out.append(
                    Surprise(
                        5,
                        name,
                        n,
                        f"{name}: {n} row(s) under workspace {short_id(tenant)}, not the founding workspace - "
                        "the move carries only the founding workspace's rows (decide: move, leave unread, "
                        "or drop)",
                    )
                )

    cat = inv.tables.get("fact_catalyst")
    if cat is not None:
        # 6. any company catalyst under a workspace
        for tenant in sorted({tid for tid in (cat.by_tenant or {}) if tid != data}, key=str):
            for is_set, label in ((True, "set"), (False, "NULL")):
                n = _ratified(cat, tenant, is_set=is_set)
                if n:
                    out.append(
                        Surprise(
                            6,
                            "fact_catalyst",
                            n,
                            f"fact_catalyst: {n} row(s) under workspace {short_id(tenant)} with ratified_by "
                            f"{label} - V2 converts these to thesis catalysts (W6/W10); none were expected",
                        )
                    )
        # 7. a data-tenant company catalyst with a ratifier
        n = _ratified(cat, data, is_set=True)
        if n:
            out.append(
                Surprise(
                    7,
                    "fact_catalyst",
                    n,
                    f"fact_catalyst: {n} row(s) under the data tenant with ratified_by set - a feed row "
                    "carries no ratifier; no plan covers these",
                )
            )

    # 8. a data-tenant purity row with no ratifier (D2 moves only ratified rows)
    mix = inv.tables.get("fact_revenue_mix")
    if mix is not None:
        n = _ratified(mix, data, is_set=False)
        if n:
            out.append(
                Surprise(
                    8,
                    "fact_revenue_mix",
                    n,
                    f"fact_revenue_mix: {n} row(s) under the data tenant with ratified_by NULL - purity is "
                    "read from the workspace only in V2 and no plan moves an unratified row",
                )
            )

    # 9. a thesis under the data tenant; a user whose workspace is the data tenant
    thesis = inv.tables.get("thesis")
    n = ((thesis and thesis.by_tenant) or {}).get(data, 0)
    if n:
        out.append(
            Surprise(
                9,
                "thesis",
                n,
                f"thesis: {n} row(s) under the data tenant - a thesis belongs to a workspace",
            )
        )
    n = sum(r.users for r in inv.tenants if r.id == data)
    if n:
        out.append(
            Surprise(
                9,
                "app_user",
                n,
                f"app_user: {n} user(s) whose workspace is the data tenant - a user's workspace is never "
                "the data tenant",
            )
        )

    return sorted(out, key=lambda s: (s.rule, s.table, s.text))


def exit_code(found: Sequence[Surprise]) -> int:
    """0 clean, 1 any surprise, 2 any unmapped or missing table."""
    if any(s.fatal for s in found):
        return 2
    return 1 if found else 0


# --- the output ------------------------------------------------------------------------------------------------


def _by_tenant_text(t: TableCount) -> str:
    if t.by_tenant is None:
        return "-"
    parts = [
        f"{short_id(k)}:{n}" for k, n in sorted(t.by_tenant.items(), key=lambda kv: str(kv[0]))
    ]
    return " ".join(parts) or "-"


def _by_ratified_text(t: TableCount) -> str:
    """Rows per ``ratified_by`` summed over the tenants (the JSON keeps the per-tenant split)."""
    if t.by_ratified is None:
        return "-"
    totals: dict[str, int] = {}
    for (_, by), n in t.by_ratified.items():
        key = "null" if by is None else by
        totals[key] = totals.get(key, 0) + n
    return " ".join(f"{k}:{n}" for k, n in sorted(totals.items())) or "-"


def _result_line(found: Sequence[Surprise]) -> str:
    code = exit_code(found)
    if code == 2:
        n = sum(1 for s in found if s.fatal)
        return f"RESULT: REFUSE - {n} unmapped or missing table(s). Exit 2."
    if code == 1:
        return f"RESULT: REFUSE - {len(found)} surprise(s). Exit 1."
    return "RESULT: CLEAN - no surprises. Exit 0."


def render(inv: Inventory, found: Sequence[Surprise]) -> str:
    """The dry run's text. Columns are as wide as the plan's example and widen to fit longer content."""
    at = inv.read_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    schema = f"{inv.migrations_applied} files applied" + (
        f", newest {inv.newest_migration}" if inv.newest_migration else ""
    )
    lines = [
        "V2 CUTOVER - DRY RUN (read-only; nothing written)",
        f"target DB : {inv.database}",
        f"read at   : {at} (database clock)",
        f"schema    : {schema}",
    ]
    for i, r in enumerate(inv.tenants):
        label = "tenants   : " if i == 0 else " " * 12
        lines.append(
            f"{label}{r.id}  {r.kind:<10} {chr(34) + r.name + chr(34):<22} users {r.users}"
        )
    if not inv.tenants:
        lines.append("tenants   : none")
    lines.append("")

    rows = []
    for name in sorted(inv.tables):
        t = inv.tables[name]
        owner = _owner(name)
        plan = plan_of(name) if owner is not None else "?"
        rows.append(
            (name, owner or "UNMAPPED", plan, str(t.rows), _by_tenant_text(t), _by_ratified_text(t))
        )
    w_name = max([24] + [len(r[0]) for r in rows]) + 2
    w_owner = 12
    w_plan = max([12] + [len(r[2]) for r in rows])  # ROWS is right-aligned with a leading space
    w_rows = max([6] + [len(r[3]) + 1 for r in rows])
    w_tenant = max([23] + [len(r[4]) for r in rows]) + 2
    lines.append(
        f"{'TABLE':<{w_name}}{'OWNER':<{w_owner}}{'PLAN':<{w_plan}}{'ROWS':>{w_rows}}  "
        f"{'BY TENANT':<{w_tenant}}BY ratified_by"
    )
    for name, owner, plan, n, by_t, by_r in rows:
        lines.append(
            f"{name:<{w_name}}{owner:<{w_owner}}{plan:<{w_plan}}{n:>{w_rows}}  {by_t:<{w_tenant}}{by_r}"
        )
    lines.append("")

    lines.append(f"SURPRISES ({len(found)})")
    for i, s in enumerate(found, 1):
        lines.extend(
            textwrap.wrap(
                s.text,
                width=_WRAP,
                initial_indent=f"  {i}. ",
                subsequent_indent=" " * (len(str(i)) + 4),
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    unmapped = sorted(s.table for s in found if s.rule == 1)
    lines.append(f"UNMAPPED TABLES: {', '.join(unmapped) if unmapped else 'none'}")
    lines.append(_result_line(found))
    return "\n".join(lines) + "\n"


def to_json(inv: Inventory, found: Sequence[Surprise]) -> dict[str, Any]:
    """The same data as ``render``, as plain JSON (ids as strings), for comparing rehearsals."""
    return {
        "database": inv.database,
        "read_at": inv.read_at.astimezone(timezone.utc).isoformat(),
        "schema": {"applied": inv.migrations_applied, "newest": inv.newest_migration},
        "tenants": [
            {"id": str(r.id), "kind": r.kind, "name": r.name, "users": r.users} for r in inv.tenants
        ],
        "tables": [
            {
                "name": name,
                "owner": _owner(name),
                "plan": plan_of(name) if _owner(name) is not None else None,
                "rows": t.rows,
                "by_tenant": (
                    None
                    if t.by_tenant is None
                    else {
                        str(k): n for k, n in sorted(t.by_tenant.items(), key=lambda kv: str(kv[0]))
                    }
                ),
                "by_ratified": (
                    None
                    if t.by_ratified is None
                    else [
                        {"tenant": None if k is None else str(k), "ratified_by": by, "rows": n}
                        for (k, by), n in sorted(
                            t.by_ratified.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or "")
                        )
                    ]
                ),
            }
            for name, t in sorted(inv.tables.items())
        ],
        "surprises": [
            {"rule": s.rule, "table": s.table, "count": s.count, "text": s.text, "fatal": s.fatal}
            for s in found
        ],
        "unmapped": sorted(s.table for s in found if s.rule == 1),
        "missing": sorted(s.table for s in found if s.rule == 2),
        "exit_code": exit_code(found),
    }


# --- the CLI ---------------------------------------------------------------------------------------------------


def _plan(json_path: str | None) -> int:
    conn = session.connect()
    try:
        conn.read_only = True  # the session itself refuses a write
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
        inv = inventory(conn)
        found = surprises(inv)
        conn.rollback()
    finally:
        conn.close()
    print(render(inv, found), end="")
    if json_path:
        Path(json_path).write_text(
            json.dumps(to_json(inv, found), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return exit_code(found)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.v2_cutover",
        description="The V2 cutover tooling. `plan` is a read-only dry run: every table, its owner, its cutover "
        "action and its counts, and every row no plan expects. Exit 0 clean, 1 surprises, 2 an unmapped or "
        "missing table.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="the read-only dry run (writes nothing to the database)")
    plan.add_argument("--json", metavar="PATH", help="also write the same data as JSON to PATH")
    args = p.parse_args(argv)
    if args.command == "plan":
        return _plan(args.json)
    return 2  # pragma: no cover - argparse refuses an unknown command first


if __name__ == "__main__":
    raise SystemExit(main())
