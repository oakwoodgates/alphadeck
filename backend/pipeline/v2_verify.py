"""The V2 cutover's VERIFY step (W10b): ``python -m pipeline.v2_cutover verify --stage ...``.

READ-ONLY. It proves, after each stage of the cutover, that the result is what was intended, against the baseline
the apply path (``pipeline/v2_reset.py``) wrote into its provenance file before its first write. Its most
important proof is RECALL: no card lost a company.

::

    python -m pipeline.v2_cutover verify --stage reset|backfill|nightly|edition --database NAME [--prod]
        --provenance PATH [--json PATH] [--health-url URL] [--night YYYY-MM-DD] [--run-kind cron|any]

**Read-only, provably.** One connection, opened with ``default_transaction_read_only = on`` for the session (so
every transaction on it, not only the first, refuses a write), ``conn.read_only`` and a REPEATABLE READ snapshot
(every check reads the same instant), ``lock_timeout`` 5 s (it never queues behind a lock). Plain ``SELECT``s only:
``ACCESS SHARE`` locks, which block no insert, update or delete the nightly makes; no advisory lock, no journal
row. It writes nothing but its printed report and, with ``--json PATH``, that one file.

**The guard** (the apply path's own pure functions, ``v2_reset.assert_cutover_target``): the name read with
``current_database()`` on the verifying connection must equal ``--database``; the production name ``alphadeck``
only with ``--prod``, ``--prod`` only with it; otherwise a fork or a test database. No ``--confirm`` (nothing is
written). Then the provenance file: it must exist, not be a ``.pending`` file (an unfinished run), parse, be a
finished ``v2_cutover`` record (``committed_at``, a baseline) and name this database.

**Stages are cumulative** (``edition`` runs every check). Each check is a small named function returning
``(ok, sentence)``; one line each, ``ok:`` or ``FAIL:``, plus ``note:`` lines that never fail; then a verdict.

- reset: V1 no dry-run surprise (rules 1-9) but the acknowledged orphan purity (rule 8, ``--orphan-purity leave``
  with the provenance's count); V2 every migration applied and 0206's two constraints validated; V3 tenants: the data
  tenant is ``kind='data'``, no thesis and no user on it, every enabled user's workspace a ``kind='workspace'``
  tenant, at least one enabled ``super_admin``; V3 users equal the baseline; V4 every baseline thesis with its id,
  tenant, ``created_at`` and ``archived_at``, and no new one; **V5 recall**: every baseline ``basket_member`` row
  still present, by id AND by (thesis, security_id, segment), a missing one NAMED (thesis, ticker, security);
  V5 children (per thesis, the nine child tables) and V5 decisions (``operator_decision``) equal the baseline;
  V6 platform facts: no share-count or cash/burn row outside the data tenant, the data tenant's counts equal the
  baseline's data + founding counts, every company that had a share count (a cash/burn row) still has one in the
  data tenant; V7 purity: no ratified purity row on the data tenant, founding purity = baseline + moved; V8 V1's
  history KEPT (operator decision 2026-11-19): before the V2 cron starts (reset, backfill) each of the eight history
  tables holds exactly the baseline's rows; from the nightly stage on, the ``calls`` rows recorded at or before the
  cutover's journal row equal the baseline's, no other history table has fewer rows, and every baseline edition is
  still there; V9 exactly one ``v2_cutover`` journal row, the provenance's, with its counts; V10 ``--health-url``
  answers 200 (a note when not given).
- backfill: + B1 every non-ETF company on a live card has a ``backfill`` extraction attempt; B2 decision B4
  unlocked (a ``done`` backfill attempt); B3 the share of those companies with a share count (and with a cash/burn
  row) is at or above the baseline's; ``note:`` attempts by status and failures by reason.
- nightly: the N checks read V2's rows only, the ``calls`` rows RECORDED AFTER the cutover's journal row (V1's
  rows are history, kept and checked by V8). + for ``--night`` (default the newest such ``calls.asof``) N1 the
  night has V2 call rows ("not yet" otherwise: V1's last night never passes for the first V2 night); N2 a row for
  every live thesis created by then; N3 no reconstructed V2 row; N4 the night's rows are ``run_kind`` cron (any
  with ``--run-kind any``); N5 no V2 row dated before the cutover's journal date (market time); N6 no call row
  under the data tenant; ``note:`` the share of the night's rows with ``ingest_fresh=false``.
- edition: + E1 for each workspace whose baseline held an edition, the NEXT one (the automatic cut's Edition N+1
  after the kept Edition N): its parent the kept edition, its label a rule change (``rule change:``), the kept
  edition closed ("not yet" before the cut); E2 an ``edition_entry`` row for every live thesis of a workspace in
  its newest edition, once that edition is new since the cutover; E3 no edition made since the cutover for a
  workspace with no live card (a kept V1 edition is history, never a stray).

From the ``nightly`` stage on (after the V2 cron starts, step 10, users edit their cards again), the checks that
compare user-editable state for EQUALITY (users, archived state, new theses, child and decision counts, founding
purity) report a difference as a ``note:``; recall (no baseline basket row gone), a baseline thesis gone or moved,
and every platform-fact check stay FAILs at every stage.

Exit codes (none reuses one of the apply path's with another meaning): 0 every check passed; 2 cannot verify (the
wrong database or flags, the provenance file missing, ``.pending``, unreadable or of another database, or a table
the ownership registry does not name / a named table the database lacks); 5 one or more checks failed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

import domain.scopes as scopes
from db import session
from domain import market_time
from pipeline import v2_cutover as cutover
from pipeline import v2_move
from pipeline import v2_reset as reset
from platform_values.extraction import backfill_applied, extractable
from repositories import platform_extraction_repo

STAGES: tuple[str, ...] = ("reset", "backfill", "nightly", "edition")
RUN_KINDS: tuple[str, ...] = ("cron", "any")

EXIT_VERIFIED = 0
EXIT_CANNOT_VERIFY = (
    2  # the same meaning as the apply path's 2: the wrong target, or a table nobody owns
)
EXIT_FAILED = 5  # one or more checks failed (the apply path's codes stop at 4, and 75)

LOCK_TIMEOUT = "5s"
_NAMED = 25  # a FAIL line names this many items; the --json report lists every one

# read at call time through this module, so a test can inject a name (never by connecting to a prod-named DB)
database_name = v2_move.database_name

_BASELINE_KEYS = (
    "theses",
    "basket_members",
    "children",
    "operator_decision",
    "share_security_ids",
    "cash_security_ids",
    "moved_tables_by_tenant",
    "app_users",
    "history",
    "editions",
)
_PLATFORM = (("fact_shares_outstanding", "share count"), ("fact_cash_burn", "cash/burn"))


@dataclass(frozen=True)
class Options:
    database: str
    stage: str
    provenance: Path | None = None
    prod: bool = False
    json_path: Path | None = None
    health_url: str | None = None
    night: date | str | None = None
    run_kind: str = "cron"


@dataclass
class Ctx:
    """What every check reads: one cursor on the read-only snapshot, the provenance and the stage."""

    cur: Any
    conn: Any
    name: str
    prov: dict[str, Any]
    opts: Options
    health_get: Callable[[str], int]
    notes: list[str] = field(default_factory=list)
    fatal: bool = False
    cut_at: datetime | None = (
        None  # the cutover's journal row (``applied_at``): V1's rows were recorded by then
    )
    cut_date: date | None = None
    night: date | None = None
    details: dict[str, list[str]] = field(default_factory=dict)

    @property
    def base(self) -> dict[str, Any]:
        return self.prov["baseline"]

    @property
    def frozen(self) -> bool:
        """Before the V2 cron starts (steps 7-9) nobody edits a card: user-editable state must EQUAL the baseline."""
        return STAGES.index(self.opts.stage) <= STAGES.index("backfill")

    def note(self, text: str) -> None:
        self.notes.append(text)


Result = tuple[bool, str] | None  # None: nothing to check (the check added a note)


# --- small helpers ------------------------------------------------------------------------------------------


def _rows(cur: Any, query: Any, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cur.execute(query, tuple(params))
    return [dict(r) for r in cur.fetchall()]


def _scalar(cur: Any, query: Any, params: Iterable[Any] = ()) -> Any:
    return reset._scalar(cur, query, tuple(params))


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _ts(v: Any) -> datetime | None:
    """A timestamp from the database or from the provenance JSON (``str(datetime)``), comparable."""
    if v is None:
        return None
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _named(items: list[str]) -> str:
    head = "; ".join(items[:_NAMED])
    return head + (
        f"; ... and {len(items) - _NAMED} more (--json lists all)" if len(items) > _NAMED else ""
    )


def _tickers(cur: Any, ids: Iterable[str]) -> dict[str, str]:
    ids = sorted(set(ids))
    if not ids:
        return {}
    rows = _rows(cur, "SELECT id, ticker FROM security_master WHERE id = ANY(%s::uuid[])", (ids,))
    return {str(r["id"]): r["ticker"] for r in rows}


def _live_theses(cur: Any) -> list[dict[str, Any]]:
    return _rows(
        cur,
        "SELECT id, tenant_id, name, created_at FROM thesis WHERE archived_at IS NULL AND tenant_id <> %s "
        "ORDER BY id",
        (scopes.DATA_TENANT_ID,),
    )


def _thesis_label(t: dict[str, Any]) -> str:
    return f"{t['id']} ({t.get('name')!r})"


# --- the reset stage ----------------------------------------------------------------------------------------


def check_surprises(ctx: Ctx) -> Result:
    """V1: the dry run's rules 1-9, except the orphan purity the run acknowledged (rule 8, ``leave``)."""
    found = cutover.surprises(cutover.inventory(ctx.conn))
    orphan = (ctx.prov.get("counts") or {}).get("orphan_purity") or {}
    problems = []
    for s in found:
        if s.rule == 8 and orphan.get("action") == "leave" and s.count == orphan.get("rows"):
            ctx.note(
                f"V1 rule 8 acknowledged (--orphan-purity leave): {s.count} unratified data-tenant purity row(s)"
            )
            continue
        ctx.fatal = ctx.fatal or s.fatal
        problems.append(f"rule {s.rule}: {s.text}")
    if problems:
        return False, f"{_plural(len(problems), 'surprise')}: " + _named(problems)
    return (
        True,
        "no surprise beyond the acknowledged orphan purity; every table is in a known scope",
    )


