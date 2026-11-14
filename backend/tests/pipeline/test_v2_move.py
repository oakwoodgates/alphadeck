"""The V2 platform-facts move (``python -m pipeline.v2_cutover move-platform-facts [--apply]``, V2 PR 2 / W0b).

It relabels the founding workspace's share counts and cash/burn to the data tenant, and (operator decision D2 (a))
the data tenant's RATIFIED purity rows to the founding workspace, in place, with migration 0078's method (the
append-only trigger off and on by name inside one transaction). The apply refuses any database it does not
positively recognize as a rehearsal database (a fork or a test database).

The pure tests need no database; the DB tests use the per-worktree test database (the shared ``db`` fixture, which
truncates the security master and with it these three tables; skips when Postgres is unreachable).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID
from pipeline import v2_cutover as C
from pipeline import v2_move as M
from pipeline.run_lock import EXIT_ALREADY_RUNNING, acquire_pass_lock
from tests.workspaces import new_workspace

W = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
V = uuid.UUID("00000000-0000-0000-0000-0000000000d2")


@pytest.mark.parametrize(
    "name", ["alphadeck_v2", "alphadeck_test", "alphadeck_test_ab12cd", "alphadeck_test_ab12cd_gw3"]
)
def test_the_guard_allows_a_fork_or_a_test_database(name):
    M.assert_rehearsal_db(name)  # no raise


@pytest.mark.parametrize(
    "name",
    [
        "alphadeck",  # prod
        "alphadeck_dev",  # the dev tier
        "alphadeck_sig",  # the Signals-Lab tier
        "alphadeck_prod",
        "postgres",
        "",
        "Alphadeck_v2",
        "alphadeck_",
        "alphadeck_V2",
        "alphadeck-v2",
        "alphadeck_v2 ",
        "alphadeck_v2;drop",
        "xalphadeck_v2",
        "alphadeck_test-x",
    ],
)
def test_the_guard_refuses_every_other_name(name):
    with pytest.raises(M.NotARehearsalDatabase) as exc:
        M.assert_rehearsal_db(name)
    assert repr(name) in str(exc.value)


# --- DB: the move on the test database -------------------------------------------------------------------------

_TABLES = ("fact_shares_outstanding", "fact_cash_burn", "fact_revenue_mix")
ACCEPTED = datetime(2026, 5, 8, 21, 3, 11, tzinfo=timezone.utc)


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1)),
        )
    return sid


def _shares(db, sid, tenant, ref, by, *, accepted=None, recorded_at=None) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_shares_outstanding (tenant_id, security_id, shares, source, source_ref, ratified_by, "
            "valid_from, note, vouched, filing_accession, filing_accepted, recorded_at) VALUES "
            "(%s, %s, 52990947, '10-q-cover', %s, %s, '2026-03-31', 'a note', 'confirmed', %s, %s, "
            "coalesce(%s, now())) RETURNING id",
            (
                tenant,
                sid,
                ref,
                by,
                "0001104659-26-057983" if accepted else None,
                accepted,
                recorded_at,
            ),
        )
        return cur.fetchone()["id"]


def _cash(db, sid, tenant, ref, by="operator", *, recorded_at=None) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_cash_burn (tenant_id, security_id, cash_usd, quarterly_burn_usd, source, source_ref, "
            "ratified_by, valid_from, recorded_at) VALUES (%s, %s, 41000000, 6500000, '10-q', %s, %s, "
            "'2026-03-31', coalesce(%s, now())) RETURNING id",
            (tenant, sid, ref, by, recorded_at),
        )
        return cur.fetchone()["id"]


def _mix(db, sid, tenant, ref, by, *, recorded_at=None) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
            "ratified_by, valid_from, recorded_at) VALUES (%s, %s, 'enrichment', 62.5, 'ratified', %s, %s, "
            "'2025-12-31', coalesce(%s, now())) RETURNING id",
            (tenant, sid, ref, by, recorded_at),
        )
        return cur.fetchone()["id"]


def _rows(db, table: str) -> dict[uuid.UUID, dict]:
    with db.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table)))
        return {r["id"]: dict(r) for r in cur.fetchall()}


def _tenants(db, table: str) -> dict[uuid.UUID, uuid.UUID]:
    return {i: r["tenant_id"] for i, r in _rows(db, table).items()}


def test_the_move_relabels_in_place(db):
    """Founding-workspace share counts (an ``auto`` one with its filing acceptance, an ``operator`` one) and a cash
    row move to the data tenant; only ``tenant_id`` changes; a data-tenant share row is untouched.
    """
    sid = _security(db, "MOVE1")
    auto = _shares(db, sid, FOUNDING_WORKSPACE_ID, "Q1-COVER", "auto", accepted=ACCEPTED)
    oper = _shares(db, sid, FOUNDING_WORKSPACE_ID, "Q2-COVER", "operator")
    cash = _cash(db, sid, FOUNDING_WORKSPACE_ID, "Q1-10Q")
    feed = _shares(db, sid, DATA_TENANT_ID, "FEED-COVER", None)
    db.commit()
    before = {t: _rows(db, t) for t in _TABLES}

    plan = M.plan_move(db)
    result = M.apply_move(db, plan)

    assert result.moved == {
        "fact_shares_outstanding": 2,
        "fact_cash_burn": 1,
        "fact_revenue_mix": 0,
    }
    after = {t: _rows(db, t) for t in _TABLES}
    for table, moved in (("fact_shares_outstanding", (auto, oper)), ("fact_cash_burn", (cash,))):
        assert set(after[table]) == set(before[table])  # same ids, nothing added or removed
        for rid in moved:
            was, now = before[table][rid], after[table][rid]
            assert was["tenant_id"] == FOUNDING_WORKSPACE_ID and now["tenant_id"] == DATA_TENANT_ID
            assert {k: v for k, v in now.items() if k != "tenant_id"} == {
                k: v for k, v in was.items() if k != "tenant_id"
            }
    assert after["fact_shares_outstanding"][auto]["filing_accepted"] == ACCEPTED
    assert after["fact_shares_outstanding"][feed] == before["fact_shares_outstanding"][feed]


def _trigger_on(db) -> dict[str, str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT c.relname AS t, tg.tgenabled AS e FROM pg_trigger tg JOIN pg_class c ON c.oid = tg.tgrelid "
            "WHERE tg.tgname = 'no_update' AND c.relname = ANY(%s)",
            (list(_TABLES),),
        )
        out = {r["t"]: r["e"] for r in cur.fetchall()}
    db.rollback()
    return out


def _update_refused(db, table: str) -> bool:
    try:
        with db.cursor() as cur:
            cur.execute(sql.SQL("UPDATE {} SET source = source").format(sql.Identifier(table)))
        return False
    except psycopg.errors.RaiseException:
        return True
    finally:
        db.rollback()


def _journal(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM data_repair WHERE tool = %s ORDER BY seq", (M.TOOL,))
        out = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return out


def test_the_only_update_trigger_on_the_moved_tables_is_no_update(db):
    """The move disables ONE trigger by name; any other trigger that fires on UPDATE would refuse the relabel
    (or, worse, need disabling too). Pinned, so a new trigger fails here first."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND (t.tgtype & 16) <> 0 AND c.relname = ANY(%s)",
            (list(_TABLES),),
        )
        live = {(r["relname"], r["tgname"]) for r in cur.fetchall()}
    assert live == {(t, "no_update") for t in _TABLES} and M.MOVED_TABLES == _TABLES


