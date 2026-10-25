"""The split list's store (migration 0052) — append-only for real, the as-of read on both clocks, and every
operator verb with its inverse. Every count below is ``count(*)`` of the TABLE, not of the read (the read
dedups, so a duplicate append would hide behind it). The versions are the matcher's verdicts on REAL
Massive.com rows and REAL filings (``tests/fixtures/splits``). DB-backed."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID
from domain.splits import (
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_REVOKED,
    VIA_AUTO,
    VIA_OPERATOR,
    decide,
)
from pipeline.provision_tenant import provision_tenant
from repositories import split_repo
from repositories.split_repo import SplitConflict
from tests.domain.test_splits import sec
from tests.split_fixtures import polygon_row

ASOF = date(2026, 10, 20)
OTHER_TENANT = uuid.UUID("00000000-0000-0000-0000-0000000005b1")


def _security(db, ticker: str, tenant=DEFAULT_TENANT_ID) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, tenant, ticker, "2025-01-01"),
        )
    db.commit()
    return sid


def _append(db, sid, decision, *, tenant=DEFAULT_TENANT_ID):
    v = split_repo.append_version(
        db,
        tenant_id=tenant,
        security_id=sid,
        key_date=decision.key_date,
        values=split_repo.decision_values(decision),
        action=split_repo.ACTION_BACKFILL,
        author=split_repo.AUTHOR_BACKFILL,
    )
    db.commit()
    return v


def _btoc():
    return decide(
        polygon_row("BTOC", "2026-10-15"),
        [sec("0001213900-26-108468.txt", form="8-K", filed="2026-10-09")],
        asof=ASOF,
    )


def _wnrs():
    return decide(
        polygon_row("WNRS", "2026-05-13"),
        [sec("WNRS_1aa.htm", form="1-A/A", filed="2026-04-14", accession="0001493152-26-016607")],
        asof=ASOF,
    )


def _n(db) -> int:
    return split_repo.count_rows(db)


def test_a_version_is_stored_with_both_clocks_and_a_derived_factor(db):
    sid = _security(db, "BTOC")
    v = _append(db, sid, _btoc())
    assert (v.status, v.confirmed_via, v.ratio_from, v.ratio_to) == (
        STATUS_CONFIRMED,
        VIA_AUTO,
        Decimal(16),
        Decimal(1),
    )
    assert v.price_factor == 16  # derived by the DB, old / new
    assert v.valid_from == date(2026, 10, 9)  # public on the 8-K's filing date
    assert v.recorded_at.tzinfo is not None  # the DB's now(), never passed in
    assert "Beginning with the opening of trading on October 15, 2026" in v.sec_passage
    assert (v.polygon_split_from, v.polygon_execution_date) == (Decimal(16), date(2026, 10, 15))


def test_update_and_delete_both_raise_and_the_table_is_unchanged(db):
    sid = _security(db, "BTOC")
    v = _append(db, sid, _btoc())
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("UPDATE security_split SET note = 'x' WHERE id = %s", (v.id,))
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("DELETE FROM security_split WHERE id = %s", (v.id,))
    db.rollback()
    assert _n(db) == 1
    assert split_repo.get_version(db, v.id).note == v.note


def test_the_natural_key_carries_the_security_and_the_version_stamp(db):
    """Two versions of ONE split in one transaction share now() and collide; the same key date on two
    securities (one filing stored per security scope) does not."""
    a, b = _security(db, "BTOC"), _security(db, "BTOC2")
    d = _btoc()
    vals = split_repo.decision_values(d)
    kw = dict(key_date=d.key_date, values=vals, action="backfill", author="system:backfill_splits")
    split_repo.append_version(db, tenant_id=DEFAULT_TENANT_ID, security_id=a, **kw)
    split_repo.append_version(db, tenant_id=DEFAULT_TENANT_ID, security_id=b, **kw)
    with pytest.raises(psycopg.errors.UniqueViolation):
        split_repo.append_version(db, tenant_id=DEFAULT_TENANT_ID, security_id=a, **kw)
    db.rollback()
    assert _n(db) == 0


def test_the_as_of_read_honors_both_clocks(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    between = db_now(db)  # the DB clock (the one-clock rule), in its own transaction
    db.commit()
    rejected = split_repo.reject(db, v1.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert rejected.recorded_at > v1.recorded_at
    # transaction time: pinned before the reject, the read sees the backfill's version
    early = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, known_at=between)
    assert [x.id for x in early] == [v1.id]
    now = split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID)
    assert [x.status for x in now] == [STATUS_REJECTED]
    # nothing is visible before it was recorded
    before = v1.recorded_at - timedelta(seconds=1)
    assert split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, known_at=before) == []
    # valid time: publicly known only from valid_from — the 1-A/A's filing day (it states the 1:300 split,
    # "pending FINRA", a month before Massive.com's market date)
    assert v1.valid_from == date(2026, 4, 14)
    assert split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, asof=date(2026, 4, 13)) == []
    assert (
        len(split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID, asof=date(2026, 4, 14)))
        == 1
    )


def test_a_tenant_reads_only_its_own_splits(db):
    provision_tenant(db, "split-test", tenant_id=OTHER_TENANT)
    mine = _security(db, "BTOC")
    theirs = _security(db, "BTOC", tenant=OTHER_TENANT)
    _append(db, mine, _btoc())
    _append(db, theirs, _btoc(), tenant=OTHER_TENANT)
    assert {v.security_id for v in split_repo.latest_versions(db, tenant_id=DEFAULT_TENANT_ID)} == {
        mine
    }
    assert {v.security_id for v in split_repo.latest_versions(db, tenant_id=OTHER_TENANT)} == {
        theirs
    }


def test_confirm_polygon_then_revoke_then_restore_is_exact(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    assert split_repo.confirm_options(v1) == ["polygon"]  # the 1-A/A states no date: one option
    assert split_repo.allowed_actions(v1) == ["confirm:polygon", "reject"]
    c = split_repo.confirm(db, v1.id, source="polygon", tenant_id=DEFAULT_TENANT_ID, today=ASOF)
    db.commit()
    assert (c.status, c.confirmed_via, c.value_source, c.author, c.action) == (
        STATUS_CONFIRMED,
        VIA_OPERATOR,
        "polygon",
        "operator",
        "confirm",
    )
    assert (c.ratio_from, c.effective_date) == (Decimal(300), date(2026, 5, 13))
    assert _n(db) == 2
    r = split_repo.revoke(db, c.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert r.status == STATUS_REVOKED and split_repo.prior_status(db, r) == STATUS_CONFIRMED
    back = split_repo.restore(db, r.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert back.content() == c.content()  # EXACTLY the state the revoke replaced
    assert _n(db) == 4  # every verb one version; nothing updated, nothing deleted


def test_reject_then_restore_returns_to_review(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    rj = split_repo.reject(db, v1.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert rj.status == STATUS_REJECTED and split_repo.allowed_actions(rj) == ["restore"]
    back = split_repo.restore(db, rj.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    assert back.status == STATUS_NEEDS_REVIEW and back.content() == v1.content()
    assert _n(db) == 3


def test_a_stale_click_or_a_wrong_status_is_refused(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    rj = split_repo.reject(db, v1.id, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    with pytest.raises(SplitConflict, match="changed since you looked"):
        split_repo.confirm(db, v1.id, source="polygon", tenant_id=DEFAULT_TENANT_ID, today=ASOF)
    db.rollback()
    btoc = _append(db, _security(db, "BTOC"), _btoc())
    with pytest.raises(SplitConflict, match="revoke a confirmed split instead"):
        split_repo.reject(db, btoc.id, tenant_id=DEFAULT_TENANT_ID)
    db.rollback()
    with pytest.raises(SplitConflict, match="only a confirmed split can be revoked"):
        split_repo.revoke(db, rj.id, tenant_id=DEFAULT_TENANT_ID)
    db.rollback()
    with pytest.raises(LookupError):
        split_repo.reject(db, uuid.uuid4(), tenant_id=DEFAULT_TENANT_ID)
    db.rollback()
    assert _n(db) == 3


def test_confirming_a_source_that_states_no_date_is_refused(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    with pytest.raises(SplitConflict, match="SEC states no complete ratio and date"):
        split_repo.confirm(db, v1.id, source="sec", tenant_id=DEFAULT_TENANT_ID, today=ASOF)
    db.rollback()
    assert _n(db) == 1


def test_a_future_first_session_confirms_as_announced(db):
    sid = _security(db, "WNRS")
    v1 = _append(db, sid, _wnrs())
    c = split_repo.confirm(
        db, v1.id, source="polygon", tenant_id=DEFAULT_TENANT_ID, today=date(2026, 5, 12)
    )
    db.commit()
    assert c.status == STATUS_ANNOUNCED and c.confirmed_via == VIA_OPERATOR
    assert split_repo.allowed_actions(c) == ["reject"]
