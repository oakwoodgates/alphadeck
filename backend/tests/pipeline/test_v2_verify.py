"""The V2 cutover's VERIFY step (``python -m pipeline.v2_cutover verify --stage ...``, W10b).

Every test starts from a REAL post-cutover database: a small V1-state seed, then the apply path itself
(``v2_reset.run`` with a stub dump runner and a clock outside the nightly window), which writes the real provenance
file the verify reads. Then a test breaks the result in ONE way and asserts the stage fails NAMING it.

Safety: the per-worktree test database only (the ``db`` fixture); no pg_dump (a fail-loud stub for the module); the
two 0206 constraints the apply validates are put back to NOT VALID after each test; the one app_user a test adds is
deleted after it.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

import domain.scopes as scopes
import pipeline.backup as B
from db import session
from domain import market_time
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, SCOPE_OF
from pipeline import v2_cutover as C
from pipeline import v2_reset as R
from pipeline import v2_verify as V

COMPLETE = "--\n-- PostgreSQL database dump\n--\n\n--\n-- PostgreSQL database dump complete\n--\n\n"
_VOCABULARY = (
    "CHECK (ratified_by IS NULL OR ratified_by IN ('auto', 'operator') "
    "OR ratified_by ~ '^[^@[:space:]]+@[^@[:space:]]+$') NOT VALID"
)


@pytest.fixture(autouse=True)
def _never_a_real_pg_dump(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a test reached the real pg_dump")

    monkeypatch.setattr(B, "_pg_dump", boom)


@pytest.fixture(autouse=True)
def _vocabulary_back_to_not_valid(request):
    """The apply VALIDATEs 0206's two constraints; other files read them as the migration leaves them."""
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
                    _not_valid(cur, table, name)
        conn.commit()
    finally:
        conn.close()


def _not_valid(cur, table: str, name: str) -> None:
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


class Dumps:
    def __call__(self, url: str, dest: Path, *, timeout_s: float, tables=None) -> None:
        Path(dest).write_text(COMPLETE, encoding="utf-8")


# --- the seed and the cutover ------------------------------------------------------------------------------


def _name(db) -> str:
    with db.cursor() as cur:
        cur.execute("SELECT current_database() AS d")
        out = cur.fetchone()["d"]
    db.rollback()
    return out


def _counts(db) -> dict[str, int]:
    """count(*) of EVERY base table."""
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


def _security(cur, ticker: str, kind: str = "equity") -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from, instrument_kind) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1), kind),
    )
    return sid


def _share(cur, sid, tenant, ref, by) -> None:
    cur.execute(
        "INSERT INTO fact_shares_outstanding (tenant_id, security_id, shares, source, source_ref, ratified_by, "
        "valid_from) VALUES (%s, %s, 1000, '10-q-cover', %s, %s, '2026-03-31')",
        (tenant, sid, ref, by),
    )


def _member(cur, tid, ordinal: int, ticker: str, sid, segment: str) -> None:
    cur.execute(
        "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, segment) "
        "VALUES (%s, %s, %s, %s, 'core', %s, %s)",
        (FOUNDING_WORKSPACE_ID, tid, ordinal, ticker, sid, segment),
    )


@pytest.fixture
def admin(db):
    """One enabled super_admin in the founding workspace (V3 needs one); deleted after the test."""
    uid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'verify test', 'x', 'super_admin', %s)",
            (uid, f"verify-{uid.hex}@example.test", FOUNDING_WORKSPACE_ID),
        )
    db.commit()
    yield uid
    db.rollback()
    with db.cursor() as cur:
        cur.execute("DELETE FROM app_user WHERE id = %s", (uid,))
    db.commit()


