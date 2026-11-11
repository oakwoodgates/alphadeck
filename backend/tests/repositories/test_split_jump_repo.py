"""The split-jump dismissal log's writers (``repositories/split_jump_repo.py``) move the company's change stamp
(``db.change_stamp``: the dashboards' cache drops that company's pieces). The panel behavior is
``tests/app/test_admin_split_list_api.py``'s. DB-backed."""

from __future__ import annotations

import uuid
from datetime import date

from db.change_stamp import stamp_map
from db.session import DEFAULT_TENANT_ID
from repositories import split_jump_repo


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "2025-01-01"),
        )
    db.commit()
    return sid


def _seq(db, sid):
    got = stamp_map(db).get(sid)
    db.rollback()
    return got


def test_dismiss_and_undo_each_move_the_companys_change_stamp(db):
    sid, other = _security(db, "AAA"), _security(db, "BBB")
    assert _seq(db, sid) is None
    row = split_jump_repo.dismiss(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        security_id=sid,
        step_date=date(2026, 8, 24),
        reason="a real move",
    )
    db.commit()
    dismissed = _seq(db, sid)
    assert dismissed is not None
    split_jump_repo.undo(db, row["id"], tenant_id=DEFAULT_TENANT_ID, reason="undone")
    db.commit()
    assert _seq(db, sid) > dismissed
    assert _seq(db, other) is None  # only that company
