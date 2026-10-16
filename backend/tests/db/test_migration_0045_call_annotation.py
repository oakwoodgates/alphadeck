"""0045 — ``call_annotation``: the append-only judgment log beside ``calls`` (D3b), proven at the DB.

The guarantees the Scoreboard's reversible exclusion rests on, each a real DB guard rather than a
convention: UPDATE is refused (the ``no_update`` trigger); a revoke is an appended row that makes its
target INACTIVE; a revoke can name only an exclude/note of the SAME call, never another revoke, and at most
once; the action/revokes pairing is a CHECK; and the file re-executes as a no-op. DB-backed.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID
from repositories import call_annotations_repo

_SQL = (MIGRATIONS_DIR / "0045_call_annotation.sql").read_text(encoding="utf-8")
_ASOF = date(2026, 10, 14)


def _call(db, *, state: str = "armed") -> tuple[uuid.UUID, uuid.UUID]:
    tid, cid = uuid.uuid4(), uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "annotated", "x"),
        )
        cur.execute(
            "INSERT INTO calls (id, tenant_id, thesis_id, asof, state, verdict, card) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (cid, DEFAULT_TENANT_ID, tid, _ASOF, state, "starter_entry", "{}"),
        )
    db.commit()
    return tid, cid


def _insert(db, cid: uuid.UUID, action: str, revokes: uuid.UUID | None = None) -> uuid.UUID:
    aid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO call_annotation (id, tenant_id, call_id, thesis_id, asof, action, reason, "
            "rule, revokes) SELECT %s, tenant_id, id, thesis_id, asof, %s, 'r', 'test@x', %s "
            "FROM calls WHERE id = %s",
            (aid, action, revokes, cid),
        )
    return aid


def test_the_file_applies_cleanly_and_re_executes_as_a_no_op(db):
    with db.cursor() as cur:
        cur.execute(_SQL)  # the runner applied it once already; a second execution changes nothing
        cur.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = 'call_annotation'"
        )
        assert cur.fetchone()["n"] == 1
        cur.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'call_annotation'::regclass "
            "AND NOT tgisinternal ORDER BY tgname"
        )
        assert [r["tgname"] for r in cur.fetchall()] == ["check_revoke", "no_update"]
    db.commit()


def test_update_is_refused_append_only(db):
    _, cid = _call(db)
    aid = _insert(db, cid, "exclude")
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("UPDATE call_annotation SET reason = 'edited' WHERE id = %s", (aid,))
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT reason FROM call_annotation WHERE id = %s", (aid,))
        assert cur.fetchone()["reason"] == "r"


def test_a_revoke_makes_its_target_inactive_and_nothing_else(db):
    _, cid = _call(db)
    keep = _insert(db, cid, "note")
    gone = _insert(db, cid, "exclude")
    db.commit()
    assert {a.id for a in call_annotations_repo.active_for_call(db, cid)} == {keep, gone}

    rid = call_annotations_repo.revoke(
        db, gone, reason="operator disagrees", rule="operator-revoke@x"
    )
    db.commit()
    assert {a.id for a in call_annotations_repo.active_for_call(db, cid)} == {keep}
    # the revoke is a ROW (append-only history), carrying its target's call / thesis / as-of
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM call_annotation WHERE call_id = %s", (cid,))
        assert cur.fetchone()["n"] == 3
        cur.execute("SELECT action, revokes, asof FROM call_annotation WHERE id = %s", (rid,))
        row = cur.fetchone()
    assert (row["action"], row["revokes"], row["asof"]) == ("revoke", gone, _ASOF)


def test_revoke_shapes_the_active_predicate_cannot_read_are_refused(db):
    _, cid = _call(db)
    _, other = _call(db)
    target = _insert(db, cid, "exclude")
    db.commit()
    first = _insert(db, cid, "revoke", revokes=target)
    db.commit()

    # a second revoke of the same annotation — the partial unique index
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert(db, cid, "revoke", revokes=target)
    db.rollback()
    # a revoke of a revoke — the trigger (no un-revoke chains)
    with pytest.raises(psycopg.errors.RaiseException, match="cannot revoke another revoke"):
        _insert(db, cid, "revoke", revokes=first)
    db.rollback()
    # a revoke on a DIFFERENT call than its target — the trigger
    live = _insert(db, cid, "note")
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="same call"):
        _insert(db, other, "revoke", revokes=live)
    db.rollback()
    # the action/revokes pairing — the CHECK, both ways
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(db, cid, "revoke")
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(db, cid, "exclude", revokes=live)
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(db, cid, "delete")
    db.rollback()


def test_deleting_the_call_takes_its_annotations_with_it(db):
    """ON DELETE CASCADE mirrors ``calls.thesis_id``: the reconstructed-row repair (a real DELETE path)
    can never strand an annotation pointing at nothing."""
    _, cid = _call(db)
    target = _insert(db, cid, "exclude")
    _insert(db, cid, "revoke", revokes=target)
    db.commit()
    with db.cursor() as cur:
        cur.execute("DELETE FROM calls WHERE id = %s", (cid,))
        cur.execute("SELECT count(*) AS n FROM call_annotation WHERE call_id = %s", (cid,))
        assert cur.fetchone()["n"] == 0
    db.commit()