def _seed(db) -> dict:
    """A V1-state database: founding share counts and cash/burn, a feed share row, a ratified data-tenant purity
    row (moves to founding), the orphan (unratified, data tenant), two theses (RSTA + RSTB, and RSTA again in
    another segment), a kill criterion, a decision, V1 calls with an annotation, and one edition."""
    names = sorted(SCOPE_OF)
    out: dict = {}
    with db.cursor() as cur:
        cur.execute(
            sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(map(sql.Identifier, names)))
        )
        a, b, orphan = _security(cur, "RSTA"), _security(cur, "RSTB"), _security(cur, "DEVCO")
        out.update(a=a, b=b)
        _share(cur, a, FOUNDING_WORKSPACE_ID, "A-1", "auto")
        _share(cur, b, FOUNDING_WORKSPACE_ID, "B-1", "operator")
        _share(cur, a, DATA_TENANT_ID, "A-FEED", None)
        cur.execute(
            "INSERT INTO fact_cash_burn (tenant_id, security_id, cash_usd, quarterly_burn_usd, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 41000000, 6500000, '10-q', 'B-10Q', 'operator', '2026-03-31')",
            (FOUNDING_WORKSPACE_ID, b),
        )
        for ref, by, sid in (("A-10K", "operator", a), ("DEVCO-10K", None, orphan)):
            cur.execute(
                "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
                "ratified_by, valid_from) VALUES (%s, %s, 'reactors', 77, '10-k-segment', %s, %s, '2025-12-31')",
                (DATA_TENANT_ID, sid, ref, by),
            )
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        out.update(t1=t1, t2=t2)
        for tid, label in ((t1, "nuclear"), (t2, "grid")):
            cur.execute(
                "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, 'n')",
                (tid, FOUNDING_WORKSPACE_ID, label),
            )
        _member(cur, t1, 0, "RSTA", a, "reactors")
        _member(cur, t1, 1, "RSTB", b, "fuel")
        _member(cur, t2, 0, "RSTA", a, "wires")
        cur.execute(
            "INSERT INTO kill_criterion (id, tenant_id, thesis_id, text, ordinal) VALUES (%s, %s, %s, 'k', 0)",
            (uuid.uuid4(), FOUNDING_WORKSPACE_ID, t1),
        )
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, action, decision_date) VALUES (%s, %s, 'take', %s)",
            (FOUNDING_WORKSPACE_ID, t1, date(2026, 6, 2)),
        )
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind) "
            "VALUES (%s, %s, %s, 'Armed', 'go', '{}'::jsonb, 'cron') RETURNING id",
            (FOUNDING_WORKSPACE_ID, t1, date(2026, 6, 1)),
        )
        call_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO call_annotation (id, tenant_id, call_id, thesis_id, asof, action, reason, rule) "
            "SELECT %s, tenant_id, id, thesis_id, asof, 'exclude', 'r', 'test@x' FROM calls WHERE id = %s",
            (uuid.uuid4(), call_id),
        )
        _edition(cur, FOUNDING_WORKSPACE_ID, 1, [(t1, a)])
    db.commit()
    return out


def _edition(cur, tenant, number: int, entries) -> uuid.UUID:
    cur.execute(
        "INSERT INTO edition (tenant_id, number, label, causes, engine_fingerprint, config_hash, data_known_by, "
        "first_night) VALUES (%s, %s, 'first re-run', ARRAY['initial'], 'fp', 'cfg', now(), %s) RETURNING id",
        (tenant, number, date(2026, 6, 1)),
    )
    ed = cur.fetchone()["id"]
    for tid, sid in entries:
        cur.execute(
            "INSERT INTO edition_entry (tenant_id, edition_id, thesis_id, security_id, entry_date, estimated, "
            "method) VALUES (%s, %s, %s, %s, %s, false, 'snapshot')",
            (tenant, ed, tid, sid, date(2026, 5, 1)),
        )
    return ed


def _outside_window() -> datetime:
    return datetime.combine(market_time.market_today(), time(9, 0), tzinfo=market_time.market_tz())


@pytest.fixture
def cut(db, admin, tmp_path) -> dict:
    """A real post-cutover database and its real provenance file."""
    seed = _seed(db)
    name = _name(db)
    code = R.run(
        R.Options(
            database=name,
            confirm=f"reset {name}",
            apply=True,
            provenance_dir=tmp_path / "prov",
        ),
        dump_runner=Dumps(),
        backups_dir=tmp_path / "backups",
        now=_outside_window,
    )
    assert code == R.EXIT_OK
    (prov,) = sorted((tmp_path / "prov").glob("*.json"))
    return {**seed, "name": name, "prov": prov, "admin": admin, "tmp": tmp_path}


def _run(cut, capsys, stage: str = "reset", health_get=None, **kw) -> tuple[int, str]:
    capsys.readouterr()
    base = dict(database=cut["name"], stage=stage, provenance=cut["prov"])
    base.update(kw)
    code = V.run(V.Options(**base), health_get=health_get)
    return code, capsys.readouterr().out


