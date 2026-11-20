"""The V2 cutover's apply path (``python -m pipeline.v2_cutover run ...``, W10a): guard, preflight, backup, the
platform-facts move in ONE transaction, journal and provenance.

V1's HISTORY IS KEPT (operator decision 2026-11-19, "keep the history"): the calls log, ``call_annotation`` and the
six ``edition*`` tables keep every row; the only delete left is the orphan purity row, on request. The cutover still
rewrites the platform facts' tenants, so most of these tests are refusals: each one proves a path that must write
NOTHING (every table counted before and after), or that the one transaction is all-or-nothing.

Safety of the tests themselves:

- Every DB test runs on the per-worktree test database (the shared ``db`` fixture, fail-closed to
  ``alphadeck_test…`` by ``db/testdb.py``). The production branch (``--prod`` with ``alphadeck``) is tested ONLY
  through the guard's pure function, or by injecting a name into ``database_name`` for a case that must REFUSE;
  no test connects to, or creates, a database named like production.
- No test shells out to ``pg_dump``: ``pipeline.backup._pg_dump`` is replaced by a fail-loud stub for the whole
  module, and every apply passes a stub ``dump_runner`` that writes a small file into ``tmp_path``.
- Lock checks filter ``pg_locks`` to ``current_database()``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from psycopg import sql

import pipeline.backup as B
from db import session
from domain import market_time
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, SCOPE_OF
from pipeline import v2_cutover as C
from pipeline import v2_move as M
from pipeline import v2_reset as R
from pipeline.run_lock import LOCK_NAMESPACE, acquire_job_lock, acquire_pass_lock

COMPLETE = (
    "--\n-- PostgreSQL database dump\n--\n\nSET statement_timeout = 0;\n"
    "--\n-- PostgreSQL database dump complete\n--\n\n"
)
INCOMPLETE = "--\n-- PostgreSQL database dump\n--\n\nSET statement_timeout = 0;\nCOPY public.calls (id) FROM stdin;\n"
_MOVED = ("fact_shares_outstanding", "fact_cash_burn", "fact_revenue_mix")
# V1's history: kept by the cutover, every row (operator decision 2026-11-19)
_HISTORY = (
    "calls",
    "call_annotation",
    "edition",
    "edition_event",
    "edition_entry",
    "edition_call",
    "edition_outcome",
    "edition_cause",
)
# the journal summary for ``_seed_v1``'s four moved rows (2 share counts, 1 cash/burn, 1 purity)
_SUMMARY = "V2 cutover: moved 4 share-count, cash/burn and purity row(s) to their V2 tenants; V1 history kept"


@pytest.fixture(autouse=True)
def _never_a_real_pg_dump(monkeypatch):
    """No test in this module may shell out to the real binary."""

    def boom(*a, **k):
        raise AssertionError("a test reached the real pg_dump")

    monkeypatch.setattr(B, "_pg_dump", boom)


_VOCABULARY = (
    "CHECK (ratified_by IS NULL OR ratified_by IN ('auto', 'operator') "
    "OR ratified_by ~ '^[^@[:space:]]+@[^@[:space:]]+$') NOT VALID"
)


@pytest.fixture(autouse=True)
def _vocabulary_back_to_not_valid(request):
    """An apply VALIDATEs migration 0206's two constraints; other test files read them as the migration leaves
    them (NOT VALID). After each DB test, put back any that this module validated."""
    yield
    if "db" not in request.fixturenames:
        return
    conn = session.connect()
    try:
        with conn.cursor() as cur:
            for table, name in R.VALIDATED_CONSTRAINTS.items():
                cur.execute(
                    "SELECT convalidated FROM pg_constraint WHERE conname = %s AND conrelid = %s::regclass",
                    (name, table),
                )
                row = cur.fetchone()
                if row is not None and row["convalidated"]:
                    cur.execute(
                        sql.SQL("ALTER TABLE {t} DROP CONSTRAINT {c}").format(
                            t=sql.Identifier(table), c=sql.Identifier(name)
                        )
                    )
                    cur.execute(
                        sql.SQL("ALTER TABLE {t} ADD CONSTRAINT {c} " + _VOCABULARY).format(
                            t=sql.Identifier(table), c=sql.Identifier(name)
                        )
                    )
        conn.commit()
    finally:
        conn.close()


class Dumps:
    """A stub ``dump_runner``: records each call and writes a small file (complete unless told otherwise)."""

    def __init__(self, body: str = COMPLETE, before=None):
        self.body = body
        self.calls: list[dict] = []
        self.before = before

    def __call__(self, url: str, dest: Path, *, timeout_s: float, tables=None) -> None:
        if self.before is not None:
            self.before()
        self.calls.append({"url": url, "dest": dest, "tables": tables})
        Path(dest).write_text(self.body, encoding="utf-8")


# --- helpers ----------------------------------------------------------------------------------------------------


def _name(db) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT current_database() AS d")
        out = cur.fetchone()["d"]
    db.rollback()
    return out


def _counts(db) -> dict[str, int]:
    """count(*) of EVERY base table (the idempotency and nothing-written proofs count the tables)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        out = {}
        for n in names:
            cur.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(n)))
            out[n] = int(cur.fetchone()["n"])
    db.rollback()
    return out


def _rows(db, table: str) -> dict:
    with db.cursor() as cur:
        cur.execute(sql.SQL("SELECT * FROM {} ORDER BY id").format(sql.Identifier(table)))
        out = {r["id"]: dict(r) for r in cur.fetchall()}
    db.rollback()
    return out


def _journal(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM data_repair WHERE tool = %s ORDER BY seq", (R.TOOL,))
        out = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return out


def _triggers(db) -> dict[str, str]:
    out = M.triggers_enabled(db, _MOVED)
    db.rollback()
    return out


def _our_advisory_locks(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND classid = %s "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
            (LOCK_NAMESPACE,),
        )
        n = int(cur.fetchone()["n"])
    db.rollback()
    return n


def _constraints_valid(db) -> dict[str, bool]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT conname, convalidated FROM pg_constraint WHERE conname = ANY(%s)",
            (list(R.VALIDATED_CONSTRAINTS.values()),),
        )
        out = {r["conname"]: r["convalidated"] for r in cur.fetchall()}
    db.rollback()
    return out


