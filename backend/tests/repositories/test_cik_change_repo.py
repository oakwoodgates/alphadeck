"""The security CIK change log (migration 0049, T7) on a real DB, over the REAL measured master rows and
successor documents (``tests/fixtures/edgar/successor_cik_2026_10.json``): Adopt re-points the CIK IN PLACE
(same id — the facts and basket links stay attached) and refreshes the CIK-dependent identity through the
enrich_identity derivation; Undo restores the master BYTE FOR BYTE; each appends exactly one row; every
refusal writes nothing."""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import SecurityIdentity
from ingest.edgar.submissions import parse_identity
from ingest.prices.eod_loader import ingest_prices
from repositories import cik_change_repo
from repositories.price_feed_repo import RepairConflict
from securities import master
from tests import successor_cik_fixtures as fx

BASIS = "operator:adopt-cik"


def _row(db, sid) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM security_master WHERE id = %s", (sid,))
        return dict(cur.fetchone())


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed names
        return cur.fetchone()["n"]


def _identity(ticker: str) -> SecurityIdentity:
    return parse_identity(fx.successor_doc(ticker))


def _adopt(db, sid, ticker: str, **kw):
    args = dict(
        new_cik=fx.successor_cik(ticker),
        expected_cik=fx.case(ticker)["master"]["cik"],
        identity=_identity(ticker),
        entity_name=fx.successor_doc(ticker)["name"],
        basis=BASIS,
        detail={"pass_asof": "2026-10-16"},
    )
    args.update(kw)
    out = cik_change_repo.adopt(db, sid, **args)
    db.commit()
    return out


def test_ADOPT_repoints_the_CIK_IN_PLACE_and_refreshes_the_CIK_dependent_identity(db):
    sid = fx.insert_security(db, "REAX")
    # a row FK'd to the id must survive the re-point (8+ tables FK security_id). A STRUCTURAL row: its
    # value is irrelevant — the contract under test is "the id stays, so nothing orphans"
    ingest_prices(
        db,
        sid,
        [
            {
                "d": date(2026, 10, 15),
                "open": None,
                "high": None,
                "low": None,
                "close": 1.0,
                "volume": 0,
            }
        ],
    )
    db.commit()
    bars = _count(db, "fact_price_eod")
    before = _row(db, sid)
    assert (before["recent_foreign_form"], before["files_domestic_forms"]) == ("40-F", False)

    change = _adopt(db, sid, "REAX")

    after = _row(db, sid)
    assert after["id"] == sid and after["ticker"] == "REAX"  # the SAME row
    assert after["cik"] == "0002136387"
    assert after["name"] == "Real REMAX Group Inc."  # the successor's entity name
    assert (
        after["recent_foreign_form"] is None
    )  # no 40-F on the successor: insider flow is available
    assert after["incorporation"] == "DE"
    assert after["enriched_source"] == "submissions:CIK0002136387"
    assert after["exchange"] == before["exchange"] == "Nasdaq"  # enrich only fills a NULL exchange
    assert after["recorded_at"] == before["recorded_at"]  # identity UPDATE, never a new version
    assert _count(db, "fact_price_eod") == bars
    assert _count(db, "security_cik_change") == 1
    assert change.action == "adopt" and change.basis == BASIS
    assert (change.prior_cik, change.new_cik) == ("0001862461", "0002136387")
    assert change.prior_values["recent_foreign_form"] == "40-F"
    assert change.new_values["name"] == "Real REMAX Group Inc."
    assert change.detail == {"pass_asof": "2026-10-16"}


def test_UNDO_restores_the_master_BYTE_FOR_BYTE_and_appends_ONE_row(db):
    for ticker in ("UROY", "REAX"):
        sid = fx.insert_security(db, ticker)
        before = _row(db, sid)
        n = _count(db, "security_cik_change")

        change = _adopt(db, sid, ticker)
        assert _count(db, "security_cik_change") == n + 1
        assert _row(db, sid) != before

        undone = cik_change_repo.undo(db, change.id, basis="operator:undo")
        db.commit()

        assert (
            _row(db, sid) == before
        )  # EVERY column, byte for byte (enriched_at to the microsecond)
        assert _count(db, "security_cik_change") == n + 2
        assert undone.action == "undo" and undone.undoes == change.id
        assert (undone.prior_cik, undone.new_cik) == (change.new_cik, change.prior_cik)
        assert cik_change_repo.standing_change_for(db, sid) is None


