"""The V2 platform-facts move (V2 PR 2 / W0b): ``python -m pipeline.v2_cutover move-platform-facts [--apply]``.

V2 reads share counts and cash/burn from the DATA tenant only, and purity from the workspace only. Today the
founding workspace (``…0002``) holds every ratified share count and cash/burn row (migration 0078 put them there),
and a purity row ratified with ``pipeline.ratify_revenue_mix``'s default sits in the data tenant. This module moves
them, IN PLACE, with migration 0078's method:

- ``fact_shares_outstanding``, ``fact_cash_burn``: every founding-workspace row -> the data tenant;
- ``fact_revenue_mix``: every data-tenant row with ``ratified_by`` set -> the founding workspace (operator decision
  D2 (a)); an unratified purity row stays (the cutover dry run names it as a surprise).

Only ``tenant_id`` changes: ids, ``recorded_at``, ``supersedes`` and every other column are untouched, so the
public clock and the version chains read exactly as before.

SAFETY (the point of this module):

- **Fork-only.** ``assert_rehearsal_db`` refuses any database it does not positively recognize as a rehearsal
  database: a test database (``alphadeck_test…``) or a fork (``alphadeck_<name>``, the names
  ``scripts/refresh-fork.sh`` derives), never ``alphadeck`` (prod), ``alphadeck_dev``, ``alphadeck_sig`` or
  ``alphadeck_prod``. The name is read from the SAME connection that would write (``current_database()``), before
  anything else is read. Exit 2. The dry run is guarded too: fail closed. W10's prod cutover gets its own entry
  point that calls ``plan_move`` and ``apply_move`` directly.
- **Dry run by default.** Without ``--apply`` the connection is read-only (``conn.read_only`` and ``SET
  TRANSACTION READ ONLY``) and rolled back.
- **One transaction, the trigger always back on.** The apply disables each table's append-only trigger
  (``no_update``) BY NAME inside its transaction, relabels, re-enables it, re-counts, and commits. Any failure
  rolls the transaction back, and ``ALTER TABLE … DISABLE TRIGGER`` is transactional, so the rollback restores
  the trigger too; ``apply_move`` also re-enables every trigger it disabled before it re-raises (belt and
  braces), on a clean transaction.
- **Refuses, writing nothing,** on: share or cash rows in any other workspace, a key collision (the target
  tenant already holds the row's ``(source_ref, recorded_at)``: the tables' ``UNIQUE (tenant_id, source_ref,
  recorded_at)`` would reject the relabel), the data tenant or the founding workspace missing. Exit 1.
- **Locked, stamped, journaled.** The CLI apply holds the repair lock (``pipeline.run_lock``; exit 75 while a
  pass, a backfill or another repair runs), stamps every moved company changed in the same transaction
  (``db.change_stamp.mark_changed``: the dashboards' cache), and journals ONE ``data_repair`` row (tool
  ``v2_move_platform_facts``) with the counts after the commit.
- **Idempotent.** A second run plans zero rows, prints "nothing to move", exits 0 and journals nothing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from psycopg import sql

from db import session
from db.change_stamp import mark_changed
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID
from pipeline.run_lock import repair_lock_or_exit
from repositories import data_repair_repo

TOOL = "v2_move_platform_facts"
TRIGGER = (
    "no_update"  # the append-only trigger on every fact table (raise_no_update), disabled BY NAME
)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_REHEARSAL = 2

# --- the guard -------------------------------------------------------------------------------------------------

# The tiers that are never a rehearsal database. ``alphadeck`` itself fails the pattern (no suffix).
_RESERVED = frozenset({"alphadeck_dev", "alphadeck_sig", "alphadeck_prod"})
# A fork's database is ``alphadeck_<FORK_NAME>`` with FORK_NAME ``^[a-z0-9_]+$`` (scripts/refresh-fork.sh); the
# per-worktree test database is ``alphadeck_test…`` (db/testdb.py) and matches the same pattern.
_REHEARSAL = re.compile(r"alphadeck_[a-z0-9_]+")


class NotARehearsalDatabase(RuntimeError):
    """The database is not one this move may write: only a fork or a test database."""


def assert_rehearsal_db(name: str) -> None:
    """Return quietly when ``name`` is a rehearsal database (a fork ``alphadeck_<name>`` or a test database
    ``alphadeck_test…``); raise ``NotARehearsalDatabase`` for anything else: prod ``alphadeck``, the reserved
    tiers ``alphadeck_dev`` / ``alphadeck_sig`` / ``alphadeck_prod``, any other name, any odd character or case.
    Allow-list, so a name this function does not positively recognize is refused."""
    if not isinstance(name, str) or not _REHEARSAL.fullmatch(name) or name in _RESERVED:
        raise NotARehearsalDatabase(
            f"database {name!r} is not a rehearsal database (a fork alphadeck_<name> or a test database "
            "alphadeck_test...); the V2 move refuses it. Prod (alphadeck), alphadeck_dev, alphadeck_sig and "
            "alphadeck_prod are never moved by this command."
        )


def database_name(conn: Any) -> str:
    """``current_database()`` of the connection that would write (the guard reads THIS, never a URL)."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_database() AS db")
        row = cur.fetchone()
    return str(row["db"] if isinstance(row, dict) else row[0])


