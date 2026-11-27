"""The split-filing dismissal log (``repositories/split_filing_dismissal_repo.py``, migration 0218; framework
completion F1-2): the steward's append-only, undoable \"not a split to list\" on a filing the nightly could not place.

- ``dismiss`` appends one row; a second dismiss of a filing whose dismissal stands conflicts; ``undo`` appends an
  ``undo`` naming it and the filing stands undismissed again; a second undo conflicts; an unknown id is a
  ``LookupError``; a blank reason (or a non-operator author, or a malformed accession) is a ``ValueError``.
- ``standing`` is PURE: a dismissal stands while no undo names it, and the newest standing dismissal of a filing
  wins, whatever order the rows arrive in.
- Tenant scoping per the scope map (``domain.scopes``: DATA): a write under a workspace is refused before it writes,
  and a read names one tenant and never sees a row stored under another.
- Display only: no ``mark_changed`` (no dashboard shows unplaced filings), and no engine source names the table or
  this repository (``editions.fingerprint.engine_files``), so it is never a call input.

DB-backed except the pure and structural tests (the shared ``db`` fixture; skips when Postgres is unreachable).
No workspace here is the data tenant (``…0001``).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from db.change_stamp import stamp_map
from domain.scopes import DATA_TENANT_ID
from editions import fingerprint as fp
from repositories import split_filing_dismissal_repo as repo
from repositories.split_filing_dismissal_repo import FilingDismissalConflict
from tests.workspaces import new_workspace

ACC = "0001628280-26-066700"  # META's 8-K of the 2026-11-25 artifact
ACC2 = "0001493152-26-052687"
W = uuid.UUID("00000000-0000-0000-0000-0000000000c1")  # a workspace, never the data tenant


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "2025-01-01"),
        )
    db.commit()
    return sid


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM split_filing_dismissal")
        return cur.fetchone()["n"]


def _dismiss(db, sid, accession=ACC, **kw):
    kw.setdefault("reason", "not a split to list, said the steward")
    kw.setdefault("detail", {"form": "8-K", "reason": "ratio but no date"})
    return repo.dismiss(db, tenant_id=DATA_TENANT_ID, security_id=sid, accession=accession, **kw)


# --- the writers ------------------------------------------------------------------------------------------------


def test_dismiss_appends_one_row_and_it_stands(db):
    sid = _security(db, "META")
    row = _dismiss(db, sid, reason="  an authorized range, not a split  ")
    db.commit()
    assert row["action"] == "dismiss" and row["undoes"] is None
    assert row["author"] == repo.AUTHOR_DISMISS == "operator:dismiss"
    assert row["reason"] == "an authorized range, not a split"  # stripped
    assert row["accession"] == ACC and row["security_id"] == sid
    assert row["tenant_id"] == DATA_TENANT_ID
    assert row["detail"] == {"form": "8-K", "reason": "ratio but no date"}
    got = repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID)
    assert set(got) == {(sid, ACC)}
    assert got[(sid, ACC)]["id"] == row["id"]
    assert _count(db) == 1


def test_a_second_dismiss_of_a_standing_filing_conflicts_and_writes_nothing(db):
    sid = _security(db, "META")
    _dismiss(db, sid)
    db.commit()
    with pytest.raises(FilingDismissalConflict):
        _dismiss(db, sid)
    db.rollback()
    assert _count(db) == 1
    # another filing of the same company is its own key
    _dismiss(db, sid, accession=ACC2)
    db.commit()
    assert set(repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID)) == {(sid, ACC), (sid, ACC2)}


def test_undo_appends_an_undo_and_the_filing_is_back_then_a_new_dismissal_stands(db):
    sid = _security(db, "META")
    first = _dismiss(db, sid)
    db.commit()
    undo = repo.undo(db, first["id"], tenant_id=DATA_TENANT_ID, reason="  listed it after all ")
    db.commit()
    assert undo["action"] == "undo" and undo["undoes"] == first["id"]
    assert undo["author"] == repo.AUTHOR_UNDO == "operator:undo"
    assert undo["reason"] == "listed it after all"
    assert (undo["security_id"], undo["accession"]) == (sid, ACC)
    assert repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID) == {}
    # dismissed again after the undo: the newest dismissal of the filing is the one that stands
    second = _dismiss(db, sid, reason="dismissed again")
    db.commit()
    got = repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID)
    assert got[(sid, ACC)]["id"] == second["id"]
    assert _count(db) == 3  # append-only: nothing rewritten


def test_a_second_undo_conflicts(db):
    sid = _security(db, "META")
    row = _dismiss(db, sid)
    repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="undone")
    db.commit()
    with pytest.raises(FilingDismissalConflict):
        repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="undone twice")
    db.rollback()
    assert _count(db) == 2


def test_an_unknown_id_or_an_undo_row_is_a_lookup_error(db):
    sid = _security(db, "META")
    row = _dismiss(db, sid)
    undo = repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="undone")
    db.commit()
    with pytest.raises(LookupError):
        repo.undo(db, uuid.uuid4(), tenant_id=DATA_TENANT_ID, reason="x")
    with pytest.raises(LookupError):  # an undo is not a dismissal: there is nothing to take back
        repo.undo(db, undo["id"], tenant_id=DATA_TENANT_ID, reason="x")
    db.rollback()
    assert _count(db) == 2


@pytest.mark.parametrize("reason", ["", "   ", "\n"])
def test_a_blank_reason_is_a_value_error(db, reason):
    sid = _security(db, "META")
    with pytest.raises(ValueError):
        _dismiss(db, sid, reason=reason)
    row = _dismiss(db, sid)
    db.commit()
    with pytest.raises(ValueError):
        repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason=reason)
    db.rollback()
    assert _count(db) == 1


def test_a_non_operator_author_or_a_malformed_accession_is_a_value_error(db):
    sid = _security(db, "META")
    with pytest.raises(ValueError):
        _dismiss(db, sid, author="system:nightly")
    with pytest.raises(ValueError):
        _dismiss(db, sid, accession="0001628280-26-06670")
    row = _dismiss(db, sid)
    db.commit()
    with pytest.raises(ValueError):
        repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="x", author="system:nightly")
    db.rollback()
    assert _count(db) == 1


def test_each_write_takes_a_per_filing_advisory_lock(db):
    """Two clicks on one filing serialize: the write holds a transaction-scoped advisory lock until commit."""
    sid = _security(db, "META")

    def advisory_locks() -> int:
        with db.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            )
            return cur.fetchone()["n"]

    assert advisory_locks() == 0
    row = _dismiss(db, sid)
    assert advisory_locks() == 1
    db.commit()
    assert advisory_locks() == 0
    repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="undone")
    assert advisory_locks() == 1
    db.commit()


# --- tenant scoping (domain.scopes: DATA) -----------------------------------------------------------------------


def test_a_write_under_a_workspace_is_refused_and_writes_nothing(db):
    new_workspace(db, "workspace C1", workspace_id=W)
    sid = _security(db, "META")
    with pytest.raises(ValueError, match="data tenant"):
        repo.dismiss(db, tenant_id=W, security_id=sid, accession=ACC, reason="x")
    db.rollback()
    row = _dismiss(db, sid)
    db.commit()
    with pytest.raises(ValueError, match="data tenant"):
        repo.undo(db, row["id"], tenant_id=W, reason="x")
    db.rollback()
    assert _count(db) == 1


def test_a_read_names_one_tenant_and_never_sees_another_tenants_row(db):
    """A POISON row stored under a workspace (no writer puts one there; planted raw) is invisible to the data
    tenant's read, does not block the data tenant's dismissal, and cannot be undone through the data tenant.
    """
    new_workspace(db, "workspace C1", workspace_id=W)
    sid = _security(db, "META")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO split_filing_dismissal (tenant_id, security_id, accession, action, reason, author) "
            "VALUES (%s, %s, %s, 'dismiss', 'a stray', 'operator:dismiss') RETURNING id",
            (W, sid, ACC),
        )
        poison = cur.fetchone()["id"]
    db.commit()
    assert repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID) == {}
    assert set(repo.standing_dismissals(db, tenant_id=W)) == {
        (sid, ACC)
    }  # the read is tenant-filtered
    row = _dismiss(db, sid)  # no conflict with the stray
    db.commit()
    assert repo.standing_dismissals(db, tenant_id=DATA_TENANT_ID)[(sid, ACC)]["id"] == row["id"]
    with pytest.raises(LookupError):
        repo.undo(db, poison, tenant_id=DATA_TENANT_ID, reason="x")
    db.rollback()


# --- display only -----------------------------------------------------------------------------------------------


def test_no_write_moves_the_companys_change_stamp(db):
    """No ``mark_changed``: no dashboard shows unplaced filings, so a dismissal leaves every cache alone."""
    sid = _security(db, "META")
    row = _dismiss(db, sid)
    db.commit()
    repo.undo(db, row["id"], tenant_id=DATA_TENANT_ID, reason="undone")
    db.commit()
    assert stamp_map(db).get(sid) is None
    db.rollback()


def test_no_engine_source_names_the_log_or_its_repository():
    """STRUCTURAL (the LISTED evidence): of every file the editions fingerprint hashes, only the scope map names
    the table, and none names this repository: a dismissal is never a call input."""
    hits = {}
    for path in fp.engine_files():
        text = path.read_text(encoding="utf-8")
        names = [n for n in ("split_filing_dismissal", "split_filing_dismissal_repo") if n in text]
        if names:
            hits[path.relative_to(fp._ROOT).as_posix()] = names
    assert hits == {"domain/scopes.py": ["split_filing_dismissal"]}


# --- standing(): pure ---------------------------------------------------------------------------------------------


def _row(action, sid, accession, day, *, undoes=None, rid=None):
    return {
        "id": rid or uuid.uuid4(),
        "security_id": sid,
        "accession": accession,
        "action": action,
        "undoes": undoes,
        "recorded_at": datetime(2026, 11, day, 12, tzinfo=timezone.utc),
    }


def test_standing_is_pure_and_an_undo_takes_a_dismissal_back():
    sid = uuid.uuid4()
    d = _row("dismiss", sid, ACC, 1)
    assert repo.standing([]) == {}
    assert repo.standing([d]) == {(sid, ACC): d}
    assert repo.standing([d, _row("undo", sid, ACC, 2, undoes=d["id"])]) == {}


def test_standing_the_newest_dismissal_of_a_filing_wins_in_any_order():
    sid, other = uuid.uuid4(), uuid.uuid4()
    old, new = _row("dismiss", sid, ACC, 1), _row("dismiss", sid, ACC, 3)
    elsewhere = _row("dismiss", other, ACC, 2)
    sibling = _row("dismiss", sid, ACC2, 2)
    for rows in ([old, new, elsewhere, sibling], [sibling, new, elsewhere, old]):
        got = repo.standing(rows)
        assert got == {(sid, ACC): new, (other, ACC): elsewhere, (sid, ACC2): sibling}
    # the newest undone: the older one, which no undo names, stands
    undo_new = _row("undo", sid, ACC, 4, undoes=new["id"])
    assert repo.standing([undo_new, new, old])[(sid, ACC)] is old