def check_schema(ctx: Ctx) -> Result:
    """V2: every migration applied; migration 0206's two constraints validated."""
    missing = reset._unapplied_migrations(ctx.cur)
    rows = _rows(
        ctx.cur,
        "SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(%s)",
        (list(reset.VALIDATED_CONSTRAINTS.values()),),
    )
    valid = {r["conname"]: bool(r["convalidated"]) for r in rows}
    not_valid = [c for c in reset.VALIDATED_CONSTRAINTS.values() if not valid.get(c)]
    problems = []
    if missing:
        problems.append(f"{_plural(len(missing), 'migration')} not applied ({', '.join(missing)})")
    if not_valid:
        problems.append(f"not validated: {', '.join(not_valid)}")
    if problems:
        return False, "; ".join(problems)
    return True, "every migration applied; both 0206 constraints validated"


def check_tenants(ctx: Ctx) -> Result:
    """V3: the data tenant holds no thesis and no user; every enabled user's workspace is a workspace; a steward."""
    cur, data = ctx.cur, scopes.DATA_TENANT_ID
    problems = []
    kind = _scalar(cur, "SELECT kind FROM tenant WHERE id = %s", (data,))
    if kind != "data":
        problems.append(f"the data tenant {data} is kind {kind!r}, not 'data'")
    n = int(_scalar(cur, "SELECT count(*) AS n FROM thesis WHERE tenant_id = %s", (data,)))
    if n:
        problems.append(f"{_plural(n, 'thesis', 'theses')} on the data tenant")
    n = int(_scalar(cur, "SELECT count(*) AS n FROM app_user WHERE workspace_id = %s", (data,)))
    if n:
        problems.append(f"{_plural(n, 'user')} whose workspace is the data tenant")
    bad = _rows(
        cur,
        "SELECT u.id, t.kind FROM app_user u LEFT JOIN tenant t ON t.id = u.workspace_id "
        "WHERE u.disabled_at IS NULL AND t.kind IS DISTINCT FROM 'workspace' ORDER BY u.id",
    )
    if bad:
        problems.append(
            "enabled user(s) whose workspace is not a workspace tenant: "
            + ", ".join(f"{r['id']} ({r['kind']})" for r in bad)
        )
    admins = int(
        _scalar(
            cur,
            "SELECT count(*) AS n FROM app_user WHERE role = 'super_admin' AND disabled_at IS NULL",
        )
    )
    if not admins:
        problems.append("no enabled super_admin (the steward)")
    if problems:
        return False, "; ".join(problems)
    return True, f"no thesis or user on the data tenant; {_plural(admins, 'enabled super_admin')}"


