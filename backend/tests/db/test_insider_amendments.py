"""R8-A — the Form 4/A amendments END TO END on Postgres, on REAL filings (``tests/form4a_fixtures.py``):
ingest + link (COUNT the tables), the read-path resolution through ``db.bitemporal`` (both the scoped
``as_of`` and the basket ``as_of_many``), the operator's decisions and their Undo, the point-in-time gate on
the link's ``recorded_at``, and the other-issuer screen.

Every behavior is asserted on the call's OWN detector (``signals.insider_conviction``) or on counted rows —
never on a fitted number: the ATCH CORE is the five real 4/As' own arithmetic, the FLS loss of CORE is the
real cancellation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import as_of, as_of_many
from db.clock import db_now
from db.insider_resolution import load_inputs
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade
from domain.insider_amendments import AMENDS_ACCESSION, INSIDER_TXN_COLUMNS
from ingest.edgar.form4 import ingest_form4, other_issuer_cik
from repositories import insider_amendment_repo
from signals import insider_conviction
from signals.base import PointInTimeData
from tests.form4a_fixtures import (
    CIKS,
    amendments_of,
    ingest_amendment,
    ingest_originals,
    make_security,
    seed,
    xml,
)

T = DEFAULT_TENANT_ID
FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)


def _count(db, table: str, sid) -> int:
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE security_id = %s", (sid,)
        )  # noqa: S608
        return cur.fetchone()["n"]


def _rows(db, sid, asof: date, known_at: datetime = FAR) -> list[dict]:
    return as_of(db, "fact_insider_txn", security_id=sid, asof=asof, known_at=known_at, tenant_id=T)


def _signal(db, sid, asof: date, known_at: datetime = FAR):
    pit = PointInTimeData(db, asof=asof, known_at=known_at, basket=[sid])
    return insider_conviction.detect(pit, sid, asof, DEFAULT_CONFIG)


# --- the table shape ------------------------------------------------------------------------------------


def test_emitted_rows_carry_every_fact_insider_txn_column(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'fact_insider_txn' "
            "ORDER BY ordinal_position"
        )
        cols = tuple(r["column_name"] for r in cur.fetchall())
    assert cols == INSIDER_TXN_COLUMNS


# --- ingest + link: COUNT the tables ----------------------------------------------------------------------


def test_amendments_are_stored_beside_never_inside_fact_insider_txn(db):
    sid = make_security(db, "ATCH")
    stored = ingest_originals(db, sid, "ATCH")
    before = _count(db, "fact_insider_txn", sid)
    assert before == stored == 9
    for acc in amendments_of("ATCH"):
        out = ingest_amendment(db, sid, acc)
        assert out.link is not None and out.link.disposition == "correction"
    assert _count(db, "fact_insider_txn", sid) == before  # nothing written into the fact table
    assert _count(db, "insider_amendment", sid) == 5
    assert _count(db, "insider_amendment_link", sid) == 5


def test_the_leg_is_idempotent_count_the_tables(db):
    """The nightly leg re-run over the same feed appends NOTHING (the stored accessions are skipped)."""
    from ingest.edgar.form4a import ingest_amendments
    from tests.form4a_fixtures import FILINGS

    sid = make_security(db, "ATCH")
    ingest_originals(db, sid, "ATCH")
    accs = sorted(FILINGS)
    recent = {
        "form": [FILINGS[a].form for a in accs if FILINGS[a].ticker == "ATCH"],
        "accessionNumber": [a for a in accs if FILINGS[a].ticker == "ATCH"],
        "primaryDocument": [f"xslF345X06/{a}.xml" for a in accs if FILINGS[a].ticker == "ATCH"],
        "filingDate": [FILINGS[a].filed.isoformat() for a in accs if FILINGS[a].ticker == "ATCH"],
        "acceptanceDateTime": [
            FILINGS[a].accepted.isoformat() for a in accs if FILINGS[a].ticker == "ATCH"
        ],
    }
    subs = {"filings": {"recent": recent}}

    class _Client:  # serves the real documents by the forms/ cache key the leg asks for
        def get_text(self, url, key):
            return xml(key.split("/")[1])

    first, skips = ingest_amendments(db, _Client(), sid, CIKS["ATCH"], "ATCH", subs)
    db.commit()
    assert len(first.stored) == 5 and skips == ()
    counts = (_count(db, "insider_amendment", sid), _count(db, "insider_amendment_link", sid))
    again, _ = ingest_amendments(db, _Client(), sid, CIKS["ATCH"], "ATCH", subs)
    db.commit()
    assert again.stored == []
    assert (
        _count(db, "insider_amendment", sid),
        _count(db, "insider_amendment_link", sid),
    ) == counts
    assert _count(db, "fact_insider_txn", sid) == 9


def test_a_clerical_amendment_adds_no_counted_value(db):
    sid = make_security(db, "RPAY")
    ingest_originals(db, sid, "RPAY")
    asof = date(2026, 4, 30)
    before = _rows(db, sid, asof)
    out = ingest_amendment(db, sid, "0001104659-26-041960")
    assert out.link.disposition == "clerical"
    after = _rows(db, sid, asof)
    assert len(after) == len(before) == 3
    assert sum(float(r["usd"]) for r in after) == sum(float(r["usd"]) for r in before)


# --- the resolver on the call's own detector ----------------------------------------------------------------


def test_atch_recodes_restore_the_missed_core(db):
    sid = make_security(db, "ATCH")
    ingest_originals(db, sid, "ATCH")
    asof = date(2026, 10, 1)
    assert _signal(db, sid, asof) is None  # the originals code every purchase "A" — no trigger
    for acc in amendments_of("ATCH"):
        ingest_amendment(db, sid, acc)
    ev = _signal(db, sid, asof)
    assert ev is not None and ev.grade == Grade.CORE
    assert ev.label.startswith("5 insiders") and "$162,524" in ev.label and "9 txns" in ev.label
    # the provenance cites the 4/As — the filings that state code P
    assert {p.ref for p in ev.provenance} == set(amendments_of("ATCH"))
    assert all(r[AMENDS_ACCESSION] for r in _rows(db, sid, asof))


def test_fls_void_is_applied_by_the_system_and_keep_original_is_its_undo(db):
    """R8-B: the FLS cancellation resolves to its one original, so the ingest applies it in the link's own
    transaction (basis system:filer-correction): the canceled buy is suppressed and the CORE is gone. Keep
    original (the operator's exact Undo) restores the buy; undoing the keep restores the system's application.
    """
    sid = make_security(db, "FLS")
    ingest_originals(db, sid, "FLS")
    asof = date(2026, 6, 15)
    before = _signal(db, sid, asof)
    assert before is not None and before.grade == Grade.CORE  # two directors, $231,615
    out = ingest_amendment(db, sid, "0001628280-26-037326")
    assert (out.link.disposition, out.link.status) == ("void", "applied")
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"], d["detail"]["via"]) == (
        "apply",
        "system:filer-correction",
        "ingest",
    )
    assert _signal(db, sid, asof) is None  # the canceled buy no longer fires
    assert "0001628280-26-035913" not in {r["accession"] for r in _rows(db, sid, asof)}
    assert _count(db, "fact_insider_txn", sid) == 3  # suppressed at read, never deleted
    it = _item(db, sid, "0001628280-26-037326")
    assert (it.review, it.system_basis, it.needs_operator) == (
        "auto",
        "system:filer-correction",
        False,
    )
    keep = insider_amendment_repo.decide(
        db, sid, "0001628280-26-037326", "keep", reason="not canceled", basis="operator:keep"
    )
    db.commit()
    assert _signal(db, sid, asof) == before  # the buy STANDS again — the exact prior state
    assert _item(db, sid, "0001628280-26-037326").review == "decided"
    insider_amendment_repo.revoke(db, keep["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _signal(db, sid, asof) is None  # the system's application stands again
    assert _item(db, sid, "0001628280-26-037326").review == "auto"


def test_the_systems_application_cannot_be_ratified_twice_and_revoking_it_hands_it_over(db):
    """An operator apply over the system's is refused (it already applies). Revoking the system's own
    decision (an API-only path) leaves the void PENDING — still suppressing, the operator's condition — and
    makes it the operator's: owed, never re-applied by the sweep."""
    sid = seed(db, "FLS")
    acc, asof = "0001628280-26-037326", date(2026, 6, 15)
    with pytest.raises(insider_amendment_repo.AmendmentConflict, match="already applied"):
        insider_amendment_repo.decide(db, sid, acc, "apply", reason="x", basis="operator:apply")
    db.rollback()
    (system,) = _decisions(db, sid)
    insider_amendment_repo.revoke(db, system["id"], reason="mine", basis="operator:revoke")
    db.commit()
    it = _item(db, sid, acc)
    assert (it.effective.status, it.review) == ("pending", "owed")
    assert _signal(db, sid, asof) is None  # a pending void still suppresses
    n = _count(db, "insider_amendment_decision", sid)
    insider_amendment_repo.sweep(db, security_ids=[sid])
    db.commit()
    assert _count(db, "insider_amendment_decision", sid) == n  # the sweep never takes it back
    insider_amendment_repo.decide(db, sid, acc, "apply", reason="canceled", basis="operator:apply")
    db.commit()
    assert _item(db, sid, acc).review == "decided" and _signal(db, sid, asof) is None


def test_azta_keep_original_restores_the_buy_and_undo_suppresses_again(db):
    sid = make_security(db, "AZTA")
    ingest_originals(db, sid, "AZTA")
    asof = date(2025, 8, 15)
    fired = _signal(db, sid, asof)
    assert fired is not None  # the CFO's open-market buy
    ingest_amendment(db, sid, "0001193125-25-282945")
    # the power-of-attorney 4/A reads as a void and (R8-B) the system applies it: the buy is suppressed until
    # the operator keeps the original — the case Keep original exists for
    assert _signal(db, sid, asof) is None
    keep = insider_amendment_repo.decide(
        db, sid, "0001193125-25-282945", "keep", reason="only a POA", basis="operator:keep"
    )
    db.commit()
    assert _signal(db, sid, asof) == fired  # the buy STANDS
    insider_amendment_repo.revoke(db, keep["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _signal(db, sid, asof) is None


def test_tdg_duplicates_are_counted_once(db):
    sid = make_security(db, "TDG")
    ingest_originals(db, sid, "TDG")
    asof = date(2026, 6, 30)
    tripled = _rows(db, sid, asof)
    assert len(tripled) == 69  # three byte-identical 23-row filings
    ingest_amendment(db, sid, "0001352573-26-000011")
    once = _rows(db, sid, asof)
    assert len(once) == 23 and {r["accession"] for r in once} == {"0001352573-26-000008"}
    sells = sum(float(r["usd"]) for r in once if r["txn_code"] == "S")
    assert abs(sum(float(r["usd"]) for r in tripled if r["txn_code"] == "S") - 3 * sells) < 1e-6


def test_an_ambiguous_link_suppresses_nothing_until_linked_and_applied(db):
    sid = make_security(db, "SKYT")
    ingest_originals(db, sid, "SKYT")
    asof = date(2026, 3, 31)
    original = _rows(db, sid, asof)
    assert len(original) == 4
    out = ingest_amendment(db, sid, "0001856680-26-000016")
    assert out.link.disposition == "ambiguous"
    assert _rows(db, sid, asof) == original  # flagged, never applied
    link = insider_amendment_repo.link_candidate(
        db,
        sid,
        "0001856680-26-000016",
        "0001856680-26-000013",
        reason="the S row",
        basis="operator:link",
    )
    db.commit()
    assert link["disposition"] == "partial"
    assert _rows(db, sid, asof) == original  # a linked partial is pending: no provisional effect
    applied = insider_amendment_repo.decide(
        db, sid, "0001856680-26-000016", "apply", reason="restated", basis="operator:apply"
    )
    db.commit()
    now = _rows(db, sid, asof)
    assert {r["accession"] for r in now} == {"0001856680-26-000010", "0001856680-26-000016"}
    assert [float(r["shares"]) for r in now if r["accession"] == "0001856680-26-000016"] == [
        71693.0
    ]
    insider_amendment_repo.revoke(db, applied["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.revoke(db, link["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _rows(db, sid, asof) == original


# --- the signal-effect rule (operator-approved 2026-10-18) -------------------------------------------------


def _decisions(db, sid) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s ORDER BY recorded_at, id",
            (sid,),
        )
        return cur.fetchall()


def _item(db, sid, accession):
    return next(
        i for i in insider_amendment_repo.items(db, security_ids=[sid]) if i.accession == accession
    )


def test_a_grant_only_void_is_resolved_by_the_system_and_keep_is_its_undo(db):
    """AVAV: a zero-row 4/A voiding an original of code-A grants only — no call signal reads those rows, so
    the ingest appends the system's apply under basis system:no-signal-effect (the 2026-10-18 rule, kept by
    R8-B) in its own transaction; Keep original undoes it, and undoing the keep restores it."""
    sid = make_security(db, "AVAV")
    ingest_originals(db, sid, "AVAV")
    out = ingest_amendment(db, sid, "0001538012-26-000006")
    assert out.link.auto_resolved and not out.link.signal_effect
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"]) == ("apply", "system:no-signal-effect")
    it = _item(db, sid, "0001538012-26-000006")
    assert it.auto_resolved and not it.needs_operator and it.effective.status == "applied"
    assert it.system_basis == "system:no-signal-effect"
    asof = date(2026, 7, 31)
    assert _rows(db, sid, asof) == []  # the void applies (grants suppressed) — no call reads them
    assert _count(db, "fact_insider_txn", sid) == 2  # suppressed at read, never deleted
    keep = insider_amendment_repo.decide(
        db, sid, "0001538012-26-000006", "keep", reason="a holdings fix", basis="operator:keep"
    )
    db.commit()
    assert len(_rows(db, sid, asof)) == 2  # the grants stand
    insider_amendment_repo.revoke(db, keep["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _rows(db, sid, asof) == [] and _item(db, sid, "0001538012-26-000006").auto_resolved


def test_an_identical_signal_row_addition_is_resolved_by_the_system(db):
    """MASS: the 4/A adds a code-M row beside the original's UNCHANGED sale — applied by the system, the
    sale reads exactly as before (the amendment's own row now)."""
    sid = make_security(db, "MASS")
    ingest_originals(db, sid, "MASS")
    asof = date(2026, 3, 31)
    sale_before = [(r["txn_code"], float(r["shares"])) for r in _rows(db, sid, asof)]
    out = ingest_amendment(db, sid, "0002115341-26-000008")
    assert (out.link.disposition, out.link.auto_resolved) == ("addition", True)
    after = _rows(db, sid, asof)
    assert sorted(r["txn_code"] for r in after) == ["M", "S"]
    assert [
        (r["txn_code"], float(r["shares"])) for r in after if r["txn_code"] == "S"
    ] == sale_before


def test_frhc_role_restatement_is_applied_by_the_system_and_keep_restores_the_title(db):
    sid = make_security(db, "FRHC")
    ingest_originals(db, sid, "FRHC")
    out = ingest_amendment(db, sid, "0000929638-26-003399")
    assert (out.link.disposition, out.link.status) == ("field_change", "applied")
    (d,) = _decisions(db, sid)
    assert d["basis"] == "system:filer-correction"
    asof = date(2026, 8, 31)
    assert {r["insider_role"] for r in _rows(db, sid, asof)} == {"Special Advisor to CEO"}
    keep = insider_amendment_repo.decide(
        db, sid, "0000929638-26-003399", "keep", reason="check the title", basis="operator:keep"
    )
    db.commit()
    assert {r["insider_role"] for r in _rows(db, sid, asof)} == {"Chief Financial Officer"}
    insider_amendment_repo.revoke(db, keep["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert {r["insider_role"] for r in _rows(db, sid, asof)} == {"Special Advisor to CEO"}


def test_the_urgency_marks_what_sits_inside_tonights_signal_windows(db):
    """In-window = a changed signal row inside a signal's live read horizon (the detectors' own) at asof —
    computed for every item (Admin shows it), and it orders the OWED ones first."""
    sids = {t: seed(db, t) for t in ("FLS", "AZTA", "TDG", "FRHC", "SKYT")}
    asof = date(2026, 10, 16)
    got = {
        i.ticker: i.in_window
        for i in insider_amendment_repo.items(db, security_ids=list(sids.values()), asof=asof)
    }
    assert got == {
        "FLS": ("P",),
        "TDG": ("S",),
        "FRHC": ("S",),
        "AZTA": (),  # AZTA's buy is 2025-08-11
        "SKYT": (),  # SKYT's rows are 2026-03-16 — outside the 120-day sell window
    }
    # hand FLS to the operator (revoke the system's application): an OWED item in tonight's P window leads
    (system,) = _decisions(db, sids["FLS"])
    insider_amendment_repo.revoke(db, system["id"], reason="mine", basis="operator:revoke")
    db.commit()
    first = insider_amendment_repo.items(db, security_ids=list(sids.values()), asof=asof)
    assert first[0].ticker == "FLS" and first[0].needs_operator and first[0].in_window


# --- R8-B: the real in-window sells, the ambiguous review horizon --------------------------------------------


def test_iehc_addition_and_mram_restatement_take_effect_at_ingest(db):
    """The filer's restated sales replace the original's the moment the system holds the 4/A (one
    recorded_at with its link)."""
    iehc = seed(db, "IEHC")
    rows = _rows(db, iehc, date(2026, 10, 20))
    assert {r["accession"] for r in rows} == {"0001174947-26-000849"}
    assert sorted((str(r["valid_from"]), float(r["shares"])) for r in rows) == [
        ("2026-09-03", 100.0),
        ("2026-09-03", 100.0),
        ("2026-09-03", 1000.0),
        ("2026-09-04", 3800.0),
    ]
    mram = seed(db, "MRAM")
    (row,) = _rows(db, mram, date(2026, 10, 20))
    assert (
        row["accession"],
        str(row["valid_from"]),
        float(row["shares"]),
        float(row["price"]),
    ) == (
        "0001628280-26-047722",
        "2026-07-06",
        36289.0,
        19.65,
    )
    assert {d["basis"] for d in _decisions(db, iehc) + _decisions(db, mram)} == {
        "system:filer-correction"
    }


def test_a_recent_ambiguous_amendment_is_owed_and_an_old_one_is_historical(db):
    """SKYT (its candidates' sales dated 2026-03-16, inside the 940-day review horizon at 2026-10-20) is OWED
    the operator; ELTP (a 2012 sale) is "unresolved — historical": no call reads those rows, the originals
    stand exactly as stored, nothing is decided for anyone."""
    skyt, eltp = seed(db, "SKYT"), make_security(db, "ELTP")
    ingest_originals(db, eltp, "ELTP")
    asof = date(2012, 3, 31)
    original = _rows(db, eltp, asof)
    assert {r["txn_code"] for r in original} == {"C", "S"}
    ingest_amendment(db, eltp, "0001465825-12-000008")
    assert _rows(db, eltp, asof) == original  # the originals stand
    found = {
        i.ticker: i
        for i in insider_amendment_repo.items(
            db, security_ids=[skyt, eltp], asof=date(2026, 10, 20)
        )
    }
    assert (found["SKYT"].review, found["SKYT"].in_review, found["SKYT"].needs_operator) == (
        "owed",
        True,
        True,
    )
    assert (found["ELTP"].review, found["ELTP"].in_review, found["ELTP"].needs_operator) == (
        "historical",
        False,
        False,
    )
    assert found["ELTP"].signal_effect  # it does touch a code-S row — just one no call reads
    assert _decisions(db, skyt) == [] and _decisions(db, eltp) == []
    # the horizon is the dial: unbounded, ELTP would be owed too
    again = insider_amendment_repo.items(
        db, security_ids=[eltp], asof=date(2026, 10, 20), review_days=None
    )
    assert again[0].review == "owed"


# --- R8-B: the SWEEP of what a pre-R8-B ingest left pending -------------------------------------------------


def _as_r8a_left_it(db, sid) -> None:
    """Reproduce the state R8-A's ingest left: it recorded NO decision for a correction or a signal-touching
    routed amendment (only ``system:no-signal-effect`` applies) — so drop the ingest's filer-correction rows.
    (A test-DB fixture step: the prod tables hold exactly this shape for the 1,098 links R8-A stored.)
    """
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM insider_amendment_decision WHERE security_id = %s AND basis = %s",
            (sid, "system:filer-correction"),
        )
    db.commit()


def _table_count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        return cur.fetchone()["n"]


def test_the_sweep_applies_the_backlog_once_and_rerunning_appends_nothing(db):
    sids = {t: seed(db, t) for t in ("FLS", "IEHC", "ATCH", "SKYT", "AVAV")}
    for sid in sids.values():
        _as_r8a_left_it(db, sid)
    before = _table_count(db)
    assert before == 1  # AVAV's no-signal-effect apply only (R8-A wrote it)
    queued = {
        i.ticker
        for i in insider_amendment_repo.items(db, security_ids=list(sids.values()))
        if i.review == "queued"
    }
    assert queued == {
        "FLS",
        "IEHC",
    }  # the corrections read applied by their links (auto), SKYT owed
    res = insider_amendment_repo.sweep(db, security_ids=list(sids.values()))
    db.commit()
    assert len(res.applied) == 7  # FLS void, IEHC addition, ATCH's five corrections
    assert res.by_disposition == {"void": 1, "addition": 1, "correction": 5}
    assert res.by_basis == {"system:filer-correction": 7}
    assert _table_count(db) == before + 7  # COUNT THE TABLE
    again = insider_amendment_repo.sweep(db, security_ids=list(sids.values()))
    db.commit()
    assert again.applied == () and _table_count(db) == before + 7  # idempotent: nothing appended
    swept = [d for d in _decisions(db, sids["FLS"])]
    assert [(d["basis"], d["detail"]["via"]) for d in swept] == [
        ("system:filer-correction", "sweep")
    ]
    assert _decisions(db, sids["SKYT"]) == []  # ambiguous: never applied by the system


def test_the_sweep_never_touches_what_the_operator_decided_or_linked(db):
    fls, azta, skyt = seed(db, "FLS"), seed(db, "AZTA"), seed(db, "SKYT")
    for sid in (fls, azta):
        _as_r8a_left_it(db, sid)
    # the operator kept FLS's original; applied then UNDID AZTA's void; linked SKYT to a candidate
    insider_amendment_repo.decide(
        db, fls, "0001628280-26-037326", "keep", reason="x", basis="operator:keep"
    )
    d = insider_amendment_repo.decide(
        db, azta, "0001193125-25-282945", "apply", reason="x", basis="operator:apply"
    )
    insider_amendment_repo.revoke(db, d["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.link_candidate(
        db, skyt, "0001856680-26-000016", "0001856680-26-000013", reason="x", basis="operator:link"
    )
    db.commit()
    before = _table_count(db)
    res = insider_amendment_repo.sweep(db, security_ids=[fls, azta, skyt])
    db.commit()
    assert res.applied == () and _table_count(db) == before
    owed = {
        i.ticker: i.review for i in insider_amendment_repo.items(db, security_ids=[fls, azta, skyt])
    }
    assert owed == {"FLS": "decided", "AZTA": "owed", "SKYT": "owed"}


def test_a_replay_pinned_before_the_sweep_reads_the_pending_state_it_held(db):
    """Bitemporal honesty: the sweep's decisions carry recorded_at = now, so a read pinned before them sees the
    state the system held then — IEHC's original sales (a pending addition has no effect) and FLS's buy
    suppressed (a pending void suppresses) — and a read after sees the applied state. No rewrite of the past.
    """
    iehc, fls = seed(db, "IEHC"), seed(db, "FLS")
    for sid in (iehc, fls):
        _as_r8a_left_it(db, sid)
    pinned = db_now(db)
    db.commit()
    insider_amendment_repo.sweep(db, security_ids=[iehc, fls])
    db.commit()
    asof = date(2026, 10, 20)
    then = _rows(db, iehc, asof, known_at=pinned)
    assert {r["accession"] for r in then} == {"0001174947-26-000817"}  # the original, as held
    now = _rows(db, iehc, asof)
    assert {r["accession"] for r in now} == {"0001174947-26-000849"}  # the filer's restatement
    for known_at in (pinned, FAR):  # the void suppressed while pending AND once applied
        assert "0001628280-26-035913" not in {
            r["accession"] for r in _rows(db, fls, date(2026, 6, 15), known_at=known_at)
        }


# --- PIT: the link's recorded_at gates it -------------------------------------------------------------------


def test_a_read_pinned_before_the_link_sees_the_original(db):
    sid = make_security(db, "ATCH")
    t_orig = datetime(2026, 9, 29, 2, 30, tzinfo=timezone.utc)
    ingest_originals(db, sid, "ATCH", recorded_at=t_orig)
    t_link = datetime(2026, 9, 30, 2, 30, tzinfo=timezone.utc)
    for acc in amendments_of("ATCH"):
        ingest_amendment(db, sid, acc, recorded_at=t_link)
    asof = date(2026, 10, 1)
    before = _rows(db, sid, asof, known_at=t_link - timedelta(microseconds=1))
    assert {r["txn_code"] for r in before} == {"A"} and len(before) == 9
    assert _signal(db, sid, asof, known_at=t_link - timedelta(microseconds=1)) is None
    at = _rows(db, sid, asof, known_at=t_link)
    assert {r["txn_code"] for r in at} == {"P"}
    assert _signal(db, sid, asof, known_at=t_link).grade == Grade.CORE


def test_a_read_pinned_before_a_decision_sees_the_pending_state(db):
    sid = seed(db, "AZTA")
    asof = date(2025, 8, 15)
    before_keep = db_now(db)
    db.commit()
    insider_amendment_repo.decide(
        db, sid, "0001193125-25-282945", "keep", reason="only a POA", basis="operator:keep"
    )
    db.commit()
    assert (
        _signal(db, sid, asof, known_at=before_keep) is None
    )  # what the system held then: pending
    assert _signal(db, sid, asof) is not None


# --- both read shapes agree ----------------------------------------------------------------------------------


def test_as_of_many_equals_the_scoped_read_with_amendments(db):
    sids = [seed(db, t) for t in ("ATCH", "FLS", "TDG", "SKYT")]
    asof, lower = date(2026, 10, 1), date(2026, 3, 1)
    many = as_of_many(
        db,
        "fact_insider_txn",
        security_ids=sids,
        asof=asof,
        known_at=FAR,
        tenant_id=T,
        valid_from_lower=lower,
    )

    def key(r: dict) -> tuple:
        return (r["accession"], r["insider_name"], r["valid_from"], r["txn_seq"])

    for sid in sids:
        scoped = [r for r in _rows(db, sid, asof) if r["valid_from"] >= lower]
        assert sorted(map(key, many[sid])) == sorted(map(key, scoped))


# --- the other-issuer screen --------------------------------------------------------------------------------


def test_a_new_other_issuer_form4_is_not_stored(db):
    """VisionWave's own feed lists its Form 4 as REPORTING OWNER of SaverOne (issuer CIK 1894693)."""
    doc = xml("0001731122-26-000909")
    assert other_issuer_cik(doc, CIKS["VWAV"]) == "1894693"
    assert (
        other_issuer_cik(doc, "0001894693") is None
    )  # on SaverOne's own tape it is its insider flow
    assert other_issuer_cik(doc, None) is None  # no member CIK: keep-when-unknown
    sid = make_security(db, "VWAV")
    out = ingest_form4(db, sid, doc, "0001731122-26-000909", member_cik=CIKS["VWAV"])
    db.commit()
    assert out == (0, 0)
    assert _count(db, "fact_insider_txn", sid) == 0


def test_an_other_issuer_row_stored_before_the_screen_is_kept_and_screened(db):
    sid = make_security(db, "VWAV")
    # stored the way the leg stored it BEFORE R8-A (no member screen) — the 147-row shape on prod
    ingest_form4(db, sid, xml("0001731122-26-000909"), "0001731122-26-000909")
    db.commit()
    assert _count(db, "fact_insider_txn", sid) > 0  # KEPT (deleting them is a separate operator go)
    assert _rows(db, sid, date(2026, 7, 31)) == []  # screened at read: not VWAV's insider flow


def test_the_cik_set_includes_the_cik_change_log(db):
    """The loader's contract: every CIK the security has been known by — the master's AND each T7 re-point's
    prior/new — so a successor-CIK adopt never screens the predecessor's own Form 4 history. (A direct-input
    contract test: the prior CIK is a placeholder, not a claim about VWAV's history.)"""
    sid = make_security(db, "VWAV")
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_cik_change (tenant_id, security_id, action, prior_cik, new_cik, "
            "prior_values, new_values, basis) "
            "VALUES (%s, %s, 'adopt', %s, %s, '{}', '{}', 'operator:adopt-cik')",
            (T, sid, "0009999999", CIKS["VWAV"]),
        )
    db.commit()
    inputs = load_inputs(db, [sid], known_at=FAR, tenant_id=T)
    assert inputs.own_ciks[str(sid)] == {"2038439", "9999999"}
