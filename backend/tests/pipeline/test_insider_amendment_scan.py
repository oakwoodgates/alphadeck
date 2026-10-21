"""R8-A / R8-B — the nightly Form 4/A inventory and sweep, on REAL filings (``tests/form4a_fixtures.py``).

R8-B (operator decision 2026-10-20): what the linker resolves to a definite original is the filer's own
correction, APPLIED BY THE SYSTEM (at ingest, or by the pass's SWEEP for what a pre-R8-B ingest left pending) —
never paged, one quiet count line. Only what the system cannot resolve AND a call can read is OWED and pages
ONCE (the T1/T2 page-once machinery): an ambiguous link touching a signal row inside the review horizon (plus
an operator-linked one awaiting its verdict, or one the operator took back). A known one stays quiet; a decision
takes it out and its Undo puts it back. Benign-class: the page never makes the Admin cron verdict unhealthy.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID, connect
from pipeline import cron_run_log, daily
from pipeline.insider_amendment_scan import (
    PAGE_NAME_CAP,
    AmendmentScan,
    PendingAmendment,
    SweepOutcome,
    count_line,
    keys_of,
    new_labels_of,
    new_more_of,
    page_named,
    previous_amendment_keys,
    report_lines,
    run_amendment_scan,
    run_amendment_sweep,
)
from repositories import insider_amendment_repo
from tests.form4a_fixtures import ingest_amendment, ingest_originals, make_security, seed

CVNA_4A = "0001690820-25-000275"


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


def _scan(prior, **kw):
    return run_amendment_scan(
        allow_live=True, connect=connect, previous=lambda: prior, clock=lambda: 0.0, **kw
    )


def _asth(db):
    sid = make_security(db, "ASTH")
    ingest_originals(db, sid, "ASTH")
    ingest_amendment(db, sid, "0001134366-13-000009")
    return sid


def _decisions_count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        return cur.fetchone()["n"]


def _as_r8a_left_it(db, sid) -> None:
    """The decision-less backlog a pre-R8-B ingest left: drop the decisions R8-B's ingest wrote (``detail.via
    = ingest``) — a test-DB fixture step; prod holds exactly this shape."""
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM insider_amendment_decision WHERE security_id = %s AND detail->>'via' = 'ingest'",
            (sid,),
        )
    db.commit()


def _hand_over(db, sid) -> None:
    """The operator takes a system application back (revokes it): the amendment is then the operator's."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT id FROM insider_amendment_decision WHERE security_id = %s AND basis LIKE 'system:%%'",
            (sid,),
        )
        ids = [r["id"] for r in cur.fetchall()]
    for i in ids:
        insider_amendment_repo.revoke(db, i, reason="mine", basis="operator:revoke")
    db.commit()


def test_only_what_the_system_cannot_resolve_and_a_call_can_read_is_owed(db):
    """FLS's void, TDG's duplicates, ATCH's corrections: applied by the system. RPAY: clerical. ASTH: ambiguous
    but its rows are from 2008 / 2012 (historical). Only CVNA — ambiguous, its candidates' sales inside the review
    horizon — is owed, and the whole inventory pages ONCE without a baseline."""
    sids = [seed(db, t) for t in ("FLS", "ATCH", "RPAY", "TDG", "CVNA")] + [_asth(db)]
    _place(db, sids)
    scan = _scan(None)
    assert scan.evaluated and not scan.baseline
    (cvna,) = scan.pending
    assert (cvna.ticker, cvna.accession, cvna.disposition, cvna.candidates) == (
        "CVNA",
        CVNA_4A,
        "ambiguous",
        2,
    )
    assert "2 candidate originals; pick the one it amends" in cvna.label
    assert scan.new == scan.pending  # no baseline: the whole (owed) inventory pages ONCE
    assert scan.new_labels == (
        cvna.label,
    )  # the page names only owed items — no system application