def _fails(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("FAIL:")]


def _exec(db, query: str, params=()) -> None:
    with db.cursor() as cur:
        cur.execute(query, params)
    db.commit()


# --- the later stages' honest writes (what the backfill, the nightly and Edition 1 add) ---------------------


def _backfill(db, cut, *, status: str = "done") -> None:
    for sid in (cut["a"], cut["b"]):
        _exec(
            db,
            "INSERT INTO platform_extraction_attempt (tenant_id, security_id, run_kind, inputs_key, status, error) "
            "VALUES (%s, %s, 'backfill', 'k', %s, %s)",
            (DATA_TENANT_ID, sid, status, "not cached" if status == "error" else None),
        )


def _night(db, theses, *, asof: date | None = None, run_kind: str = "cron", **cols) -> None:
    night = asof or market_time.market_today()
    for tid in theses:
        _exec(
            db,
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind, reconstructed, "
            "ingest_fresh) VALUES (%s, %s, %s, 'Incubating', 'none', '{}'::jsonb, %s, %s, %s)",
            (
                cols.get("tenant", FOUNDING_WORKSPACE_ID),
                tid,
                night,
                run_kind,
                cols.get("reconstructed", False),
                cols.get("ingest_fresh", True),
            ),
        )


def _edition_1(db, cut, entries=None) -> None:
    with db.cursor() as cur:
        _edition(
            cur,
            FOUNDING_WORKSPACE_ID,
            1,
            (
                entries
                if entries is not None
                else [(cut["t1"], cut["a"]), (cut["t1"], cut["b"]), (cut["t2"], cut["a"])]
            ),
        )
    db.commit()


# --- the clean path -----------------------------------------------------------------------------------------


def test_a_clean_cutover_verifies_at_every_stage(db, cut, capsys):
    code, out = _run(cut, capsys, "reset")
    assert code == V.EXIT_VERIFIED, out
    assert "RESULT: VERIFIED. Exit 0." in out and not _fails(out)
    assert "2 theses, 3 basket rows, 0 dropped" in out
    _backfill(db, cut)
    code, out = _run(cut, capsys, "backfill")
    assert code == 0 and not _fails(out), out
    _night(db, [cut["t1"], cut["t2"]])
    code, out = _run(cut, capsys, "nightly")
    assert code == 0 and not _fails(out), out
    _edition_1(db, cut)
    code, out = _run(cut, capsys, "edition")
    assert code == 0 and not _fails(out), out
    for prefix in ("ok: V1", "ok: V5", "ok: B1", "ok: N1", "ok: E1"):
        assert prefix in out


def test_every_line_is_ok_fail_or_note_and_the_url_is_never_printed(db, cut, capsys):
    code, out = _run(cut, capsys, "reset")
    body = [
        line
        for line in out.splitlines()
        if line and not line.startswith(("V2 CUTOVER", "target DB"))
    ]
    assert all(line.startswith(("ok: ", "FAIL: ", "note: ", "RESULT: ")) for line in body), out
    assert session.database_url() not in out and "postgresql://" not in out


def test_verify_is_read_only(db, cut, capsys, tmp_path):
    """Every stage leaves every table's row count unchanged, holds no advisory lock, writes no file but --json, and
    its connection refuses a write even in a fresh transaction."""
    _backfill(db, cut)
    _night(db, [cut["t1"], cut["t2"]])
    _edition_1(db, cut)
    before = _counts(db)
    files_before = sorted(p for p in tmp_path.rglob("*"))
    for (
        stage
    ) in V.STAGES:  # the earlier stages FAIL V8 on this post-edition state: only the counts matter
        code, out = _run(cut, capsys, stage)
        assert code == (0 if stage == "edition" else V.EXIT_FAILED), out
    assert _counts(db) == before
    assert sorted(p for p in tmp_path.rglob("*")) == files_before
    out_json = tmp_path / "report" / "verify.json"
    assert _run(cut, capsys, "edition", json_path=out_json)[0] == 0
    assert _counts(db) == before
    conn = V.connect_read_only()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW default_transaction_read_only")
            assert cur.fetchone()["default_transaction_read_only"] == "on"
        conn.rollback()
        for _ in range(2):  # a rollback opens a fresh transaction: still read-only
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM basket_member")
            conn.rollback()
    finally:
        conn.close()
    assert _counts(db) == before


