"""V2 W1b — the new tables and columns (migrations 0200-0204), schema only.

Nothing reads or writes these yet (W2-W7 do); these tests pin the SHAPE every later workstream relies on:

- ``extraction_candidate`` (0200, DATA scope): an extractor result the steward has not applied. It is NOT a fact:
  no as-of read (live or public clock), no point-in-time accessor and no mirror export can return it, and no
  display or call module names it;
- ``steward_pin`` (0201, DATA scope): append-only pin / unpin events, latest per company and value type wins;
- ``value_flag`` (0202, user-keyed, no tenant): one open flag per user and value, closed once, gone with its user;
- the three nullable cap columns on ``tenant`` (0203): NULL = no cap, so no existing workspace is blocked;
- the four thesis catalyst columns (0204): empty on every existing row, and (until W6 changes the writer) wiped by
  ``thesis_repo.set_catalysts`` on every save.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every write is rolled back.
"""

from __future__ import annotations

import inspect
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from db.bitemporal import _FACT_IDENTITY, append_fact, as_of, as_of_many, fact_exists
from db.migrate import MIGRATIONS_DIR
from db.public_clock import PUBLIC_TIME
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID
from domain.thesis import Catalyst, Thesis
from repositories import thesis_repo
from tests.workspaces import new_workspace

_BACKEND = Path(__file__).resolve().parents[2]
_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_NEW_MIGRATIONS = (
    "0200_v2_extraction_candidate.sql",
    "0201_v2_steward_pin.sql",
    "0202_v2_value_flag.sql",
    "0203_v2_workspace_caps.sql",
    "0204_v2_thesis_catalyst_columns.sql",
)


# --- helpers -------------------------------------------------------------------------------------------------


def _columns(db: psycopg.Connection, table: str) -> dict[str, tuple[str, str, str | None]]:
    """``{column: (data_type, is_nullable, column_default)}`` for ``table`` (empty when it does not exist)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {
            r["column_name"]: (r["data_type"], r["is_nullable"], r["column_default"])
            for r in cur.fetchall()
        }


def _require_table(db: psycopg.Connection, table: str) -> None:
    assert _columns(db, table), f"table {table} does not exist (migration not applied)"


def _security(db: psycopg.Connection, ticker: str = "CAND") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1)),
        )
    return sid


def _user(db: psycopg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    workspace = new_workspace(db, "a flagger's workspace")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'u', 'x', 'user', %s)",
            (uid, f"{uid.hex}@example.test", workspace),
        )
    return uid


def _refused(db: psycopg.Connection, exc: type[BaseException], query: str, params=()) -> None:
    """``query`` raises ``exc`` inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(exc):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(query, params)


def _candidate(db: psycopg.Connection, sid: uuid.UUID, **over) -> uuid.UUID:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "fact_type": "shares_outstanding",
        "outcome": "flag",
        "flags": ["dual-class"],
        "source": "cover",
        "source_ref": "https://www.sec.gov/Archives/edgar/data/99/000009926000001/x10k.htm",
        "event_date": date(2026, 3, 31),
        "shares": 12_345_678,
    }
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO extraction_candidate ({cols}) VALUES ({marks}) RETURNING id",
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _flag(db: psycopg.Connection, uid: uuid.UUID, sid: uuid.UUID, **over) -> uuid.UUID:
    row = {"user_id": uid, "security_id": sid, "value_type": "shares_outstanding"}
    row.update(over)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO value_flag ({cols}) VALUES ({marks}) RETURNING id", tuple(row.values())
        )
        return cur.fetchone()["id"]


def _thesis_with_catalysts(db: psycopg.Connection) -> tuple[uuid.UUID, uuid.UUID, list[Catalyst]]:
    workspace = new_workspace(db, "a catalyst workspace")
    t = Thesis(id=uuid.uuid4(), name="Catalysts", narrative="n", tenant_id=workspace)
    thesis_repo.upsert(db, t)
    cats = [
        Catalyst(id=uuid.uuid4(), label="FDA vote", kind="regulatory", when_date=date(2026, 12, 1)),
        Catalyst(id=uuid.uuid4(), label="Q4 print", when_label="Q4"),
    ]
    thesis_repo.set_catalysts(db, t.id, cats, tenant_id=workspace)
    return t.id, workspace, cats