def test_the_trigger_is_back_on(db):
    sid = _security(db, "TRIG1")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "T-COVER", "auto")
    _cash(db, sid, FOUNDING_WORKSPACE_ID, "T-10Q")
    _mix(db, sid, DATA_TENANT_ID, "T-10K", "operator")
    db.commit()
    M.apply_move(db, M.plan_move(db))
    assert _trigger_on(db) == {t: "O" for t in _TABLES}
    for table in _TABLES:
        assert _update_refused(db, table), table


def test_a_failure_mid_move_rolls_back_and_the_trigger_is_back_on(db):
    """A real failure while the trigger is OFF: a collision that appears after the plan was read makes the
    UPDATE itself raise (the UNIQUE key). Nothing moved, every trigger on, the error surfaces."""
    sid = _security(db, "FAIL1")
    rec = datetime(2026, 9, 1, 18, 13, 46, tzinfo=timezone.utc)
    moving = _shares(db, sid, FOUNDING_WORKSPACE_ID, "F-COVER", "auto", recorded_at=rec)
    db.commit()
    plan = M.plan_move(db)
    db.rollback()
    _shares(
        db, sid, DATA_TENANT_ID, "F-COVER", None, recorded_at=rec
    )  # the stale plan never saw it
    db.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        M.apply_move(db, plan)

    assert _trigger_on(db) == {t: "O" for t in _TABLES}
    assert _tenants(db, "fact_shares_outstanding")[moving] == FOUNDING_WORKSPACE_ID
    assert _journal(db) == []
    assert _update_refused(db, "fact_shares_outstanding")


