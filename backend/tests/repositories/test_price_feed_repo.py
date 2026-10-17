"""The price-feed repair log (migration 0046) — every operator repair has an EXACT, auditable inverse.

Each undo test counts the TABLE (the idempotency-test rule) and compares the master row byte-for-byte
before the change and after its undo. The names are the real measured ones (HLLK -> SDCO, WONDF)."""

from __future__ import annotations

import psycopg
import pytest

from repositories import price_feed_repo as repo
from repositories.price_feed_repo import RepairConflict
from securities import master
from tests import price_feed_fixtures as fx


def _master(db, sid) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT ticker, name, price_symbol, price_symbol_basis FROM security_master WHERE id = %s",
            (sid,),
        )
        return dict(cur.fetchone())


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed table names
        return cur.fetchone()["n"]


def test_adopt_then_undo_restores_the_master_EXACTLY_and_keeps_the_history(db):
    """HLLK -> SDCO with the name refreshed to EDGAR's current entity name, then Undo: price_symbol,
    price_symbol_basis and name are back byte-for-byte, and the log holds BOTH rows (append-only).
    """
    sid = fx.insert_security(db, "HLLK")
    before = _master(db, sid)
    assert before == {
        "ticker": "HLLK",
        "name": "HALLMARK VENTURE GROUP, INC.",
        "price_symbol": None,
        "price_symbol_basis": None,
    }

    change = repo.apply_change(
        db,
        sid,
        action="adopt",
        symbol="sdco",
        basis="operator:adopt",
        new_name="SDR Drone, Inc.",
        detail={"probe": {"symbol": "SDCO", "verified": True}},
    )
    db.commit()
    assert _master(db, sid) == {
        "ticker": "HLLK",
        "name": "SDR Drone, Inc.",
        "price_symbol": "SDCO",
        "price_symbol_basis": "operator:adopt",
    }
    assert (change.prior_symbol, change.new_symbol, change.name_changed) == (None, "SDCO", True)
    assert _count(db, "price_symbol_change") == 1

    undo = repo.undo_change(db, change.id, basis="operator:undo")
    db.commit()

    assert _master(db, sid) == before  # EXACT
    assert _count(db, "price_symbol_change") == 2  # the adopt AND its undo — nothing deleted
    assert undo.undoes == change.id and undo.action == "undo" and undo.new_symbol is None
    assert repo.standing_change_for(db, sid) is None  # nothing stands once undone


def test_a_chain_of_changes_undoes_back_to_the_original_one_step_at_a_time(db):
    """Set A, then set B over it; undo B -> A exactly (A's basis too); undo A -> the original. Undoing A
    while B stands is refused (a later change stands), so the order is enforced."""
    sid = fx.insert_security(db, "WONDF", with_tape=False)
    original = _master(db, sid)
    a = repo.apply_change(db, sid, action="set", symbol="WNDR.TO", basis="operator:set")
    db.commit()
    after_a = _master(db, sid)
    b = repo.apply_change(db, sid, action="set", symbol="WONDF.NE", basis="operator:set")
    db.commit()

    with pytest.raises(RepairConflict, match="a later price-symbol change stands"):
        repo.undo_change(db, a.id, basis="operator:undo")
    db.rollback()

    repo.undo_change(db, b.id, basis="operator:undo")
    db.commit()
    assert _master(db, sid) == after_a
    repo.undo_change(db, a.id, basis="operator:undo")
    db.commit()
    assert _master(db, sid) == original
    assert _count(db, "price_symbol_change") == 4


def test_undo_REFUSES_when_the_master_moved_since_and_writes_nothing(db):
    """Something else re-pointed the symbol after the Adopt (the --adopt CLI, the finalize self-heal): the
    undo would clobber a value it did not write, so it refuses and changes nothing."""
    sid = fx.insert_security(db, "HLLK")
    change = repo.apply_change(db, sid, action="adopt", symbol="SDCO", basis="operator:adopt")
    db.commit()
    master.set_price_symbol(db, sid, "SDCOX", basis="operator:adopt")  # a different writer, later
    db.commit()
    moved = _master(db, sid)

    with pytest.raises(RepairConflict, match="master changed since this repair"):
        repo.undo_change(db, change.id, basis="operator:undo")
    db.rollback()

    assert _master(db, sid) == moved and _count(db, "price_symbol_change") == 1


