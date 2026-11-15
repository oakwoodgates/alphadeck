"""V2 W2c: the steward's six actions on a platform value (``platform_values.steward``), from real rows.

Every action takes the company write lock, re-reads the state under it, appends (never updates), stamps the
company changed in the same transaction and records the steward's EMAIL. Each test counts the TABLES, not only the
read: a refused action writes nothing in any of them.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). A steward action commits on its own
connection and must start with no open transaction, so every seed is committed first.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from db.session import connect, name_session
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.market_time import market_today
from domain.platform_values import ValueState
from domain.scopes import DATA_TENANT_ID
from ingest.shares import ingest_shares_outstanding
from platform_values import state, steward
from platform_values.steward import PlatformValueConflict, PlatformValueInvalid
from repositories import platform_value_repo as repo

STEWARD = "steward@example.test"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
OTHER_REF = "https://www.sec.gov/Archives/edgar/data/7/0003-a.htm"
D1, D2 = date(2026, 3, 31), date(2026, 6, 30)
T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)

TABLES = (
    "fact_shares_outstanding",
    "fact_cash_burn",
    "platform_value_decision",
    "extraction_candidate",
    "steward_pin",
)


@pytest.fixture
def no_wait(monkeypatch):
    """A busy company answers at once (the request wait is 15 s in production)."""
    from domain.settings import get_settings

    monkeypatch.setenv("ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _company(db, ticker: str = "STEW") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", "0000000007", date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _shares(db, sid: UUID, ref: str, d: date, count: float, by: str | None = "auto"):
    fid = ingest_shares_outstanding(
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
    db.commit()
    return fid


def _flag(db, sid: UUID, d: date = D2, *, fact_type: str = "shares_outstanding", **values):
    if not values:
        values = (
            {"shares": 30_000_000}
            if fact_type == "shares_outstanding"
            else {"cash_usd": 9_000_000, "quarterly_burn_usd": 1_500_000}
        )
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=NEW_REF,
        event_date=d,
        located_passages=[{"text": "Class A common stock", "url": NEW_REF}],
        **values,
    )
    db.commit()
    return cid


def _nothing(db, sid: UUID, fact_type: str = "cash_burn") -> UUID:
    cid = repo.append_candidate(
        db, security_id=sid, fact_type=fact_type, outcome="nothing", reason="cash-generative"
    )
    db.commit()
    return cid


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — a fixed list
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _stamps(db, sid: UUID) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_change WHERE security_id = %s", (sid,))
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _state(db, sid: UUID, fact_type: str = "shares_outstanding"):
    db.rollback()
    got = state.read_state(db, sid, fact_type)
    db.rollback()
    return got


def _decision(db, did: UUID) -> dict:
    db.rollback()
    d = repo.decision(db, did)
    db.rollback()
    return d


# --- approve -------------------------------------------------------------------------------------------------


def test_approve_a_flag_writes_the_value_with_the_stewards_email(db):
    sid = _company(db)
    cid = _flag(db, sid)
    before, stamps = _counts(db), _stamps(db, sid)
    out = steward.approve(db, cid, by=STEWARD, note="read the cover myself")
    after = _counts(db)
    assert after["fact_shares_outstanding"] == before["fact_shares_outstanding"] + 1
    assert after["platform_value_decision"] == before["platform_value_decision"] + 1
    assert _stamps(db, sid) > stamps
    got = _state(db, sid)
    assert (got.state, got.ratifier, got.awaiting) == (ValueState.VERIFIED, "steward", None)
    assert got.value["id"] == out.fact_id
    assert float(got.value["shares"]) == 30_000_000
    assert (got.value["ratified_by"], got.value["source_ref"], got.value["valid_from"]) == (
        STEWARD,
        NEW_REF,
        D2,
    )
    d = _decision(db, out.decision_id)
    assert (d["action"], d["candidate_id"], d["fact_id"], d["decided_by"], d["tenant_id"]) == (
        "approve",
        cid,
        out.fact_id,
        STEWARD,
        DATA_TENANT_ID,
    )


def test_approve_a_cash_flag(db):
    sid = _company(db)
    cid = _flag(db, sid, fact_type="cash_burn")
    out = steward.approve(db, cid, by=STEWARD)
    got = _state(db, sid, "cash_burn")
    assert (got.state, got.value["id"]) == (ValueState.VERIFIED, out.fact_id)
    assert (float(got.value["cash_usd"]), float(got.value["quarterly_burn_usd"])) == (
        9_000_000,
        1_500_000,
    )


@pytest.mark.parametrize("kind", ["nothing", "no_value"])
def test_approve_refuses_a_candidate_with_no_value(db, kind):
    sid = _company(db)
    if kind == "nothing":
        cid = _nothing(db, sid)
    else:
        cid = _flag(db, sid, fact_type="cash_burn", cash_usd=9_000_000)  # the burn was not read
    before = _counts(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        steward.approve(db, cid, by=STEWARD)
    assert exc.value.kind == kind
    assert _counts(db) == before


def test_approve_twice_is_refused(db):
    sid = _company(db)
    cid = _flag(db, sid)
    steward.approve(db, cid, by=STEWARD)
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        steward.approve(db, cid, by=STEWARD)
    assert exc.value.kind == "already_decided"
    assert _counts(db) == before


def test_approve_a_stale_candidate_names_the_blocking_date(db):
    sid = _company(db)
    cid = _flag(db, sid, D1)
    _shares(db, sid, OTHER_REF, D2, 10_000_000)  # a newer value on file
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        steward.approve(db, cid, by=STEWARD)
    assert exc.value.kind == "older_than_shown"
    assert D2.isoformat() in str(exc.value) and OTHER_REF in str(exc.value)
    assert _counts(db) == before


def test_approve_an_unknown_candidate_is_a_lookup_error(db):
    with pytest.raises(LookupError):
        steward.approve(db, uuid4(), by=STEWARD)


# --- correct -------------------------------------------------------------------------------------------------


def _correct(db, sid, *, ref=NEW_REF, d=D2, count=40_000_000, **kw):
    return steward.correct(
        db,
        sid,
        "shares_outstanding",
        {"shares": count},
        source_ref=ref,
        event_date=d,
        by=STEWARD,
        **kw,
    )


def test_correct_with_the_same_date_and_another_filing_wins(db):
    """D1 (a): on a date tie the newest write wins, whatever the URLs' order."""
    sid = _company(db)
    _shares(db, sid, "https://www.sec.gov/zzz-sorts-last.htm", D2, 10_000_000)
    out = _correct(db, sid, ref="https://www.sec.gov/aaa-sorts-first.htm")
    got = _state(db, sid)
    assert (got.state, got.value["id"], float(got.value["shares"])) == (
        ValueState.VERIFIED,
        out.fact_id,
        40_000_000,
    )
    assert got.value["source"] == "ratified"
    d = _decision(db, out.decision_id)
    assert (d["action"], d["candidate_id"], d["fact_id"]) == ("correct", None, out.fact_id)


