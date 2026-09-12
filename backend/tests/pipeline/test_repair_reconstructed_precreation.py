"""``pipeline.repair_reconstructed_precreation`` — delete RECONSTRUCTED rows dated before their thesis
existed, and nothing else. Every count is asserted against the raw TABLE COUNT (the CLAUDE.md
convention). The rule under test is the backfill's own ``thesis_existed_on`` (market time, by the
calendar day), so the boundary case here is deliberately one where UTC and market time DISAGREE.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from db.session import DEFAULT_TENANT_ID, connect
from pipeline import repair_reconstructed_precreation as repair

# T is created 2026-08-21 03:30Z = 2026-08-20 23:30 EDT: on the MARKET calendar it existed on 08-20.
_T_BORN = datetime(2026, 8, 21, 3, 30, tzinfo=timezone.utc)
_U_BORN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _thesis(db, name: str, created_at: datetime) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "x", created_at),
        )
    return tid


def _row(db, tid: uuid.UUID, asof: date, *, reconstructed: bool, state: str = "armed") -> uuid.UUID:
    cid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (id, tenant_id, thesis_id, asof, state, verdict, card, reconstructed) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (cid, DEFAULT_TENANT_ID, tid, asof, state, "core_entry", "{}", reconstructed),
        )
    return cid


def _ids(db) -> set[uuid.UUID]:
    with db.cursor() as cur:
        cur.execute("SELECT id FROM calls")
        return {r["id"] for r in cur.fetchall()}


def _count(conn) -> int:
    """COUNT THE TABLE — every row, never a filtered read."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        return cur.fetchone()["n"]


def _seed(db):
    t = _thesis(db, "Navy Shipbuilding Rebuild", _T_BORN)
    u = _thesis(db, "Old thesis", _U_BORN)
    rows = {
        "t_pre_0807": _row(db, t, date(2026, 8, 7), reconstructed=True),  # DELETE
        "t_pre_0819": _row(db, t, date(2026, 8, 19), reconstructed=True, state="warming"),  # DELETE
        "t_born_day_0820": _row(db, t, date(2026, 8, 20), reconstructed=True),  # KEEP (market day)
        "t_post_0825": _row(db, t, date(2026, 8, 25), reconstructed=True),  # KEEP (existed)
        "t_nightly_0807": _row(
            db, t, date(2026, 8, 7), reconstructed=False
        ),  # KEEP (never a candidate)
        "u_0807": _row(db, u, date(2026, 8, 7), reconstructed=True),  # KEEP (U existed)
    }
    db.commit()
    return rows


def test_find_precreation_rows_applies_the_backfills_market_time_rule(db):
    rows = _seed(db)
    found = repair.find_precreation_rows(db)
    assert [(r.thesis_name, r.asof, r.state) for r in found] == [
        ("Navy Shipbuilding Rebuild", date(2026, 8, 7), "armed"),
        ("Navy Shipbuilding Rebuild", date(2026, 8, 19), "warming"),
    ]
    assert {r.call_id for r in found} == {rows["t_pre_0807"], rows["t_pre_0819"]}


def test_dry_run_is_the_default_prints_every_row_and_deletes_NOTHING(db, capsys):
    _seed(db)
    before = _count(db)
    assert before == 6

    repair.main([])  # no --apply: the default is a dry run

    out = capsys.readouterr().out
    assert "DRY-RUN (nothing written)" in out
    assert "Navy Shipbuilding Rebuild: asof 2026-08-07 · armed / core_entry" in out
    assert "Navy Shipbuilding Rebuild: asof 2026-08-19 · warming / core_entry" in out
    assert "thesis created 2026-08-20 (market)" in out  # the MARKET date, not the UTC 08-21
    assert "2026-08-20 ·" not in out and "2026-08-25" not in out  # the kept rows are not listed
    assert "2 reconstructed pre-creation row(s) across 1 thesis" in out
    assert _count(db) == before  # COUNT THE TABLE: nothing landed


def test_apply_deletes_exactly_the_precreation_reconstructed_rows(db, capsys):
    rows = _seed(db)
    assert _count(db) == 6

    repair.main(["--apply"])

    out = capsys.readouterr().out
    assert "APPLY" in out
    assert "Navy Shipbuilding Rebuild: -2 row(s)" in out
    assert "SUMMARY: total_deleted=2 theses=1" in out
    fresh = (
        connect()
    )  # a fresh connection: the delete was COMMITTED, not just visible to the writer
    try:
        assert _count(fresh) == 4
    finally:
        fresh.rollback()
        fresh.close()
    assert _ids(db) == {
        rows["t_born_day_0820"],
        rows["t_post_0825"],
        rows["t_nightly_0807"],
        rows["u_0807"],
    }

    # a second apply finds nothing and deletes nothing (idempotent) — count the table again
    repair.main(["--apply"])
    assert "SUMMARY: total_deleted=0 theses=0" in capsys.readouterr().out
    assert _count(db) == 4