def test_a_known_item_stays_quiet_and_a_decision_and_its_undo_move_it(db):
    sid = seed(db, "CVNA")
    _place(db, [sid])
    first = _scan(None)
    keys = {p.key for p in first.pending}
    second = _scan(keys)
    assert second.baseline and second.new == () and second.known == 1  # known: quiet
    insider_amendment_repo.link_candidate(
        db, sid, CVNA_4A, "0001690820-25-000273", reason="the S rows", basis="operator:link"
    )
    db.commit()
    linked = _scan(keys)
    assert [p.accession for p in linked.pending] == [
        CVNA_4A
    ] and linked.new == ()  # still the operator's
    assert linked.pending[0].candidates == 0  # linked: no longer "pick the original"
    d = insider_amendment_repo.decide(
        db, sid, CVNA_4A, "apply", reason="restated", basis="operator:apply"
    )
    db.commit()
    third = _scan(keys)
    assert third.pending == ()  # decided: nothing owed
    insider_amendment_repo.revoke(db, d["id"], reason="undo", basis="operator:revoke")
    db.commit()
    fourth = _scan({p.key for p in third.pending})
    assert [p.accession for p in fourth.new] == [CVNA_4A]  # back, and it pages once


def test_an_amendment_outside_the_active_baskets_is_not_paged(db):
    seed(db, "CVNA")  # owed, but no thesis holds CVNA
    assert _scan(None).pending == ()


def test_no_live_neither_sweeps_nor_scans_and_a_fault_is_reported_never_raised():
    def _never():
        raise AssertionError("must not connect")

    off = run_amendment_scan(allow_live=False, connect=_never, previous=_never, clock=lambda: 0.0)
    assert off == AmendmentScan()
    assert run_amendment_sweep(allow_live=False, connect=_never) == SweepOutcome()

    def _down():
        raise OSError("db down")

    broken = run_amendment_scan(allow_live=True, connect=_down, previous=_never, clock=lambda: 0.0)
    assert not broken.evaluated and broken.error == "OSError: db down"
    assert report_lines(broken)[0].startswith("FORM 4/A INVENTORY FAILED")
    sweep = run_amendment_sweep(allow_live=True, connect=_down)
    assert sweep.ran and sweep.error == "OSError: db down" and sweep.applied == 0
    with_failed_sweep = AmendmentScan(evaluated=True, sweep_error=sweep.error)
    assert report_lines(with_failed_sweep)[0].startswith("FORM 4/A SWEEP FAILED")


def test_the_baseline_is_the_newest_live_finished_evaluated_pass(db, tmp_path):
    sid = seed(db, "CVNA")
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
    labels = (f"CVNA {CVNA_4A}: ambiguous — 2 candidate originals; pick the one it amends",)
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


def test_the_page_names_owed_items_in_window_first_and_counts_beyond_the_cap(db):
    """Owed items here: three the operator took back from the system (FLS's buy void — tonight's P window;
    TDG's duplicate sells and FRHC's role-restated sales — the S window; AZTA's 2025 buy — outside) and CVNA's
    ambiguous link (outside tonight's window, inside the review horizon). The page names the in-window ones
    first, then the rest; nothing the system applied (AVAV) and nothing historical (ASTH) is on it.
    """
    sids = {t: seed(db, t) for t in ("FLS", "TDG", "FRHC", "AZTA", "CVNA", "AVAV")}
    sids["ASTH"] = _asth(db)
    for t in ("FLS", "TDG", "FRHC", "AZTA"):
        _hand_over(db, sids[t])
    _place(db, sids.values())
    scan = run_amendment_scan(
        asof=date(2026, 10, 16),
        allow_live=True,
        connect=connect,
        previous=lambda: None,
        clock=lambda: 0.0,
    )
    assert [p.ticker for p in scan.pending][:3] == [
        "FLS",
        "TDG",
        "FRHC",
    ]  # in-window, buy, suppressing
    assert {p.ticker for p in scan.pending} == {"FLS", "TDG", "FRHC", "AZTA", "CVNA"}
    named = scan.new_labels
    assert len(named) == 5 and scan.new_more == 0  # under the cap: every owed item named
    assert [label.split(" ")[0] for label in named][:3] == ["FLS", "TDG", "FRHC"]
    health = daily.assess_health(
        [],
        asof=date(2026, 10, 16),
        allow_live=True,
        freeze_check=False,
        insider_amendments_new=named,
        insider_amendments_more=scan.new_more,
    )
    assert "5 Form 4/A amendment(s) NEW" in health.label and "AVAV" not in health.label
    doc = scan.payload()
    assert new_labels_of(doc) == named and new_more_of(doc) == 0  # the Admin history re-derives it