def test_a_second_undo_and_an_undo_of_an_undo_are_refused(db):
    sid = fx.insert_security(db, "HLLK")
    change = repo.apply_change(db, sid, action="adopt", symbol="SDCO", basis="operator:adopt")
    undo = repo.undo_change(db, change.id, basis="operator:undo")
    db.commit()
    with pytest.raises(RepairConflict, match="already undone"):
        repo.undo_change(db, change.id, basis="operator:undo")
    db.rollback()
    with pytest.raises(LookupError):
        repo.undo_change(db, undo.id, basis="operator:undo")
    db.rollback()
    assert _count(db, "price_symbol_change") == 2


def test_a_change_that_changes_nothing_is_refused(db):
    """Setting the symbol a name is already priced under writes no history row. A symbol equal to the
    canonical ticker IS the healthy NULL state, so it changes nothing either."""
    sid = fx.insert_security(db, "WONDF", with_tape=False)
    with pytest.raises(RepairConflict, match="already priced under WONDF"):
        repo.apply_change(db, sid, action="set", symbol="wondf", basis="operator:set")
    db.rollback()
    assert _count(db, "price_symbol_change") == 0


def test_acknowledge_is_IDEMPOTENT_and_revoke_appends_the_inverse(db):
    """Acknowledge WONDF twice: ONE row (a double-click is one decision). Revoke appends a revoke row; the
    name is no longer acknowledged; a second revoke finds nothing active. Count the table each step.
    """
    sid = fx.insert_security(db, "WONDF", with_tape=False)
    ack, created = repo.acknowledge(
        db, sid, reason="40-F filer, no US listing", basis="operator:acknowledge"
    )
    again, created_again = repo.acknowledge(db, sid, reason="again", basis="operator:acknowledge")
    db.commit()
    assert created is True and created_again is False and again.id == ack.id
    assert _count(db, "price_feed_ack") == 1
    assert repo.acknowledged_security_ids(db) == {sid}

    rv = repo.revoke(db, ack.id, reason="revoked by the operator", basis="operator:revoke")
    db.commit()
    assert rv.revokes == ack.id and rv.action == "revoke"
    assert repo.acknowledged_security_ids(db) == set()
    assert _count(db, "price_feed_ack") == 2
    with pytest.raises(LookupError):
        repo.revoke(db, ack.id, reason="again", basis="operator:revoke")
    db.rollback()

    # acknowledging again after a revoke is a NEW decision — a new row, active again
    _, created3 = repo.acknowledge(
        db, sid, reason="keep it after all", basis="operator:acknowledge"
    )
    db.commit()
    assert created3 is True and repo.acknowledged_security_ids(db) == {sid}
    assert _count(db, "price_feed_ack") == 3


def test_both_logs_are_APPEND_ONLY_at_the_database(db):
    sid = fx.insert_security(db, "WONDF", with_tape=False)
    repo.acknowledge(db, sid, reason="keep", basis="operator:acknowledge")
    repo.apply_change(db, sid, action="set", symbol="WNDR.TO", basis="operator:set")
    db.commit()
    for table in ("price_feed_ack", "price_symbol_change"):
        with pytest.raises(psycopg.Error):
            with db.cursor() as cur:
                cur.execute(f"UPDATE {table} SET basis = 'tampered'")  # noqa: S608
        db.rollback()


def test_the_database_refuses_an_undo_of_another_securitys_change(db):
    """The structural guard under the repository: a hand-typed undo naming a different security's change
    is rejected by the trigger."""
    a = fx.insert_security(db, "WONDF", with_tape=False)
    b = fx.insert_security(db, "HLLK")
    change = repo.apply_change(db, a, action="set", symbol="WNDR.TO", basis="operator:set")
    db.commit()
    with pytest.raises(psycopg.Error, match="same security"):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO price_symbol_change (tenant_id, security_id, action, basis, undoes) "
                "SELECT tenant_id, %s, 'undo', 'hand', %s FROM security_master WHERE id = %s",
                (b, change.id, b),
            )
    db.rollback()


def test_standing_changes_lists_each_names_newest_change_with_its_undoability(db):
    hllk = fx.insert_security(db, "HLLK")
    wondf = fx.insert_security(db, "WONDF", with_tape=False)
    repo.apply_change(db, hllk, action="adopt", symbol="SDCO", basis="operator:adopt")
    w = repo.apply_change(db, wondf, action="set", symbol="WNDR.TO", basis="operator:set")
    db.commit()
    master.set_price_symbol(db, wondf, "OTHER", basis="resolver:auto")  # moved underneath
    db.commit()

    rows = {r.ticker: r for r in repo.standing_changes(db)}
    assert set(rows) == {"HLLK", "WONDF"}
    assert rows["HLLK"].undoable is True and rows["HLLK"].current_symbol == "SDCO"
    assert rows["WONDF"].id == w.id and rows["WONDF"].undoable is False
