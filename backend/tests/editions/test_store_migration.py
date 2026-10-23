"""S3 — migration 0063, the editions store, proven at the DB: every table is tenant-scoped and append-only (the
``no_update`` trigger), Edition 0 can never be stored, an edition number is unique per tenant, a night has one
page per thesis, a child row cannot sit under another tenant's edition, and the file re-executes as a no-op.
Postgres only (no engine), so it runs in a lean venv too."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Json

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID

_SQL = (MIGRATIONS_DIR / "0063_scoreboard_editions.sql").read_text(encoding="utf-8")
TABLES = ("edition", "edition_event", "edition_entry", "edition_call")
OTHER_TENANT = uuid.UUID("00000000-0000-0000-0000-0000000e0063")
CUT = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


def _thesis(db) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "editions", "x"),
        )
    db.commit()
    return tid


def _edition(db, number: int = 1, tenant_id: uuid.UUID = DEFAULT_TENANT_ID) -> uuid.UUID:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition (tenant_id, number, label, causes, engine_fingerprint, config_hash, "
            "data_known_by, first_night) VALUES (%s, %s, 'first re-run', ARRAY['initial'], 'fp', "
            "'cfg', %s, %s) RETURNING id",
            (tenant_id, number, CUT, date(2026, 6, 10)),
        )
        return cur.fetchone()["id"]


def _page(db, edition_id, thesis_id, asof=date(2026, 7, 10), tenant_id=DEFAULT_TENANT_ID):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_call (tenant_id, edition_id, thesis_id, asof, state, verdict, card, "
            "data_known_by, engine_fingerprint, page_kind, computed_at) "
            "VALUES (%s, %s, %s, %s, 'incubating', 'stand_aside', %s, %s, 'fp', 'rerun', %s) "
            "RETURNING id",
            (tenant_id, edition_id, thesis_id, asof, Json({}), CUT, CUT),
        )
        return cur.fetchone()["id"]


def test_the_file_applies_cleanly_and_re_executes_as_a_no_op(db):
    with db.cursor() as cur:
        cur.execute(_SQL)  # the runner applied it once already; a second execution changes nothing
        for t in TABLES:
            cur.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal",
                (t,),
            )
            assert [r["tgname"] for r in cur.fetchall()] == ["no_update"], t
    db.commit()


def test_every_table_carries_a_required_tenant_id(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name, is_nullable FROM information_schema.columns "
            "WHERE column_name = 'tenant_id' AND table_name = ANY(%s)",
            (list(TABLES),),
        )
        got = {r["table_name"]: r["is_nullable"] for r in cur.fetchall()}
    assert got == {t: "NO" for t in TABLES}


def test_update_is_refused_on_every_table(db):
    tid = _thesis(db)
    eid = _edition(db)
    pid = _page(db, eid, tid)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_entry (tenant_id, edition_id, thesis_id, security_id, entry_date, "
            "estimated, method) VALUES (%s, %s, %s, %s, %s, true, 'first_pull') RETURNING id",
            (DEFAULT_TENANT_ID, eid, tid, uuid.uuid4(), date(2026, 6, 10)),
        )
        nid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO edition_event (tenant_id, edition_id, kind) VALUES (%s, %s, 'published') "
            "RETURNING id",
            (DEFAULT_TENANT_ID, eid),
        )
        vid = cur.fetchone()["id"]
    db.commit()
    for table, rid, col, val in (
        ("edition", eid, "label", "edited"),
        ("edition_call", pid, "state", "armed"),
        ("edition_entry", nid, "method", "snapshot"),
        ("edition_event", vid, "kind", "closed"),
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            with db.cursor() as cur:
                cur.execute(
                    f"UPDATE {table} SET {col} = %s WHERE id = %s", (val, rid)
                )  # noqa: S608
        db.rollback()


def test_edition_0_can_never_be_stored(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        _edition(db, number=0)
    db.rollback()


def test_an_edition_number_is_unique_per_tenant(db):
    _edition(db, number=1)
    db.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        _edition(db, number=1)
    db.rollback()
    with db.cursor() as cur:  # another tenant may hold its own Edition 1
        cur.execute(
            "INSERT INTO tenant (id, name) VALUES (%s, 'editions-other') ON CONFLICT DO NOTHING",
            (OTHER_TENANT,),
        )
    _edition(db, number=1, tenant_id=OTHER_TENANT)
    db.rollback()


def test_one_page_per_thesis_night(db):
    tid = _thesis(db)
    eid = _edition(db)
    _page(db, eid, tid)
    db.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        _page(db, eid, tid)
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM edition_call WHERE edition_id = %s", (eid,))
        assert cur.fetchone()["n"] == 1


def test_a_page_cannot_sit_under_another_tenants_edition(db):
    tid = _thesis(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name) VALUES (%s, 'editions-other') ON CONFLICT DO NOTHING",
            (OTHER_TENANT,),
        )
    eid = _edition(db)  # left uncommitted, like the tenant: the rollback leaves nothing behind
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _page(db, eid, tid, tenant_id=OTHER_TENANT)
    db.rollback()


def test_an_unknown_cause_or_page_kind_is_refused(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO edition (tenant_id, number, label, causes, engine_fingerprint, "
                "config_hash, data_known_by, first_night) VALUES (%s, 2, 'x', ARRAY['whim'], 'fp', "
                "'cfg', %s, %s)",
                (DEFAULT_TENANT_ID, CUT, date(2026, 6, 10)),
            )
    db.rollback()
    tid = _thesis(db)
    eid = _edition(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO edition_call (tenant_id, edition_id, thesis_id, asof, state, verdict, "
                "card, data_known_by, engine_fingerprint, page_kind, computed_at) VALUES "
                "(%s, %s, %s, %s, 's', 'v', '{}', %s, 'fp', 'guess', %s)",
                (DEFAULT_TENANT_ID, eid, tid, date(2026, 7, 10), CUT, CUT),
            )
    db.rollback()