def _catalyst_rows(db: psycopg.Connection, thesis_id: uuid.UUID) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT label, security_ids, grade, citation, entered_at FROM catalyst "
            "WHERE thesis_id = %s ORDER BY ordinal",
            (thesis_id,),
        )
        return cur.fetchall()


# --- extraction_candidate: not a fact ------------------------------------------------------------------------


def test_a_candidate_is_not_a_fact(db, tmp_path):
    """A candidate is structurally unable to reach an as-of read: the as-of whitelist (``_FACT_IDENTITY``)
    omits it, so every as-of / existence / append path raises; it has no public clock; the point-in-time views
    (live and replay) carry no accessor for it; and a mirror export, of either clock, writes no file for it
    even with a candidate row present."""
    _require_table(db, "extraction_candidate")
    sid = _security(db)
    _candidate(db, sid)
    _candidate(db, sid, outcome="nothing", reason="cover-not-located", source_ref=None)

    table = "extraction_candidate"
    assert table not in _FACT_IDENTITY
    assert table not in PUBLIC_TIME
    for clock in ("record", "public"):
        with pytest.raises(ValueError, match="unknown fact table"):
            as_of(
                db,
                table,
                security_id=sid,
                asof=date(2026, 6, 1),
                known_at=_KNOWN,
                tenant_id=DATA_TENANT_ID,
                clock=clock,
            )
        with pytest.raises(ValueError, match="unknown fact table"):
            as_of_many(
                db,
                table,
                security_ids=[sid],
                asof=date(2026, 6, 1),
                known_at=_KNOWN,
                tenant_id=DATA_TENANT_ID,
                clock=clock,
            )
    with pytest.raises(ValueError):
        fact_exists(db, table, security_id=sid, tenant_id=DATA_TENANT_ID)
    with pytest.raises(ValueError):
        append_fact(db, table, {"tenant_id": DATA_TENANT_ID, "security_id": sid})

    from signals.base import PointInTimeData

    pytest.importorskip("duckdb")
    from replay.export import FACT_TABLES, export_snapshot
    from replay.pit import ReplayPointInTimeData

    assert table not in FACT_TABLES
    for view in (PointInTimeData, ReplayPointInTimeData):
        names = [n for n, _ in inspect.getmembers(view)]
        assert not [n for n in names if "candidate" in n.lower()], view.__name__
        assert "extraction_candidate" not in inspect.getsource(view)

    for clock in ("record", "public"):
        out = tmp_path / clock
        manifest = export_snapshot(
            db, out, data_tenant=DATA_TENANT_ID, workspace=FOUNDING_WORKSPACE_ID, clock=clock
        )
        assert not [p.name for p in out.rglob("*") if "candidate" in p.name]
        assert "candidate" not in repr(manifest)


def test_no_display_or_call_module_names_the_candidate_table():
    """``extraction_candidate`` is read by one function (W2's state derivation); no display, call, signal,
    dashboard or mirror module may name it — so a candidate cannot leak into a meter, a card or a replay.
    """
    creates = [
        p.name
        for p in MIGRATIONS_DIR.glob("*.sql")
        if re.search(
            r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+extraction_candidate\b", p.read_text("utf-8")
        )
    ]
    assert creates == ["0200_v2_extraction_candidate.sql"]  # the grep below is not vacuous
    hits = []
    for rel in (
        "workbench",
        "dashboards",
        "signals",
        "calls",
        "app/routers/dashboards",
        "replay",
        "backtest",
        "editions",
    ):
        root = _BACKEND / rel
        assert root.is_dir(), rel
        for path in root.rglob("*.py"):
            if "extraction_candidate" in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(_BACKEND)))
    assert hits == []


