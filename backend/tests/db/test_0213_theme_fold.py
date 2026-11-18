"""Migration 0213 (V2 W6c): the theme conviction folds into the card's catalyst list.

On the migrated test database: theme-conviction rows in the OLD store (``fact_theme_conviction``: a ratified,
flip, thesis-scoped row and a later re-ratification superseding it), then 0213's SQL executed directly (the
runner skips an applied file; the 0078 test's precedent). After it:

- each theme version is ONE ``fact_thesis_catalyst`` row of the converted shape (operator decision N4 (a)): a
  whole-card (no members), counting, flip entry with the same label, ``citation`` = the old ``source_ref``,
  ``relevant_until`` = the old ``horizon_end``, the same ``valid_from`` and ``recorded_at``, ``source`` kept
  (``ratified``), no entry time (so it counts from 06:00 ET of its stated date, the one named exception to R5),
  ``supersedes`` mapped; its ``entry_id`` is ``md5(thesis_id::text || source_ref)::uuid``, the id the seed names;
- the card gains ONE undated calendar row (``id`` = ``entry_id``, kind ``theme``), so the entry has a row the
  operator can switch off and the catalyst surface (dated rows only) does not move;
- the call reads the same theme fact it read from the old store;
- a second execution writes nothing (the tables are COUNTED, not just read).

The test's transaction is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from pipeline.call_for_thesis import load_call_inputs
from pipeline.seed import NUCLEAR_THEME_ENTRY_ID, NUCLEAR_THEME_SOURCE_REF, NUCLEAR_THESIS_ID
from repositories.thesis_catalyst_repo import theme_entry_id
from signals import theme_conviction
from tests.thesis_catalyst_fixtures import card

_SQL = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0213_v2_theme_conviction_fold.sql"
).read_text(encoding="utf-8")
_REF = "https://example.com/theme"
_T1 = datetime(2026, 6, 10, 4, 37, 21, tzinfo=timezone.utc)
_T2 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def _old_rows(db, tid: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    common = {
        "tenant_id": DEFAULT_TENANT_ID,
        "thesis_id": tid,
        "grade": "flip",
        "source": "ratified",
        "source_ref": _REF,
        "ratified_by": "operator",
    }
    first = append_fact(
        db,
        "fact_theme_conviction",
        {
            **common,
            "label": "a theme",
            "valid_from": date(2026, 1, 15),
            "horizon_end": date(2027, 1, 15),
            "recorded_at": _T1,
        },
    )
    second = append_fact(
        db,
        "fact_theme_conviction",
        {
            **common,
            "label": "a theme, re-ratified",
            "valid_from": date(2026, 6, 30),
            "horizon_end": None,
            "recorded_at": _T2,
            "supersedes": first,
        },
    )
    return first, second


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a test-fixed table name
        return cur.fetchone()["n"]


def _run(db) -> None:
    with db.cursor() as cur:
        cur.execute(_SQL)


def test_the_theme_rows_convert_to_one_whole_card_entry(db):
    tid, _, _ = card(db)
    first, second = _old_rows(db, tid)
    _run(db)
    eid = theme_entry_id(tid, _REF)
    with db.cursor() as cur:
        cur.execute("SELECT md5(%s::text || %s)::uuid AS u", (tid, _REF))
        assert cur.fetchone()["u"] == eid  # the SQL and the Python id agree
        cur.execute(
            "SELECT * FROM fact_thesis_catalyst WHERE thesis_id = %s ORDER BY recorded_at", (tid,)
        )
        rows = cur.fetchall()
    assert [r["recorded_at"] for r in rows] == [_T1, _T2]
    a, b = rows
    for r in rows:
        assert r["entry_id"] == eid and r["tenant_id"] == DEFAULT_TENANT_ID
        assert r["counts"] is True and list(r["security_ids"]) == []
        assert r["grade"] == "flip" and r["citation"] == _REF and r["source"] == "ratified"
        assert r["entered_at"] is None and r["entered_by"] is None
    assert (a["label"], a["valid_from"], a["relevant_until"]) == (
        "a theme",
        date(2026, 1, 15),
        date(2027, 1, 15),
    )
    assert (b["label"], b["valid_from"], b["relevant_until"]) == (
        "a theme, re-ratified",
        date(2026, 6, 30),
        None,
    )
    assert a["supersedes"] is None and b["supersedes"] == a["id"]
    with db.cursor() as cur:
        cur.execute("SELECT * FROM catalyst WHERE id = %s", (eid,))
        (cal,) = cur.fetchall()
    assert cal["thesis_id"] == tid and cal["kind"] == "theme"
    assert cal["label"] == "a theme, re-ratified"  # the newest version's label
    assert cal["when_date"] is None and cal["when_label"] is None
    assert cal["ordinal"] == 1  # after the card's one existing row


def test_the_call_reads_the_same_theme_fact_after_the_fold(db):
    tid, _, _ = card(db)
    _old_rows(db, tid)
    _run(db)
    db.commit()
    night = date(2026, 6, 5)
    _, pit = load_call_inputs(db, tid, night, known_at=datetime(2100, 1, 1, tzinfo=timezone.utc))
    assert theme_conviction.detect_fact(pit, tid, night) == {
        "valid_from": date(2026, 1, 15),
        "horizon_end": date(2027, 1, 15),
        "grade": "flip",
        "label": "a theme",
        "source": "ratified",
        "source_ref": _REF,
    }


def test_a_second_run_writes_nothing(db):
    tid, _, _ = card(db)
    _old_rows(db, tid)
    _run(db)
    before = (_count(db, "fact_thesis_catalyst"), _count(db, "catalyst"))
    _run(db)
    assert (_count(db, "fact_thesis_catalyst"), _count(db, "catalyst")) == before


def test_the_seed_names_the_migrations_id():
    assert NUCLEAR_THEME_ENTRY_ID == theme_entry_id(NUCLEAR_THESIS_ID, NUCLEAR_THEME_SOURCE_REF)
