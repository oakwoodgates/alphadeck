"""V2 W5b: migration 0210 links a user's flag (``value_flag``) to what it saw, to the decision that fixed it and to
the closed flag it reopens (``docs/PLATFORM_VALUES.md`` §Flags).

- three nullable columns: ``seen_candidate_id`` (the candidate awaiting the steward when the flag was raised),
  ``fixed_decision_id`` (the steward's decision behind a ``fixed`` close) and ``reopens`` (the closed flag a new
  open row reopens);
- NULL-SAFE: a ``fixed_decision_id`` is refused on an open row (``outcome`` NULL: a plain ``outcome = 'fixed'``
  CHECK would be unknown there and let it through) and on a ``kept`` or ``withdrawn`` row;
- a flag sees a value OR a candidate, never both;
- at most one row reopens a given flag, and a reopen row copies the reopened flag's user, company and value (a
  trigger) and never reopens an open or a user-withdrawn flag;
- 0202's closed-once trigger still refuses any update of a closed row;
- the file re-runs cleanly (twice) and changes nothing.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every write is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg import errors

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID
from tests.workspaces import new_workspace

_MIGRATION = MIGRATIONS_DIR / "0210_v2_value_flag_links.sql"


def _security(db: psycopg.Connection, ticker: str = "FLG") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0000000098", date(2026, 1, 1)),
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


def _close(db: psycopg.Connection, fid: uuid.UUID, outcome: str = "kept", **extra) -> None:
    sets = ", ".join(f"{k} = %s" for k in extra)
    with db.cursor() as cur:
        cur.execute(
            "UPDATE value_flag SET closed_at = now(), closed_by = 's@example.test', outcome = %s"
            + (f", {sets}" if sets else "")
            + " WHERE id = %s",
            (outcome, *extra.values(), fid),
        )


def _decision(db: psycopg.Connection, sid: uuid.UUID) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO platform_value_decision (tenant_id, security_id, fact_type, action, fact_id, decided_by) "
            "VALUES (%s, %s, 'shares_outstanding', 'correct', %s, 's@example.test') RETURNING id",
            (DATA_TENANT_ID, sid, uuid.uuid4()),
        )
        return cur.fetchone()["id"]


def _candidate(db: psycopg.Connection, sid: uuid.UUID) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, flags, source, "
            "source_ref, event_date, shares) VALUES (%s, %s, 'shares_outstanding', 'flag', %s, 'cover', "
            "'https://www.sec.gov/x.htm', %s, 1000) RETURNING id",
            (DATA_TENANT_ID, sid, ["dual-class"], date(2026, 3, 31)),
        )
        return cur.fetchone()["id"]


def _refused(db: psycopg.Connection, exc: type[BaseException], query: str, params=()) -> None:
    """``query`` raises ``exc`` inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(exc):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(query, params)


def _columns(db: psycopg.Connection) -> dict[str, tuple[str, str]]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'value_flag'"
        )
        return {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in cur.fetchall()}


def test_the_three_link_columns_exist_and_are_nullable(db):
    cols = _columns(db)
    for name in ("seen_candidate_id", "fixed_decision_id", "reopens"):
        assert cols.get(name) == ("uuid", "YES"), name


def test_a_decision_link_is_refused_on_an_open_row(db):
    """The null-safe check: ``outcome`` is NULL on an open row, so ``fixed_decision_id IS NULL OR outcome =
    'fixed'`` would be UNKNOWN there and pass. The constraint must reject it."""
    sid = _security(db)
    did = _decision(db, sid)
    uid = _user(db)
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type, fixed_decision_id) "
        "VALUES (%s, %s, 'shares_outstanding', %s)",
        (uid, sid, did),
    )
    fid = _flag(db, uid, sid)
    _refused(
        db,
        errors.CheckViolation,
        "UPDATE value_flag SET fixed_decision_id = %s WHERE id = %s",
        (did, fid),
    )


@pytest.mark.parametrize("outcome", ["kept", "withdrawn"])
def test_a_decision_link_is_refused_unless_the_outcome_is_fixed(db, outcome):
    sid = _security(db)
    did = _decision(db, sid)
    fid = _flag(db, _user(db), sid)
    _refused(
        db,
        errors.CheckViolation,
        "UPDATE value_flag SET closed_at = now(), closed_by = 'x', outcome = %s, fixed_decision_id = %s "
        "WHERE id = %s",
        (outcome, did, fid),
    )