def check_users(ctx: Ctx) -> Result:
    """V3: every user as the baseline had them (id, role, workspace, disabled state)."""
    now = {
        str(r["id"]): r
        for r in _rows(ctx.cur, "SELECT id, role, workspace_id, disabled_at FROM app_user")
    }
    diffs = []
    for u in ctx.base["app_users"]:
        r = now.get(str(u["id"]))
        if r is None:
            diffs.append(f"user {u['id']} is gone")
            continue
        for key in ("role", "workspace_id"):
            if _s(r[key]) != _s(u[key]):
                diffs.append(f"user {u['id']} {key} {u[key]} -> {r[key]}")
        if _ts(r["disabled_at"]) != _ts(u["disabled_at"]):
            diffs.append(f"user {u['id']} disabled_at {u['disabled_at']} -> {r['disabled_at']}")
    added = sorted(set(now) - {str(u["id"]) for u in ctx.base["app_users"]})
    diffs += [f"user {uid} is new" for uid in added]
    total = len(ctx.base["app_users"])
    if not diffs:
        return True, f"{_plural(total, 'user')}, unchanged"
    if not ctx.frozen:
        ctx.note(f"V3 users changed since the cutover (users act after step 10): {_named(diffs)}")
        return True, f"{_plural(total, 'baseline user')}; changes since are notes at this stage"
    return False, _named(diffs)


def check_theses(ctx: Ctx) -> Result:
    """V4: every baseline thesis, same id, tenant, created_at and archived_at; no new thesis while frozen."""
    now = {
        str(r["id"]): r
        for r in _rows(ctx.cur, "SELECT id, tenant_id, created_at, archived_at FROM thesis")
    }
    hard, soft = [], []
    for t in ctx.base["theses"]:
        r = now.get(str(t["id"]))
        if r is None:
            hard.append(f"thesis {t['id']} is gone")
            continue
        if _s(r["tenant_id"]) != _s(t["tenant_id"]):
            hard.append(f"thesis {t['id']} moved tenant {t['tenant_id']} -> {r['tenant_id']}")
        if _ts(r["created_at"]) != _ts(t["created_at"]):
            hard.append(f"thesis {t['id']} created_at {t['created_at']} -> {r['created_at']}")
        if _ts(r["archived_at"]) != _ts(t["archived_at"]):
            soft.append(f"thesis {t['id']} archived state {t['archived_at']} -> {r['archived_at']}")
    added = sorted(set(now) - {str(t["id"]) for t in ctx.base["theses"]})
    soft += [f"thesis {tid} is new" for tid in added]
    if not ctx.frozen and soft:
        ctx.note(f"V4 card changes since the cutover: {_named(soft)}")
        soft = []
    problems = hard + soft
    ctx.details["V4 theses"] = problems
    if problems:
        return False, _named(problems)
    return (
        True,
        f"{_plural(len(ctx.base['theses']), 'thesis', 'theses')}: ids, tenants and dates unchanged",
    )


def check_recall(ctx: Ctx) -> Result:
    """V5 RECALL: every baseline basket_member row is still present, by id and by (thesis, security, segment).
    A missing row is named: the thesis, the ticker, the security and the segment."""
    rows = _rows(ctx.cur, "SELECT id, thesis_id, security_id, segment FROM basket_member")
    by_id = {str(r["id"]): r for r in rows}
    keys = {(str(r["thesis_id"]), _s(r["security_id"]), r["segment"]) for r in rows}
    dropped, total = [], 0
    for tid, members in sorted(ctx.base["basket_members"].items()):
        for m in members:
            total += 1
            key = (tid, _s(m["security_id"]), m["segment"])
            r = by_id.get(str(m["id"]))
            same_row = (
                r is not None
                and (
                    str(r["thesis_id"]),
                    _s(r["security_id"]),
                    r["segment"],
                )
                == key
            )
            if same_row and key in keys:
                continue
            why = "its row id is gone" if key in keys else "no row for it"
            dropped.append(
                f"thesis {tid} lost {m['ticker']} (security {m['security_id']}, segment {m['segment']!r}, "
                f"row id {m['id']}: {why})"
            )
    head = (
        f"{_plural(len(ctx.base['theses']), 'thesis', 'theses')}, {_plural(total, 'basket row')}, "
        f"{len(dropped)} dropped"
    )
    added = len(rows) - (total - len(dropped))
    if added > 0:
        ctx.note(f"V5 {_plural(added, 'basket row')} added since the cutover")
    ctx.details["V5 recall"] = dropped
    if dropped:
        return False, f"{head}: {_named(dropped)}"
    return True, head


def check_children(ctx: Ctx) -> Result:
    """V5: per thesis, the rows of each child table equal the baseline."""
    now: dict[str, dict[str, int]] = {}
    for table in reset._CHILD_TABLES:
        for r in _rows(
            ctx.cur,
            sql.SQL("SELECT thesis_id, count(*) AS n FROM {} GROUP BY 1").format(
                sql.Identifier(table)
            ),
        ):
            now.setdefault(str(r["thesis_id"]), {})[table] = int(r["n"])
    diffs = []
    for t in ctx.base["theses"]:
        tid = str(t["id"])
        was, is_ = ctx.base["children"].get(tid, {}), now.get(tid, {})
        for table in reset._CHILD_TABLES:
            if was.get(table, 0) != is_.get(table, 0):
                diffs.append(f"thesis {tid} {table} {was.get(table, 0)} -> {is_.get(table, 0)}")
    if not diffs:
        return (
            True,
            f"per-thesis counts of {len(reset._CHILD_TABLES)} child tables equal the baseline",
        )
    if not ctx.frozen:
        ctx.note(f"V5 child rows changed since the cutover: {_named(diffs)}")
        return True, "child counts: changes since the cutover are notes at this stage"
    return False, _named(diffs)