def _clean(db) -> None:
    """Start from an empty tenant-scoped database, COMMITTED (the CLI reads on its own connection): every scoped
    table, so rows other tests committed cannot reach the surprise rules. ``tenant`` and the user tables stay.
    """
    names = sorted(SCOPE_OF)
    with db.cursor() as cur:
        cur.execute(
            sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(map(sql.Identifier, names)))
        )
    db.commit()


def _security(cur, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1)),
    )
    return sid


def _share(cur, sid, tenant, ref, by) -> uuid.UUID:
    cur.execute(
        "INSERT INTO fact_shares_outstanding (tenant_id, security_id, shares, source, source_ref, ratified_by, "
        "valid_from) VALUES (%s, %s, 1000, '10-q-cover', %s, %s, '2026-03-31') RETURNING id",
        (tenant, sid, ref, by),
    )
    return cur.fetchone()["id"]


def _seed_v1(db) -> dict:
    """A small V1-state database: founding share counts (``auto`` and ``operator``) and cash/burn, a ratified
    data-tenant purity row (moves to founding), the DEVCO-like orphan (unratified, data tenant), one thesis with
    its basket, a kill criterion and a decision, two calls, an annotation, and one edition with every child.
    """
    _clean(db)
    out: dict = {}
    with db.cursor() as cur:
        a, b, orphan = _security(cur, "RSTA"), _security(cur, "RSTB"), _security(cur, "DEVCO")
        out["securities"] = (a, b, orphan)
        out["shares"] = [
            _share(cur, a, FOUNDING_WORKSPACE_ID, "A-1", "auto"),
            _share(cur, b, FOUNDING_WORKSPACE_ID, "B-1", "operator"),
        ]
        out["feed_share"] = _share(cur, a, DATA_TENANT_ID, "A-FEED", None)
        cur.execute(
            "INSERT INTO fact_cash_burn (tenant_id, security_id, cash_usd, quarterly_burn_usd, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 41000000, 6500000, '10-q', 'B-10Q', 'operator', "
            "'2026-03-31') RETURNING id",
            (FOUNDING_WORKSPACE_ID, b),
        )
        out["cash"] = cur.fetchone()["id"]
        for tenant, ref, by, sid in (
            (DATA_TENANT_ID, "A-10K", "operator", a),
            (DATA_TENANT_ID, "DEVCO-10K", None, orphan),
        ):
            cur.execute(
                "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
                "ratified_by, valid_from) VALUES (%s, %s, 'reactors', 77, '10-k-segment', %s, %s, '2025-12-31') "
                "RETURNING id",
                (tenant, sid, ref, by),
            )
            out["mix" if by else "orphan"] = cur.fetchone()["id"]
        tid = uuid.uuid4()
        out["thesis"] = tid
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'nuclear', 'n')",
            (tid, FOUNDING_WORKSPACE_ID),
        )
        for i, sid in enumerate((a, b)):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, segment) "
                "VALUES (%s, %s, %s, 'T', 'core', %s, 'reactors')",
                (FOUNDING_WORKSPACE_ID, tid, i, sid),
            )
        cur.execute(
            "INSERT INTO kill_criterion (id, tenant_id, thesis_id, text, ordinal) VALUES (%s, %s, %s, 'k', 0)",
            (uuid.uuid4(), FOUNDING_WORKSPACE_ID, tid),
        )
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, action, decision_date) "
            "VALUES (%s, %s, 'take', %s)",
            (FOUNDING_WORKSPACE_ID, tid, date(2026, 6, 2)),
        )
        call_ids = []
        for asof in (date(2026, 6, 1), date(2026, 6, 2)):
            cur.execute(
                "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind) "
                "VALUES (%s, %s, %s, 'Armed', 'go', '{}'::jsonb, 'cron') RETURNING id",
                (FOUNDING_WORKSPACE_ID, tid, asof),
            )
            call_ids.append(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO call_annotation (id, tenant_id, call_id, thesis_id, asof, action, reason, rule) "
            "SELECT %s, tenant_id, id, thesis_id, asof, 'exclude', 'r', 'test@x' FROM calls WHERE id = %s",
            (uuid.uuid4(), call_ids[0]),
        )
        cur.execute(
            "INSERT INTO edition (tenant_id, number, label, causes, engine_fingerprint, config_hash, "
            "data_known_by, first_night) VALUES (%s, 1, 'first re-run', ARRAY['initial'], 'fp', 'cfg', now(), %s) "
            "RETURNING id",
            (FOUNDING_WORKSPACE_ID, date(2026, 6, 1)),
        )
        ed = cur.fetchone()["id"]
        w = FOUNDING_WORKSPACE_ID
        cur.execute(
            "INSERT INTO edition_event (tenant_id, edition_id, kind) VALUES (%s, %s, 'published')",
            (w, ed),
        )
        cur.execute(
            "INSERT INTO edition_entry (tenant_id, edition_id, thesis_id, security_id, entry_date, estimated, "
            "method) VALUES (%s, %s, %s, %s, %s, false, 'snapshot')",
            (w, ed, tid, a, date(2026, 5, 1)),
        )
        cur.execute(
            "INSERT INTO edition_call (tenant_id, edition_id, thesis_id, asof, state, verdict, card, "
            "data_known_by, engine_fingerprint, page_kind, computed_at) "
            "VALUES (%s, %s, %s, %s, 'Incubating', 'none', '{}'::jsonb, now(), 'fp', 'rerun', now())",
            (w, ed, tid, date(2026, 6, 1)),
        )
        cur.execute(
            "INSERT INTO edition_outcome (tenant_id, edition_id, thesis_id, security_id, arm_date, kind, outcome, "
            "night, prices_known_by) VALUES (%s, %s, %s, %s, %s, 'matured', '{}'::jsonb, %s, now())",
            (w, ed, tid, a, date(2026, 5, 4), date(2026, 6, 1)),
        )
        cur.execute(
            "INSERT INTO edition_cause (tenant_id, edition_id, thesis_id, security_id, night, cause) "
            "VALUES (%s, %s, %s, %s, %s, 'rule_change')",
            (w, ed, tid, a, date(2026, 6, 1)),
        )
    db.commit()
    return out