def test_candidate_checks(db):
    _require_table(db, "extraction_candidate")
    sid = _security(db)
    cid = _candidate(db, sid)
    # a 'nothing' carries its reason; a 'flag' carries its filing (URL + cover/period date)
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome) "
        "VALUES (%s, %s, 'cash_burn', 'nothing')",
        (DATA_TENANT_ID, sid),
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, event_date) "
        "VALUES (%s, %s, 'cash_burn', 'flag', '2026-03-31')",
        (DATA_TENANT_ID, sid),
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, source_ref) "
        "VALUES (%s, %s, 'cash_burn', 'flag', 'https://x')",
        (DATA_TENANT_ID, sid),
    )
    # only the two platform values, only the two outcomes
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, reason) "
        "VALUES (%s, %s, 'revenue_mix', 'nothing', 'r')",
        (DATA_TENANT_ID, sid),
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, reason) "
        "VALUES (%s, %s, 'cash_burn', 'auto', 'r')",
        (DATA_TENANT_ID, sid),
    )
    # a 'nothing' with its reason inserts, with the empty defaults
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, reason) "
            "VALUES (%s, %s, 'cash_burn', 'nothing', 'no-annual-filing') RETURNING id",
            (DATA_TENANT_ID, sid),
        )
        nid = cur.fetchone()["id"]
        cur.execute(
            "SELECT flags, located_passages, recorded_at FROM extraction_candidate WHERE id = %s",
            (nid,),
        )
        row = cur.fetchone()
    assert row["flags"] == [] and row["located_passages"] == [] and row["recorded_at"] is not None
    # append-only: a candidate is never edited (a newer one is written)
    _refused(
        db,
        errors.RaiseException,
        "UPDATE extraction_candidate SET note = 'edited' WHERE id = %s",
        (cid,),
    )


# --- steward_pin -----------------------------------------------------------------------------------------------


def test_pin_rows_are_append_only(db):
    _require_table(db, "steward_pin")
    sid = _security(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO steward_pin (tenant_id, security_id, value_type, action, decided_by) "
            "VALUES (%s, %s, 'shares_outstanding', 'pin', 'steward@example.test') RETURNING id",
            (DATA_TENANT_ID, sid),
        )
        pid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO steward_pin (tenant_id, security_id, value_type, action, decided_by) "
            "VALUES (%s, %s, 'shares_outstanding', 'unpin', 'steward@example.test')",
            (DATA_TENANT_ID, sid),
        )
    _refused(
        db, errors.RaiseException, "UPDATE steward_pin SET action = 'unpin' WHERE id = %s", (pid,)
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO steward_pin (tenant_id, security_id, value_type, action, decided_by) "
        "VALUES (%s, %s, 'shares_outstanding', 'lock', 's')",
        (DATA_TENANT_ID, sid),
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO steward_pin (tenant_id, security_id, value_type, action, decided_by) "
        "VALUES (%s, %s, 'catalyst', 'pin', 's')",
        (DATA_TENANT_ID, sid),
    )
    _refused(
        db,
        errors.NotNullViolation,
        "INSERT INTO steward_pin (tenant_id, security_id, value_type, action) "
        "VALUES (%s, %s, 'cash_burn', 'pin')",
        (DATA_TENANT_ID, sid),
    )


# --- value_flag ------------------------------------------------------------------------------------------------


def test_one_open_flag_per_user_and_value(db):
    _require_table(db, "value_flag")
    sid = _security(db)
    me, other = _user(db), _user(db)
    first = _flag(db, me, sid)
    _refused(
        db,
        errors.UniqueViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type) "
        "VALUES (%s, %s, 'shares_outstanding')",
        (me, sid),
    )
    _flag(db, me, sid, value_type="cash_burn")  # another value of the same company
    _flag(db, other, sid)  # another user may open the same key
    _flag(db, me, sid, value_type="catalyst", value_ref="https://sec/a")
    _flag(db, me, sid, value_type="catalyst", value_ref="https://sec/b")  # another catalyst
    with db.cursor() as cur:
        cur.execute(
            "UPDATE value_flag SET closed_at = now(), closed_by = 's@example.test', outcome = 'kept' "
            "WHERE id = %s",
            (first,),
        )
    _flag(db, me, sid)  # after closing the first, a new one opens


