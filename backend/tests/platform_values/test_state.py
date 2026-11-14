"""V2 W2a: the state read of a platform value (``platform_values.state``) and its repository
(``repositories.platform_value_repo``), from real rows.

The state of a company's share count or cash/burn is derived ONCE (``domain.platform_values.derive_state``) from:
the value the meters show (the data tenant's per-filing rows through the same point-in-time accessor, then the one
pick), the newest candidate, the standing steward decision on it, and the newest pin event. A row stored under a
workspace is never read; an undone approve falls back to the earlier value and reopens its candidate.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every test commits what it reads (the
state read builds its own point-in-time view on the same connection) and the next test's fixture truncates.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from db.bitemporal import append_fact
from domain.platform_values import ValueState
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import ingest_shares_outstanding
from platform_values import state
from repositories import platform_value_repo as repo
from tests.workspaces import new_workspace

STEWARD = "steward@example.test"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
D1, D2 = date(2026, 3, 31), date(2026, 6, 30)
T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _company(db, ticker: str = "STATE") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000007", date(2026, 1, 1)),
        )
    return sid


def _shares(db, sid: UUID, ref: str, d: date, count: float, by: str | None = "auto", at: int = 0):
    return ingest_shares_outstanding(
        db,
        sid,
        shares=count,
        source="cover",
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
        recorded_at=T0 + timedelta(hours=at),
    )


def _cash(db, sid: UUID, ref: str, d: date, cash: float, by: str | None = "auto"):
    return ingest_cash_burn(
        db,
        sid,
        cash_usd=cash,
        quarterly_burn_usd=1_000_000.0,
        source="10-Q",
        source_ref=ref,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
        recorded_at=T0,
    )


def _flag(db, sid: UUID, d: date, fact_type: str = "shares_outstanding", ref: str = NEW_REF):
    return repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=ref,
        event_date=d,
        shares=30_000_000 if fact_type == "shares_outstanding" else None,
        located_passages=[{"text": "Class A common stock", "url": ref}],
    )


def _read(db, sid: UUID, fact_type: str = "shares_outstanding"):
    db.commit()
    return state.read_state(db, sid, fact_type)


# --- each state from real rows -------------------------------------------------------------------------------


@pytest.mark.parametrize("fact_type", ["shares_outstanding", "cash_burn"])
def test_no_value_and_no_candidate(db, fact_type):
    got = _read(db, _company(db), fact_type)
    assert (got.state, got.reason, got.value, got.awaiting) == (
        ValueState.NO_VALUE,
        "not-extracted",
        None,
        None,
    )


def test_from_the_filing(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000)
    _cash(db, sid, OLD_REF, D1, 5_000_000)
    for fact_type in ("shares_outstanding", "cash_burn"):
        got = _read(db, sid, fact_type)
        assert (got.state, got.ratifier, got.value["source_ref"]) == (
            ValueState.FROM_FILING,
            "machine",
            OLD_REF,
        )


@pytest.mark.parametrize(("by", "ratifier"), [("operator", "carried"), (STEWARD, "steward")])
def test_verified(db, by, ratifier):
    """A V1 row stamped ``operator`` (carried over) and a steward's own both read ``verified``."""
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000, by=by)
    _cash(db, sid, OLD_REF, D1, 5_000_000, by=by)
    for fact_type in ("shares_outstanding", "cash_burn"):
        got = _read(db, sid, fact_type)
        assert (got.state, got.ratifier) == (ValueState.VERIFIED, ratifier)


def test_a_v1_null_row_reads_from_the_filing(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000, by=None)
    assert _read(db, sid).state is ValueState.FROM_FILING


def test_awaiting_the_steward(db):
    sid = _company(db)
    cid = _flag(db, sid, D2)
    got = _read(db, sid)
    assert (got.state, got.value, got.meter_value) == (ValueState.AWAITING_STEWARD, None, None)
    assert got.awaiting["id"] == cid
    assert got.awaiting["located_passages"] == [{"text": "Class A common stock", "url": NEW_REF}]