def check_decisions(ctx: Ctx) -> Result:
    """V5: the operator's decisions are all kept."""
    was = int(ctx.base["operator_decision"])
    n = int(_scalar(ctx.cur, "SELECT count(*) AS n FROM operator_decision"))
    if n == was:
        return True, f"operator_decision {n} (the baseline's)"
    if not ctx.frozen and n > was:
        ctx.note(f"V5 operator_decision {was} -> {n} since the cutover")
        return True, f"operator_decision {n}, at or above the baseline's {was}"
    return False, f"operator_decision {n}, the baseline had {was}"


def check_platform_facts(ctx: Ctx) -> Result:
    """V6: share counts and cash/burn live in the data tenant only; none lost."""
    cur, data, founding = ctx.cur, str(scopes.DATA_TENANT_ID), str(scopes.FOUNDING_WORKSPACE_ID)
    problems, lost_named = [], []
    for (table, label), key in zip(
        _PLATFORM, ("share_security_ids", "cash_security_ids"), strict=True
    ):
        by = {
            str(r["tenant_id"]): int(r["n"])
            for r in _rows(
                cur,
                sql.SQL("SELECT tenant_id, count(*) AS n FROM {} GROUP BY 1").format(
                    sql.Identifier(table)
                ),
            )
        }
        outside = {t: n for t, n in by.items() if t != data and n}
        if outside:
            problems.append(
                f"{table}: "
                + ", ".join(f"{n} row(s) under workspace {t}" for t, n in sorted(outside.items()))
            )
        was = ctx.base["moved_tables_by_tenant"].get(table, {})
        expected = int(was.get(data, 0)) + int(was.get(founding, 0))
        n = by.get(data, 0)
        if ctx.opts.stage == "reset" and n != expected:
            problems.append(
                f"{table}: {n} data-tenant row(s), the baseline's data + founding is {expected}"
            )
        elif n < expected:
            problems.append(
                f"{table}: {n} data-tenant row(s), fewer than the baseline's {expected}"
            )
        have = {
            str(r["security_id"])
            for r in _rows(
                cur,
                sql.SQL("SELECT DISTINCT security_id FROM {} WHERE tenant_id = %s").format(
                    sql.Identifier(table)
                ),
                (scopes.DATA_TENANT_ID,),
            )
        }
        lost = sorted(set(ctx.base[key]) - have)
        if lost:
            names = _tickers(cur, lost)
            items = [f"{names.get(s, '?')} ({s})" for s in lost]
            lost_named += [f"{label}: {i}" for i in items]
            problems.append(
                f"{_plural(len(lost), 'company', 'companies')} lost a {label}: {_named(items)}"
            )
    ctx.details["V6 platform facts"] = lost_named
    if problems:
        return False, "; ".join(problems)
    return True, (
        f"share counts and cash/burn only in the data tenant; {len(ctx.base['share_security_ids'])} companies "
        f"with a share count and {len(ctx.base['cash_security_ids'])} with cash/burn, none lost"
    )


def check_purity(ctx: Ctx) -> Result:
    """V7: no ratified purity row on the data tenant; the founding workspace's purity = baseline + moved."""
    cur = ctx.cur
    ratified = int(
        _scalar(
            cur,
            "SELECT count(*) AS n FROM fact_revenue_mix WHERE tenant_id = %s AND ratified_by IS NOT NULL",
            (scopes.DATA_TENANT_ID,),
        )
    )
    founding = int(
        _scalar(
            cur,
            "SELECT count(*) AS n FROM fact_revenue_mix WHERE tenant_id = %s",
            (scopes.FOUNDING_WORKSPACE_ID,),
        )
    )
    was = int(
        ctx.base["moved_tables_by_tenant"]
        .get("fact_revenue_mix", {})
        .get(str(scopes.FOUNDING_WORKSPACE_ID), 0)
    )
    moved = int(((ctx.prov.get("counts") or {}).get("moved") or {}).get("fact_revenue_mix", 0))
    problems = []
    if ratified:
        problems.append(f"{_plural(ratified, 'ratified purity row')} on the data tenant")
    if founding != was + moved:
        text = f"founding purity {founding}, expected the baseline's {was} + {moved} moved"
        if ctx.frozen:
            problems.append(text)
        else:
            ctx.note(f"V7 {text} (purity is the user's to edit after step 10)")
    if problems:
        return False, "; ".join(problems)
    return True, f"no ratified purity on the data tenant; founding purity {founding}"


def check_history(ctx: Ctx) -> Result:
    """V8: V1's history is KEPT (operator decision 2026-11-19). Before the V2 cron starts each history table holds
    exactly the baseline's rows; after it, the nightly and the editions job append, so the ``calls`` rows recorded
    at or before the cutover equal the baseline's and no other history table has fewer rows. Every baseline
    edition is still there, at every stage."""
    base = {t: int(n) for t, n in ctx.base["history"].items()}
    now = {
        t: int(_scalar(ctx.cur, sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))))
        for t in reset.HISTORY_TABLES
    }
    problems: list[str] = []
    if ctx.frozen:
        diffs = [f"{t} {base.get(t, 0)} -> {now[t]}" for t in now if now[t] != base.get(t, 0)]
        if diffs:
            problems.append(
                "history rows differ from the baseline (nothing writes them until the V2 cron starts): "
                + ", ".join(diffs)
            )
    else:
        if ctx.cut_at is None:
            problems.append("no cutover journal row to date the cutover")
        else:
            v1 = int(
                _scalar(
                    ctx.cur,
                    "SELECT count(*) AS n FROM calls WHERE recorded_at <= %s",
                    (ctx.cut_at,),
                )
            )
            if v1 != base.get("calls", 0):
                problems.append(
                    f"calls recorded before the cutover {v1}, the baseline had {base.get('calls', 0)}"
                )
        fewer = [
            f"{t} {base.get(t, 0)} -> {now[t]}"
            for t in now
            if t != "calls" and now[t] < base.get(t, 0)
        ]
        if fewer:
            problems.append("history rows lost: " + ", ".join(fewer))
        grown = [f"{t} {base.get(t, 0)} -> {now[t]}" for t in now if now[t] > base.get(t, 0)]
        if grown:
            ctx.note(f"V8 history appended since the cutover: {', '.join(grown)}")
    ids = {str(r["id"]) for r in _rows(ctx.cur, "SELECT id FROM edition")}
    gone = [
        f"{e['id']} (workspace {e['tenant_id']}, Edition {e['number']})"
        for e in ctx.base["editions"]
        if str(e["id"]) not in ids
    ]
    if gone:
        problems.append(f"{_plural(len(gone), 'baseline edition')} gone: {_named(gone)}")
    ctx.details["V8 history"] = problems
    if problems:
        return False, "; ".join(problems)
    if ctx.frozen:
        return (
            True,
            "V1's history kept: " + ", ".join(f"{t} {now[t]}" for t in now) + " (the baseline's)",
        )
    return True, (
        f"V1's history kept: calls recorded before the cutover {base.get('calls', 0)} (the baseline's), "
        f"no history table below the baseline, {_plural(len(ctx.base['editions']), 'baseline edition')} present"
    )


