"""V2 W5b: the flag module (``platform_values.flags``) from real rows.

A user's flag says "this shared value looks wrong (or is missing)" to the steward; it never changes a value. What
these tests pin:

- RAISE records what was current on the server, never what a request claims: an applied value's fact id, the
  candidate awaiting the steward, or neither for "no value yet"; an applied value with a newer candidate pending is
  the applied value;
- one open flag per user and value (a conflict that never reaches the allowance charge); a spent allowance writes
  nothing; a withdrawal never refunds, and a flag raised again after it is a new row that charges again;
- WITHDRAW: only the owner, only an open flag; another user's id is "not found", exactly like an unknown id;
- MINE: the caller's own flags only, open first then the newest closed, paged;
- the steward's side, written now for W5c: CLOSE (fixed names a standing approve or correct of the SAME company and
  value; kept needs a note that is not blank), REOPEN (a new open row copying the original's identity; never an
  open, already reopened or user-withdrawn flag, nor over the user's newer open flag), FOR_COMPANY and
  OPEN_BY_COMPANY.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from domain.scopes import DATA_TENANT_ID
from ingest.shares import ingest_shares_outstanding
from platform_values import flags
from repositories import platform_value_repo as pv_repo
from tests.workspaces import new_workspace

STEWARD = "steward@example.test"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
D1, D2 = date(2026, 3, 31), date(2026, 6, 30)
T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _company(db, ticker: str = "FLAG") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000007", date(2026, 1, 1)),
        )
    return sid


def _user(db, email: str | None = None) -> UUID:
    uid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'u', 'x', 'user', %s)",
            (uid, email or f"{uid.hex}@example.test", new_workspace(db, "flags")),
        )
    return uid


def _shares(db, sid: UUID, ref: str, d: date, count: float, by: str | None = "auto") -> UUID:
    return ingest_shares_outstanding(
        db,
        sid,
        shares=count,
        source="cover",
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
        recorded_at=T0,
    )


def _candidate(db, sid: UUID, d: date, fact_type: str = "shares_outstanding") -> UUID:
    return pv_repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=NEW_REF,
        event_date=d,
        shares=30_000_000 if fact_type == "shares_outstanding" else None,
        located_passages=[{"text": "Class A common stock", "url": NEW_REF}],
    )


def _decision(db, sid: UUID, *, action: str = "correct", fact_type="shares_outstanding") -> UUID:
    if action == "dismiss":
        return pv_repo.append_decision(
            db,
            security_id=sid,
            fact_type=fact_type,
            action="dismiss",
            candidate_id=_candidate(db, sid, D2, fact_type),
            reason="no",
            decided_by=STEWARD,
        )
    return pv_repo.append_decision(
        db,
        security_id=sid,
        fact_type=fact_type,
        action=action,
        fact_id=uuid4(),
        decided_by=STEWARD,
    )


class _Charge:
    """The allowance seam: counts its calls and answers ``ok``."""

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.ok


def _raise(
    db, uid: UUID, sid: UUID, value_type: str = "shares_outstanding", note=None, charge=None
):
    return flags.raise_flag(
        db,
        user_id=uid,
        security_id=sid,
        value_type=value_type,
        note=note,
        charge=charge or _Charge(),
    )


def _count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM value_flag")
        return cur.fetchone()["n"]


# --- raise: what was current is the server's ---------------------------------------------------------------------


def test_a_flag_on_an_applied_value_records_its_fact(db):
    sid, uid = _company(db), _user(db)
    fid = _shares(db, sid, OLD_REF, D1, 10_000_000)
    db.commit()
    raised = _raise(db, uid, sid, note="the cover lists two classes")
    row = raised.flag
    assert (row["seen_fact_id"], row["seen_candidate_id"]) == (fid, None)
    assert (row["user_id"], row["security_id"], row["value_type"]) == (
        uid,
        sid,
        "shares_outstanding",
    )
    assert (row["note"], row["closed_at"], row["outcome"]) == (
        "the cover lists two classes",
        None,
        None,
    )
    assert raised.state.value is not None and raised.state.value["id"] == fid


def test_a_flag_on_a_value_awaiting_the_steward_records_the_candidate(db):
    sid, uid = _company(db), _user(db)
    cid = _candidate(db, sid, D2)
    db.commit()
    row = _raise(db, uid, sid).flag
    assert (row["seen_fact_id"], row["seen_candidate_id"]) == (None, cid)


@pytest.mark.parametrize("value_type", ["shares_outstanding", "cash_burn"])
def test_a_flag_on_no_value_yet_records_neither(db, value_type):
    sid, uid = _company(db), _user(db)
    db.commit()
    row = _raise(db, uid, sid, value_type).flag
    assert (row["seen_fact_id"], row["seen_candidate_id"], row["value_type"]) == (
        None,
        None,
        value_type,
    )


def test_an_applied_value_with_a_newer_candidate_pending_is_the_target(db):
    sid, uid = _company(db), _user(db)
    fid = _shares(db, sid, OLD_REF, D1, 10_000_000)
    _candidate(db, sid, D2)  # newer than the value shown: pending, not the value
    db.commit()
    raised = _raise(db, uid, sid)
    assert raised.state.pending is not None
    assert (raised.flag["seen_fact_id"], raised.flag["seen_candidate_id"]) == (fid, None)


def test_a_second_open_flag_on_the_same_value_is_a_conflict_and_never_charges(db):
    sid, uid = _company(db), _user(db)
    _raise(db, uid, sid)
    charge = _Charge()
    with pytest.raises(flags.FlagConflict) as exc:
        _raise(db, uid, sid, charge=charge)
    assert exc.value.kind == "already_open"
    assert charge.calls == 0 and _count(db) == 1


def test_another_value_or_another_user_is_not_a_conflict(db):
    sid, me, other = _company(db), _user(db), _user(db)
    _raise(db, me, sid)
    _raise(db, me, sid, "cash_burn")
    _raise(db, other, sid)  # B's flag does not depend on A's
    assert _count(db) == 3


def test_a_spent_allowance_writes_nothing(db):
    sid, uid = _company(db), _user(db)
    charge = _Charge(ok=False)
    with pytest.raises(flags.FlagAllowanceSpent):
        _raise(db, uid, sid, charge=charge)
    assert charge.calls == 1 and _count(db) == 0


def test_an_empty_note_is_stored_as_none(db):
    sid, uid = _company(db), _user(db)
    assert _raise(db, uid, sid, note="").flag["note"] is None


# --- withdraw ---------------------------------------------------------------------------------------------------


def test_withdraw_closes_it_as_the_users_own_and_a_new_flag_charges_again(db):
    sid, uid = _company(db), _user(db)
    first = _raise(db, uid, sid).flag
    row = flags.withdraw(db, first["id"], user_id=uid)
    assert (row["outcome"], row["closed_by"], row["outcome_note"]) == ("withdrawn", "user", None)
    assert row["closed_at"] is not None
    with pytest.raises(flags.FlagConflict) as exc:
        flags.withdraw(db, first["id"], user_id=uid)
    assert exc.value.kind == "already_closed"
    charge = _Charge()
    again = _raise(db, uid, sid, charge=charge).flag
    assert again["id"] != first["id"] and charge.calls == 1  # a re-raise spends a unit


def test_another_users_flag_is_not_found_like_an_unknown_id(db):
    sid, owner, other = _company(db), _user(db), _user(db)
    fid = _raise(db, owner, sid).flag["id"]
    for flag_id in (fid, uuid4()):
        with pytest.raises(flags.FlagNotFound):
            flags.withdraw(db, flag_id, user_id=other)
    assert flags.mine(db, owner, limit=10, offset=0).items[0]["closed_at"] is None  # untouched


# --- mine ---------------------------------------------------------------------------------------------------------


def test_mine_is_the_callers_own_open_first_then_newest_closed_paged(db):
    me, other = _user(db), _user(db)
    a, b, c = _company(db, "AAA"), _company(db, "BBB"), _company(db, "CCC")
    old_closed = _raise(db, me, a).flag["id"]
    flags.withdraw(db, old_closed, user_id=me)
    with (
        db.cursor() as cur
    ):  # the first close happened earlier than the second (no clock in the order)
        cur.execute("ALTER TABLE value_flag DISABLE TRIGGER value_flag_closed_once")
        cur.execute(
            "UPDATE value_flag SET closed_at = closed_at - interval '1 day' WHERE id = %s",
            (old_closed,),
        )
        cur.execute("ALTER TABLE value_flag ENABLE TRIGGER value_flag_closed_once")
    new_closed = _raise(db, me, b).flag["id"]
    flags.withdraw(db, new_closed, user_id=me)
    still_open = _raise(db, me, c).flag["id"]
    _raise(db, other, a)  # never listed for me
    page = flags.mine(db, me, limit=10, offset=0)
    assert page.total == 3
    assert [r["id"] for r in page.items] == [still_open, new_closed, old_closed]
    assert [r["ticker"] for r in page.items] == ["CCC", "BBB", "AAA"]
    assert [r["id"] for r in flags.mine(db, me, limit=1, offset=1).items] == [new_closed]
    assert flags.mine(db, me, limit=10, offset=3).items == []


# --- the steward's close (W5c's routes call these) ---------------------------------------------------------------


def test_close_as_fixed_links_a_standing_decision_of_the_same_value(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    did = _decision(db, sid)
    row = flags.close(
        db,
        fid,
        outcome="fixed",
        note="corrected to the cover total",
        decision_id=did,
        closed_by=STEWARD,
    )
    assert (row["outcome"], row["fixed_decision_id"], row["closed_by"], row["outcome_note"]) == (
        "fixed",
        did,
        STEWARD,
        "corrected to the cover total",
    )


def test_close_as_fixed_refuses_a_decision_that_does_not_fix_this_value(db):
    sid, other_sid, uid = _company(db), _company(db, "OTH"), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    undone = _decision(db, sid)
    pv_repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=undone,
        decided_by=STEWARD,
    )
    wrong = {
        "another company": _decision(db, other_sid),
        "another value": _decision(db, sid, fact_type="cash_burn"),
        "an undone decision": undone,
        "a dismissal": _decision(db, sid, action="dismiss"),
        "no such decision": uuid4(),
    }
    for what, did in wrong.items():
        with pytest.raises(flags.FlagConflict) as exc:
            flags.close(db, fid, outcome="fixed", note=None, decision_id=did, closed_by=STEWARD)
        assert exc.value.kind == "wrong_decision", what
    with pytest.raises(flags.FlagInvalid) as exc:
        flags.close(db, fid, outcome="fixed", note=None, decision_id=None, closed_by=STEWARD)
    assert exc.value.kind == "fixed_needs_decision"
    assert flags.mine(db, uid, limit=5, offset=0).items[0]["closed_at"] is None


@pytest.mark.parametrize("note", [None, "", "   \n\t "])
def test_close_as_kept_needs_a_note_that_is_not_blank(db, note):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    with pytest.raises(flags.FlagInvalid) as exc:
        flags.close(db, fid, outcome="kept", note=note, decision_id=None, closed_by=STEWARD)
    assert exc.value.kind == "blank_note"


def test_close_as_kept_names_no_decision_and_closes_once(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    with pytest.raises(flags.FlagInvalid) as exc:
        flags.close(
            db, fid, outcome="kept", note="right", decision_id=_decision(db, sid), closed_by=STEWARD
        )
    assert exc.value.kind == "decision_on_kept"
    row = flags.close(
        db,
        fid,
        outcome="kept",
        note="  the filing states two classes  ",
        decision_id=None,
        closed_by=STEWARD,
    )
    assert (row["outcome"], row["outcome_note"]) == ("kept", "the filing states two classes")
    with pytest.raises(flags.FlagConflict) as exc:
        flags.close(db, fid, outcome="kept", note="again", decision_id=None, closed_by=STEWARD)
    assert exc.value.kind == "already_closed"
    with pytest.raises(flags.FlagNotFound):
        flags.close(db, uuid4(), outcome="kept", note="x", decision_id=None, closed_by=STEWARD)


def test_a_withdrawn_flag_cannot_be_closed_by_the_steward(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    flags.withdraw(db, fid, user_id=uid)
    with pytest.raises(flags.FlagConflict):
        flags.close(db, fid, outcome="kept", note="x", decision_id=None, closed_by=STEWARD)


# --- reopen -------------------------------------------------------------------------------------------------------


def test_reopen_appends_a_new_open_row_copying_the_original(db):
    sid, uid = _company(db), _user(db)
    cid = _candidate(db, sid, D2)
    db.commit()
    original = _raise(db, uid, sid, note="looks like class A only").flag
    flags.close(
        db,
        original["id"],
        outcome="kept",
        note="it is the total",
        decision_id=None,
        closed_by=STEWARD,
    )
    row = flags.reopen(db, original["id"], reopened_by=STEWARD)
    assert row["id"] != original["id"] and row["reopens"] == original["id"]
    for key in ("user_id", "security_id", "value_type", "value_ref", "seen_fact_id", "note"):
        assert row[key] == original[key], key
    assert row["seen_candidate_id"] == cid
    assert (row["closed_at"], row["outcome"]) == (None, None)
    assert row["reopened_by"] == STEWARD  # who reopened it (0211); when = its opened_at
    old = flags.for_company(db, sid).closed[0]
    assert (old["id"], old["outcome"], old["outcome_note"]) == (
        original["id"],
        "kept",
        "it is the total",
    )  # the closed row is unchanged


def test_reopen_refuses_an_open_a_reopened_and_a_withdrawn_flag(db):
    sid, uid = _company(db), _user(db)
    open_one = _raise(db, uid, sid).flag["id"]
    with pytest.raises(flags.FlagConflict) as exc:
        flags.reopen(db, open_one, reopened_by=STEWARD)
    assert exc.value.kind == "not_closed"
    flags.close(db, open_one, outcome="kept", note="right", decision_id=None, closed_by=STEWARD)
    again = flags.reopen(db, open_one, reopened_by=STEWARD)
    flags.close(
        db, again["id"], outcome="kept", note="still right", decision_id=None, closed_by=STEWARD
    )
    with pytest.raises(flags.FlagConflict) as exc:
        flags.reopen(db, open_one, reopened_by=STEWARD)
    assert exc.value.kind == "already_reopened"
    withdrawn = _raise(db, uid, sid, "cash_burn").flag["id"]
    flags.withdraw(db, withdrawn, user_id=uid)
    with pytest.raises(flags.FlagConflict) as exc:
        flags.reopen(db, withdrawn, reopened_by=STEWARD)
    assert exc.value.kind == "withdrawn_by_user"
    with pytest.raises(flags.FlagNotFound):
        flags.reopen(db, uuid4(), reopened_by=STEWARD)


def test_reopen_refuses_when_the_user_has_a_newer_open_flag_on_the_value(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    flags.close(db, fid, outcome="kept", note="right", decision_id=None, closed_by=STEWARD)
    _raise(db, uid, sid)  # the user flagged it again
    with pytest.raises(flags.FlagConflict) as exc:
        flags.reopen(db, fid, reopened_by=STEWARD)
    assert exc.value.kind == "user_has_open_flag"
    assert _count(db) == 2


def test_reopen_records_a_steward(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    flags.close(db, fid, outcome="kept", note="right", decision_id=None, closed_by=STEWARD)
    for by in ("", "user", "auto"):
        with pytest.raises(ValueError):
            flags.reopen(db, fid, reopened_by=by)
    assert _count(db) == 1


def test_reopen_refusal_names_why_a_flag_cannot_be_reopened(db):
    sid, a, b = _company(db), _user(db), _user(db)
    kept = _raise(db, a, sid).flag["id"]
    flags.close(db, kept, outcome="kept", note="right", decision_id=None, closed_by=STEWARD)
    withdrawn = _raise(db, a, sid, "cash_burn").flag["id"]
    flags.withdraw(db, withdrawn, user_id=a)
    reopened = _raise(db, b, sid).flag["id"]
    flags.close(db, reopened, outcome="kept", note="right", decision_id=None, closed_by=STEWARD)
    newer = flags.reopen(db, reopened, reopened_by=STEWARD)
    got = flags.for_company(db, sid)
    assert got.reopened_as == {reopened: newer["id"]}
    by = {r["id"]: r for r in got.open + got.closed}
    assert flags.reopen_refusal(by[kept], got) is None
    assert flags.reopen_refusal(by[withdrawn], got) == "withdrawn_by_user"
    assert flags.reopen_refusal(by[reopened], got) == "already_reopened"
    assert flags.reopen_refusal(by[newer["id"]], got) == "not_closed"
    _raise(db, a, sid)  # A flags the share count again: the kept one is no longer reopened
    got = flags.for_company(db, sid)
    kept_row = next(r for r in got.closed if r["id"] == kept)
    assert flags.reopen_refusal(kept_row, got) == "user_has_open_flag"


# --- the steward's close under the company lock -------------------------------------------------------------------


def test_steward_close_takes_the_company_lock_and_commits(db):
    from db.session import connect
    from db.write_lock import CompanyLockBusy, acquire_company_lock

    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    db.commit()
    with connect() as other:
        held = acquire_company_lock(other, sid, wait_s=0)
        try:
            with pytest.raises(CompanyLockBusy):
                flags.steward_close(
                    db, fid, outcome="kept", note="right", decision_id=None, by=STEWARD, wait_s=0
                )
        finally:
            held.release()
    assert flags.mine(db, uid, limit=5, offset=0).items[0]["closed_at"] is None
    db.rollback()
    row = flags.steward_close(
        db, fid, outcome="kept", note="right", decision_id=None, by=STEWARD, wait_s=0
    )
    assert row["outcome"] == "kept"
    with connect() as other, other.cursor() as cur:  # committed
        cur.execute("SELECT outcome FROM value_flag WHERE id = %s", (fid,))
        assert cur.fetchone()["outcome"] == "kept"


def test_steward_close_needs_an_idle_connection_and_a_steward(db):
    sid, uid = _company(db), _user(db)
    fid = _raise(db, uid, sid).flag["id"]
    with pytest.raises(RuntimeError):  # a transaction is open
        flags.steward_close(
            db, fid, outcome="kept", note="right", decision_id=None, by=STEWARD, wait_s=0
        )
    db.commit()
    with pytest.raises(ValueError):
        flags.steward_close(
            db, fid, outcome="kept", note="right", decision_id=None, by="user", wait_s=0
        )
    with pytest.raises(flags.FlagNotFound):
        flags.steward_close(
            db, uuid4(), outcome="kept", note="right", decision_id=None, by=STEWARD, wait_s=0
        )


# --- the steward's reads ------------------------------------------------------------------------------------------


def test_for_company_lists_open_then_closed_paged_with_the_users_email(db):
    sid, other_sid = _company(db), _company(db, "OTH")
    a, b = _user(db, "a@flags.test"), _user(db, "b@flags.test")
    closed = _raise(db, a, sid).flag["id"]
    flags.withdraw(db, closed, user_id=a)
    open_b = _raise(db, b, sid, "cash_burn").flag["id"]
    _raise(db, a, other_sid)  # another company: never listed
    got = flags.for_company(db, sid, closed_limit=20, closed_offset=0)
    assert [(r["id"], r["user_email"]) for r in got.open] == [(open_b, "b@flags.test")]
    assert [(r["id"], r["user_email"]) for r in got.closed] == [(closed, "a@flags.test")]
    assert got.closed_total == 1
    assert flags.for_company(db, sid, closed_limit=20, closed_offset=1).closed == []


def test_open_by_company_counts_open_flags_per_company(db):
    s1, s2, s3 = _company(db, "S1"), _company(db, "S2"), _company(db, "S3")
    a, b = _user(db), _user(db)
    first = _raise(db, a, s1).flag
    _raise(db, b, s1)
    _raise(db, a, s1, "cash_burn")
    _raise(db, a, s2)
    gone = _raise(db, a, s3).flag["id"]
    flags.withdraw(db, gone, user_id=a)
    got = flags.open_by_company(db)
    assert set(got) == {s1, s2}
    assert (got[s1].count, got[s1].shares, got[s1].cash_burn) == (3, 2, 1)
    assert got[s1].oldest_opened_at == first["opened_at"]
    assert set(flags.open_by_company(db, [s2, s3])) == {s2}
    assert flags.open_by_company(db, []) == {}


def test_the_lock_key_is_per_user_and_value(db):
    """Two different users or values never share a lock key (a shared key would only slow them, but the key
    names exactly what one flag serializes)."""
    sid, a, b = _company(db), _user(db), _user(db)
    keys = {
        flags.lock_key(a, sid, "shares_outstanding"),
        flags.lock_key(a, sid, "cash_burn"),
        flags.lock_key(b, sid, "shares_outstanding"),
    }
    assert len(keys) == 3
    assert flags.lock_key(a, sid, "shares_outstanding") == flags.lock_key(
        a, sid, "shares_outstanding"
    )