def test_nothing_found_reads_its_reason(db):
    sid = _company(db)
    repo.append_candidate(
        db, security_id=sid, fact_type="cash_burn", outcome="nothing", reason="cash-generative"
    )
    got = _read(db, sid, "cash_burn")
    assert (got.state, got.reason) == (ValueState.NO_VALUE, "cash-generative")
    assert (
        _read(db, sid, "shares_outstanding").reason == "not-extracted"
    )  # the other type is untouched


def test_a_row_stored_under_a_workspace_is_never_read(db):
    sid = _company(db)
    workspace = new_workspace(db, "a stray workspace")
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": workspace,
            "security_id": sid,
            "shares": 99,
            "source": "cover",
            "source_ref": OLD_REF,
            "valid_from": D1,
            "ratified_by": "operator",
        },
    )
    assert _read(db, sid).state is ValueState.NO_VALUE


def test_a_newer_flag_beside_a_value_is_pending_and_an_older_one_is_not(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000)
    older = _flag(db, sid, date(2025, 12, 31), ref="https://www.sec.gov/x/older.htm")
    got = _read(db, sid)
    assert (got.state, got.pending) == (ValueState.FROM_FILING, None)
    newer = _flag(db, sid, D2)
    got = _read(db, sid)
    assert (got.state, got.pending["id"], got.meter_value["source_ref"]) == (
        ValueState.FROM_FILING,
        newer,
        OLD_REF,
    )
    assert older != newer


def test_a_dismissed_flag_with_no_value_reads_dismissed(db):
    sid = _company(db)
    cid = _flag(db, sid, D2)
    repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="dismiss",
        candidate_id=cid,
        reason="the other class",
        decided_by=STEWARD,
    )
    got = _read(db, sid)
    assert (got.state, got.reason, got.awaiting) == (ValueState.NO_VALUE, "dismissed", None)


def _approve(db, sid: UUID, cid: UUID) -> tuple[UUID, UUID]:
    fid = _shares(db, sid, NEW_REF, D2, 30_000_000, by=STEWARD, at=24)
    did = repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="approve",
        candidate_id=cid,
        fact_id=fid,
        decided_by=STEWARD,
    )
    return fid, did


def test_an_undone_approve_reads_the_fallback_and_the_candidate_again(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000)
    cid = _flag(db, sid, D2)
    fid, did = _approve(db, sid, cid)
    got = _read(db, sid)
    assert (got.state, got.ratifier, got.value["id"], got.pending) == (
        ValueState.VERIFIED,
        "steward",
        fid,
        None,
    )
    repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=did,
        reason="misread the cover",
        decided_by=STEWARD,
    )
    got = _read(db, sid)
    assert (got.state, got.value["source_ref"], got.pending["id"]) == (
        ValueState.FROM_FILING,
        OLD_REF,
        cid,
    )


def test_an_undone_approve_with_nothing_else_on_file_is_awaiting_again(db):
    sid = _company(db)
    cid = _flag(db, sid, D2)
    _, did = _approve(db, sid, cid)
    assert _read(db, sid).state is ValueState.VERIFIED
    repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=did,
        decided_by=STEWARD,
    )
    got = _read(db, sid)
    assert (got.state, got.awaiting["id"]) == (ValueState.AWAITING_STEWARD, cid)


def test_the_pin_rides_along(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D1, 10_000_000)
    repo.append_pin(
        db, security_id=sid, fact_type="shares_outstanding", action="pin", decided_by=STEWARD
    )
    assert _read(db, sid).pinned
    assert not _read(db, sid, "cash_burn").pinned
    repo.append_pin(
        db, security_id=sid, fact_type="shares_outstanding", action="unpin", decided_by=STEWARD
    )
    assert not _read(db, sid).pinned


# --- the company read and the awaiting list ------------------------------------------------------------------