def _opts(db, tmp_path, **kw) -> R.Options:
    name = _name(db)
    base = dict(database=name, confirm=f"reset {name}", provenance_dir=tmp_path / "prov")
    base.update(kw)
    return R.Options(**base)


def _apply(db, tmp_path, dumps=None, **kw) -> int:
    return R.run(
        _opts(db, tmp_path, apply=True, **kw),
        dump_runner=dumps or Dumps(),
        backups_dir=tmp_path / "backups",
    )


def _backups(tmp_path) -> list[str]:
    d = tmp_path / "backups"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# --- the guard: pure ------------------------------------------------------------------------------------------


def test_the_guard_function_allows_prod_only_with_prod():
    R.assert_cutover_target(
        "alphadeck", prod=True
    )  # the one production name, with the flag: allowed
    R.assert_cutover_target("alphadeck_v2", prod=False)  # a fork
    R.assert_cutover_target("alphadeck_test_ab12cd", prod=False)  # a test database
    for name, prod in (
        ("alphadeck", False),  # prod without --prod
        ("alphadeck_v2", True),  # --prod with any other name
        ("alphadeck_test_ab12cd", True),
        ("alphadeck_dev", False),
        ("alphadeck_dev", True),
        ("alphadeck_sig", False),
        ("alphadeck_sig", True),
        ("alphadeck_prod", False),
        ("alphadeck_prod", True),
        ("Alphadeck", True),
        ("alphadeck ", True),
        ("postgres", False),
        ("", False),
    ):
        with pytest.raises(R.NotACutoverTarget):
            R.assert_cutover_target(name, prod=prod)


def test_the_guard_refusal_reads_the_name_twice_and_the_confirmation_exactly():
    ok = dict(current="alphadeck", database="alphadeck", confirm="reset alphadeck", prod=True)
    assert R.guard_refusal(**ok, apply=False, expect_plan=False) is None
    assert R.guard_refusal(**ok, apply=True, expect_plan=True) is None
    # --prod --apply needs --expect-plan
    assert "--expect-plan" in R.guard_refusal(**ok, apply=True, expect_plan=False)
    for bad in (
        "reset alphadeckx",
        "reset alphadec",
        "Reset alphadeck",
        "reset  alphadeck",
        "reset alphadeck ",
    ):
        assert R.guard_refusal(**{**ok, "confirm": bad}, apply=False, expect_plan=False)
    # --database must be the database the connection is on
    assert R.guard_refusal(
        current="alphadeck_test_x",
        database="alphadeck_test_y",
        confirm="reset alphadeck_test_y",
        prod=False,
        apply=False,
        expect_plan=False,
    )


# --- the guard: the CLI, nothing read beyond the name ---------------------------------------------------------


_GUARD_CASES = {
    "wrong --database": lambda real: (real, real + "_x", f"reset {real}_x", False),
    "confirm off by one character": lambda real: (real, real, f"reset {real[:-1]}", False),
    "confirm with a trailing space": lambda real: (real, real, f"reset {real} ", False),
    "prod name without --prod": lambda real: ("alphadeck", "alphadeck", "reset alphadeck", False),
    "--prod on a test name": lambda real: (real, real, f"reset {real}", True),
    "alphadeck_dev": lambda real: ("alphadeck_dev", "alphadeck_dev", "reset alphadeck_dev", False),
    "alphadeck_dev --prod": lambda real: (
        "alphadeck_dev",
        "alphadeck_dev",
        "reset alphadeck_dev",
        True,
    ),
    "alphadeck_sig": lambda real: ("alphadeck_sig", "alphadeck_sig", "reset alphadeck_sig", False),
    "alphadeck_sig --prod": lambda real: (
        "alphadeck_sig",
        "alphadeck_sig",
        "reset alphadeck_sig",
        True,
    ),
    "alphadeck_prod": lambda real: (
        "alphadeck_prod",
        "alphadeck_prod",
        "reset alphadeck_prod",
        False,
    ),
    "alphadeck_prod --prod": lambda real: (
        "alphadeck_prod",
        "alphadeck_prod",
        "reset alphadeck_prod",
        True,
    ),
}


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
@pytest.mark.parametrize("case", sorted(_GUARD_CASES))
def test_the_guard_matrix(db, monkeypatch, capsys, tmp_path, case, apply):
    """Every refusal exits 2 having read nothing but ``current_database()`` and written nothing: no lock, no
    inventory, no plan, no backup."""
    _seed_v1(db)
    real = _name(db)
    current, database, confirm, prod = _GUARD_CASES[case](real)
    before = _counts(db)
    if current != real:
        monkeypatch.setattr(R, "database_name", lambda conn: current)

    def forbidden(*a, **k):
        raise AssertionError("the guard must refuse before this")

    for target, attr in (
        (C, "inventory"),
        (M, "plan_move"),
        (R, "acquire_repair_lock"),
        (R, "acquire_job_lock"),
        (B, "run_backup"),
    ):
        monkeypatch.setattr(target, attr, forbidden)
    argv = ["run", "--database", database, "--confirm", confirm]
    argv += ["--prod"] if prod else []
    argv += ["--apply", "--expect-plan", str(tmp_path / "p.json")] if apply else []
    assert C.main(argv) == 2
    out = capsys.readouterr().out
    assert out.rstrip().endswith("RESULT: REFUSED - nothing read or written. Exit 2.")
    assert "REFUSE:" in out
    assert _counts(db) == before and not (tmp_path / "p.json").exists()


# --- the dry run ------------------------------------------------------------------------------------------------