def test_correct_with_an_older_date_is_refused_with_the_blocking_date(db):
    sid = _company(db)
    _shares(db, sid, OLD_REF, D2, 10_000_000)
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        _correct(db, sid, ref=OTHER_REF, d=D1)
    assert exc.value.kind == "older_than_shown"
    assert exc.value.blocking["valid_from"] == D2
    assert D2.isoformat() in str(exc.value)
    assert _counts(db) == before


def test_correct_dated_after_today_is_refused(db):
    sid = _company(db)
    before = _counts(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        _correct(db, sid, d=market_today() + timedelta(days=1))
    assert exc.value.kind == "after_today"
    assert _counts(db) == before


def test_correct_of_the_same_filing_replaces_its_value(db):
    sid = _company(db)
    _shares(db, sid, NEW_REF, D2, 10_000_000)
    out = _correct(db, sid, ref=NEW_REF, d=D2)
    assert _state(db, sid).value["id"] == out.fact_id


def test_correct_with_a_candidate_closes_it(db):
    sid = _company(db)
    cid = _flag(db, sid)
    out = _correct(db, sid, candidate_id=cid, note="the other class added")
    got = _state(db, sid)
    assert (got.state, got.awaiting, got.pending) == (ValueState.VERIFIED, None, None)
    assert _decision(db, out.decision_id)["candidate_id"] == cid
    assert awaiting_ids(db) == []


def awaiting_ids(db) -> list[UUID]:
    db.rollback()
    ids = [i.candidate["id"] for i in state.awaiting(db)]
    db.rollback()
    return ids


def test_correct_with_another_companys_candidate_is_refused(db):
    sid, other = _company(db), _company(db, "OTHR")
    cid = _flag(db, other)
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        _correct(db, sid, candidate_id=cid)
    assert exc.value.kind == "wrong_candidate"
    assert _counts(db) == before


def test_correct_with_a_decided_candidate_is_refused(db):
    sid = _company(db)
    cid = _flag(db, sid)
    steward.dismiss(db, cid, by=STEWARD, reason="the other class")
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        _correct(db, sid, candidate_id=cid)
    assert exc.value.kind == "already_decided"
    assert _counts(db) == before


@pytest.mark.parametrize(
    "ads",
    [
        {"ads_ratio": 5},  # a ratio with no status
        {"ads_ratio_status": "known"},  # known with no ratio
        {"ads_ratio": 5, "ads_ratio_status": "unread"},  # unread with a ratio
    ],
)
def test_correct_refuses_a_bad_ads_pairing(db, ads):
    sid = _company(db)
    before = _counts(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        steward.correct(
            db,
            sid,
            "shares_outstanding",
            {"shares": 1_000_000, **ads},
            source_ref=NEW_REF,
            event_date=D2,
            by=STEWARD,
        )
    assert exc.value.kind == "bad_ads"
    assert _counts(db) == before


def test_correct_cash_burn(db):
    sid = _company(db)
    out = steward.correct(
        db,
        sid,
        "cash_burn",
        {"cash_usd": 12_000_000, "quarterly_burn_usd": 2_000_000},
        source_ref=NEW_REF,
        event_date=D2,
        by=STEWARD,
    )
    got = _state(db, sid, "cash_burn")
    assert (got.state, got.value["id"], got.value["ratified_by"]) == (
        ValueState.VERIFIED,
        out.fact_id,
        STEWARD,
    )


def test_correct_an_unknown_company_is_a_lookup_error(db):
    with pytest.raises(LookupError):
        _correct(db, uuid4())


# --- dismiss -------------------------------------------------------------------------------------------------


def test_dismiss_closes_the_candidate_and_leaves_the_value(db):
    sid = _company(db)
    fid = _shares(db, sid, OLD_REF, D1, 10_000_000)
    cid = _flag(db, sid)
    assert _state(db, sid).pending["id"] == cid
    before = _counts(db)
    out = steward.dismiss(db, cid, by=STEWARD, reason="the other class is not listed")
    after = _counts(db)
    assert after["platform_value_decision"] == before["platform_value_decision"] + 1
    assert after["fact_shares_outstanding"] == before["fact_shares_outstanding"]
    got = _state(db, sid)
    assert (got.value["id"], got.pending) == (fid, None)
    d = _decision(db, out.decision_id)
    assert (d["action"], d["reason"], d["decided_by"]) == (
        "dismiss",
        "the other class is not listed",
        STEWARD,
    )


def test_dismiss_needs_a_reason(db):
    sid = _company(db)
    cid = _flag(db, sid)
    before = _counts(db)
    with pytest.raises(PlatformValueInvalid) as exc:
        steward.dismiss(db, cid, by=STEWARD, reason="   ")
    assert exc.value.kind == "blank_reason"
    assert _counts(db) == before


def test_dismiss_twice_is_refused(db):
    sid = _company(db)
    cid = _flag(db, sid)
    steward.dismiss(db, cid, by=STEWARD, reason="no")
    with pytest.raises(PlatformValueConflict) as exc:
        steward.dismiss(db, cid, by=STEWARD, reason="no")
    assert exc.value.kind == "already_decided"


# --- undo ----------------------------------------------------------------------------------------------------


def test_undo_an_approve_reads_the_earlier_value_and_the_candidate_again(db):
    sid = _company(db)
    old = _shares(db, sid, OLD_REF, D1, 10_000_000)
    cid = _flag(db, sid)
    out = steward.approve(db, cid, by=STEWARD)
    assert _state(db, sid).value["id"] == out.fact_id
    undone = steward.undo(db, out.decision_id, by=STEWARD, reason="misread the cover")
    got = _state(db, sid)
    assert (got.state, got.value["id"], got.pending["id"]) == (ValueState.FROM_FILING, old, cid)
    d = _decision(db, undone.decision_id)
    assert (d["action"], d["revokes"], d["decided_by"]) == ("undo", out.decision_id, STEWARD)
    assert _decision(db, out.decision_id)["undone"] is True


def test_undo_a_correct_of_the_same_filing_reads_the_earlier_version(db):
    sid = _company(db)
    old = _shares(db, sid, NEW_REF, D2, 10_000_000)
    out = _correct(db, sid, ref=NEW_REF, d=D2)
    assert _state(db, sid).value["id"] == out.fact_id
    steward.undo(db, out.decision_id, by=STEWARD)
    got = _state(db, sid)
    assert (got.value["id"], float(got.value["shares"])) == (old, 10_000_000)


def test_undo_a_dismiss_makes_the_candidate_awaiting_again(db):
    sid = _company(db)
    cid = _flag(db, sid)
    out = steward.dismiss(db, cid, by=STEWARD, reason="no")
    assert _state(db, sid).reason == "dismissed"
    steward.undo(db, out.decision_id, by=STEWARD)
    got = _state(db, sid)
    assert (got.state, got.awaiting["id"]) == (ValueState.AWAITING_STEWARD, cid)
    assert awaiting_ids(db) == [cid]


def test_an_undo_cannot_be_undone(db):
    sid = _company(db)
    cid = _flag(db, sid)
    out = steward.dismiss(db, cid, by=STEWARD, reason="no")
    undone = steward.undo(db, out.decision_id, by=STEWARD)
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        steward.undo(db, undone.decision_id, by=STEWARD)
    assert exc.value.kind == "undo_of_undo"
    assert _counts(db) == before


def test_a_second_undo_of_one_decision_is_refused(db):
    sid = _company(db)
    cid = _flag(db, sid)
    out = steward.approve(db, cid, by=STEWARD)
    steward.undo(db, out.decision_id, by=STEWARD)
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        steward.undo(db, out.decision_id, by=STEWARD)
    assert exc.value.kind == "already_undone"
    assert _counts(db) == before


def test_undo_an_unknown_decision_is_a_lookup_error(db):
    with pytest.raises(LookupError):
        steward.undo(db, uuid4(), by=STEWARD)


# --- pin / unpin ---------------------------------------------------------------------------------------------


def test_pin_and_unpin(db):
    sid = _company(db)
    stamps = _stamps(db, sid)
    steward.pin(db, sid, "shares_outstanding", by=STEWARD, note="a dual-class name")
    assert _state(db, sid).pinned and not _state(db, sid, "cash_burn").pinned
    assert _stamps(db, sid) > stamps
    with pytest.raises(PlatformValueConflict) as exc:
        steward.pin(db, sid, "shares_outstanding", by=STEWARD)
    assert exc.value.kind == "already_pinned"
    steward.unpin(db, sid, "shares_outstanding", by=STEWARD)
    assert not _state(db, sid).pinned
    before = _counts(db)
    with pytest.raises(PlatformValueConflict) as exc:
        steward.unpin(db, sid, "shares_outstanding", by=STEWARD)
    assert exc.value.kind == "not_pinned"
    assert _counts(db) == before
    db.rollback()
    pin = repo.latest_pin(db, sid, "shares_outstanding")
    assert (pin["action"], pin["decided_by"], pin["tenant_id"]) == (
        "unpin",
        STEWARD,
        DATA_TENANT_ID,
    )


def test_pin_an_unknown_company_is_a_lookup_error(db):
    with pytest.raises(LookupError):
        steward.pin(db, uuid4(), "shares_outstanding", by=STEWARD)


# --- the lock ------------------------------------------------------------------------------------------------


def test_a_held_company_is_busy_and_nothing_is_written(db, no_wait):
    sid = _company(db)
    cid = _flag(db, sid)
    other = connect()
    name_session(other, "alphadeck-writer:daily-pass")
    try:
        hold = acquire_company_lock(other, sid, wait_s=0)
        before = _counts(db)
        for act in (
            lambda: steward.approve(db, cid, by=STEWARD),
            lambda: _correct(db, sid),
            lambda: steward.dismiss(db, cid, by=STEWARD, reason="no"),
            lambda: steward.pin(db, sid, "shares_outstanding", by=STEWARD),
        ):
            with pytest.raises(CompanyLockBusy):
                act()
        assert _counts(db) == before
        hold.release()
        steward.approve(db, cid, by=STEWARD)  # the company is free again
    finally:
        other.close()


def test_a_running_repair_makes_every_company_busy(db, no_wait):
    from pipeline.run_lock import acquire_repair_lock

    sid = _company(db)
    before = _counts(db)
    repair = acquire_repair_lock(holder="test repair", wait_s=0)
    try:
        with pytest.raises(CompanyLockBusy) as exc:
            _correct(db, sid)
        assert exc.value.gate is True
    finally:
        repair.release()
    assert _counts(db) == before


def test_an_open_transaction_is_refused(db):
    """A steward action owns its transaction: started after the lock, so its write is the newest."""
    sid = _company(db)
    with db.cursor() as cur:
        cur.execute("SELECT 1")
    with pytest.raises(RuntimeError, match="open transaction"):
        _correct(db, sid)
    db.rollback()