def check_journal(ctx: Ctx) -> Result:
    """V9: exactly one ``v2_cutover`` journal row: the provenance's, with the same counts."""
    rows = _rows(
        ctx.cur,
        "SELECT id, applied_at, counts FROM data_repair WHERE tool = %s ORDER BY applied_at",
        (reset.TOOL,),
    )
    if len(rows) != 1:
        return False, f"{len(rows)} '{reset.TOOL}' journal rows, expected exactly 1"
    row = rows[0]
    if str(row["id"]) != str(ctx.prov.get("journal_id")):
        return (
            False,
            f"journal row {row['id']} is not the provenance's ({ctx.prov.get('journal_id')})",
        )
    if reset._jsonable(row["counts"]) != ctx.prov.get("counts"):
        return False, f"journal row {row['id']}'s counts differ from the provenance's"
    return True, f"one '{reset.TOOL}' journal row ({row['id']}), its counts equal the provenance's"


def _http_status(url: str) -> int:
    try:
        with urllib.request.urlopen(
            url, timeout=10
        ) as r:  # noqa: S310 - an operator-given health URL
            return int(r.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def check_health(ctx: Ctx) -> Result:
    """V10 (optional): ``--health-url`` answers 200."""
    url = ctx.opts.health_url
    if not url:
        ctx.note("V10 health not checked (no --health-url)")
        return None
    try:
        status = ctx.health_get(url)
    except Exception as exc:  # noqa: BLE001 - any failure to answer is the failure
        return False, f"{url} did not answer ({exc.__class__.__name__})"
    if status != 200:
        return False, f"{url} answered {status}, not 200"
    return True, f"{url} answered 200"


# --- the backfill stage -------------------------------------------------------------------------------------


def _extractable_on_cards(ctx: Ctx) -> list[dict[str, Any]]:
    return [c for c in platform_extraction_repo.on_card_companies(ctx.conn) if extractable(c)]


def check_backfill_attempts(ctx: Ctx) -> Result:
    """B1: every non-ETF company on a live card has at least one ``backfill`` extraction attempt."""
    companies = _extractable_on_cards(ctx)
    tried = {
        str(r["security_id"])
        for r in _rows(
            ctx.cur,
            "SELECT DISTINCT security_id FROM platform_extraction_attempt WHERE tenant_id = %s "
            "AND run_kind = 'backfill'",
            (scopes.DATA_TENANT_ID,),
        )
    }
    rows = _rows(
        ctx.cur,
        "SELECT status, coalesce(error, detail, shares_reason) AS why, count(*) AS n "
        "FROM platform_extraction_attempt WHERE tenant_id = %s AND run_kind = 'backfill' GROUP BY 1, 2",
        (scopes.DATA_TENANT_ID,),
    )
    by_status: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    for r in rows:
        by_status[r["status"]] += int(r["n"])
        if r["status"] != "done":
            failures[f"{r['status']}: {r['why'] or 'no reason'}"] += int(r["n"])
    ctx.note(
        "B attempts by status: "
        + (", ".join(f"{k} {n}" for k, n in sorted(by_status.items())) or "none")
    )
    if failures:
        ctx.note(
            "B failures by reason: " + ", ".join(f"{k} {n}" for k, n in failures.most_common(10))
        )
    missing = [c for c in companies if str(c["security_id"]) not in tried]
    items = [f"{c['ticker']} ({c['security_id']})" for c in missing]
    ctx.details["B1 attempts"] = items
    if missing:
        return False, (
            f"{_plural(len(missing), 'company', 'companies')} of {len(companies)} on a live card with no "
            f"backfill attempt: {_named(items)}"
        )
    return (
        True,
        f"every one of {_plural(len(companies), 'company', 'companies')} on a live card was attempted",
    )


def check_b4(ctx: Ctx) -> Result:
    """B2: decision B4 is unlocked (a ``done`` backfill attempt exists)."""
    if backfill_applied(ctx.conn):
        return True, "B4 unlocked: a backfill attempt extracted a company"
    return False, "B4 still locked: no backfill attempt is 'done' (re-run the backfill, live)"


def check_coverage(ctx: Ctx) -> Result:
    """B3: the share of live-card companies with a share count (and with cash/burn) is at or above the
    baseline's (the live-card companies of the baseline's own cards, before the cutover)."""
    cur = ctx.cur
    kinds = {
        str(r["id"]): r["instrument_kind"]
        for r in _rows(cur, "SELECT id, instrument_kind FROM security_master")
    }
    live = {str(t["id"]) for t in ctx.base["theses"] if t["archived_at"] is None}
    before = {
        str(m["security_id"])
        for tid, members in ctx.base["basket_members"].items()
        if tid in live
        for m in members
        if m["security_id"] is not None
        and extractable({"instrument_kind": kinds.get(str(m["security_id"]))})
    }
    now = {str(c["security_id"]) for c in _extractable_on_cards(ctx)}
    problems, parts = [], []
    for (table, label), key in zip(
        _PLATFORM, ("share_security_ids", "cash_security_ids"), strict=True
    ):
        have = {
            str(r["security_id"])
            for r in _rows(
                cur,
                sql.SQL("SELECT DISTINCT security_id FROM {} WHERE tenant_id = %s").format(
                    sql.Identifier(table)
                ),
                (scopes.DATA_TENANT_ID,),
            )
        }
        was = len(before & set(ctx.base[key])) / len(before) if before else 0.0
        is_ = len(now & have) / len(now) if now else 1.0
        text = f"{label} {len(now & have)}/{len(now)} ({is_:.1%}; baseline {was:.1%})"
        parts.append(text)
        if is_ < was:
            problems.append(f"{text}: below the baseline")
    if problems:
        return False, "; ".join(problems)
    return True, "; ".join(parts)


# --- the nightly stage --------------------------------------------------------------------------------------


def _night_rows(ctx: Ctx) -> list[dict[str, Any]]:
    """The night's V2 rows: recorded after the cutover (V1's rows for the same night are history)."""
    return _rows(
        ctx.cur,
        "SELECT thesis_id, tenant_id, run_kind, reconstructed, ingest_fresh FROM calls "
        "WHERE asof = %s AND recorded_at > %s",
        (ctx.night, ctx.cut_at),
    )


def check_night(ctx: Ctx) -> Result:
    """N1: the night verified has V2 call rows (by default the newest ``calls.asof`` recorded after the cutover);
    "not yet" otherwise. V1's rows are kept, so V1's last night must never pass for the first V2 night.
    """
    if ctx.cut_at is None:
        return False, "no cutover journal row to date the cutover"
    if ctx.opts.night is not None:
        night = date.fromisoformat(str(ctx.opts.night))
        ctx.night = night
        n = len(_night_rows(ctx))
        if not n:
            ctx.night = None  # nothing for N2-N6 to read
            return (
                False,
                f"no V2 call row for night {night} (recorded after the cutover): the night has not been "
                "recorded (not yet?)",
            )
    else:
        ctx.night = _scalar(
            ctx.cur, "SELECT max(asof) AS d FROM calls WHERE recorded_at > %s", (ctx.cut_at,)
        )
        if ctx.night is None:
            return False, (
                "no V2 nightly call recorded yet: not yet (the first V2 night writes them; V1's kept rows "
                "do not count)"
            )
        n = len(_night_rows(ctx))
    stale = sum(1 for r in _night_rows(ctx) if r["ingest_fresh"] is False)
    ctx.note(f"N ingest: {stale} of {n} call row(s) for night {ctx.night} have ingest_fresh=false")
    return True, f"night {ctx.night}: {_plural(n, 'call row')}"


def check_one_per_thesis(ctx: Ctx) -> Result:
    """N2: every live thesis that existed by the night has a call row for it."""
    if ctx.night is None:
        return None
    tz = market_time.market_tz()
    live = [t for t in _live_theses(ctx.cur) if t["created_at"].astimezone(tz).date() <= ctx.night]
    count = Counter(str(r["thesis_id"]) for r in _night_rows(ctx))
    missing = [_thesis_label(t) for t in live if not count.get(str(t["id"]))]
    extra = sorted(tid for tid, n in count.items() if n > 1)
    if extra:
        ctx.note(
            f"N {_plural(len(extra), 'thesis', 'theses')} with more than one row for {ctx.night}"
        )
    ctx.details["N2 one per thesis"] = missing
    if missing:
        return False, (
            f"{_plural(len(missing), 'live thesis', 'live theses')} of {len(live)} with no row for night "
            f"{ctx.night}: {_named(missing)}"
        )
    return True, f"a row for each of {_plural(len(live), 'live thesis', 'live theses')}"


def check_reconstructed(ctx: Ctx) -> Result:
    """N3: no V2 call row (recorded after the cutover) is reconstructed; V1's kept rows are history."""
    if ctx.night is None:
        return None
    n = int(
        _scalar(
            ctx.cur,
            "SELECT count(*) AS n FROM calls WHERE reconstructed AND recorded_at > %s",
            (ctx.cut_at,),
        )
    )
    if n:
        return False, f"{_plural(n, 'reconstructed V2 call row')} (recorded after the cutover)"
    return True, "no reconstructed V2 call row"


def check_run_kind(ctx: Ctx) -> Result:
    """N4: the night's rows were written by the cron (any kind with ``--run-kind any``, the fork rehearsal)."""
    if ctx.night is None:
        return None
    kinds = Counter(str(r["run_kind"]) for r in _night_rows(ctx))
    if ctx.opts.run_kind == "any":
        return True, "run kinds " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
    other = {k: n for k, n in kinds.items() if k != "cron"}
    if other:
        return False, "not written by the cron: " + ", ".join(
            f"{k} {n}" for k, n in sorted(other.items())
        )
    return True, f"every row run_kind cron ({sum(kinds.values())})"


def check_after_cutover(ctx: Ctx) -> Result:
    """N5: no V2 call row (recorded after the cutover) is dated before the cutover's journal date (market time).
    V1's kept rows are dated before it by nature; V8 proves they are unchanged."""
    if ctx.night is None:
        return None
    if ctx.cut_date is None:
        return False, "no cutover journal row to date the cutover"
    n = int(
        _scalar(
            ctx.cur,
            "SELECT count(*) AS n FROM calls WHERE recorded_at > %s AND asof < %s",
            (ctx.cut_at, ctx.cut_date),
        )
    )
    if n:
        return False, (
            f"{_plural(n, 'call row')} recorded after the cutover but dated before it ({ctx.cut_date})"
        )
    return True, f"every V2 call row dated on or after the cutover ({ctx.cut_date})"


def check_calls_tenant(ctx: Ctx) -> Result:
    """N6: no call row under the data tenant."""
    if ctx.night is None:
        return None
    n = int(
        _scalar(
            ctx.cur,
            "SELECT count(*) AS n FROM calls WHERE tenant_id = %s",
            (scopes.DATA_TENANT_ID,),
        )
    )
    if n:
        return False, f"{_plural(n, 'call row')} under the data tenant"
    return True, "no call row under the data tenant"


# --- the edition stage --------------------------------------------------------------------------------------


def _editions(ctx: Ctx) -> dict[str, list[dict[str, Any]]]:
    """Every edition by tenant, oldest first, with whether it is closed (a ``closed`` event)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in _rows(
        ctx.cur,
        "SELECT e.id, e.tenant_id, e.number, e.parent_id, e.label, EXISTS (SELECT 1 FROM edition_event v "
        "WHERE v.edition_id = e.id AND v.kind = 'closed') AS closed "
        "FROM edition e ORDER BY e.tenant_id, e.number",
    ):
        out.setdefault(str(r["tenant_id"]), []).append(r)
    return out


def _kept_newest(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Each workspace's newest edition in the baseline (the kept V1 edition the cut builds on)."""
    out: dict[str, dict[str, Any]] = {}
    for e in ctx.base["editions"]:
        ws = str(e["tenant_id"])
        if ws not in out or int(e["number"]) > int(out[ws]["number"]):
            out[ws] = e
    return out


def _baseline_edition_ids(ctx: Ctx) -> set[str]:
    return {str(e["id"]) for e in ctx.base["editions"]}


def _live_by_workspace(ctx: Ctx) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in _live_theses(ctx.cur):
        out.setdefault(str(t["tenant_id"]), []).append(t)
    return out


def check_edition(ctx: Ctx) -> Result:
    """E1: after each kept edition, the NEXT one. For every workspace whose baseline held an edition (newest N):
    Edition N+1 exists, its parent is the kept Edition N, its label is a rule change (``rule change:``), and
    Edition N is closed. "not yet" before the cut (``editions.run cut``, by hand on cutover day)."""
    eds, kept, live = _editions(ctx), _kept_newest(ctx), _live_by_workspace(ctx)
    for ws in sorted(set(live) - set(kept)):
        ctx.note(
            f"E1 workspace {ws} has a live card and held no edition before the cutover: nothing to cut after"
        )
    if not kept:
        return True, "no workspace held an edition before the cutover: nothing to cut after"
    problems, done = [], []
    for ws, k in sorted(kept.items()):
        n = int(k["number"])
        mine = {int(e["number"]): e for e in eds.get(ws, [])}
        new = mine.get(n + 1)
        if new is None:
            problems.append(
                f"workspace {ws} has no Edition {n + 1} after the kept Edition {n} yet: cut it "
                "(editions.run cut) (not yet)"
            )
            continue
        bad = []
        if str(new["parent_id"]) != str(k["id"]):
            bad.append(f"its parent is {new['parent_id']}, not the kept Edition {n} ({k['id']})")
        if "rule change:" not in new["label"]:
            bad.append(f"its label {new['label']!r} names no rule change ('rule change:')")
        prev = mine.get(n)
        if prev is None or str(prev["id"]) != str(k["id"]):
            bad.append(f"the kept Edition {n} ({k['id']}) is gone")
        elif not prev["closed"]:
            bad.append(f"the kept Edition {n} is not closed")
        if bad:
            problems.append(f"workspace {ws}'s Edition {n + 1}: " + "; ".join(bad))
        else:
            done.append(f"Edition {n + 1} after the kept Edition {n} in workspace {ws}")
        later = sorted(m for m in mine if m > n + 1)
        if later:
            ctx.note(f"E1 workspace {ws} has later editions too: {', '.join(map(str, later))}")
    if problems:
        return False, "; ".join(problems)
    return True, "; ".join(done)


def check_entries(ctx: Ctx) -> Result:
    """E2: a workspace's newest edition, once it is new since the cutover, has entry rows for every live thesis
    of the workspace (a kept edition is history: E1 names a cut not made yet)."""
    eds, live, kept = _editions(ctx), _live_by_workspace(ctx), _baseline_edition_ids(ctx)
    missing, checked = [], 0
    for ws, theses in sorted(live.items()):
        mine = eds.get(ws, [])
        if not mine or str(mine[-1]["id"]) in kept:
            continue  # no edition, or no new one yet (E1 names it)
        have = {
            str(r["thesis_id"])
            for r in _rows(
                ctx.cur,
                "SELECT DISTINCT thesis_id FROM edition_entry WHERE edition_id = %s",
                (mine[-1]["id"],),
            )
        }
        checked += len(theses)
        missing += [_thesis_label(t) for t in theses if str(t["id"]) not in have]
    ctx.details["E2 entries"] = missing
    if missing:
        return False, (
            f"{_plural(len(missing), 'live thesis', 'live theses')} with no edition_entry row: {_named(missing)}"
        )
    return True, f"entry rows for each of {_plural(checked, 'live thesis', 'live theses')}"


def check_no_card_no_edition(ctx: Ctx) -> Result:
    """E3: no edition made since the cutover for a workspace (or the data tenant) with no live card. A kept V1
    edition is history, never a stray."""
    eds, live, kept = _editions(ctx), _live_by_workspace(ctx), _baseline_edition_ids(ctx)
    stray = {
        ws: n
        for ws, rows in eds.items()
        if ws not in live and (n := sum(1 for r in rows if str(r["id"]) not in kept))
    }
    if stray:
        return (
            False,
            "edition(s) made since the cutover for a tenant with no live card: "
            + ", ".join(f"{ws} ({n})" for ws, n in sorted(stray.items())),
        )
    return True, "no edition made since the cutover for a workspace with no live card"


# --- the stages -----------------------------------------------------------------------------------------------

Check = tuple[str, Callable[[Ctx], Result]]
CHECKS: dict[str, tuple[Check, ...]] = {
    "reset": (
        ("V1 surprises", check_surprises),
        ("V2 schema", check_schema),
        ("V3 tenants", check_tenants),
        ("V3 users", check_users),
        ("V4 theses", check_theses),
        ("V5 recall", check_recall),
        ("V5 children", check_children),
        ("V5 decisions", check_decisions),
        ("V6 platform facts", check_platform_facts),
        ("V7 purity", check_purity),
        ("V8 history", check_history),
        ("V9 journal", check_journal),
        ("V10 health", check_health),
    ),
    "backfill": (
        ("B1 attempts", check_backfill_attempts),
        ("B2 B4", check_b4),
        ("B3 coverage", check_coverage),
    ),
    "nightly": (
        ("N1 night", check_night),
        ("N2 one per thesis", check_one_per_thesis),
        ("N3 reconstructed", check_reconstructed),
        ("N4 run kind", check_run_kind),
        ("N5 after the cutover", check_after_cutover),
        ("N6 tenant", check_calls_tenant),
    ),
    "edition": (
        ("E1 edition", check_edition),
        ("E2 entries", check_entries),
        ("E3 no card", check_no_card_no_edition),
    ),
}


def checks_for(stage: str) -> list[Check]:
    """The stage's checks: cumulative (every earlier stage's, then its own)."""
    return [c for s in STAGES[: STAGES.index(stage) + 1] for c in CHECKS[s]]


# --- the guard and the provenance (refusals: exit 2) ------------------------------------------------------------


def target_refusal(*, current: str, database: str, prod: bool) -> str | None:
    """Why verify may not read ``current`` with these flags, or ``None``. The apply path's own allow-list
    (``v2_reset.assert_cutover_target``); no ``--confirm`` (verify writes nothing). PURE."""
    if database != current:
        return f"--database {database!r} is not the database this connection is on ({current!r})"
    try:
        reset.assert_cutover_target(current, prod=prod)
    except reset.NotACutoverTarget as exc:
        return str(exc)
    return None


def load_provenance(path: Path | str | None, name: str) -> tuple[dict[str, Any] | None, str | None]:
    """The provenance document, or ``(None, why it cannot be used)``. Reads only the file."""
    if path is None:
        return None, (
            "no provenance file was given: pass --provenance PATH (the file `run --apply` printed after "
            "'provenance:')"
        )
    p = Path(path)
    if p.name.endswith(".pending"):
        return None, (
            f"{p.name} is a .pending provenance file: the cutover did not finish writing it (rolled back, or "
            "committed but not finalized); read that run's console output before anything else"
        )
    if not p.is_file():
        pending = p.with_name(p.name + ".pending")
        if pending.is_file():
            return None, (
                f"the provenance file {p.name} does not exist, but {pending.name} does: a .pending file is an "
                "unfinished run; read that run's console output before anything else"
            )
        return None, f"the provenance file {p} does not exist"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the provenance file {p.name} cannot be read ({exc.__class__.__name__})"
    base = doc.get("baseline") if isinstance(doc, dict) else None
    if (
        isinstance(doc, dict)
        and doc.get("tool") == reset.TOOL
        and isinstance(base, dict)
        and "history" not in base
    ):
        return None, (
            f"{p.name} was written by the cutover that reset V1's history; this verify checks the cutover "
            "that keeps it (its baseline counts the history)"
        )
    if (
        not isinstance(doc, dict)
        or doc.get("tool") != reset.TOOL
        or not doc.get("committed_at")
        or not isinstance(base, dict)
        or any(k not in base for k in _BASELINE_KEYS)
    ):
        return (
            None,
            f"{p.name} is not a finished '{reset.TOOL}' provenance file (no committed baseline)",
        )
    if doc.get("database") != name:
        return (
            None,
            f"the provenance file {p.name} is for database {doc.get('database')!r}, not {name!r}",
        )
    return doc, None


# --- the run --------------------------------------------------------------------------------------------------


def connect_read_only() -> psycopg.Connection:
    """The verifying connection: read-only for the whole SESSION (``default_transaction_read_only``, so a
    rollback's fresh transaction is read-only too), ``conn.read_only``, a REPEATABLE READ snapshot, and a short
    ``lock_timeout``. Nothing is read before the read-only setting."""
    conn = session.connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
            cur.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT)))
        conn.autocommit = False
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    except BaseException:
        conn.close()
        raise
    return conn