# --- the plan --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """One table's relabel: every row under ``source`` (only the ratified ones when ``ratified_only``) to
    ``target``."""

    table: str
    source: UUID
    target: UUID
    ratified_only: bool
    # rows in another workspace refuse the move (the move carries only the founding workspace's rows)
    refuse_other_workspaces: bool


LEGS: tuple[Leg, ...] = (
    Leg("fact_shares_outstanding", FOUNDING_WORKSPACE_ID, DATA_TENANT_ID, False, True),
    Leg("fact_cash_burn", FOUNDING_WORKSPACE_ID, DATA_TENANT_ID, False, True),
    # operator decision D2 (a): ratified purity rows leave the data tenant for the founding workspace
    Leg("fact_revenue_mix", DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, True, False),
)
MOVED_TABLES: tuple[str, ...] = tuple(leg.table for leg in LEGS)


@dataclass(frozen=True)
class LegPlan:
    leg: Leg
    to_move: int
    by_ratified: dict[str, int]  # the rows to move, per ratified_by ("null" for NULL)
    already_in_target: int
    other_workspaces: dict[
        UUID, int
    ]  # rows under any tenant but the data tenant and the founding workspace
    unratified_left: (
        int  # rows under the source this leg leaves where they are (ratified_only legs)
    )
    collisions: int  # rows to move whose (source_ref, recorded_at) the target already holds
    security_ids: frozenset[UUID]

    @property
    def table(self) -> str:
        return self.leg.table


@dataclass(frozen=True)
class MovePlan:
    database: str
    legs: tuple[LegPlan, ...]
    missing_tenants: tuple[UUID, ...] = ()

    @property
    def total(self) -> int:
        return sum(p.to_move for p in self.legs)

    @property
    def security_ids(self) -> frozenset[UUID]:
        return frozenset().union(*(p.security_ids for p in self.legs))

    def refusals(self) -> list[str]:
        """Every reason the apply refuses (empty = the apply may run)."""
        out = [
            f"tenant {t} is missing from this database (the data tenant and the founding workspace must exist)"
            for t in self.missing_tenants
        ]
        for p in self.legs:
            if p.leg.refuse_other_workspaces:
                for tenant, n in sorted(p.other_workspaces.items(), key=lambda kv: str(kv[0])):
                    out.append(
                        f"{p.table}: {n} row(s) under workspace {tenant}, not the founding workspace - the move "
                        "carries only the founding workspace's rows (decide: move, leave unread, or drop)"
                    )
            if p.collisions:
                out.append(
                    f"{p.table}: {p.collisions} row(s) to move whose (source_ref, recorded_at) the target tenant "
                    "already holds - the relabel would break UNIQUE (tenant_id, source_ref, recorded_at)"
                )
        return out


def _where(leg: Leg, alias: str = "") -> sql.Composable:
    col = (alias + ".") if alias else ""
    clause = col + "tenant_id = %s"
    if leg.ratified_only:
        clause += f" AND {col}ratified_by IS NOT NULL"
    return sql.SQL(clause)  # type: ignore[arg-type]


def _plan_leg(cur: Any, leg: Leg) -> LegPlan:
    tbl = sql.Identifier(leg.table)
    cur.execute(
        sql.SQL(
            "SELECT coalesce(ratified_by, 'null') AS by, count(*) AS n FROM {t} WHERE {w} "
            "GROUP BY 1 ORDER BY 1"
        ).format(t=tbl, w=_where(leg)),
        (leg.source,),
    )
    by_ratified = {r["by"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute(
        sql.SQL("SELECT DISTINCT security_id FROM {t} WHERE {w}").format(t=tbl, w=_where(leg)),
        (leg.source,),
    )
    security_ids = frozenset(r["security_id"] for r in cur.fetchall())
    cur.execute(
        sql.SQL("SELECT count(*) AS n FROM {t} WHERE tenant_id = %s").format(t=tbl), (leg.target,)
    )
    already = int(cur.fetchone()["n"])
    cur.execute(
        sql.SQL(
            "SELECT tenant_id, count(*) AS n FROM {t} WHERE tenant_id <> ALL(%s) GROUP BY 1 ORDER BY 1"
        ).format(t=tbl),
        ([DATA_TENANT_ID, FOUNDING_WORKSPACE_ID],),
    )
    other = {r["tenant_id"]: int(r["n"]) for r in cur.fetchall()}
    unratified_left = 0
    if leg.ratified_only:
        cur.execute(
            sql.SQL(
                "SELECT count(*) AS n FROM {t} WHERE tenant_id = %s AND ratified_by IS NULL"
            ).format(t=tbl),
            (leg.source,),
        )
        unratified_left = int(cur.fetchone()["n"])
    cur.execute(
        sql.SQL(
            "SELECT count(*) AS n FROM {t} s WHERE {w} AND EXISTS (SELECT 1 FROM {t} d "
            "WHERE d.tenant_id = %s AND d.source_ref = s.source_ref AND d.recorded_at = s.recorded_at)"
        ).format(t=tbl, w=_where(leg, "s")),
        (leg.source, leg.target),
    )
    collisions = int(cur.fetchone()["n"])
    return LegPlan(
        leg=leg,
        to_move=sum(by_ratified.values()),
        by_ratified=by_ratified,
        already_in_target=already,
        other_workspaces=other,
        unratified_left=unratified_left,
        collisions=collisions,
        security_ids=security_ids,
    )


def plan_move(conn: Any) -> MovePlan:
    """What the move would do. Reads only, in the caller's transaction (a dict-row psycopg connection)."""
    name = database_name(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM tenant WHERE id = ANY(%s)", ([DATA_TENANT_ID, FOUNDING_WORKSPACE_ID],)
        )
        present = {r["id"] for r in cur.fetchall()}
        missing = tuple(t for t in (DATA_TENANT_ID, FOUNDING_WORKSPACE_ID) if t not in present)
        legs = tuple(_plan_leg(cur, leg) for leg in LEGS)
    return MovePlan(database=name, legs=legs, missing_tenants=missing)