def test_a_closed_flag_cannot_be_reopened_or_edited(db):
    _require_table(db, "value_flag")
    sid = _security(db)
    fid = _flag(db, _user(db), sid, note="looks stale")
    # closing needs both the time and the outcome
    _refused(
        db, errors.CheckViolation, "UPDATE value_flag SET closed_at = now() WHERE id = %s", (fid,)
    )
    _refused(
        db, errors.CheckViolation, "UPDATE value_flag SET outcome = 'fixed' WHERE id = %s", (fid,)
    )
    _refused(
        db,
        errors.CheckViolation,
        "UPDATE value_flag SET closed_at = now(), outcome = 'ignored' WHERE id = %s",
        (fid,),
    )
    with db.cursor() as cur:
        cur.execute(
            "UPDATE value_flag SET closed_at = now(), closed_by = 's@example.test', outcome = 'fixed', "
            "outcome_note = 'corrected' WHERE id = %s",
            (fid,),
        )
    for change in (
        "closed_at = NULL, outcome = NULL",  # reopen
        "outcome = 'kept'",
        "outcome_note = 'edited'",
        "note = 'edited'",
    ):
        _refused(db, errors.RaiseException, f"UPDATE value_flag SET {change} WHERE id = %s", (fid,))


def test_flags_go_with_their_user(db):
    _require_table(db, "value_flag")
    sid = _security(db)
    uid, keep = _user(db), _user(db)
    _flag(db, uid, sid)
    _flag(db, uid, sid, value_type="cash_burn")
    _flag(db, keep, sid)
    with db.cursor() as cur:
        cur.execute("DELETE FROM app_user WHERE id = %s", (uid,))
        cur.execute("SELECT user_id FROM value_flag")
        assert [r["user_id"] for r in cur.fetchall()] == [keep]


def test_value_ref_only_for_catalysts(db):
    _require_table(db, "value_flag")
    sid = _security(db)
    uid = _user(db)
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type, value_ref) "
        "VALUES (%s, %s, 'shares_outstanding', 'https://x')",
        (uid, sid),
    )
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type) VALUES (%s, %s, 'purity')",
        (uid, sid),
    )
    fid = _flag(db, uid, sid, value_type="catalyst", value_ref="https://x")
    with db.cursor() as cur:
        cur.execute("SELECT value_ref FROM value_flag WHERE id = %s", (fid,))
        assert cur.fetchone()["value_ref"] == "https://x"
    assert _columns(db, "value_flag")["value_ref"][1] == "NO"
    assert "tenant_id" not in _columns(db, "value_flag")  # user-keyed, like user_action_count


# --- the workspace caps ----------------------------------------------------------------------------------------


def test_caps_default_to_no_cap(db):
    from pipeline.users import create_user

    cols = _columns(db, "tenant")
    caps = ("cap_live_cards", "cap_companies", "cap_terms_per_card")
    for c in caps:
        assert cols.get(c, (None,))[:2] == ("integer", "YES"), c
        assert cols[c][2] is None, c  # no default: NULL = no cap
    uid, _ = create_user(db, email=f"{uuid.uuid4().hex}@example.test", display_name="Capless")
    with db.cursor() as cur:
        cur.execute("SELECT workspace_id FROM app_user WHERE id = %s", (uid,))
        new_ws = cur.fetchone()["workspace_id"]
        cur.execute(
            "SELECT id, cap_live_cards, cap_companies, cap_terms_per_card FROM tenant ORDER BY id"
        )
        rows = cur.fetchall()
    assert {DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, new_ws} <= {r["id"] for r in rows}
    for r in rows:
        assert (r["cap_live_cards"], r["cap_companies"], r["cap_terms_per_card"]) == (
            None,
            None,
            None,
        )
    for c in caps:
        _refused(db, errors.CheckViolation, f"UPDATE tenant SET {c} = -1 WHERE id = %s", (new_ws,))
        with db.cursor() as cur:
            cur.execute(f"UPDATE tenant SET {c} = 0 WHERE id = %s", (new_ws,))  # zero is a cap