def test_a_recount_that_disagrees_with_the_plan_rolls_back(db):
    """A row added after the plan: the UPDATE moves more than the plan said, so the apply refuses and rolls back
    (trigger on, nothing moved)."""
    sid = _security(db, "RECNT")
    first = _shares(db, sid, FOUNDING_WORKSPACE_ID, "R-1", "auto")
    db.commit()
    plan = M.plan_move(db)
    db.rollback()
    second = _shares(db, sid, FOUNDING_WORKSPACE_ID, "R-2", "operator")
    db.commit()
    with pytest.raises(M.MoveRefused, match="the plan said 1"):
        M.apply_move(db, plan)
    tenants = _tenants(db, "fact_shares_outstanding")
    assert tenants[first] == tenants[second] == FOUNDING_WORKSPACE_ID
    assert _trigger_on(db) == {t: "O" for t in _TABLES} and _journal(db) == []


def test_a_second_run_moves_nothing_and_journals_nothing(db):
    sid = _security(db, "TWICE")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "W-COVER", "auto")
    db.commit()
    first = M.apply_move(db, M.plan_move(db))
    assert first.total == 1 and len(_journal(db)) == 1
    again = M.plan_move(db)
    assert again.total == 0 and again.refusals() == []
    second = M.apply_move(db, again)
    assert second.total == 0 and second.journal_id is None
    assert len(_journal(db)) == 1


def test_rows_in_another_workspace_refuse_and_change_nothing(db):
    w = new_workspace(db, "workspace W", workspace_id=W)
    sid = _security(db, "OTHER")
    mine = _shares(db, sid, FOUNDING_WORKSPACE_ID, "O-1", "auto")
    theirs = _cash(db, sid, w, "O-2")
    db.commit()
    plan = M.plan_move(db)
    assert [r.split(" - ")[0] for r in plan.refusals()] == [
        f"fact_cash_burn: 1 row(s) under workspace {w}, not the founding workspace"
    ]
    with pytest.raises(M.MoveRefused):
        M.apply_move(db, plan)
    assert _tenants(db, "fact_shares_outstanding")[mine] == FOUNDING_WORKSPACE_ID
    assert _tenants(db, "fact_cash_burn")[theirs] == w
    assert _journal(db) == []


def test_a_second_workspace_with_none_of_these_rows_does_not_refuse(db):
    """The fork's shape: a second workspace exists and holds none of the moved rows (its own purity is normal)."""
    w = new_workspace(db, "workspace W", workspace_id=W)
    sid = _security(db, "SECWS")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "S-1", "auto")
    _mix(db, sid, w, "S-10K", "someone@example.com")
    db.commit()
    plan = M.plan_move(db)
    assert plan.refusals() == [] and plan.total == 1


def test_a_key_collision_refuses(db):
    sid = _security(db, "COLL1")
    rec = datetime(2026, 9, 1, 18, 13, 46, tzinfo=timezone.utc)
    mine = _shares(db, sid, FOUNDING_WORKSPACE_ID, "C-COVER", "operator", recorded_at=rec)
    _shares(db, sid, DATA_TENANT_ID, "C-COVER", None, recorded_at=rec)
    db.commit()
    plan = M.plan_move(db)
    assert plan.legs[0].collisions == 1
    assert any("UNIQUE (tenant_id, source_ref, recorded_at)" in r for r in plan.refusals())
    with pytest.raises(M.MoveRefused):
        M.apply_move(db, plan)
    assert _tenants(db, "fact_shares_outstanding")[mine] == FOUNDING_WORKSPACE_ID
    assert _journal(db) == []


def test_the_moved_companies_are_stamped_changed(db):
    a, b, c = _security(db, "STMPA"), _security(db, "STMPB"), _security(db, "STMPC")
    _shares(db, a, FOUNDING_WORKSPACE_ID, "A-COVER", "auto")
    _cash(db, b, FOUNDING_WORKSPACE_ID, "B-10Q")
    _mix(db, c, DATA_TENANT_ID, "C-10K", "operator")
    untouched = _security(db, "STMPD")
    _shares(db, untouched, DATA_TENANT_ID, "D-COVER", None)
    db.commit()
    result = M.apply_move(db, M.plan_move(db))
    assert result.stamped == 3
    with db.cursor() as cur:
        cur.execute("SELECT security_id FROM security_change")
        stamped = {r["security_id"] for r in cur.fetchall()}
    db.rollback()
    assert stamped == {a, b, c}


