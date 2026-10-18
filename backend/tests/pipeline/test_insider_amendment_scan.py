"""R8-A — the nightly Form 4/A inventory: what is OWED the operator's decision pages ONCE (the T1/T2 page-once
machinery), a known one stays quiet, a decision takes it out of the inventory and its Undo puts it back (and
pages it once more — the operator asked for it back). Applied 1:1 corrections never page. Benign-class: the
page never makes the Admin cron verdict unhealthy."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from db.session import DEFAULT_TENANT_ID, connect
from pipeline import cron_run_log, daily
from pipeline.insider_amendment_scan import (
    AmendmentScan,
    keys_of,
    new_labels_of,
    previous_amendment_keys,
    report_lines,
    run_amendment_scan,
)
from repositories import insider_amendment_repo
from tests.form4a_fixtures import seed


def _place(db, sids) -> None:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "T", "n"),
        )
        for i, sid in enumerate(sids):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, tid, i, f"T{i}", "core", sid),
            )
    db.commit()


def _scan(prior):
    return run_amendment_scan(
        allow_live=True, connect=connect, previous=lambda: prior, clock=lambda: 0.0
    )


def test_only_what_is_owed_a_decision_is_in_the_inventory(db):
    sids = {t: seed(db, t) for t in ("FLS", "ATCH", "RPAY", "TDG")}
    _place(db, sids.values())
    scan = _scan(None)
    assert scan.evaluated and not scan.baseline
    got = {p.accession: p for p in scan.pending}
    # the FLS void + the TDG duplicates; the ATCH corrections applied themselves, RPAY is clerical
    assert set(got) == {"0001628280-26-037326", "0001352573-26-000011"}
    fls = got["0001628280-26-037326"]
    assert fls.suppressing == ("0001628280-26-035913",) and fls.touches_buy
    assert "suppressing 0001628280-26-035913 until you decide" in fls.label
    assert scan.new == scan.pending  # no baseline: the whole inventory pages ONCE


def test_a_known_item_stays_quiet_and_a_decision_and_its_undo_move_it(db):
    sid = seed(db, "FLS")
    _place(db, [sid])
    first = _scan(None)
    keys = {p.key for p in first.pending}
    second = _scan(keys)
    assert second.baseline and second.new == () and second.known == 1  # known: quiet
    d = insider_amendment_repo.decide(
        db, sid, "0001628280-26-037326", "apply", reason="cancelled", basis="operator:apply"
    )
    db.commit()
    third = _scan(keys)
    assert third.pending == ()  # ratified: nothing owed
    insider_amendment_repo.revoke(db, d["id"], reason="undo", basis="operator:revoke")
    db.commit()
    fourth = _scan({p.key for p in third.pending})
    assert [p.accession for p in fourth.new] == ["0001628280-26-037326"]  # back, and it pages once


def test_an_amendment_outside_the_active_baskets_is_not_paged(db):
    seed(db, "FLS")  # stored, but no thesis holds FLS
    assert _scan(None).pending == ()


def test_no_live_does_not_scan_and_a_fault_is_reported_never_raised():
    def _never():
        raise AssertionError("must not connect")

    off = run_amendment_scan(allow_live=False, connect=_never, previous=_never, clock=lambda: 0.0)
    assert off == AmendmentScan()

    def _down():
        raise OSError("db down")

    broken = run_amendment_scan(allow_live=True, connect=_down, previous=_never, clock=lambda: 0.0)
    assert not broken.evaluated and broken.error == "OSError: db down"
    assert report_lines(broken)[0].startswith("FORM 4/A INVENTORY FAILED")


def test_the_baseline_is_the_newest_live_finished_evaluated_pass(db, tmp_path):
    sid = seed(db, "FLS")
    _place(db, [sid])
    scan = _scan(None)
    t0 = datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc)

    def _write(i, *, amendments, allow_live=True):
        at = t0 + timedelta(hours=i)
        return cron_run_log.write_cron_run_log(
            [],
            asof=date(2026, 10, 17),
            allow_live=allow_live,
            started_at=at,
            finished_at=at,
            base_dir=tmp_path,
            amendments=amendments,
        )

    assert previous_amendment_keys(base_dir=tmp_path) is None
    _write(0, amendments=scan)
    _write(1, amendments=AmendmentScan(error="OperationalError: down"))  # a failed scan: skipped
    _write(2, amendments=AmendmentScan(evaluated=True), allow_live=False)  # --no-live: skipped
    assert previous_amendment_keys(base_dir=tmp_path) == {p.key for p in scan.pending}
    doc = scan.payload()
    assert keys_of(doc) == {p.key for p in scan.pending}
    assert new_labels_of(doc) == scan.new_labels


def test_the_page_is_benign_class():
    labels = ("FLS 0001628280-26-037326: void — suppressing 0001628280-26-035913 until you decide",)
    health = daily.assess_health(
        [],
        asof=date(2026, 10, 17),
        allow_live=True,
        freeze_check=False,
        insider_amendments_new=labels,
    )
    assert health is not None and health.insider_amendments_new == labels
    assert "Form 4/A amendment(s) NEW and owed your decision" in health.label
    from app.routers.admin import _BENIGN_MARK, _problems

    lines = _problems(health)
    assert len(lines) == 1 and _BENIGN_MARK in lines[0]