def test_json_report(db, cut, capsys, tmp_path):
    path = tmp_path / "out" / "v.json"
    _exec(db, "DELETE FROM kill_criterion")
    code, out = _run(cut, capsys, "reset", json_path=path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert code == doc["exit_code"] == V.EXIT_FAILED
    assert doc["stage"] == "reset" and doc["database"] == cut["name"] and doc["failed"] >= 1
    assert any(c["id"].startswith("V5") and not c["ok"] for c in doc["checks"])
    assert session.database_url() not in path.read_text(encoding="utf-8")


# --- the reset stage: one break, one named FAIL -------------------------------------------------------------


def _ticker_line(out: str, check: str) -> str:
    lines = [f for f in _fails(out) if f.startswith(f"FAIL: {check}")]
    assert lines, out
    return "\n".join(lines)


def test_recall_names_the_thesis_and_the_company_a_card_lost(db, cut, capsys):
    _exec(db, "DELETE FROM basket_member WHERE thesis_id = %s AND ticker = 'RSTB'", (cut["t1"],))
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED
    line = _ticker_line(out, "V5 recall")
    assert str(cut["t1"]) in line and "RSTB" in line and str(cut["b"]) in line
    assert "1 dropped" in line
    assert "RESULT: 1 check(s) failed. Exit 5." in out or "check(s) failed. Exit 5." in out


def test_recall_fails_a_basket_row_recreated_with_a_new_id(db, cut, capsys):
    _exec(db, "DELETE FROM basket_member WHERE thesis_id = %s AND ticker = 'RSTA'", (cut["t2"],))
    _member_commit(db, cut["t2"], "RSTA", cut["a"], "wires")
    code, out = _run(cut, capsys)
    line = _ticker_line(out, "V5 recall")
    assert code == V.EXIT_FAILED and "RSTA" in line and "id" in line and str(cut["t2"]) in line


def _member_commit(db, tid, ticker, sid, segment) -> None:
    with db.cursor() as cur:
        _member(cur, tid, 5, ticker, sid, segment)
    db.commit()


def test_recall_fails_a_company_moved_to_another_segment(db, cut, capsys):
    _exec(db, "DELETE FROM basket_member WHERE thesis_id = %s AND ticker = 'RSTB'", (cut["t1"],))
    _member_commit(db, cut["t1"], "RSTB", cut["b"], "other")
    _, out = _run(cut, capsys)
    assert "RSTB" in _ticker_line(out, "V5 recall")


def test_a_thesis_recreated_with_a_new_id_fails(db, cut, capsys):
    new = uuid.uuid4()
    _exec(
        db,
        "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'grid', 'n')",
        (new, FOUNDING_WORKSPACE_ID),
    )
    _exec(db, "DELETE FROM thesis WHERE id = %s", (cut["t2"],))
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED
    assert str(cut["t2"]) in _ticker_line(out, "V4 theses")
    assert "RSTA" in _ticker_line(out, "V5 recall")


def test_an_archived_thesis_fails(db, cut, capsys):
    _exec(db, "UPDATE thesis SET archived_at = now() WHERE id = %s", (cut["t2"],))
    _, out = _run(cut, capsys)
    assert "archived" in _ticker_line(out, "V4 theses")


@pytest.mark.parametrize(
    ("query", "check"),
    [
        ("DELETE FROM kill_criterion", "V5 children"),
        ("DELETE FROM operator_decision", "V5 decisions"),
    ],
)
def test_child_and_decision_counts_must_equal_the_baseline(db, cut, capsys, query, check):
    _exec(db, query)
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED and _ticker_line(out, check)


def test_a_changed_user_fails(db, cut, capsys):
    _exec(db, "UPDATE app_user SET disabled_at = now() WHERE id = %s", (cut["admin"],))
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED and str(cut["admin"]) in _ticker_line(out, "V3 users")


def test_no_enabled_super_admin_fails(db, cut, capsys):
    _exec(db, "UPDATE app_user SET role = 'user' WHERE role = 'super_admin'")
    _, out = _run(cut, capsys)
    assert "super_admin" in _ticker_line(out, "V3 tenants")


def test_a_thesis_on_the_data_tenant_fails(db, cut, capsys):
    _exec(
        db,
        "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'stray', 'n')",
        (uuid.uuid4(), DATA_TENANT_ID),
    )
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED and "data tenant" in _ticker_line(out, "V3 tenants")


def test_a_share_row_left_in_a_workspace_fails(db, cut, capsys):
    with db.cursor() as cur:
        _share(cur, cut["a"], FOUNDING_WORKSPACE_ID, "A-LEFT", "auto")
    db.commit()
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED and "fact_shares_outstanding" in _ticker_line(
        out, "V6 platform facts"
    )


def test_a_company_that_lost_its_share_count_is_named(db, cut, capsys):
    _exec(db, "ALTER TABLE fact_shares_outstanding DISABLE TRIGGER USER")
    try:
        _exec(db, "DELETE FROM fact_shares_outstanding WHERE security_id = %s", (cut["b"],))
    finally:
        _exec(db, "ALTER TABLE fact_shares_outstanding ENABLE TRIGGER USER")
    _, out = _run(cut, capsys)
    assert "RSTB" in _ticker_line(out, "V6 platform facts")


def test_a_ratified_purity_row_on_the_data_tenant_fails(db, cut, capsys):
    _exec(
        db,
        "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
        "ratified_by, valid_from) VALUES (%s, %s, 'x', 5, '10-k-segment', 'X', 'operator', '2025-12-31')",
        (DATA_TENANT_ID, cut["b"]),
    )
    _, out = _run(cut, capsys)
    assert _ticker_line(out, "V7 purity")


def test_a_calls_row_left_fails(db, cut, capsys):
    _night(db, [cut["t1"]])
    code, out = _run(cut, capsys)
    assert code == V.EXIT_FAILED and "calls" in _ticker_line(out, "V8 history")


def test_a_constraint_left_not_valid_fails(db, cut, capsys):
    with db.cursor() as cur:
        _not_valid(cur, "fact_cash_burn", R.VALIDATED_CONSTRAINTS["fact_cash_burn"])
    db.commit()
    _, out = _run(cut, capsys)
    assert "fact_cash_burn_ratified_by_vocabulary" in _ticker_line(out, "V2 schema")


def test_an_unapplied_migration_fails(db, cut, capsys, monkeypatch, tmp_path):
    fake = tmp_path / "migrations"
    fake.mkdir()
    for p in Path(R.MIGRATIONS_DIR).glob("*.sql"):
        (fake / p.name).write_text("", encoding="utf-8")
    (fake / "9999_future.sql").write_text("", encoding="utf-8")
    monkeypatch.setattr(R, "MIGRATIONS_DIR", fake)
    _, out = _run(cut, capsys)
    assert "9999_future.sql" in _ticker_line(out, "V2 schema")


def test_a_second_journal_row_fails(db, cut, capsys):
    _exec(
        db,
        "INSERT INTO data_repair (tenant_id, tool, action, summary, counts, rows_changed, deletes) "
        "VALUES (%s, %s, 'apply', 'again', '{}'::jsonb, 0, true)",
        (DATA_TENANT_ID, R.TOOL),
    )
    _, out = _run(cut, capsys)
    assert "2" in _ticker_line(out, "V9 journal")


def test_an_unmapped_table_exits_2(db, cut, capsys, monkeypatch):
    monkeypatch.delitem(scopes.SCOPE_OF, "steward_pin")
    code, out = _run(cut, capsys)
    assert code == V.EXIT_CANNOT_VERIFY
    assert "steward_pin" in _ticker_line(out, "V1 surprises")
    assert C.mapped_tables()  # the map is restored by monkeypatch


def test_the_health_url_is_optional_and_must_answer_200(db, cut, capsys):
    code, out = _run(cut, capsys)
    assert code == 0 and "note: V10" in out
    code, out = _run(cut, capsys, health_url="http://h/health", health_get=lambda url: 200)
    assert code == 0 and "ok: V10" in out
    code, out = _run(cut, capsys, health_url="http://h/health", health_get=lambda url: 503)
    assert code == V.EXIT_FAILED and "503" in _ticker_line(out, "V10 health")

    def down(url):
        raise OSError("connection refused")

    code, out = _run(cut, capsys, health_url="http://h/health", health_get=down)
    assert code == V.EXIT_FAILED and _ticker_line(out, "V10 health")


# --- refusals: nothing checked, exit 2 ------------------------------------------------------------------


def _refused(cut, capsys, **kw) -> str:
    code, out = _run(cut, capsys, **kw)
    assert code == V.EXIT_CANNOT_VERIFY, out
    assert "RESULT: CANNOT VERIFY" in out and "ok:" not in out
    return out


def test_refuses_a_missing_provenance_file(db, cut, capsys):
    assert "no provenance file" in _refused(cut, capsys, provenance=None)
    assert "does not exist" in _refused(cut, capsys, provenance=cut["tmp"] / "nope.json")


def test_refuses_a_pending_provenance_file(db, cut, capsys):
    pending = cut["prov"].with_name(cut["prov"].name + ".pending")
    cut["prov"].rename(pending)
    assert ".pending" in _refused(cut, capsys, provenance=pending)
    assert ".pending" in _refused(cut, capsys, provenance=cut["prov"])


def test_refuses_a_provenance_file_of_another_database(db, cut, capsys):
    doc = json.loads(cut["prov"].read_text(encoding="utf-8"))
    doc["database"] = "alphadeck_otherfork"
    cut["prov"].write_text(json.dumps(doc), encoding="utf-8")
    assert "alphadeck_otherfork" in _refused(cut, capsys)


def test_refuses_an_unreadable_provenance_file(db, cut, capsys):
    cut["prov"].write_text("{not json", encoding="utf-8")
    assert "cannot be read" in _refused(cut, capsys)


def test_refuses_a_database_that_is_not_the_named_one(db, cut, capsys):
    assert "is not the database" in _refused(cut, capsys, database="alphadeck_test_other")


def test_refuses_production_without_prod(db, cut, capsys, monkeypatch):
    real = V.database_name
    monkeypatch.setattr(V, "database_name", lambda conn: "alphadeck")
    assert "--prod" in _refused(cut, capsys, database="alphadeck")
    monkeypatch.setattr(V, "database_name", real)
    assert "--prod" in _refused(cut, capsys, database=cut["name"], prod=True)


# --- the backfill stage ---------------------------------------------------------------------------------


def test_backfill_names_a_company_with_no_backfill_attempt(db, cut, capsys):
    _exec(
        db,
        "INSERT INTO platform_extraction_attempt (tenant_id, security_id, run_kind, inputs_key, status) "
        "VALUES (%s, %s, 'backfill', 'k', 'done')",
        (DATA_TENANT_ID, cut["a"]),
    )
    code, out = _run(cut, capsys, "backfill")
    assert code == V.EXIT_FAILED and "RSTB" in _ticker_line(out, "B1 attempts")


def test_backfill_with_no_done_attempt_leaves_b4_locked(db, cut, capsys):
    _backfill(db, cut, status="error")
    code, out = _run(cut, capsys, "backfill")
    assert code == V.EXIT_FAILED and _ticker_line(out, "B2 B4")
    assert "note: B" in out and "not cached" in out


def test_backfill_coverage_below_the_baseline_fails(db, cut, capsys):
    _backfill(db, cut)
    with db.cursor() as cur:
        c = _security(cur, "RSTC")
        _member(cur, cut["t2"], 3, "RSTC", c, "wires")
        cur.execute(
            "INSERT INTO platform_extraction_attempt (tenant_id, security_id, run_kind, inputs_key, status) "
            "VALUES (%s, %s, 'backfill', 'k', 'done')",
            (DATA_TENANT_ID, c),
        )
    db.commit()
    code, out = _run(cut, capsys, "backfill")
    assert code == V.EXIT_FAILED and "share count" in _ticker_line(out, "B3 coverage")


def test_an_etf_on_a_card_needs_no_backfill_attempt(db, cut, capsys):
    _backfill(db, cut)
    with db.cursor() as cur:
        e = _security(cur, "RETF", kind="etf")
        _member(cur, cut["t2"], 3, "RETF", e, "wires")
    db.commit()
    code, out = _run(cut, capsys, "backfill")
    assert not [f for f in _fails(out) if f.startswith(("FAIL: B1", "FAIL: B3"))], out


# --- the nightly stage ----------------------------------------------------------------------------------


@pytest.fixture
def backfilled(db, cut):
    _backfill(db, cut)
    return cut


def test_no_nightly_call_yet_is_an_honest_fail(db, backfilled, capsys):
    code, out = _run(backfilled, capsys, "nightly")
    assert code == V.EXIT_FAILED and "not yet" in _ticker_line(out, "N1 night")


def test_a_live_thesis_missing_from_the_night_is_named(db, backfilled, capsys):
    _night(db, [backfilled["t1"]])
    code, out = _run(backfilled, capsys, "nightly")
    assert code == V.EXIT_FAILED and str(backfilled["t2"]) in _ticker_line(out, "N2 one per thesis")


def test_a_reconstructed_nightly_row_fails(db, backfilled, capsys):
    _night(db, [backfilled["t1"]])
    _night(db, [backfilled["t2"]], reconstructed=True)
    _, out = _run(backfilled, capsys, "nightly")
    assert _ticker_line(out, "N3 reconstructed")


def test_the_run_kind_must_be_cron_unless_any(db, backfilled, capsys):
    _night(db, [backfilled["t1"], backfilled["t2"]], run_kind="manual")
    code, out = _run(backfilled, capsys, "nightly")
    assert code == V.EXIT_FAILED and "manual" in _ticker_line(out, "N4 run kind")
    code, out = _run(backfilled, capsys, "nightly", run_kind="any")
    assert code == 0, out


def test_a_call_dated_before_the_cutover_fails(db, backfilled, capsys):
    _night(db, [backfilled["t1"], backfilled["t2"]])
    _night(db, [backfilled["t1"]], asof=market_time.market_today() - timedelta(days=3))
    _, out = _run(backfilled, capsys, "nightly")
    assert _ticker_line(out, "N5 after the cutover")


def test_a_call_under_the_data_tenant_fails(db, backfilled, capsys):
    _night(db, [backfilled["t1"], backfilled["t2"]])
    _night(db, [backfilled["t1"]], tenant=DATA_TENANT_ID)
    _, out = _run(backfilled, capsys, "nightly")
    assert _ticker_line(out, "N6 tenant")


def test_an_explicit_night_with_no_rows_fails(db, backfilled, capsys):
    _night(db, [backfilled["t1"], backfilled["t2"]])
    other = (market_time.market_today() + timedelta(days=1)).isoformat()
    code, out = _run(backfilled, capsys, "nightly", night=other)
    assert code == V.EXIT_FAILED and other in _ticker_line(out, "N1 night")


def test_stale_ingest_is_a_note_not_a_fail(db, backfilled, capsys):
    _night(db, [backfilled["t1"]], ingest_fresh=False)
    _night(db, [backfilled["t2"]])
    code, out = _run(backfilled, capsys, "nightly")
    assert code == 0 and "note: N" in out and "1 of 2" in out


# --- the edition stage ----------------------------------------------------------------------------------


@pytest.fixture
def nightly(db, backfilled):
    _night(db, [backfilled["t1"], backfilled["t2"]])
    return backfilled


def test_no_edition_yet_is_an_honest_fail(db, nightly, capsys):
    code, out = _run(nightly, capsys, "edition")
    assert code == V.EXIT_FAILED and "not yet" in _ticker_line(out, "E1 edition")


def test_two_editions_fail(db, nightly, capsys):
    _edition_1(db, nightly)
    with db.cursor() as cur:
        _edition(cur, FOUNDING_WORKSPACE_ID, 2, [])
    db.commit()
    _, out = _run(nightly, capsys, "edition")
    assert "2 editions" in _ticker_line(out, "E1 edition")


def test_a_live_thesis_with_no_entry_is_named(db, nightly, capsys):
    _edition_1(db, nightly, entries=[(nightly["t1"], nightly["a"])])
    _, out = _run(nightly, capsys, "edition")
    assert str(nightly["t2"]) in _ticker_line(out, "E2 entries")


def test_an_edition_for_a_workspace_with_no_live_card_fails(db, nightly, capsys):
    _edition_1(db, nightly)
    _exec(db, "UPDATE thesis SET archived_at = now()")
    _, out = _run(nightly, capsys, "edition")
    assert _ticker_line(out, "E3 no card")


def test_the_cli_wires_verify(db, cut, capsys):
    capsys.readouterr()
    assert C.main(["verify", "--stage", "reset", "--database", cut["name"]]) == V.EXIT_CANNOT_VERIFY
    assert "no provenance file" in capsys.readouterr().out
    argv = [
        "verify",
        "--stage",
        "nightly",
        "--database",
        cut["name"],
        "--provenance",
        str(cut["prov"]),
    ]
    assert C.main([*argv, "--run-kind", "any"]) == V.EXIT_FAILED  # no night recorded yet
    assert "not yet" in capsys.readouterr().out