def test_a_dry_run_is_read_only_and_takes_no_backup(db, monkeypatch, capsys, tmp_path):
    seeded = _seed_v1(db)
    before = _counts(db)
    opened: list = []
    real_connect = session.connect

    def spy():
        conn = real_connect()
        opened.append(conn)
        return conn

    monkeypatch.setattr(session, "connect", spy)
    dumps = Dumps()
    plan_out = tmp_path / "plan.json"
    code = R.run(
        _opts(db, tmp_path, plan_out=plan_out), dump_runner=dumps, backups_dir=tmp_path / "backups"
    )
    out = capsys.readouterr().out
    assert code == 0, out
    assert _counts(db) == before and _journal(db) == []
    assert dumps.calls == [] and _backups(tmp_path) == []
    assert not (tmp_path / "prov").exists()
    assert len(opened) == 1 and opened[0].read_only is True and opened[0].closed
    assert out.rstrip().endswith("RESULT: DRY RUN - nothing written. Exit 0.")
    assert "REFUSE" not in out
    doc = json.loads(plan_out.read_text(encoding="utf-8"))
    assert doc["moves"]["fact_shares_outstanding"] == {
        "rows": 2,
        "by_ratified": {"auto": 1, "operator": 1},
    }
    assert (
        doc["moves"]["fact_cash_burn"]["rows"] == 1
        and doc["moves"]["fact_revenue_mix"]["rows"] == 1
    )
    assert "resets" not in doc  # V1's history is kept: nothing to reset, nothing planned for it
    assert "plan: reset" not in out
    assert (
        "count: P9 V1 history kept: calls 2, call_annotation 1, edition 1, edition_event 1, edition_entry 1, "
        "edition_call 1, edition_outcome 1, edition_cause 1"
    ) in out
    assert "P11" not in out
    assert doc["orphan_purity"] == {"rows": 1, "action": "leave"}
    assert seeded["orphan"] in _rows(db, "fact_revenue_mix")


def test_the_cli_dry_run_runs_on_a_read_only_connection(db, monkeypatch, capsys):
    _seed_v1(db)
    name = _name(db)
    before = _counts(db)
    assert C.main(["run", "--database", name, "--confirm", f"reset {name}"]) == 0
    assert "RESULT: DRY RUN - nothing written. Exit 0." in capsys.readouterr().out
    assert _counts(db) == before


# --- the backup ---------------------------------------------------------------------------------------------------


def test_the_dump_trailer_check_is_a_pure_function_over_the_tail():
    assert R.DUMP_COMPLETE_MARKER == "-- PostgreSQL database dump complete"
    assert R.dump_is_complete(COMPLETE.encode())
    assert not R.dump_is_complete(INCOMPLETE.encode())
    assert not R.dump_is_complete(b"")
    # newer pg_dump releases print \unrestrict after the trailer: still complete
    assert R.dump_is_complete((COMPLETE + "\\unrestrict abc123\n\n").encode())


def test_verify_dump_reads_only_the_tail_and_the_name(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    good = d / "alphadeck-20261118T090000-pre-v2-reset.sql"
    good.write_text(COMPLETE, encoding="utf-8")
    assert R.verify_dump(good, backups_dir=d) is None
    # the trailer buried before the last 4 KB is not a complete dump
    buried = d / "alphadeck-20261118T090001-pre-v2-reset.sql"
    buried.write_text(COMPLETE + "x" * (R.TAIL_BYTES + 10), encoding="utf-8")
    assert "trailer" in R.verify_dump(buried, backups_dir=d)
    empty = d / "alphadeck-20261118T090002-pre-v2-reset.sql"
    empty.write_text("", encoding="utf-8")
    assert "empty" in R.verify_dump(empty, backups_dir=d)
    odd = d / "not-a-backup.sql"
    odd.write_text(COMPLETE, encoding="utf-8")
    assert "name" in R.verify_dump(odd, backups_dir=d)
    elsewhere = tmp_path / "alphadeck-20261118T090003-pre-v2-reset.sql"
    elsewhere.write_text(COMPLETE, encoding="utf-8")
    assert "backups directory" in R.verify_dump(elsewhere, backups_dir=d)
    assert "missing" in R.verify_dump(d / "alphadeck-20261118T090004.sql", backups_dir=d)


@pytest.mark.parametrize("failure", ["no trailer", "raises"])
def test_apply_refuses_when_the_backup_does_not_verify(db, capsys, tmp_path, failure):
    _seed_v1(db)
    before = _counts(db)
    if failure == "no trailer":
        dumps = Dumps(body=INCOMPLETE)
    else:

        def dumps(url, dest, *, timeout_s, tables=None):
            raise RuntimeError("pg_dump failed (exit 1): boom")

    assert _apply(db, tmp_path, dumps) == 3
    out = capsys.readouterr().out
    assert out.rstrip().endswith("Exit 3.") and "nothing written" in out
    assert _counts(db) == before and _journal(db) == []
    assert _triggers(db) == {t: "O" for t in _MOVED}
    assert _our_advisory_locks(db) == 0


def test_the_backup_must_be_of_the_database_the_guard_read(db, monkeypatch, capsys, tmp_path):
    """The dump target comes from the connection string; it must name the same database ``current_database()``
    returned, or the backup would protect some other database."""
    _seed_v1(db)
    before = _counts(db)
    monkeypatch.setattr(R, "database_url", lambda: "postgresql://u@h:1/alphadeck_test_someone_else")
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps) == 3
    assert dumps.calls == [] and _counts(db) == before
    assert "different database" in capsys.readouterr().out


def test_no_connection_string_or_password_is_printed(db, monkeypatch, capsys, tmp_path):
    """The backup's connection string carries a password: it reaches the dump runner, never the output, even
    when the runner's error message repeats the whole string."""
    _seed_v1(db)
    secret = "s3cr3t-Pa55word"
    url = f"postgresql://alphadeck:{secret}@localhost:5544/{_name(db)}"
    monkeypatch.setattr(R, "database_url", lambda: url)
    seen: list[str] = []

    def leaky(u, dest, *, timeout_s, tables=None):
        seen.append(u)
        raise RuntimeError(f"could not dump {u} (password={secret})")

    assert _apply(db, tmp_path, leaky) == 3
    out = capsys.readouterr().out
    assert seen == [url]
    assert secret not in out and "postgresql://" not in out
    assert os.environ["DATABASE_URL"] not in out
    good = Dumps()
    assert _apply(db, tmp_path, good) == 0
    out = capsys.readouterr().out
    assert secret not in out and "postgresql://" not in out