def test_one_journal_row_with_the_counts(db):
    sid = _security(db, "JRNL1")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "J-1", "auto")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "J-2", "auto")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "J-3", "operator")
    _cash(db, sid, FOUNDING_WORKSPACE_ID, "J-10Q")
    _mix(db, sid, DATA_TENANT_ID, "J-10K", "operator")
    db.commit()
    result = M.apply_move(db, M.plan_move(db))
    (row,) = _journal(db)
    assert row["id"] == result.journal_id
    assert (row["tool"], row["action"], row["rows_changed"], row["deletes"]) == (
        "v2_move_platform_facts",
        "apply",
        5,
        False,
    )
    assert row["counts"] == {
        "moved": {"fact_shares_outstanding": 3, "fact_cash_burn": 1, "fact_revenue_mix": 1},
        "by_ratified": {
            "fact_shares_outstanding": {"auto": 2, "operator": 1},
            "fact_cash_burn": {"operator": 1},
            "fact_revenue_mix": {"operator": 1},
        },
        "companies": 1,
    }


def test_ratified_purity_moves_to_the_founding_workspace_and_unratified_stays(db):
    """D2 (a): a data-tenant purity row with a ratifier moves to the founding workspace; one with ratified_by NULL
    stays (no plan moves it; the cutover dry run names it); the founding workspace's own purity is untouched.
    """
    sid = _security(db, "PURE1")
    ratified = _mix(db, sid, DATA_TENANT_ID, "P-10K", "operator")
    unratified = _mix(db, sid, DATA_TENANT_ID, "P-FEED", None)
    founding = _mix(db, sid, FOUNDING_WORKSPACE_ID, "P-OWN", "operator")
    db.commit()
    before = _rows(db, "fact_revenue_mix")
    plan = M.plan_move(db)
    (mix,) = [p for p in plan.legs if p.table == "fact_revenue_mix"]
    assert (mix.to_move, mix.unratified_left, mix.by_ratified) == (1, 1, {"operator": 1})
    M.apply_move(db, plan)
    after = _rows(db, "fact_revenue_mix")
    assert after[ratified]["tenant_id"] == FOUNDING_WORKSPACE_ID
    assert {k: v for k, v in after[ratified].items() if k != "tenant_id"} == {
        k: v for k, v in before[ratified].items() if k != "tenant_id"
    }
    assert after[unratified] == before[unratified] and after[founding] == before[founding]


def test_a_purity_collision_refuses(db):
    sid = _security(db, "PCOLL")
    rec = datetime(2026, 9, 1, 18, 13, 46, tzinfo=timezone.utc)
    _mix(db, sid, DATA_TENANT_ID, "PC-10K", "operator", recorded_at=rec)
    _mix(db, sid, FOUNDING_WORKSPACE_ID, "PC-10K", "operator", recorded_at=rec)
    db.commit()
    plan = M.plan_move(db)
    assert any(r.startswith("fact_revenue_mix: 1 row(s) to move") for r in plan.refusals())


def test_after_the_move_the_dry_run_is_clean(db):
    """The PR 1 dry run (``plan``) reports no surprise for the moved tables after the move, except the one the
    move leaves on purpose: an unratified data-tenant purity row (rule 8), as on the prod copy."""
    sid = _security(db, "CLEAN")
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "CL-1", "auto", accepted=ACCEPTED)
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "CL-2", "operator")
    _cash(db, sid, FOUNDING_WORKSPACE_ID, "CL-10Q")
    _mix(db, sid, FOUNDING_WORKSPACE_ID, "CL-OWN", "operator")
    _mix(db, sid, DATA_TENANT_ID, "CL-10K", "operator")
    _mix(db, sid, DATA_TENANT_ID, "CL-FEED", None)
    new_workspace(db, "workspace V", workspace_id=V)
    db.commit()
    M.apply_move(db, M.plan_move(db))

    inv = C.inventory(db)
    db.rollback()
    mine = [(s.rule, s.table, s.count) for s in C.surprises(inv) if s.table in _TABLES]
    assert mine == [(8, "fact_revenue_mix", 1)]
    assert inv.tables["fact_shares_outstanding"].by_tenant.get(FOUNDING_WORKSPACE_ID, 0) == 0
    assert inv.tables["fact_cash_burn"].by_tenant.get(FOUNDING_WORKSPACE_ID, 0) == 0
    assert inv.tables["fact_shares_outstanding"].by_tenant.get(V, 0) == 0