# --- the apply -------------------------------------------------------------------------------------------------


class MoveRefused(RuntimeError):
    """The move refused; nothing was written (its transaction was rolled back)."""


@dataclass(frozen=True)
class MoveResult:
    moved: dict[str, int]
    stamped: int
    journal_id: UUID | None = None
    by_ratified: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.moved.values())


def _trigger(verb: str, table: str) -> sql.Composable:
    return sql.SQL("ALTER TABLE {t} {v} TRIGGER {g}").format(
        t=sql.Identifier(table),
        v=sql.SQL(verb),  # type: ignore[arg-type]  # a literal: DISABLE or ENABLE
        g=sql.Identifier(TRIGGER),
    )


def triggers_enabled(conn: Any, tables: Sequence[str] = MOVED_TABLES) -> dict[str, str]:
    """``{table: pg_trigger.tgenabled}`` for each table's ``no_update`` trigger ('O' = on, 'D' = off)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.relname AS t, tg.tgenabled AS e FROM pg_trigger tg "
            "JOIN pg_class c ON c.oid = tg.tgrelid WHERE tg.tgname = %s AND c.relname = ANY(%s)",
            (TRIGGER, list(tables)),
        )
        return {r["t"]: str(r["e"]) for r in cur.fetchall()}


def _restore_triggers(conn: Any, tables: Sequence[str]) -> None:
    """After a failed apply's rollback: every trigger it disabled is on again. The rollback restores it
    (``ALTER TABLE`` is transactional); this checks, and re-enables and commits any that is not."""
    if not tables:
        return
    try:
        off = [t for t, e in triggers_enabled(conn, tables).items() if e != "O"]
        with conn.cursor() as cur:
            for t in off:
                cur.execute(_trigger("ENABLE", t))
        conn.commit()
    except (
        Exception
    ):  # noqa: BLE001 - the original failure is the one to report; a dead session rolled back
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - a closed connection's transaction is already gone
            pass


def apply_move(conn: Any, plan: MovePlan) -> MoveResult:
    """Relabel the planned rows in ONE transaction on ``conn``, then journal one ``data_repair`` row.

    Refuses (``NotARehearsalDatabase`` / ``MoveRefused``; nothing written) when the database is not a rehearsal
    database (the guard runs here too, on this connection), when the plan has a refusal, or when the re-count
    inside the transaction disagrees with the plan. A plan with nothing to move writes and journals nothing.
    The caller holds the repair lock (``pipeline.run_lock.repair_lock_or_exit``)."""
    assert_rehearsal_db(database_name(conn))
    reasons = plan.refusals()
    if reasons:
        conn.rollback()
        raise MoveRefused("; ".join(reasons))
    if plan.total == 0:
        conn.rollback()
        return MoveResult(moved={p.table: 0 for p in plan.legs}, stamped=0)

    disabled: list[str] = []
    moved: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for p in plan.legs:
                leg = p.leg
                cur.execute(_trigger("DISABLE", leg.table))
                disabled.append(leg.table)
                cur.execute(
                    sql.SQL("UPDATE {t} SET tenant_id = %s WHERE {w}").format(
                        t=sql.Identifier(leg.table), w=_where(leg)
                    ),
                    (leg.target, leg.source),
                )
                moved[leg.table] = cur.rowcount
                cur.execute(_trigger("ENABLE", leg.table))
                # the re-count: every planned row moved, and nothing this leg moves is left under the source
                cur.execute(
                    sql.SQL("SELECT count(*) AS n FROM {t} WHERE {w}").format(
                        t=sql.Identifier(leg.table), w=_where(leg)
                    ),
                    (leg.source,),
                )
                left = int(cur.fetchone()["n"])
                if moved[leg.table] != p.to_move or left:
                    raise MoveRefused(
                        f"{leg.table}: moved {moved[leg.table]} row(s) but the plan said {p.to_move}, and "
                        f"{left} row(s) are left under the source - rolled back, nothing written"
                    )
        stamped = mark_changed(conn, plan.security_ids)
        conn.commit()
    except BaseException:
        conn.rollback()
        _restore_triggers(conn, disabled)
        raise

    by_ratified = {p.table: dict(p.by_ratified) for p in plan.legs}
    journal_id = data_repair_repo.journal(
        conn,
        tool=TOOL,
        summary=(
            f"V2 move: {moved['fact_shares_outstanding']} share count(s) and {moved['fact_cash_burn']} "
            f"cash/burn row(s) to the data tenant; {moved['fact_revenue_mix']} ratified purity row(s) to "
            "the founding workspace"
        ),
        rows_changed=sum(moved.values()),
        counts={"moved": moved, "by_ratified": by_ratified, "companies": stamped},
    )
    return MoveResult(moved=moved, stamped=stamped, journal_id=journal_id, by_ratified=by_ratified)


# --- the output and the CLI ------------------------------------------------------------------------------------


def short_id(tenant: UUID) -> str:
    """``data`` for the data tenant, else ``…`` plus the last four hex digits (the dry run's form)."""
    return "data" if tenant == DATA_TENANT_ID else "…" + tenant.hex[-4:]


def _by_text(by: dict[str, int]) -> str:
    return ", ".join(f"{k} {n}" for k, n in sorted(by.items())) or "none"


def render_plan(plan: MovePlan, *, apply: bool) -> list[str]:
    """The move's text, one line per table (the result line is printed by ``run``)."""
    lines = [
        "V2 MOVE - share counts and cash/burn to the data tenant - "
        + ("APPLY" if apply else "DRY RUN"),
        f"target DB : {plan.database} (rehearsal database: allowed)",
    ]
    width = max(len(p.table) for p in plan.legs)
    for p in plan.legs:
        leg = p.leg
        if leg.ratified_only:
            lines.append(
                f"{p.table:<{width}} : {p.to_move} ratified row(s) {short_id(leg.source)} -> "
                f"{short_id(leg.target)} ({_by_text(p.by_ratified)}); {p.unratified_left} unratified "
                f"row(s) stay; {p.collisions} key collisions"
            )
        else:
            lines.append(
                f"{p.table:<{width}} : {p.to_move} row(s) {short_id(leg.source)} -> {short_id(leg.target)} "
                f"({_by_text(p.by_ratified)}); {p.already_in_target} already in {short_id(leg.target)}; "
                f"{sum(p.other_workspaces.values())} in other workspaces; {p.collisions} key collisions"
            )
    lines.append(f"companies stamped changed: {len(plan.security_ids)}")
    return lines


def run(*, apply: bool) -> int:
    """The CLI: guard, (lock), plan, print, (apply). Exit 0 moved or nothing to move, 1 refused, 2 not a
    rehearsal database, 75 (``SystemExit``) the repair lock is busy."""
    conn = session.connect()
    lock = None
    try:
        name = database_name(conn)
        conn.rollback()
        try:
            assert_rehearsal_db(name)
        except NotARehearsalDatabase as exc:
            print("V2 MOVE - share counts and cash/burn to the data tenant - REFUSED")
            print(f"target DB : {name} (NOT a rehearsal database)")
            print(f"REFUSE: {exc}")
            print(f"RESULT: REFUSED - nothing read or written. Exit {EXIT_NOT_REHEARSAL}.")
            return EXIT_NOT_REHEARSAL
        if apply:
            lock = repair_lock_or_exit(
                holder=TOOL
            )  # exit 75 while a pass, a backfill or a repair runs
        else:
            conn.read_only = True  # a dry run PHYSICALLY cannot write
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
        plan = plan_move(conn)
        for line in render_plan(plan, apply=apply):
            print(line)
        reasons = plan.refusals()
        if reasons:
            conn.rollback()
            for r in reasons:
                print(f"REFUSE: {r}")
            print(f"RESULT: REFUSED - nothing written. Exit {EXIT_REFUSED}.")
            return EXIT_REFUSED
        if plan.total == 0:
            conn.rollback()
            print(f"RESULT: nothing to move. Exit {EXIT_OK}.")
            return EXIT_OK
        if not apply:
            conn.rollback()
            print(f"RESULT: would move {plan.total} row(s). Run with --apply.")
            return EXIT_OK
        try:
            result = apply_move(conn, plan)
        except MoveRefused as exc:
            print(f"REFUSE: {exc}")
            print(f"RESULT: REFUSED - nothing written. Exit {EXIT_REFUSED}.")
            return EXIT_REFUSED
        print(
            f"RESULT: moved {result.total} row(s) in one transaction; journal data_repair "
            f"{result.journal_id}"
        )
        return EXIT_OK
    finally:
        conn.close()
        if lock is not None:
            lock.release()
