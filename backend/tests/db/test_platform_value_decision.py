"""V2 W2a: ``platform_value_decision`` (migration 0205), the steward's append-only decisions on platform values.

One row per approve, correct, dismiss or undo of a share count or cash/burn value, in the data tenant. These tests
pin the shapes the table refuses (its checks, its trigger, its unique indexes, its ``no_update``) and that the
migration is idempotent. Nothing writes the table yet (W2b / W2c do).

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

_STEWARD = "steward@example.test"
_REF = "https://www.sec.gov/Archives/edgar/data/99/000009926000001/q.htm"
_W2A = ("0205_v2_platform_value_decision.sql", "0206_v2_ratified_by_vocabulary.sql")


def _security(db: psycopg.Connection, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1)),
        )
    return sid


def _candidate(db: psycopg.Connection, sid: uuid.UUID, fact_type: str = "shares_outstanding"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO extraction_candidate (tenant_id, security_id, fact_type, outcome, source_ref, "
            "event_date) VALUES (%s, %s, %s, 'flag', %s, %s) RETURNING id",
            (DATA_TENANT_ID, sid, fact_type, _REF, date(2026, 3, 31)),
        )
        return cur.fetchone()["id"]


def _insert(db: psycopg.Connection, sid: uuid.UUID, action: str, **cols) -> uuid.UUID:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "fact_type": "shares_outstanding",
        "action": action,
        "decided_by": _STEWARD,
        **cols,
    }
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO platform_value_decision ({', '.join(row)}) "  # noqa: S608
            f"VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _refused(db: psycopg.Connection, exc: type[BaseException], fn, *args, **kwargs) -> None:
    """``fn`` raises ``exc`` inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(exc):
        with db.transaction():
            fn(*args, **kwargs)


def _count(db: psycopg.Connection) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM platform_value_decision")
        return cur.fetchone()["n"]


def test_every_good_shape_inserts(db):
    sid = _security(db, "GOOD")
    cand = _candidate(db, sid)
    approve = _insert(db, sid, "approve", candidate_id=cand, fact_id=uuid.uuid4())
    _insert(db, sid, "correct", fact_id=uuid.uuid4())  # a correct may name no candidate
    _insert(db, sid, "correct", fact_id=uuid.uuid4(), candidate_id=cand)  # or close one
    _insert(db, sid, "dismiss", candidate_id=cand, reason="wrong class")
    _insert(db, sid, "undo", revokes=approve, reason="misread")
    assert _count(db) == 5


def test_each_check_refuses_its_bad_shape(db):
    sid = _security(db, "CHK")
    cand = _candidate(db, sid)
    approve = _insert(db, sid, "approve", candidate_id=cand, fact_id=uuid.uuid4())
    bad = errors.CheckViolation
    _refused(db, bad, _insert, db, sid, "undo")  # an undo names the decision it undoes
    _refused(
        db, bad, _insert, db, sid, "dismiss", candidate_id=cand, revokes=approve
    )  # only an undo revokes
    _refused(db, bad, _insert, db, sid, "approve", candidate_id=cand)  # an approve wrote a fact
    _refused(db, bad, _insert, db, sid, "correct")  # so did a correct
    _refused(
        db, bad, _insert, db, sid, "dismiss", candidate_id=cand, fact_id=uuid.uuid4()
    )  # a dismiss did not
    _refused(
        db, bad, _insert, db, sid, "approve", fact_id=uuid.uuid4()
    )  # an approve names its candidate
    _refused(db, bad, _insert, db, sid, "dismiss")  # so does a dismiss
    _refused(
        db, bad, _insert, db, sid, "undo", revokes=approve, candidate_id=cand
    )  # an undo is bare
    _refused(db, bad, _insert, db, sid, "withdraw", fact_id=uuid.uuid4())  # no such action (yet)
    _refused(db, bad, _insert, db, sid, "correct", fact_id=uuid.uuid4(), fact_type="catalyst")  # D5
    _refused(
        db, bad, _insert, db, sid, "correct", fact_id=uuid.uuid4(), decided_by="  "
    )  # who decided
    assert _count(db) == 1


def test_the_trigger_refuses_an_undo_of_an_undo_and_a_foreign_target(db):
    sid = _security(db, "TRG")
    other = _security(db, "OTH")
    cand = _candidate(db, sid)
    approve = _insert(db, sid, "approve", candidate_id=cand, fact_id=uuid.uuid4())
    undo = _insert(db, sid, "undo", revokes=approve)
    raised = errors.RaiseException
    _refused(db, raised, _insert, db, sid, "undo", revokes=undo)  # an undo of an undo
    other_correct = _insert(db, other, "correct", fact_id=uuid.uuid4())
    _refused(
        db, raised, _insert, db, sid, "undo", revokes=other_correct
    )  # another company's decision
    cash = _insert(db, sid, "correct", fact_id=uuid.uuid4(), fact_type="cash_burn")
    _refused(db, raised, _insert, db, sid, "undo", revokes=cash)  # another value type's decision
    _refused(
        db, errors.ForeignKeyViolation, _insert, db, sid, "undo", revokes=uuid.uuid4()
    )  # no such one
    # a candidate of another type, or of another company, never closes by this decision
    cash_cand = _candidate(db, sid, "cash_burn")
    _refused(db, raised, _insert, db, sid, "dismiss", candidate_id=cash_cand)
    other_cand = _candidate(db, other)
    _refused(db, raised, _insert, db, sid, "dismiss", candidate_id=other_cand)
    assert _count(db) == 4


def test_one_undo_per_decision_and_one_decision_per_fact(db):
    sid = _security(db, "UNQ")
    fact = uuid.uuid4()
    correct = _insert(db, sid, "correct", fact_id=fact)
    _insert(db, sid, "undo", revokes=correct)
    _refused(db, errors.UniqueViolation, _insert, db, sid, "undo", revokes=correct)
    _refused(db, errors.UniqueViolation, _insert, db, sid, "correct", fact_id=fact)


def test_a_decision_is_never_updated(db):
    sid = _security(db, "UPD")
    did = _insert(db, sid, "correct", fact_id=uuid.uuid4())

    def update() -> None:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE platform_value_decision SET reason = 'edited' WHERE id = %s", (did,)
            )

    _refused(db, errors.RaiseException, update)


def test_the_w2a_migrations_are_idempotent(db):
    """Re-running both files (twice) changes nothing: the table, its indexes, triggers and the two vocabulary
    constraints are created once, and a stored decision survives."""
    sid = _security(db, "IDM")
    _insert(db, sid, "correct", fact_id=uuid.uuid4())

    def shape() -> tuple:
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM platform_value_decision) AS rows, "
                "(SELECT count(*) FROM pg_indexes WHERE tablename = 'platform_value_decision') AS ix, "
                "(SELECT count(*) FROM pg_trigger WHERE tgrelid = 'platform_value_decision'::regclass "
                "AND NOT tgisinternal) AS trg, "
                "(SELECT count(*) FROM pg_constraint WHERE conname LIKE '%%ratified_by_vocabulary') AS voc"
            )
            return tuple(cur.fetchone().values())

    before = shape()
    assert before == (1, 5, 2, 2)
    for _ in range(2):
        for name in _W2A:
            with db.cursor() as cur:
                cur.execute((MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    assert shape() == before