# --- the CLI: python -m pipeline.v2_cutover move-platform-facts [--apply] ----------------------------------------


def _seed_the_fork_shape(db) -> tuple[uuid.UUID, uuid.UUID]:
    """A small copy of the prod copy's shape (2026-11-14): share counts ``auto`` and ``operator`` and cash/burn in
    the founding workspace; ratified purity in the founding workspace; one unratified data-tenant purity row; a
    second workspace holding none of these rows."""
    new_workspace(db, "workspace V", workspace_id=V)
    a, b = _security(db, "FORKA"), _security(db, "FORKB")
    _shares(db, a, FOUNDING_WORKSPACE_ID, "FA-1", "auto", accepted=ACCEPTED)
    _shares(db, a, FOUNDING_WORKSPACE_ID, "FA-2", "auto")
    _shares(db, b, FOUNDING_WORKSPACE_ID, "FB-1", "operator")
    _cash(db, b, FOUNDING_WORKSPACE_ID, "FB-10Q")
    _mix(db, a, FOUNDING_WORKSPACE_ID, "FA-OWN", "operator")
    _mix(db, b, DATA_TENANT_ID, "FB-FEED", None)
    db.commit()
    return a, b


def test_the_cli_dry_run_writes_nothing_and_prints_the_plan(db, capsys):
    _seed_the_fork_shape(db)
    before = {t: _rows(db, t) for t in _TABLES}
    db.rollback()
    assert C.main(["move-platform-facts"]) == 0
    out = capsys.readouterr().out
    assert {t: _rows(db, t) for t in _TABLES} == before and _journal(db) == []
    lines = out.splitlines()
    assert lines[0] == "V2 MOVE - share counts and cash/burn to the data tenant - DRY RUN"
    assert lines[1].startswith("target DB : alphadeck_test") and lines[1].endswith(
        "(rehearsal database: allowed)"
    )
    assert lines[2:] == [
        "fact_shares_outstanding : 3 row(s) …0002 -> data (auto 2, operator 1); 0 already in data; "
        "0 in other workspaces; 0 key collisions",
        "fact_cash_burn          : 1 row(s) …0002 -> data (operator 1); 0 already in data; "
        "0 in other workspaces; 0 key collisions",
        "fact_revenue_mix        : 0 ratified row(s) data -> …0002 (none); 1 unratified row(s) stay; "
        "0 key collisions",
        "companies stamped changed: 2",
        "RESULT: would move 4 row(s). Run with --apply.",
    ]


