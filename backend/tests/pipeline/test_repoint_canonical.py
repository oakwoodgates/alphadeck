"""The one-time canonical re-point (`pipeline.repoint_canonical`) — the data fix for baskets written before
the canonical-primary slice: a member holding a NON-primary CIK sibling is re-pointed to the primary
(id + ticker), a duplicate pair (both siblings placed — the pre-fix re-draft bug) is SKIPPED loudly for the
operator's prune, and a second run changes nothing (idempotent — COUNT the table, not the read)."""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Thesis
from pipeline.repoint_canonical import main
from repositories import thesis_repo


def _security(db, ticker, *, cik, is_primary):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, is_primary, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, ticker, cik, is_primary, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _thesis(db, *members: BasketMember) -> uuid.UUID:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="ai memory",
        narrative="n",
        basket=list(members),
    )
    thesis_repo.upsert(db, t)
    db.commit()
    return t.id


def _member(ticker, sid) -> BasketMember:
    return BasketMember(ticker=ticker, role="r", archetype=Archetype.HIGH_BETA, security_id=sid)


def _basket_rows(db, tid):
    with db.cursor() as cur:
        cur.execute(
            "SELECT ticker, security_id FROM basket_member WHERE thesis_id = %s ORDER BY ticker",
            (tid,),
        )
        return [(r["ticker"], r["security_id"]) for r in cur.fetchall()]


def test_repoint_moves_a_non_primary_member_to_the_primary(db):
    asmlf = _security(db, "ASMLF", cik="0000937966", is_primary=False)
    asml = _security(db, "ASML", cik="0000937966", is_primary=True)
    tid = _thesis(db, _member("ASMLF", asmlf))

    main([])  # opens its own conn to the same test DB; the seed above is committed

    assert _basket_rows(db, tid) == [("ASML", asml)]  # id AND ticker re-pointed
    main([])  # idempotent — a second run finds nothing to re-point
    assert _basket_rows(db, tid) == [("ASML", asml)]


def test_repoint_skips_a_duplicate_pair_loudly(db, capsys):
    """Both siblings in one basket (the pre-fix re-draft duplicate): re-pointing would silently merge two
    operator-visible rows — the script SKIPS and says so; the operator prunes (#9 on the interface).
    """
    asmlf = _security(db, "ASMLF", cik="0000937966", is_primary=False)
    asml = _security(db, "ASML", cik="0000937966", is_primary=True)
    tid = _thesis(db, _member("ASMLF", asmlf), _member("ASML", asml))

    main([])

    assert _basket_rows(db, tid) == [("ASML", asml), ("ASMLF", asmlf)]  # untouched
    assert "duplicate pair" in capsys.readouterr().out


def test_repoint_dry_run_writes_nothing(db):
    asmlf = _security(db, "ASMLF", cik="0000937966", is_primary=False)
    _security(db, "ASML", cik="0000937966", is_primary=True)
    tid = _thesis(db, _member("ASMLF", asmlf))

    main(["--dry-run"])

    assert _basket_rows(db, tid) == [("ASMLF", asmlf)]  # reported, not written