def test_a_fixed_close_may_carry_its_decision(db):
    sid = _security(db)
    did = _decision(db, sid)
    fid = _flag(db, _user(db), sid)
    _close(db, fid, "fixed", fixed_decision_id=did)
    with db.cursor() as cur:
        cur.execute("SELECT outcome, fixed_decision_id FROM value_flag WHERE id = %s", (fid,))
        assert dict(cur.fetchone()) == {"outcome": "fixed", "fixed_decision_id": did}


def test_a_flag_sees_a_value_or_a_candidate_never_both(db):
    sid = _security(db)
    cid = _candidate(db, sid)
    uid = _user(db)
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type, seen_fact_id, seen_candidate_id) "
        "VALUES (%s, %s, 'shares_outstanding', %s, %s)",
        (uid, sid, uuid.uuid4(), cid),
    )
    _flag(db, uid, sid, seen_candidate_id=cid)  # the candidate alone is fine


def test_at_most_one_row_reopens_a_flag(db):
    sid = _security(db)
    uid = _user(db)
    first = _flag(db, uid, sid)
    _close(db, first)
    second = _flag(db, uid, sid, reopens=first)
    _close(
        db, second
    )  # the reopened flag is closed again; a second reopen of FIRST is still refused
    _refused(
        db,
        errors.UniqueViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type, reopens) "
        "VALUES (%s, %s, 'shares_outstanding', %s)",
        (uid, sid, first),
    )
    _flag(db, uid, sid, reopens=second)  # the newest closed row may be reopened


def test_a_reopen_row_copies_the_reopened_flags_identity(db):
    sid, other_sid = _security(db), _security(db, "OTH")
    uid, other = _user(db), _user(db)
    first = _flag(db, uid, sid)
    _close(db, first)
    for user, company, value_type in (
        (other, sid, "shares_outstanding"),  # another user
        (uid, other_sid, "shares_outstanding"),  # another company
        (uid, sid, "cash_burn"),  # another value
    ):
        _refused(
            db,
            errors.RaiseException,
            "INSERT INTO value_flag (user_id, security_id, value_type, reopens) VALUES (%s, %s, %s, %s)",
            (user, company, value_type, first),
        )


def test_an_open_or_withdrawn_flag_is_never_reopened(db):
    sid = _security(db)
    uid = _user(db)
    still_open = _flag(db, uid, sid)
    withdrawn = _flag(db, uid, sid, value_type="cash_burn")
    _close(db, withdrawn, "withdrawn")
    for target, value_type in ((still_open, "shares_outstanding"), (withdrawn, "cash_burn")):
        _refused(
            db,
            errors.RaiseException,
            "INSERT INTO value_flag (user_id, security_id, value_type, reopens) VALUES (%s, %s, %s, %s)",
            (uid, sid, value_type, target),
        )


def test_the_closed_once_trigger_still_refuses_an_update_of_a_closed_row(db):
    sid = _security(db)
    did = _decision(db, sid)
    fid = _flag(db, _user(db), sid)
    _close(db, fid, "fixed", fixed_decision_id=did)
    for change in ("fixed_decision_id = NULL", "reopens = NULL", "outcome_note = 'edited'"):
        _refused(db, errors.RaiseException, f"UPDATE value_flag SET {change} WHERE id = %s", (fid,))


def test_the_migration_re_runs_cleanly(db):
    sid = _security(db)
    _flag(db, _user(db), sid, note="kept across a re-run")

    def shape() -> tuple:
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM value_flag) AS rows, "
                "(SELECT count(*) FROM pg_indexes WHERE tablename = 'value_flag') AS ix, "
                "(SELECT count(*) FROM pg_trigger WHERE tgrelid = 'value_flag'::regclass "
                "AND NOT tgisinternal) AS trg, "
                "(SELECT count(*) FROM pg_constraint WHERE conrelid = 'value_flag'::regclass) AS con"
            )
            return tuple(cur.fetchone().values())

    before = shape()
    for _ in range(2):
        with db.cursor() as cur:
            cur.execute(_MIGRATION.read_text(encoding="utf-8"))
    assert shape() == before