def test_the_history_archive_dumps_exactly_the_history_tables(db, tmp_path):
    """K2: the archive is still taken, now a frozen snapshot of the KEPT history: exactly the eight tables, and
    never an empty list (an empty list dumps the WHOLE database, pinned at the end)."""
    _seed_v1(db)
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps) == 0
    full, archive = dumps.calls
    assert full["tables"] is None
    assert archive["tables"] == [f"public.{t}" for t in R.HISTORY_TABLES]
    assert sorted(archive["tables"]) == sorted(f"public.{t}" for t in _HISTORY)
    names = _backups(tmp_path)
    assert any(n.endswith("-pre-v2-reset.sql") for n in names)
    assert any(n.endswith("-v1-history.sql") for n in names)
    # the real runner's argv names each table once, and never carries the password
    argv = B._pg_dump_argv("postgresql://u@h/db", Path("x.sql"), tables=archive["tables"])
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--table"] == archive["tables"]
    assert B._pg_dump_argv("postgresql://u@h/db", Path("x.sql")) == [
        "pg_dump",
        "--no-owner",
        "--dbname",
        "postgresql://u@h/db",
        "--file",
        "x.sql",
    ]
    # why the table list must never be empty: no tables is a dump of the whole database, not of none
    assert "--table" not in B._pg_dump_argv("postgresql://u@h/db", Path("x.sql"), tables=[])
    whole = Dumps()
    B.run_backup(
        label=R.ARCHIVE_LABEL,
        database_url="postgresql://u@h/db",
        base_dir=tmp_path / "pin",
        dump_runner=whole,
        tables=[],
    )
    assert [c["tables"] for c in whole.calls] == [None]


def test_no_history_archive_takes_only_the_full_backup(db, tmp_path):
    _seed_v1(db)
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps, history_archive=False) == 0
    assert [c["tables"] for c in dumps.calls] == [None]


def test_a_reused_backup_must_be_newer_than_the_data(db, capsys, tmp_path):
    _seed_v1(db)
    before = _counts(db)
    d = tmp_path / "backups"
    d.mkdir()
    old = d / "alphadeck-20200101T000000-pre-v2-reset.sql"
    old.write_text(COMPLETE, encoding="utf-8")
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps, reuse_backup=str(old), history_archive=False) == 3
    assert "older than" in capsys.readouterr().out
    assert dumps.calls == [] and _counts(db) == before
    # a fresh one is used as is: no new full dump
    stamp = (datetime.now(timezone.utc) + timedelta(seconds=2)).strftime("%Y%m%dT%H%M%S")
    fresh = d / f"alphadeck-{stamp}-pre-v2-reset.sql"
    fresh.write_text(COMPLETE, encoding="utf-8")
    assert _apply(db, tmp_path, dumps, reuse_backup=str(fresh), history_archive=False) == 0
    assert dumps.calls == []


# --- the transaction -------------------------------------------------------------------------------------------


def test_apply_keeps_the_v1_history(db, capsys, tmp_path):
    """Operator decision 2026-11-19: the cutover moves the platform facts and deletes NOTHING of V1's record. The
    eight history tables keep every row and every column; the move happened in the same transaction.
    """
    seeded = _seed_v1(db)
    shares_before = _rows(db, "fact_shares_outstanding")
    history_before = {t: _rows(db, t) for t in _HISTORY}
    before = _counts(db)
    assert all(before[t] for t in _HISTORY)  # the seed holds V1 history in every table
    assert _apply(db, tmp_path) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith("RESULT: CUT OVER. Exit 0.")
    assert "reset:" not in out and "kept: V1 history 9 row(s) (2 calls)" in out
    after = _counts(db)
    assert {t: after[t] for t in _HISTORY} == {t: before[t] for t in _HISTORY}
    assert {t: _rows(db, t) for t in _HISTORY} == history_before
    shares = _rows(db, "fact_shares_outstanding")
    for rid in seeded["shares"]:
        assert shares[rid]["tenant_id"] == DATA_TENANT_ID
        assert {k: v for k, v in shares[rid].items() if k != "tenant_id"} == {
            k: v for k, v in shares_before[rid].items() if k != "tenant_id"
        }
    assert _rows(db, "fact_cash_burn")[seeded["cash"]]["tenant_id"] == DATA_TENANT_ID
    assert _rows(db, "fact_revenue_mix")[seeded["mix"]]["tenant_id"] == FOUNDING_WORKSPACE_ID
    for t in _MOVED:
        assert after[t] == before[t]
    assert _constraints_valid(db) == {c: True for c in R.VALIDATED_CONSTRAINTS.values()}
    assert _triggers(db) == {t: "O" for t in _MOVED}
    (row,) = _journal(db)
    assert row["action"] == "apply" and "reset" not in row["counts"]
    assert row["counts"]["moved"] == {
        "fact_shares_outstanding": 2,
        "fact_cash_burn": 1,
        "fact_revenue_mix": 1,
    }
    assert row["counts"]["history_kept"] == {t: before[t] for t in _HISTORY}
    assert row["counts"]["orphan_purity"] == {
        "rows": 1,
        "action": "leave",
        "deleted": 0,
    }
    provs = sorted((tmp_path / "prov").glob("*.json"))
    assert len(provs) == 1 and row["provenance"] == str(provs[0])
    assert not list((tmp_path / "prov").glob("*.pending"))
    doc = json.loads(provs[0].read_text(encoding="utf-8"))
    assert doc["journal_id"] == str(row["id"])
    assert [t["id"] for t in doc["baseline"]["theses"]] == [str(seeded["thesis"])]
    assert len(doc["baseline"]["basket_members"][str(seeded["thesis"])]) == 2
    assert doc["baseline"]["operator_decision"] == 1
    assert "resets" not in doc["plan"]
    with db.cursor() as cur:
        cur.execute("SELECT security_id FROM security_change")
        stamped = {r["security_id"] for r in cur.fetchall()}
    db.rollback()
    a, b, _ = seeded["securities"]
    assert stamped == {a, b}
    assert _our_advisory_locks(db) == 0