def test_read_company_carries_versions_candidates_and_decisions(db):
    sid = _company(db, "FULL")
    _shares(db, sid, OLD_REF, D1, 10_000_000)
    cid = _flag(db, sid, D2)
    fid, did = _approve(db, sid, cid)
    db.commit()  # a later transaction: the undo is recorded after the approve (now() is per transaction)
    undo = repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=did,
        decided_by=STEWARD,
    )
    db.commit()
    company = state.read_company(db, sid)
    assert (company.security_id, company.ticker, company.name) == (sid, "FULL", "FULL Corp")
    shares, cash = company.shares, company.cash_burn
    assert shares.state.state is ValueState.FROM_FILING
    assert [(v["source_ref"], v["set_aside"]) for v in shares.versions] == [
        (OLD_REF, False),
        (NEW_REF, True),
    ]
    assert [c["id"] for c in shares.candidates] == [cid]
    assert [(d["id"], d["action"], d["undone"]) for d in shares.decisions] == [
        (undo, "undo", False),
        (did, "approve", True),
    ]
    assert (cash.state.state, cash.versions, cash.candidates, cash.decisions) == (
        ValueState.NO_VALUE,
        [],
        [],
        [],
    )
    assert fid in {v["id"] for v in shares.versions}


def test_unknown_company_raises_lookup_error(db):
    with pytest.raises(LookupError):
        state.read_company(db, uuid4())


def test_the_awaiting_list_holds_open_flags_newest_first(db):
    waiting = _company(db, "WAIT")
    pending = _company(db, "PEND")
    decided = _company(db, "DONE")
    stale = _company(db, "STALE")
    first = _flag(db, waiting, D2)
    db.commit()  # a later transaction: the next candidates are recorded after this one
    _shares(db, pending, OLD_REF, D1, 10_000_000)
    second = _flag(db, pending, D2)
    done = _flag(db, decided, D2)
    repo.append_decision(
        db,
        security_id=decided,
        fact_type="shares_outstanding",
        action="dismiss",
        candidate_id=done,
        reason="no",
        decided_by=STEWARD,
    )
    _shares(db, stale, NEW_REF, D2, 10_000_000)
    _flag(db, stale, D1, ref=OLD_REF)  # older than the value shown: not awaiting anything
    db.commit()
    items = state.awaiting(db, limit=200)
    assert [(i.ticker, i.candidate["id"], i.state.state) for i in items] == [
        ("PEND", second, ValueState.FROM_FILING),
        ("WAIT", first, ValueState.AWAITING_STEWARD),
    ]
    assert [i.ticker for i in state.awaiting(db, limit=1)] == ["PEND"]


# --- the repository's write guard ----------------------------------------------------------------------------


def test_the_repository_writes_the_data_tenant_only(db):
    sid = _company(db)
    workspace = new_workspace(db, "not the data tenant")
    with pytest.raises(ValueError, match="data tenant"):
        repo.append_candidate(
            db,
            security_id=sid,
            fact_type="cash_burn",
            outcome="nothing",
            reason="x",
            tenant_id=workspace,
        )
    with pytest.raises(ValueError, match="data tenant"):
        repo.append_decision(
            db,
            security_id=sid,
            fact_type="cash_burn",
            action="correct",
            fact_id=uuid4(),
            decided_by=STEWARD,
            tenant_id=workspace,
        )
    with pytest.raises(ValueError, match="data tenant"):
        repo.append_pin(
            db,
            security_id=sid,
            fact_type="cash_burn",
            action="pin",
            decided_by=STEWARD,
            tenant_id=workspace,
        )


def test_stored_refs_include_set_aside_versions(db):
    """The machine's "already on file" reads every stored version, so an undone approve's filing is never
    re-applied."""
    sid = _company(db)
    cid = _flag(db, sid, D2)
    _, did = _approve(db, sid, cid)
    repo.append_decision(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        action="undo",
        revokes=did,
        decided_by=STEWARD,
    )
    db.commit()
    assert repo.stored_source_refs(db, sid, "shares_outstanding") == {NEW_REF}
    assert repo.stored_source_refs(db, sid, "cash_burn") == set()