def _result(code: int, text: str) -> int:
    print(f"RESULT: {text}. Exit {code}.")
    return code


def _cut_at(cur: Any, journal_id: Any) -> datetime | None:
    """When the cutover was journaled (the provenance's ``data_repair`` row), or ``None``."""
    return _scalar(
        cur,
        "SELECT applied_at AS at FROM data_repair WHERE tool = %s AND id::text = %s",
        (reset.TOOL, str(journal_id)),
    )


def run(opts: Options, *, health_get: Callable[[str], int] | None = None) -> int:
    """The CLI (module docstring). ``health_get(url) -> HTTP status`` is the seam for V10 (tests inject one)."""
    print(f"V2 CUTOVER - VERIFY - stage {opts.stage} (read-only; nothing written)")
    if opts.stage not in STAGES:
        print(f"REFUSE: unknown stage {opts.stage!r} (one of {', '.join(STAGES)})")
        return _result(EXIT_CANNOT_VERIFY, "CANNOT VERIFY - nothing checked")
    conn = connect_read_only()
    try:
        name = database_name(conn)
        why = target_refusal(current=name, database=opts.database, prod=opts.prod)
        if why is None:
            prov, why = load_provenance(opts.provenance, name)
        print(f"target DB : {name}" + (" (REFUSED)" if why else ""))
        if why:
            print(f"REFUSE: {why}")
            return _result(EXIT_CANNOT_VERIFY, "CANNOT VERIFY - nothing checked")
        assert prov is not None
        with conn.cursor() as cur:
            ctx = Ctx(
                cur=cur,
                conn=conn,
                name=name,
                prov=prov,
                opts=opts,
                health_get=health_get or _http_status,
            )
            ctx.cut_at = _cut_at(cur, prov.get("journal_id"))
            ctx.cut_date = (
                None
                if ctx.cut_at is None
                else ctx.cut_at.astimezone(market_time.market_tz()).date()
            )
            print(f"note: provenance {Path(str(opts.provenance)).name}, cut over on {ctx.cut_date}")
            report = []
            for check_id, fn in checks_for(opts.stage):
                before = len(ctx.notes)
                got = fn(ctx)
                if got is not None:
                    ok, text = got
                    print(f"{'ok' if ok else 'FAIL'}: {check_id}: {text}")
                    report.append(
                        {
                            "id": check_id,
                            "ok": ok,
                            "text": text,
                            "details": ctx.details.get(check_id, []),
                        }
                    )
                for note in ctx.notes[before:]:
                    print(f"note: {note}")
        conn.rollback()
    finally:
        conn.close()

    failed = sum(1 for c in report if not c["ok"])
    if ctx.fatal:
        code, text = EXIT_CANNOT_VERIFY, (
            f"CANNOT VERIFY - an unmapped or missing table; {_plural(failed, 'check')} failed"
        )
    elif failed:
        code, text = EXIT_FAILED, f"{failed} check(s) failed"
    else:
        code, text = EXIT_VERIFIED, "VERIFIED"
    if opts.json_path is not None:
        out = Path(opts.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "stage": opts.stage,
                    "database": name,
                    "provenance": str(opts.provenance),
                    "cut_date": None if ctx.cut_date is None else ctx.cut_date.isoformat(),
                    "night": None if ctx.night is None else ctx.night.isoformat(),
                    "checks": report,
                    "notes": ctx.notes,
                    "failed": failed,
                    "exit_code": code,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"note: json {out}")
    return _result(code, text)