def test_a_failure_mid_transaction_rolls_back_the_move(db, monkeypatch, capsys, tmp_path):
    seeded = _seed_v1(db)
    before = _counts(db)
    valid_before = _constraints_valid(db)

    def failing(conn, security_ids):
        raise RuntimeError("injected: the change stamp failed after the move")

    monkeypatch.setattr(R, "mark_changed", failing)
    assert _apply(db, tmp_path) == 4
    out = capsys.readouterr().out
    assert "ROLLED BACK" in out and out.rstrip().endswith("Exit 4.")
    assert _counts(db) == before and _journal(db) == []
    shares = _rows(db, "fact_shares_outstanding")
    assert {shares[r]["tenant_id"] for r in seeded["shares"]} == {FOUNDING_WORKSPACE_ID}
    assert _triggers(db) == {t: "O" for t in _MOVED}
    assert (
        _constraints_valid(db)
        == valid_before
        == {c: False for c in R.VALIDATED_CONSTRAINTS.values()}
    )
    assert any(n.endswith("-pre-v2-reset.sql") for n in _backups(tmp_path))  # the backup is kept
    prov = tmp_path / "prov"
    assert not prov.exists() or list(prov.iterdir()) == []
    assert _our_advisory_locks(db) == 0


def test_rows_written_after_the_plan_refuse(db, capsys, tmp_path):
    """A share count written while the backup runs (after the plan was printed): the in-transaction re-plan
    differs, so the apply refuses and rolls back."""
    seeded = _seed_v1(db)
    a = seeded["securities"][0]

    def late_write():
        conn = session.connect()
        try:
            with conn.cursor() as cur:
                _share(cur, a, FOUNDING_WORKSPACE_ID, f"LATE-{uuid.uuid4().hex[:6]}", "operator")
            conn.commit()
        finally:
            conn.close()

    dumps = Dumps(before=late_write)
    assert _apply(db, tmp_path, dumps, history_archive=False) == 1
    out = capsys.readouterr().out
    assert "differs from the plan" in out and out.rstrip().endswith("Exit 1.")
    assert {r["tenant_id"] for r in _rows(db, "fact_shares_outstanding").values()} == {
        FOUNDING_WORKSPACE_ID,
        DATA_TENANT_ID,
    }
    assert _counts(db)["calls"] == 2 and _journal(db) == []


def test_expect_plan_drift_refuses(db, capsys, tmp_path):
    seeded = _seed_v1(db)
    plan_out = tmp_path / "plan.json"
    assert R.run(_opts(db, tmp_path, plan_out=plan_out), backups_dir=tmp_path / "backups") == 0
    with db.cursor() as cur:
        _share(cur, seeded["securities"][1], FOUNDING_WORKSPACE_ID, "B-2", "auto")
    db.commit()
    before = _counts(db)
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps, expect_plan=plan_out) == 1
    out = capsys.readouterr().out
    assert "--expect-plan" in out and "fact_shares_outstanding" in out
    assert dumps.calls == [] and _counts(db) == before and _journal(db) == []


def test_a_matching_expect_plan_applies(db, tmp_path):
    _seed_v1(db)
    plan_out = tmp_path / "plan.json"
    assert R.run(_opts(db, tmp_path, plan_out=plan_out), backups_dir=tmp_path / "backups") == 0
    assert _apply(db, tmp_path, expect_plan=plan_out) == 0
    assert len(_journal(db)) == 1


# --- preflight refusals --------------------------------------------------------------------------------------------


def test_an_unacknowledged_surprise_refuses(db, capsys, tmp_path):
    seeded = _seed_v1(db)
    with db.cursor() as cur:  # rule 3: a platform table's row under a workspace
        cur.execute(
            "INSERT INTO fact_insider_txn (tenant_id, security_id, accession, valid_from) "
            "VALUES (%s, %s, 'W-STRAY', '2026-05-01')",
            (FOUNDING_WORKSPACE_ID, seeded["securities"][0]),
        )
    db.commit()
    before = _counts(db)
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps) == 1
    out = capsys.readouterr().out
    assert "REFUSE: P5" in out and "fact_insider_txn" in out
    assert dumps.calls == [] and _counts(db) == before