def test_the_page_cap_never_collapses_an_in_window_item():
    """The naming contract with direct inputs: every in-window item is named however many; the others fill
    the rest up to the cap; the remainder is counted ("+N more in Admin")."""

    def p(i, window):
        return PendingAmendment(
            key=f"s:{i}",
            security_id="s",
            ticker=f"T{i}",
            accession=f"A{i}",
            disposition="ambiguous",
            suppressing=(),
            touches_buy=False,
            in_window=("S",) if window else (),
            candidates=2,
        )

    many_in = tuple(p(i, True) for i in range(PAGE_NAME_CAP + 2)) + (p(99, False),)
    assert page_named(many_in) == {f"s:{i}" for i in range(PAGE_NAME_CAP + 2)}
    mixed = (p(0, True),) + tuple(p(i, False) for i in range(1, 12))
    named = page_named(mixed)
    assert "s:0" in named and len(named) == PAGE_NAME_CAP
    scan = AmendmentScan(evaluated=True, pending=mixed, new=mixed)
    assert len(scan.new_labels) == PAGE_NAME_CAP and scan.new_more == 12 - PAGE_NAME_CAP
    legacy = {
        "insider_amendments": {
            "new": [{"key": "k", "label": "L1", "in_window": ["P"]}, {"key": "k2", "label": "L2"}]
        }
    }
    assert new_labels_of(legacy) == ("L1",) and new_more_of(legacy) == 1  # a pre-R8-B artifact


# --- R8-B: the sweep inside the pass, and its one quiet count line ------------------------------------------


def test_the_sweep_applies_the_backlog_and_the_scan_counts_it_quietly(db):
    """The pass's sweep (own connection, committed) resolves what a pre-R8-B ingest left pending; the scan counts
    the system decisions recorded since the pass began (one quiet line: applications, and the zero-row 4/As kept
    as filed), and none of them pages. YYAI (words neither fix nor add) and FIGR (retraction words against three
    trades — held) are owed beside CVNA. Re-running the sweep appends nothing — COUNT THE TABLE."""
    sids = {t: seed(db, t) for t in ("FLS", "MRCY", "IEHC", "SMR", "FIGR", "YYAI", "CVNA")}
    for t in ("FLS", "MRCY", "IEHC", "SMR", "FIGR", "YYAI"):
        _as_r8a_left_it(db, sids[t])
    _place(db, sids.values())
    started = db_now(db)
    db.commit()
    before = _decisions_count(db)
    sweep = run_amendment_sweep(allow_live=True, connect=connect)
    assert (sweep.ran, sweep.error, sweep.applied) == (True, None, 5)
    assert sweep.by_basis == {
        "system:filer-correction": 2,  # FLS (a retraction), IEHC (FIX words)
        "system:no-signal-effect": 1,  # MRCY (the grant added)
        "system:no-retraction": 1,  # SMR (a holdings fix)
        "system:held": 1,  # FIGR
    }
    assert _decisions_count(db) == before + 5
    scan = _scan(None, since=started, sweep=sweep)
    assert scan.applied == sweep.by_basis and scan.swept == 5
    assert {p.ticker for p in scan.pending} == {"CVNA", "YYAI", "FIGR"}
    by = {p.ticker: p for p in scan.pending}
    assert by["YYAI"].unresolved and "neither fix nor add" in by["YYAI"].label
    assert by["FIGR"].held and "which one is your call" in by["FIGR"].label
    assert count_line(scan) == (
        "Form 4/A: 3 applied automatically (filer corrections), 1 kept as filed (their text retracts "
        "nothing), 3 owed your decision (quiet)"
    )
    assert report_lines(scan)[-1] == count_line(scan)
    assert not any(
        line.lstrip().startswith(("FLS", "MRCY", "IEHC", "SMR")) for line in report_lines(scan)
    )
    block = scan.payload()["insider_amendments"]
    assert (block["applied"], block["swept"], block["sweep_error"]) == (sweep.by_basis, 5, None)
    again = run_amendment_sweep(allow_live=True, connect=connect)
    assert again.applied == 0 and _decisions_count(db) == before + 5  # idempotent


def test_a_clean_night_prints_nothing():
    assert report_lines(AmendmentScan(evaluated=True)) == []
    assert count_line(AmendmentScan(evaluated=True)) is None