# --- the thesis catalyst columns -------------------------------------------------------------------------------


def test_existing_catalysts_get_empty_new_fields(db):
    cols = _columns(db, "catalyst")
    assert cols.get("security_ids", (None,))[:2] == ("ARRAY", "NO")
    assert cols["grade"][:2] == ("text", "YES")
    assert cols["citation"][:2] == ("text", "YES")
    assert cols["entered_at"][:2] == ("timestamp with time zone", "YES")
    assert cols["entered_at"][2] is None  # never stamped by default: NULL on every existing row
    tid, _, _ = _thesis_with_catalysts(db)
    rows = _catalyst_rows(db, tid)
    assert [(r["security_ids"], r["grade"], r["citation"], r["entered_at"]) for r in rows] == [
        ([], None, None, None),
        ([], None, None, None),
    ]
    _refused(
        db,
        errors.CheckViolation,
        "UPDATE catalyst SET grade = 'strong' WHERE thesis_id = %s",
        (tid,),
    )
    with db.cursor() as cur:  # both grades the call uses are accepted
        cur.execute(
            "UPDATE catalyst SET grade = 'core' WHERE thesis_id = %s AND ordinal = 0", (tid,)
        )
        cur.execute(
            "UPDATE catalyst SET grade = 'flip' WHERE thesis_id = %s AND ordinal = 1", (tid,)
        )


def test_set_catalysts_drops_the_new_fields_on_save(db):
    """Documents finding 3 for W6: the editor's full-list replace (delete all, re-insert) writes none of the new
    fields, so a save wipes them. W6 changes the writer to keep each entry's id and entry time and must flip
    this test."""
    tid, workspace, cats = _thesis_with_catalysts(db)
    member = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "UPDATE catalyst SET security_ids = %s, grade = 'core', citation = 'https://sec/8k', "
            "entered_at = now() WHERE thesis_id = %s",
            ([member], tid),
        )
    assert {r["grade"] for r in _catalyst_rows(db, tid)} == {"core"}
    thesis_repo.set_catalysts(db, tid, cats, tenant_id=workspace)
    rows = _catalyst_rows(db, tid)
    assert [r["label"] for r in rows] == ["FDA vote", "Q4 print"]
    assert [(r["security_ids"], r["grade"], r["citation"], r["entered_at"]) for r in rows] == [
        ([], None, None, None),
        ([], None, None, None),
    ]


# --- the migrations ----------------------------------------------------------------------------------------------


def test_migrations_are_idempotent(db):
    """The runner applies each file once; re-executing the five files' SQL over the migrated schema (twice) must
    neither fail nor change a row."""

    def counts() -> tuple[int, ...]:
        with db.cursor() as cur:
            out = []
            for t in ("extraction_candidate", "steward_pin", "value_flag", "tenant", "catalyst"):
                cur.execute(f"SELECT count(*) AS n FROM {t}")
                out.append(cur.fetchone()["n"])
            return tuple(out)

    paths = [MIGRATIONS_DIR / name for name in _NEW_MIGRATIONS]
    assert [p.exists() for p in paths] == [True] * len(paths)
    sid = _security(db)
    _candidate(db, sid)
    _flag(db, _user(db), sid)
    _thesis_with_catalysts(db)
    before = counts()
    try:
        with db.cursor() as cur:
            for _ in range(2):
                for p in paths:
                    cur.execute(p.read_text(encoding="utf-8"))
        assert counts() == before
    finally:
        db.rollback()