def test_workspace_company_catalysts_refuse(db, capsys, tmp_path):
    seeded = _seed_v1(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_catalyst (tenant_id, security_id, catalyst_type, grade, label, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 'contract', 'core', 'l', 'ratified', 'CAT-F', 'operator', "
            "'2026-05-01')",
            (FOUNDING_WORKSPACE_ID, seeded["securities"][0]),
        )
    db.commit()
    before = _counts(db)
    assert _apply(db, tmp_path) == 1
    assert "nothing converts these; none were expected" in capsys.readouterr().out
    assert _counts(db) == before


def test_a_vocabulary_violation_refuses(db, capsys, tmp_path):
    seeded = _seed_v1(db)
    with db.cursor() as cur:  # 0206 is NOT VALID: an old row may still carry an odd word
        cur.execute(
            "ALTER TABLE fact_shares_outstanding DROP CONSTRAINT fact_shares_outstanding_ratified_by_vocabulary"
        )
        _share(cur, seeded["securities"][0], FOUNDING_WORKSPACE_ID, "ODD-1", "someone")
        cur.execute(
            "ALTER TABLE fact_shares_outstanding ADD CONSTRAINT fact_shares_outstanding_ratified_by_vocabulary "
            "CHECK (ratified_by IS NULL OR ratified_by IN ('auto', 'operator') "
            "OR ratified_by ~ '^[^@[:space:]]+@[^@[:space:]]+$') NOT VALID"
        )
    db.commit()
    try:
        before = _counts(db)
        assert _apply(db, tmp_path) == 1
        assert "REFUSE: P7" in capsys.readouterr().out
        assert _counts(db) == before
    finally:
        with db.cursor() as cur:
            cur.execute("DELETE FROM fact_shares_outstanding WHERE ratified_by = 'someone'")
        db.commit()


@pytest.mark.parametrize("action", ["leave", "delete"])
def test_the_orphan_purity_row_is_left_by_default_and_deleted_on_request(db, tmp_path, action):
    seeded = _seed_v1(db)
    kw = {} if action == "leave" else {"orphan_purity": "delete"}
    assert _apply(db, tmp_path, **kw) == 0
    present = seeded["orphan"] in _rows(db, "fact_revenue_mix")
    assert present is (action == "leave")
    (row,) = _journal(db)
    assert row["counts"]["orphan_purity"] == {
        "rows": 1,
        "action": action,
        "deleted": 0 if action == "leave" else 1,
    }


def test_the_journal_row_deletes_nothing_by_default(db, tmp_path):
    """The cutover's journal row is a NON-deleting repair (``--orphan-purity leave``, K7): the automatic edition
    cut then keeps the rules change separated from it (Edition 2 labeled "rule change: ..." first). Its summary
    becomes part of that label, so it says what happened and names no reset."""
    _seed_v1(db)
    assert _apply(db, tmp_path) == 0
    (row,) = _journal(db)
    assert row["deletes"] is False
    assert (
        row["rows_changed"] == 4
    )  # 2 share counts + 1 cash/burn + 1 purity moved; nothing deleted
    assert row["summary"] == _SUMMARY == R.journal_summary(moved=4, deleted=0)
    assert "reset" not in row["summary"].lower() and "backup" not in row["summary"]


def test_the_journal_row_deletes_only_with_orphan_delete(db, tmp_path):
    _seed_v1(db)
    assert _apply(db, tmp_path, orphan_purity="delete") == 0
    (row,) = _journal(db)
    assert row["deletes"] is True
    assert row["rows_changed"] == 5  # the four moved + the one orphan purity row deleted
    assert row["summary"] == _SUMMARY + "; deleted 1 unratified orphan purity row(s)"
    assert row["summary"] == R.journal_summary(moved=4, deleted=1)


def _window_settings(monkeypatch):
    real = R.get_settings()
    patched = real.model_copy(
        update={
            "onboard_window_before_min": 60,
            "onboard_window_after_min": 60,
            "cron_run_at": "22:30",
        }
    )
    monkeypatch.setattr(R, "get_settings", lambda: patched)


@pytest.mark.parametrize("apply", [False, True])
def test_inside_the_nightly_window_exits_75(db, monkeypatch, capsys, tmp_path, apply):
    _seed_v1(db)
    before = _counts(db)
    _window_settings(monkeypatch)
    tz = market_time.market_tz()
    inside = datetime.combine(market_time.market_today(), time(22, 40), tzinfo=tz)
    dumps = Dumps()
    code = R.run(
        _opts(db, tmp_path, apply=apply),
        dump_runner=dumps,
        backups_dir=tmp_path / "backups",
        now=lambda: inside,
    )
    assert code == 75
    assert "nightly window" in capsys.readouterr().out
    assert dumps.calls == [] and _counts(db) == before and _our_advisory_locks(db) == 0
    outside = datetime.combine(market_time.market_today(), time(9, 0), tzinfo=tz)
    assert R.run(_opts(db, tmp_path), backups_dir=tmp_path / "backups", now=lambda: outside) == 0


@pytest.mark.parametrize("lock_id", [3, 7])
def test_a_held_job_lock_exits_75(db, capsys, tmp_path, lock_id):
    _seed_v1(db)
    before = _counts(db)
    held = acquire_job_lock(lock_id=lock_id, holder="another-job", what="job")
    try:
        dumps = Dumps()
        assert _apply(db, tmp_path, dumps) == 75
        out = capsys.readouterr().out
        assert "another-job" in out and out.rstrip().endswith("Exit 75.")
        assert dumps.calls == [] and _counts(db) == before
        assert (
            _our_advisory_locks(db) == 1
        )  # only the other session's lock: ours were all given back
    finally:
        held.release()
    assert _our_advisory_locks(db) == 0


def test_a_running_pass_exits_75(db, capsys, tmp_path):
    _seed_v1(db)
    held = acquire_pass_lock(holder="cron")
    try:
        assert _apply(db, tmp_path) == 75
    finally:
        held.release()
    assert _our_advisory_locks(db) == 0 and _journal(db) == []


def test_an_unapplied_migration_refuses(db, monkeypatch, capsys, tmp_path):
    _seed_v1(db)
    fake = tmp_path / "migrations"
    fake.mkdir()
    for p in R.MIGRATIONS_DIR.glob("*.sql"):
        (fake / p.name).write_text("", encoding="utf-8")
    (fake / "0999_not_applied.sql").write_text("", encoding="utf-8")
    monkeypatch.setattr(R, "MIGRATIONS_DIR", fake)
    before = _counts(db)
    assert _apply(db, tmp_path) == 1
    out = capsys.readouterr().out
    assert "REFUSE: P1" in out and "0999_not_applied.sql" in out
    assert _counts(db) == before


# P11 (every foreign key into the reset tables comes from a reset table) is gone with the reset: no table is emptied
# any more, so no cascade can start from one. The one delete left (the orphan purity rows, by id, on request) was
# never covered by P11; the apply instead checks, inside its transaction, that every history count is unchanged.


def test_the_cutover_deletes_no_history_table():
    """Operator decision 2026-11-19 ("keep the history"): no table's cutover action is a reset; the eight history
    tables are named KEPT, and they are exactly the tables the archive dumps and the baseline counts.
    """
    assert "reset" not in set(C.CUTOVER_ACTION.values())
    assert C.HISTORY_KEPT == "keep (V1 history: operator decision 2026-11-19)"
    kept = {t for t, a in C.CUTOVER_ACTION.items() if a == C.HISTORY_KEPT}
    assert kept == set(R.HISTORY_TABLES) == set(_HISTORY)
    assert len(R.HISTORY_TABLES) == 8
    assert not hasattr(R, "RESET_ORDER") and not hasattr(R, "_delete_all")


def test_a_plan_file_from_the_resetting_cutover_differs():
    """A dry run's ``--plan-out`` written before V1's history was kept carries a ``resets`` section:
    ``--expect-plan`` with it refuses (take a new dry run). PURE."""
    new = {"database": "x", "moves": {}, "orphan_purity": {"rows": 0, "action": "leave"}}
    old = {**new, "resets": {"calls": 1906}}
    assert R.plan_differences(old, new) == ["resets.calls: expected 1906, found None"]
    assert R.plan_differences(new, new) == []


# --- once only -----------------------------------------------------------------------------------------------------


def test_a_second_apply_does_nothing_and_journals_nothing(db, capsys, tmp_path):
    _seed_v1(db)
    assert _apply(db, tmp_path) == 0
    capsys.readouterr()
    after_first = _counts(db)
    backups = _backups(tmp_path)
    dumps = Dumps()
    assert _apply(db, tmp_path, dumps) == 0
    out = capsys.readouterr().out
    assert "already cut over on" in out and "nothing to do" in out
    assert _counts(db) == after_first and len(_journal(db)) == 1
    assert dumps.calls == [] and _backups(tmp_path) == backups


def test_after_a_v2_night_a_second_run_does_nothing(db, capsys, tmp_path):
    """The history rows (V1's and the first V2 night's) no longer matter to the second-run check: cut over and
    nothing left to move -> "already cut over", exit 0, no backup, no journal row, every count unchanged.
    """
    seeded = _seed_v1(db)
    assert _apply(db, tmp_path) == 0
    with db.cursor() as cur:  # the first V2 night
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind) "
            "VALUES (%s, %s, %s, 'Armed', 'go', '{}'::jsonb, 'cron')",
            (FOUNDING_WORKSPACE_ID, seeded["thesis"], date(2026, 11, 19)),
        )
    db.commit()
    capsys.readouterr()
    before = _counts(db)
    backups = _backups(tmp_path)
    dumps = Dumps()
    for apply in (False, True):
        code = R.run(
            _opts(db, tmp_path, apply=apply), dump_runner=dumps, backups_dir=tmp_path / "backups"
        )
        out = capsys.readouterr().out
        assert code == 0, out
        assert "already cut over on" in out and "nothing to do" in out and "REFUSE" not in out
    assert dumps.calls == [] and _backups(tmp_path) == backups
    assert _counts(db) == before and len(_journal(db)) == 1


