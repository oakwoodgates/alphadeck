"""Migration 0218 — ``split_filing_dismissal``, the steward's append-only, undoable dismissal of a split filing
the nightly could not place (framework completion F1-2; the shape is 0068's with ``accession`` for ``step_date``).

On the migrated test database: the table and its columns, every CHECK (the accession's shape, the two actions, the
operator author, a non-blank reason, the undo pairing), at most one undo per dismissal, the ``no_update`` trigger,
the cascade from the master, and the scope registry (DATA, ``tests/db/test_scopes.py`` parses the file). Then (the
0215 / 0217 precedent: the runner applies each file ONCE, so the test re-executes the FILE's SQL) two more runs
over stored rows change nothing. Every write is rolled back. DB-backed (the shared ``db`` fixture; skips when
Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg import errors

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID, SCOPE_OF, Scope, owner_of

_FILE = "0218_split_filing_dismissal.sql"
_ACC = "0001628280-26-066700"


def _sql() -> str:
    return (MIGRATIONS_DIR / _FILE).read_text(encoding="utf-8")


def _security(db: psycopg.Connection, ticker: str = "META") -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, date(2025, 1, 1)),
        )
    return sid


def _insert(db: psycopg.Connection, sid: uuid.UUID, **over) -> uuid.UUID:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "accession": _ACC,
        "action": "dismiss",
        "undoes": None,
        "reason": "not a split to list, said the steward",
        "author": "operator:dismiss",
        **over,
    }
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO split_filing_dismissal ({', '.join(row)}) "  # noqa: S608 — test constants
            f"VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _refused(db: psycopg.Connection, exc: type[BaseException], fn, *args, **kwargs) -> None:
    """``fn`` raises ``exc`` inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(exc):
        with db.transaction():
            fn(*args, **kwargs)


def test_0218_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'split_filing_dismissal' "
            "ORDER BY column_name"
        )
        cols = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in cur.fetchall()}
        assert cols == {
            "id": ("uuid", "NO"),
            "tenant_id": ("uuid", "NO"),
            "security_id": ("uuid", "NO"),
            "accession": ("text", "NO"),
            "action": ("text", "NO"),
            "undoes": ("uuid", "YES"),
            "reason": ("text", "NO"),
            "author": ("text", "NO"),
            "detail": ("jsonb", "NO"),
            "recorded_at": ("timestamp with time zone", "NO"),
        }
        cur.execute("SELECT count(*) AS n FROM schema_migrations WHERE filename = %s", (_FILE,))
        assert cur.fetchone()["n"] == 1


def test_0218_a_dismissal_and_its_undo_insert_with_defaults(db):
    try:
        sid = _security(db)
        did = _insert(db, sid)
        uid = _insert(db, sid, action="undo", undoes=did, author="operator:undo", reason="back")
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, action, undoes, detail, recorded_at FROM split_filing_dismissal "
                "WHERE security_id = %s ORDER BY action",
                (sid,),
            )
            rows = cur.fetchall()
        assert [(r["id"], r["action"], r["undoes"]) for r in rows] == [
            (did, "dismiss", None),
            (uid, "undo", did),
        ]
        assert all(r["detail"] == {} and r["recorded_at"] is not None for r in rows)
    finally:
        db.rollback()


@pytest.mark.parametrize(
    "over",
    [
        {"accession": "0001628280-26-06670"},  # one digit short
        {"accession": "000162828026066700"},  # no dashes
        {"accession": "0001628280-26-066700 "},  # trailing space
        {"accession": "8-K"},
        {"action": "reject"},
        {"action": "restore"},
        {"author": "system:nightly"},
        {"author": "steward"},
        {"reason": ""},
        {"reason": "   "},
    ],
    ids=[
        "accession short",
        "accession no dashes",
        "accession padded",
        "accession a form",
        "action reject",
        "action restore",
        "author system",
        "author bare",
        "reason empty",
        "reason blank",
    ],
)
def test_0218_checks_refuse_a_bad_row(db, over):
    try:
        sid = _security(db)
        _refused(db, errors.CheckViolation, _insert, db, sid, **over)
    finally:
        db.rollback()


def test_0218_an_undo_names_its_dismissal_and_a_dismissal_names_none(db):
    try:
        sid = _security(db)
        did = _insert(db, sid)
        # an undo naming nothing, and a dismiss naming something: the pairing CHECK refuses both
        _refused(db, errors.CheckViolation, _insert, db, sid, action="undo", undoes=None)
        _refused(db, errors.CheckViolation, _insert, db, sid, action="dismiss", undoes=did)
        # an undo naming a row that does not exist: the self-reference refuses it
        _refused(
            db, errors.ForeignKeyViolation, _insert, db, sid, action="undo", undoes=uuid.uuid4()
        )
    finally:
        db.rollback()


def test_0218_at_most_one_undo_per_dismissal(db):
    try:
        sid = _security(db)
        did = _insert(db, sid)
        _insert(db, sid, action="undo", undoes=did, author="operator:undo")
        _refused(
            db,
            errors.UniqueViolation,
            _insert,
            db,
            sid,
            action="undo",
            undoes=did,
            author="operator:undo",
        )
    finally:
        db.rollback()


def test_0218_a_row_is_never_updated(db):
    try:
        sid = _security(db)
        did = _insert(db, sid)

        def update() -> None:
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE split_filing_dismissal SET reason = 'rewritten' WHERE id = %s", (did,)
                )

        _refused(db, errors.RaiseException, update)
    finally:
        db.rollback()


def test_0218_the_master_delete_cascades(db):
    try:
        sid = _security(db)
        did = _insert(db, sid)
        _insert(db, sid, action="undo", undoes=did, author="operator:undo")
        with db.cursor() as cur:
            cur.execute("DELETE FROM security_master WHERE id = %s", (sid,))
            cur.execute(
                "SELECT count(*) AS n FROM split_filing_dismissal WHERE security_id = %s", (sid,)
            )
            assert cur.fetchone()["n"] == 0
    finally:
        db.rollback()


def test_0218_the_table_is_platform_data():
    """Registered in the scope map (``domain/scopes.py``): the steward's log of the platform's split list, under
    the data tenant, like ``split_jump_dismissal``."""
    assert SCOPE_OF["split_filing_dismissal"] is Scope.DATA
    assert owner_of("split_filing_dismissal") == "platform"


def test_0218_reruns_as_a_no_op_over_stored_rows(db):
    """Re-running the file (twice) changes nothing: the table, its two indexes, the trigger and the constraints are
    created once, and the stored rows survive untouched."""
    sid = _security(db)
    did = _insert(db, sid)
    _insert(db, sid, action="undo", undoes=did, author="operator:undo")

    def shape() -> tuple:
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*)::text || md5(coalesce(string_agg(d::text, '|' ORDER BY d::text), '')) "
                "        FROM split_filing_dismissal d) AS rows, "
                "(SELECT count(*) FROM pg_indexes WHERE tablename = 'split_filing_dismissal') AS ix, "
                "(SELECT count(*) FROM pg_trigger WHERE tgrelid = 'split_filing_dismissal'::regclass "
                "AND NOT tgisinternal) AS trg, "
                "(SELECT count(*) FROM pg_constraint "
                " WHERE conrelid = 'split_filing_dismissal'::regclass) AS con"
            )
            return tuple(cur.fetchone().values())

    before = shape()
    # the primary key, the per-filing index and the one-undo index; the no_update trigger
    assert before[1:3] == (3, 1)
    try:
        for _ in range(2):
            with db.cursor() as cur:
                cur.execute(_sql())
        assert shape() == before
    finally:
        db.rollback()