def test_the_cli_apply_moves_then_a_second_apply_has_nothing_to_move(db, capsys):
    _seed_the_fork_shape(db)
    assert C.main(["move-platform-facts", "--apply"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith(" - APPLY")
    (row,) = _journal(db)
    assert re.search(
        rf"^RESULT: moved 4 row\(s\) in one transaction; journal data_repair {row['id']}$",
        out,
        re.M,
    )
    assert set(_tenants(db, "fact_shares_outstanding").values()) == {DATA_TENANT_ID}
    assert set(_tenants(db, "fact_cash_burn").values()) == {DATA_TENANT_ID}

    assert C.main(["move-platform-facts", "--apply"]) == 0
    assert "RESULT: nothing to move. Exit 0." in capsys.readouterr().out
    assert len(_journal(db)) == 1


def test_the_cli_refuses_rows_in_another_workspace_with_exit_1(db, capsys):
    _seed_the_fork_shape(db)
    sid = _security(db, "OTHWS")
    _shares(db, sid, V, "V-1", "operator")
    db.commit()
    assert C.main(["move-platform-facts", "--apply"]) == 1
    out = capsys.readouterr().out
    assert f"REFUSE: fact_shares_outstanding: 1 row(s) under workspace {V}" in out
    assert out.rstrip().endswith("RESULT: REFUSED - nothing written. Exit 1.")
    assert FOUNDING_WORKSPACE_ID in set(_tenants(db, "fact_shares_outstanding").values())
    assert _journal(db) == []


@pytest.mark.parametrize(
    "name", ["alphadeck", "alphadeck_dev", "alphadeck_sig", "alphadeck_prod", "postgres"]
)
@pytest.mark.parametrize("argv", [["move-platform-facts", "--apply"], ["move-platform-facts"]])
def test_the_cli_refuses_a_prod_or_tier_name(db, monkeypatch, capsys, name, argv):
    """The database-name read says prod (or a reserved tier): exit 2 before the lock, the plan or any write."""
    _seed_the_fork_shape(db)
    before = {t: _rows(db, t) for t in _TABLES}
    db.rollback()
    monkeypatch.setattr(M, "database_name", lambda conn: name)

    def no_lock(**_):
        raise AssertionError("the guard must refuse before the lock is taken")

    monkeypatch.setattr(M, "repair_lock_or_exit", no_lock)
    assert C.main(argv) == 2
    out = capsys.readouterr().out
    assert f"target DB : {name} (NOT a rehearsal database)" in out
    assert out.rstrip().endswith("RESULT: REFUSED - nothing read or written. Exit 2.")
    assert {t: _rows(db, t) for t in _TABLES} == before and _journal(db) == []


def test_apply_move_itself_refuses_a_prod_name(db, monkeypatch):
    """The guard runs inside ``apply_move`` too, on the connection that would write: a caller that skips the CLI
    still cannot relabel prod."""
    _seed_the_fork_shape(db)
    plan = M.plan_move(db)
    monkeypatch.setattr(M, "database_name", lambda conn: "alphadeck")
    with pytest.raises(M.NotARehearsalDatabase):
        M.apply_move(db, plan)
    db.rollback()
    assert FOUNDING_WORKSPACE_ID in set(_tenants(db, "fact_shares_outstanding").values())
    assert _journal(db) == []


def test_the_cli_apply_exits_75_while_the_lock_is_held(db, capsys):
    _seed_the_fork_shape(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            C.main(["move-platform-facts", "--apply"])
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert C.main(["move-platform-facts"]) == 0  # a dry run takes no lock and still runs
    finally:
        held.release()
    assert FOUNDING_WORKSPACE_ID in set(_tenants(db, "fact_shares_outstanding").values())
    assert _journal(db) == []


def test_the_dry_run_connection_is_read_only(db, monkeypatch, capsys):
    opened: list = []
    real_connect = M.session.connect

    def spy():
        conn = real_connect()
        opened.append(conn)
        return conn

    monkeypatch.setattr(M.session, "connect", spy)
    assert C.main(["move-platform-facts"]) == 0
    assert len(opened) == 1 and opened[0].read_only is True and opened[0].closed


# --- the re-seed hook: scripts/refresh-fork.sh runs the move right after migrate -------------------------------

_REFRESH = Path(__file__).resolve().parents[3] / "scripts" / "refresh-fork.sh"
_MOVE_CMD = "python -m pipeline.v2_cutover move-platform-facts --apply"


def test_the_fork_refresh_runs_the_move_after_migrate():
    """Read as TEXT (a bash subprocess cannot run on every host). The real run applies the move right after the
    migrate step and fails the refresh when the move refuses; the --dry-run plan names the same step. Guards the
    hook against removal during a merge from main."""
    text = _REFRESH.read_text(encoding="utf-8")
    real = text[text.index("# ===================================== REAL RUN") :]
    migrate = real.index('docker exec "$FORK_BACKEND" python -m db.migrate || fail')
    move = real.index(f'docker exec "$FORK_BACKEND" {_MOVE_CMD}')
    assert migrate < move
    # the command's continuation ends in `|| fail "..."`: a refusal fails the refresh
    after = real[move:].split("\n")
    assert after[0].rstrip().endswith("\\") and after[1].lstrip().startswith("|| fail ")
    # before the cache copy and the closing OK: line
    assert move < real.index('if [ "$WITH_CACHE" -eq 1 ]') < real.index('echo "OK: fork')
    # the dry-run plan prints the same step after its migrate step
    plan = text[
        text.index('if [ "$DRY_RUN" -eq 1 ]; then') : text.index(
            "# ===================================== REAL RUN"
        )
    ]
    assert plan.index("step (migrate)") < plan.index(
        f"step (v2 move): docker exec <fork backend> {_MOVE_CMD}"
    )