def test_after_the_cutover_a_row_left_to_move_refuses_a_second_run(db, capsys, tmp_path):
    """Cut over, but a founding-workspace share count appeared since: a second cutover is refused (investigate by
    hand), whatever the history holds."""
    seeded = _seed_v1(db)
    assert _apply(db, tmp_path) == 0
    with db.cursor() as cur:
        _share(cur, seeded["securities"][0], FOUNDING_WORKSPACE_ID, "A-AFTER", "auto")
    db.commit()
    capsys.readouterr()
    before = _counts(db)
    assert _apply(db, tmp_path) == 1
    out = capsys.readouterr().out
    assert "REFUSE: P10 already cut over on" in out and "1 row(s) to move or delete" in out
    assert _counts(db) == before and len(_journal(db)) == 1


def test_the_baseline_records_the_history_counts_and_editions(db, tmp_path):
    """The recall baseline counts V1's history and lists its editions, so ``verify`` can prove nothing of it was
    lost."""
    _seed_v1(db)
    with db.cursor() as cur:
        cur.execute("SELECT id, tenant_id, number, created_at FROM edition")
        (ed,) = cur.fetchall()
    db.rollback()
    before = _counts(db)
    assert _apply(db, tmp_path) == 0
    (prov,) = sorted((tmp_path / "prov").glob("*.json"))
    base = json.loads(prov.read_text(encoding="utf-8"))["baseline"]
    assert base["history"] == {t: before[t] for t in _HISTORY}
    assert base["history"]["calls"] == 2 and base["history"]["edition"] == 1
    assert base["editions"] == [
        {
            "id": str(ed["id"]),
            "tenant_id": str(ed["tenant_id"]),
            "number": 1,
            "created_at": str(ed["created_at"]),
        }
    ]


def test_theses_baskets_decisions_and_children_are_untouched(db, tmp_path):
    """The recall proof: every table but the journal and the change log keeps its count (V1's history included),
    and the workspace's rows keep their ids and every column."""
    _seed_v1(db)
    keep = (
        "thesis",
        "basket_member",
        "kill_criterion",
        "operator_decision",
        "basket_snapshot",
        "catalyst",
    )
    rows_before = {t: _rows(db, t) for t in keep}
    before = _counts(db)
    assert _apply(db, tmp_path) == 0
    after = _counts(db)
    changed = {"data_repair", "security_change"}
    assert {t: n for t, n in after.items() if t not in changed} == {
        t: n for t, n in before.items() if t not in changed
    }
    assert {t: _rows(db, t) for t in keep} == rows_before
    assert after["thesis"] == 1 and after["basket_member"] == 2


# --- V2 W6a: the counting catalyst entries are a kept, counted child ---------------------------------------------


def test_the_counting_catalyst_entries_are_kept_and_counted(db, capsys, tmp_path):
    """``fact_thesis_catalyst`` (0212) is a per-thesis child: the baseline counts it (so ``v2_verify``'s recall
    check covers it), P9 prints its count, and the cutover keeps every row."""
    assert "fact_thesis_catalyst" in R._CHILD_TABLES
    seeded = _seed_v1(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_thesis_catalyst (tenant_id, thesis_id, entry_id, counts, grade, label, citation, "
            "source, entered_at, valid_from, recorded_at) SELECT tenant_id, id, gen_random_uuid(), true, 'flip', "
            "'a whole-card entry', 'https://example.com/x', 'thesis_catalyst', now(), '2026-06-01', now() "
            "FROM thesis WHERE id = %s",
            (seeded["thesis"],),
        )
    db.commit()
    before = _rows(db, "fact_thesis_catalyst")
    assert _apply(db, tmp_path) == 0
    assert "fact_theme_conviction 0, fact_thesis_catalyst 1" in capsys.readouterr().out
    assert _rows(db, "fact_thesis_catalyst") == before
    (prov,) = sorted((tmp_path / "prov").glob("*.json"))
    doc = json.loads(prov.read_text(encoding="utf-8"))
    assert doc["baseline"]["children"][str(seeded["thesis"])]["fact_thesis_catalyst"] == 1