def test_UNDO_REFUSES_when_the_master_moved_since_and_writes_nothing(db):
    """An enrich_identity run (or anything else) touched a column the Adopt wrote: the undo would clobber a
    value it did not write, so it refuses (409 at the API) — the master and the log are untouched.
    """
    sid = fx.insert_security(db, "UROY")
    change = _adopt(db, sid, "UROY")
    master.set_name(db, sid, "Uranium Royalty Corp. (renamed by hand)")
    db.commit()
    moved = _row(db, sid)

    with pytest.raises(RepairConflict, match="changed since this re-point"):
        cik_change_repo.undo(db, change.id, basis="operator:undo")
    db.rollback()

    assert _row(db, sid) == moved and _count(db, "security_cik_change") == 1
    (standing,) = cik_change_repo.standing_changes(db)
    assert standing.id == change.id and standing.undoable is False
    assert standing.current_cik == "0002143673"


def test_ADOPT_REFUSES_a_moved_master_an_already_adopted_one_and_a_CIK_another_row_holds(db):
    sid = fx.insert_security(db, "UROY")
    before = _row(db, sid)
    # the master no longer holds the CIK the pass saw
    with pytest.raises(RepairConflict, match="changed since the pass"):
        _adopt(db, sid, "UROY", expected_cik="0000000001")
    db.rollback()
    # another master row of the tenant already carries the successor CIK
    other = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, 'UROY', '0002143673', 'Uranium Royalty Corp.', '2026-01-01')",
            (other, DEFAULT_TENANT_ID),
        )
    db.commit()
    with pytest.raises(RepairConflict, match="two rows on one CIK"):
        _adopt(db, sid, "UROY")
    db.rollback()
    assert _row(db, sid) == before and _count(db, "security_cik_change") == 0
    with db.cursor() as cur:
        cur.execute("DELETE FROM security_master WHERE id = %s", (other,))
    db.commit()
    # adopted once; a second adopt has nothing to change
    _adopt(db, sid, "UROY")
    with pytest.raises(RepairConflict, match="already files under"):
        _adopt(db, sid, "UROY", expected_cik="0002143673")
    db.rollback()
    assert _count(db, "security_cik_change") == 1
    with pytest.raises(LookupError):
        _adopt(db, uuid.uuid4(), "UROY")
    db.rollback()


def test_ONE_undo_per_change_never_an_undo_of_an_undo_and_the_log_is_APPEND_ONLY(db):
    sid = fx.insert_security(db, "REAX")
    change = _adopt(db, sid, "REAX")
    undone = cik_change_repo.undo(db, change.id, basis="operator:undo")
    db.commit()
    with pytest.raises(RepairConflict, match="already undone"):
        cik_change_repo.undo(db, change.id, basis="operator:undo")
    db.rollback()
    with pytest.raises(LookupError):
        cik_change_repo.undo(db, undone.id, basis="operator:undo")
    db.rollback()
    with pytest.raises(LookupError):
        cik_change_repo.undo(db, uuid.uuid4(), basis="operator:undo")
    db.rollback()
    with pytest.raises(psycopg.Error):
        with db.cursor() as cur:
            cur.execute(
                "UPDATE security_cik_change SET basis = 'tampered' WHERE id = %s", (change.id,)
            )
    db.rollback()
    assert _count(db, "security_cik_change") == 2


def test_only_the_STANDING_change_undoes_and_a_re_adopt_after_an_undo_is_a_new_adopt(db):
    sid = fx.insert_security(db, "UROY")
    first = _adopt(db, sid, "UROY")
    cik_change_repo.undo(db, first.id, basis="operator:undo")
    db.commit()
    second = _adopt(db, sid, "UROY")
    assert second.id != first.id
    (standing,) = cik_change_repo.standing_changes(db)
    assert standing.id == second.id and standing.undoable is True and standing.ticker == "UROY"
    with pytest.raises(RepairConflict):
        cik_change_repo.undo(db, first.id, basis="operator:undo")  # already undone
    db.rollback()
    assert _count(db, "security_cik_change") == 3


def test_set_cik_refuses_a_malformed_CIK_before_any_write(db):
    sid = fx.insert_security(db, "UROY")
    for bad in ("2143673", "00021436730", "abcdefghij", "0000000000"):
        with pytest.raises(ValueError):
            master.set_cik(db, sid, bad)
    db.rollback()
    assert _row(db, sid)["cik"] == "0001711570"
