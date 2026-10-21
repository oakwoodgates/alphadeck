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


def test_the_leg_threads_the_index_acceptance_so_iehc_links_its_real_original(db):
    """The R8 linker fix through the nightly LEG: the submissions index it already reads carries every Form 4's
    acceptance, so IEHC's 4/A — dated its own filing day — refuses ``-000338`` (accepted 49 s after it) and
    links the July 2025 original (clerical: nothing applied, no decision, no duplicate in the read).
    """
    from ingest.edgar.form4a import ingest_amendments
    from tests.form4a_fixtures import FILINGS

    key = "IEHC-MISLINK"
    sid = make_security(db, key)
    ingest_originals(db, sid, key)
    before = _rows(db, sid, date(2026, 10, 20))
    accs = sorted(a for a in FILINGS if FILINGS[a].key == key)
    subs = {
        "filings": {
            "recent": {
                "form": [FILINGS[a].form for a in accs],
                "accessionNumber": accs,
                "primaryDocument": [f"xslF345X06/{a}.xml" for a in accs],
                "filingDate": [FILINGS[a].filed.isoformat() for a in accs],
                "acceptanceDateTime": [FILINGS[a].accepted.isoformat() for a in accs],
            }
        }
    }

    class _Client:  # serves the real documents by the forms/ cache key the leg asks for
        def get_text(self, url, key):
            return xml(key.split("/")[1])

    out, _ = ingest_amendments(db, _Client(), sid, CIKS[key], "IEHC", subs)
    db.commit()
    (one,) = out.stored
    assert one.link.original_accessions == ("0001174947-25-000979",)
    assert (one.link.disposition, one.link.excluded_later) == (
        "clerical",
        ("0001174947-26-000338",),
    )
    assert _count(db, "insider_amendment_decision", sid) == 0
    assert _rows(db, sid, date(2026, 10, 20)) == before


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


def test_an_operator_verdict_stands_over_the_systems_and_revoking_the_systems_hands_it_over(db):
    """An operator verdict stands OVER a system decision (the system's stays on record) and its Undo restores
    the system's. Revoking the system's own decision (an API-only path) leaves the void PENDING — still
    suppressing, R8-A's condition — and makes it the operator's: owed, never re-resolved by the sweep.
    """
    sid = seed(db, "FLS")
    acc, asof = "0001628280-26-037326", date(2026, 6, 15)
    (system,) = _decisions(db, sid)
    use = insider_amendment_repo.decide(db, sid, acc, "apply", reason="x", basis="operator:apply")
    db.commit()
    assert _item(db, sid, acc).review == "decided" and _signal(db, sid, asof) is None
    insider_amendment_repo.revoke(db, use["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.revoke(db, system["id"], reason="mine", basis="operator:revoke")
    db.commit()
    it = _item(db, sid, acc)
    assert (it.effective.status, it.review) == ("pending", "owed")
    assert _signal(db, sid, asof) is None  # a pending void still suppresses
    n = _count(db, "insider_amendment_decision", sid)
    insider_amendment_repo.sweep(db, security_ids=[sid])
    db.commit()
    assert _count(db, "insider_amendment_decision", sid) == n  # the sweep never takes it back
    with pytest.raises(insider_amendment_repo.AmendmentConflict, match="no rows to add"):
        insider_amendment_repo.decide(db, sid, acc, "add", reason="x", basis="operator:add")
    db.rollback()


def test_azta_power_of_attorney_void_leaves_the_buy_standing_and_use_the_correction_hides_it(db):
    """AZTA's zero-row 4/A only attaches a power of attorney: no retraction language, so the system KEEPS the
    original (``system:no-retraction``) — the CFO's buy stands. The operator's Use the correction hides it;
    its Undo brings it back."""
    sid = make_security(db, "AZTA")
    ingest_originals(db, sid, "AZTA")
    asof = date(2025, 8, 15)
    fired = _signal(db, sid, asof)
    assert fired is not None  # the CFO's open-market buy
    ingest_amendment(db, sid, "0001193125-25-282945")
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"]) == ("keep", "system:no-retraction")
    assert _signal(db, sid, asof) == fired  # the buy STANDS
    assert _item(db, sid, "0001193125-25-282945").review == "auto"
    use = insider_amendment_repo.decide(
        db, sid, "0001193125-25-282945", "apply", reason="check", basis="operator:apply"
    )
    db.commit()
    assert _signal(db, sid, asof) is None
    insider_amendment_repo.revoke(db, use["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _signal(db, sid, asof) == fired


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


def test_an_ambiguous_link_suppresses_nothing_until_the_operator_links_it(db):
    """HROW: both candidates hold a row the 4/A accounts for (the identical sale; the same sale at another
    price), so it stays ambiguous — nothing changes until the operator links one; the link's Undo restores it.
    """
    sid = make_security(db, "HROW")
    ingest_originals(db, sid, "HROW")
    asof = date(2025, 5, 31)
    original = _rows(db, sid, asof)
    assert sorted(float(r["price"]) for r in original) == [29.64, 30.011]
    out = ingest_amendment(db, sid, "0001641172-25-010338")
    assert out.link.disposition == "ambiguous" and not out.link.tie_break
    assert _rows(db, sid, asof) == original  # flagged, never applied
    assert _count(db, "insider_amendment_decision", sid) == 0  # the operator's, not the system's
    link = insider_amendment_repo.link_candidate(
        db,
        sid,
        "0001641172-25-010338",
        "0001641172-25-000741",
        reason="the price fix",
        basis="operator:link",
    )
    db.commit()
    assert link["disposition"] == "correction"  # a 1:1 correction applies by its link
    now = _rows(db, sid, asof)
    assert sorted((r["accession"], float(r["price"])) for r in now) == [
        ("0001493152-25-011028", 29.64),
        ("0001641172-25-010338", 29.64),
    ]
    insider_amendment_repo.revoke(db, link["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _rows(db, sid, asof) == original


def test_the_skyt_tie_break_is_applied_at_ingest_replacing_only_the_restated_sale(db):
    """SKYT's two candidates: only ``-000013`` holds a row the 4/A accounts for (its 81,401-share sale, restated
    as 71,693 — same owner, date, code, price), so the ingest links it and the system applies the filer's
    correction in the same transaction: that one sale is replaced; ``-000010``'s withholding and ``-000013``'s
    exercise-and-sale lines stand."""
    sid = make_security(db, "SKYT")
    ingest_originals(db, sid, "SKYT")
    asof = date(2026, 3, 31)
    original = _rows(db, sid, asof)
    out = ingest_amendment(db, sid, "0001856680-26-000016")
    assert (out.link.disposition, out.link.tie_break) == ("partial", True)
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"], d["replaced_rows"]) == (
        "apply",
        "system:filer-correction",
        ["0001856680-26-000013#2"],
    )
    now = _rows(db, sid, asof)
    assert len(now) == len(original)
    assert sorted((r["accession"], r["txn_code"], float(r["shares"])) for r in now) == [
        ("0001856680-26-000010", "F", 2842.0),
        ("0001856680-26-000013", "M", 9708.0),
        ("0001856680-26-000013", "S", 9708.0),
        ("0001856680-26-000016", "S", 71693.0),
    ]


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


def test_a_grant_only_void_without_retraction_words_leaves_the_grants_standing(db):
    """AVAV: a zero-row 4/A voiding an original of code-A grants only, its text retracting nothing — the system
    keeps the original (``system:no-retraction``); the operator's Use the correction hides the grants, its Undo
    restores them."""
    sid = make_security(db, "AVAV")
    ingest_originals(db, sid, "AVAV")
    out = ingest_amendment(db, sid, "0001538012-26-000006")
    assert out.link.void_kind == "no_retraction" and not out.link.signal_effect
    (d,) = _decisions(db, sid)
    assert (d["action"], d["basis"]) == ("keep", "system:no-retraction")
    it = _item(db, sid, "0001538012-26-000006")
    assert it.auto_resolved and not it.needs_operator and it.effective.status == "kept"
    assert it.system_basis == "system:no-retraction"
    asof = date(2026, 7, 31)
    assert len(_rows(db, sid, asof)) == 2  # the grants stand
    use = insider_amendment_repo.decide(
        db, sid, "0001538012-26-000006", "apply", reason="a holdings fix", basis="operator:apply"
    )
    db.commit()
    assert _rows(db, sid, asof) == []
    insider_amendment_repo.revoke(db, use["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert len(_rows(db, sid, asof)) == 2


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


def test_an_unresolved_match_is_owed_and_each_of_the_three_verdicts_has_its_undo(db):
    """YYAI's 4/A re-reports its buys after a reverse split in words that neither fix nor add: no system
    decision, OWED (inside the review horizon), the original standing. The operator's three verdicts:
    Use the correction (the whole original replaced), Keep the original, Add to the original (every original
    stands and the amendment's rows are added) — each with its Undo."""
    sid = seed(db, "YYAI")
    acc, asof = "0002091629-25-000011", date(2025, 10, 31)
    original = {r["accession"] for r in _rows(db, sid, asof)}
    assert original == {"0002091629-25-000003"} and _decisions(db, sid) == []
    it = _item(db, sid, acc)
    assert (it.review, it.unresolved, it.in_review) == ("owed", True, True)
    add = insider_amendment_repo.decide(db, sid, acc, "add", reason="new buy", basis="operator:add")
    db.commit()
    assert add["action"] == "apply" and add["replaced_rows"] == []
    rows = _rows(db, sid, asof)
    assert sorted((r["accession"], r["txn_code"]) for r in rows) == [
        ("0002091629-25-000003", "P"),
        ("0002091629-25-000003", "P"),
        ("0002091629-25-000011", "A"),
        ("0002091629-25-000011", "P"),
    ]
    assert _item(db, sid, acc).review == "decided"
    insider_amendment_repo.revoke(db, add["id"], reason="undo", basis="operator:revoke")
    use = insider_amendment_repo.decide(
        db, sid, acc, "apply", reason="restated", basis="operator:apply"
    )
    db.commit()
    assert {r["accession"] for r in _rows(db, sid, asof)} == {acc}  # the whole original replaced
    assert use["replaced_rows"] is None
    insider_amendment_repo.revoke(db, use["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.decide(db, sid, acc, "keep", reason="as filed", basis="operator:keep")
    db.commit()
    assert {r["accession"] for r in _rows(db, sid, asof)} == original


def test_an_omitted_line_amendment_keeps_the_sales_it_never_mentions(db):
    """MRCY's 4/A adds only the omitted grant: applied at ingest (no signal effect — the sales are untouched),
    both sales STAND beside the added grant. BAH's recodes a sale to a tax withholding (same date and shares):
    the sale is replaced, the original's unmentioned grant stands."""
    mrcy = seed(db, "MRCY")
    rows = _rows(db, mrcy, date(2026, 10, 20))
    assert sorted((r["accession"], r["txn_code"], float(r["shares"])) for r in rows) == [
        ("0001049521-26-000054", "S", 404.0),
        ("0001049521-26-000054", "S", 494.0),
        ("0001049521-26-000071", "A", 2882.0),
    ]
    (d,) = _decisions(db, mrcy)
    assert (d["basis"], d["replaced_rows"]) == ("system:no-signal-effect", [])
    it = _item(db, mrcy, "0001049521-26-000071")
    assert it.kept_rows == ("0001049521-26-000054#0", "0001049521-26-000054#1")
    bah = seed(db, "BAH")  # eight officers' Form 4s, each recoded by its own 4/A
    rows = _rows(db, bah, date(2024, 6, 30))
    assert sorted(
        (r["accession"], r["txn_code"])
        for r in rows
        if r["accession"] in ("0001443646-24-000061", "0001443646-24-000094")
    ) == [("0001443646-24-000061", "A"), ("0001443646-24-000094", "F")]
    d = next(x for x in _decisions(db, bah) if x["amendment_accession"] == "0001443646-24-000094")
    assert (d["basis"], d["replaced_rows"]) == (
        "system:filer-correction",
        ["0001443646-24-000061#1"],
    )


def test_a_recent_ambiguous_amendment_is_owed_and_an_old_one_is_historical(db):
    """HROW (its candidates' sales dated 2025-03-18, inside the 940-day review horizon at 2026-10-20) is OWED
    the operator; ASTH (code-P rows of 2008 / 2012) is "unresolved — historical": no call reads those rows, the
    originals stand exactly as stored, nothing is decided for anyone."""
    hrow, asth = seed(db, "HROW"), make_security(db, "ASTH")
    ingest_originals(db, asth, "ASTH")
    asof = date(2013, 8, 31)
    original = _rows(db, asth, asof)
    assert {r["txn_code"] for r in original} == {"P"}
    ingest_amendment(db, asth, "0001134366-13-000009")
    assert _rows(db, asth, asof) == original  # the originals stand
    found = {
        i.ticker: i
        for i in insider_amendment_repo.items(
            db, security_ids=[hrow, asth], asof=date(2026, 10, 20)
        )
    }
    assert (found["HROW"].review, found["HROW"].in_review, found["HROW"].needs_operator) == (
        "owed",
        True,
        True,
    )
    assert (found["ASTH"].review, found["ASTH"].in_review, found["ASTH"].needs_operator) == (
        "historical",
        False,
        False,
    )
    assert found["ASTH"].signal_effect  # it does touch code-P rows — just ones no call reads
    assert _decisions(db, hrow) == [] and _decisions(db, asth) == []
    # the horizon is the dial: unbounded, ASTH would be owed too
    again = insider_amendment_repo.items(
        db, security_ids=[asth], asof=date(2026, 10, 20), review_days=None
    )
    assert again[0].review == "owed"


# --- R8-B: the SWEEP of what a pre-R8-B ingest left pending -------------------------------------------------


def _as_r8a_left_it(db, sid) -> None:
    """Reproduce the decision-less backlog the sweep exists for: drop the decisions R8-B's ingest wrote
    (``detail.via = ingest``), leaving the links as a pre-R8-B ingest stored them — no decision on a correction
    or a signal-touching amendment. (A test-DB fixture step: prod holds this shape for the 1,098 links R8-A left.)
    """
    with db.cursor() as cur:
        cur.execute(
            "DELETE FROM insider_amendment_decision WHERE security_id = %s AND detail->>'via' = 'ingest'",
            (sid,),
        )
    db.commit()


def _table_count(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM insider_amendment_decision")
        return cur.fetchone()["n"]


def test_the_sweep_applies_the_backlog_once_and_rerunning_appends_nothing(db):
    sids = {t: seed(db, t) for t in ("FLS", "MRCY", "ATCH", "HROW", "IEHC", "SMR", "FIGR", "YYAI")}
    for sid in sids.values():
        _as_r8a_left_it(db, sid)
    before = _table_count(db)
    assert before == 0
    review = {
        i.accession: i.review
        for i in insider_amendment_repo.items(db, security_ids=list(sids.values()))
    }
    assert {a for a, r in review.items() if r == "queued"} == {
        "0001628280-26-037326",  # FLS void (a retraction)
        "0001049521-26-000071",  # MRCY partial (the grant added)
        "0001174947-26-000849",  # IEHC addition (FIX words)
        "0001922054-26-000007",  # SMR void (no retraction)
        "0001493152-25-024508",  # FIGR void (held)
    }  # the ATCH corrections read applied by their links (auto); HROW / YYAI are owed
    res = insider_amendment_repo.sweep(db, security_ids=list(sids.values()))
    db.commit()
    assert len(res.applied) == 10  # FLS, MRCY, IEHC, SMR, FIGR + ATCH's five corrections
    assert res.by_disposition == {
        "void": 3,
        "partial": 1,
        "addition": 1,
        "correction": 5,
    }
    assert res.by_basis == {
        "system:filer-correction": 7,
        "system:no-signal-effect": 1,
        "system:no-retraction": 1,
        "system:held": 1,
    }
    assert _table_count(db) == before + 10  # COUNT THE TABLE
    again = insider_amendment_repo.sweep(db, security_ids=list(sids.values()))
    db.commit()
    assert again.applied == () and _table_count(db) == before + 10  # idempotent: nothing appended
    (fls,) = _decisions(db, sids["FLS"])
    assert (fls["action"], fls["basis"], fls["detail"]["via"], fls["replaced_rows"]) == (
        "apply",
        "system:filer-correction",
        "sweep",
        None,  # a void removes; it carries no row match
    )
    (smr,) = _decisions(db, sids["SMR"])
    assert (smr["action"], smr["basis"]) == ("keep", "system:no-retraction")
    (mrcy,) = _decisions(db, sids["MRCY"])
    assert mrcy["replaced_rows"] == []  # the sweep stores the row match: the sales stand
    assert _decisions(db, sids["HROW"]) == [] and _decisions(db, sids["YYAI"]) == []
    owed = {
        i.ticker
        for i in insider_amendment_repo.items(db, security_ids=list(sids.values()))
        if i.needs_operator
    }
    assert owed == {"HROW", "YYAI", "FIGR"}


def test_the_sweep_never_touches_what_the_operator_decided_or_linked(db):
    fls, azta, hrow = seed(db, "FLS"), seed(db, "AZTA"), seed(db, "HROW")
    for sid in (fls, azta):
        _as_r8a_left_it(db, sid)
    # the operator kept FLS's original; applied then UNDID AZTA's void; linked HROW to a candidate
    insider_amendment_repo.decide(
        db, fls, "0001628280-26-037326", "keep", reason="x", basis="operator:keep"
    )
    d = insider_amendment_repo.decide(
        db, azta, "0001193125-25-282945", "apply", reason="x", basis="operator:apply"
    )
    insider_amendment_repo.revoke(db, d["id"], reason="undo", basis="operator:revoke")
    insider_amendment_repo.link_candidate(
        db, hrow, "0001641172-25-010338", "0001493152-25-011028", reason="x", basis="operator:link"
    )
    db.commit()
    before = _table_count(db)
    res = insider_amendment_repo.sweep(db, security_ids=[fls, azta, hrow])
    db.commit()
    assert res.applied == () and _table_count(db) == before
    owed = {
        i.ticker: i.review for i in insider_amendment_repo.items(db, security_ids=[fls, azta, hrow])
    }
    # HROW: linked to the identical filing -> clerical, nothing to apply (the operator's link stands)
    assert owed == {"FLS": "decided", "AZTA": "owed", "HROW": "inert"}


def test_a_replay_pinned_before_the_sweep_reads_the_pending_state_it_held(db):
    """Bitemporal honesty: the sweep's decisions carry recorded_at = now, so a read pinned before them sees the
    state the system held then — FRHC's original title (a pending field change has no effect), FLS's buy and
    SMR's sale suppressed (a pending void suppresses) — and a read after sees the resolved state: FRHC's title
    corrected, FLS's canceled buy still hidden, SMR's sale back (its 4/A retracts nothing). No rewrite of the
    past."""
    frhc, fls, smr = seed(db, "FRHC"), seed(db, "FLS"), seed(db, "SMR")
    for sid in (frhc, fls, smr):
        _as_r8a_left_it(db, sid)
    pinned = db_now(db)
    db.commit()
    insider_amendment_repo.sweep(db, security_ids=[frhc, fls, smr])
    db.commit()
    asof = date(2026, 8, 31)
    then = _rows(db, frhc, asof, known_at=pinned)
    assert {r["insider_role"] for r in then} == {"Chief Financial Officer"}  # as held then
    now = _rows(db, frhc, asof)
    assert {r["insider_role"] for r in now} == {"Special Advisor to CEO"}  # the filer's correction
    for known_at in (pinned, FAR):  # the retraction hides the buy while pending AND once applied
        assert "0001628280-26-035913" not in {
            r["accession"] for r in _rows(db, fls, date(2026, 6, 15), known_at=known_at)
        }
    smr_asof = date(2026, 9, 30)
    assert "S" not in {r["txn_code"] for r in _rows(db, smr, smr_asof, known_at=pinned)}
    assert "S" in {r["txn_code"] for r in _rows(db, smr, smr_asof)}


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


def test_a_read_pinned_before_a_decision_sees_the_state_it_held(db):
    sid = seed(db, "FLS")
    asof = date(2026, 6, 15)
    before_keep = db_now(db)
    db.commit()
    insider_amendment_repo.decide(
        db, sid, "0001628280-26-037326", "keep", reason="not canceled", basis="operator:keep"
    )
    db.commit()
    assert _signal(db, sid, asof, known_at=before_keep) is None  # what the system held then
    assert _signal(db, sid, asof) is not None


# --- R8-B revised (2026-10-21): the REAL-ROW check table ------------------------------------------------------


def test_the_real_row_check_table(db):
    """Every filing of the check table on its REAL documents, end to end through the ingest and the as-of read:
    what is hidden, what stands, what is added, and where it sits for the operator (asof 2026-10-20).
    """
    asof = date(2026, 10, 20)

    def codes(sid, accession, day=asof):
        return sorted(r["txn_code"] for r in _rows(db, sid, day) if r["accession"] == accession)

    def review(sid, accession):
        return next(
            i.review
            for i in insider_amendment_repo.items(db, security_ids=[sid], asof=asof)
            if i.accession == accession
        )

    # zero-row 4/As: no retraction -> the original stands (inert, quiet)
    smr = seed(db, "SMR")
    assert codes(smr, "0001922054-26-000003") == ["M", "S"]
    assert review(smr, "0001922054-26-000007") == "auto"
    azta = seed(db, "AZTA")
    assert codes(azta, "0000950170-25-107291", date(2025, 8, 15)) == ["P"]
    vwav = seed(db, "VWAV")
    assert (
        len(codes(vwav, "0002105221-26-000001")) == 2
        and review(vwav, "0002105221-26-000002") == "auto"
    )
    # retraction words against several trades -> held, owed, the original standing
    figr = seed(db, "FIGR")
    assert codes(figr, "0002079716-25-000008") == ["C", "F", "S"]
    assert review(figr, "0001493152-25-024508") == "owed"
    # a retraction of a single-trade original -> hidden
    for t, originals in (
        ("FLS", ("0001628280-26-035913",)),
        ("AVEX", ("0001193125-26-164679",)),
        ("MNKD", ("0001193125-26-100839",)),
        ("DVLT", ("0001104659-24-122190", "0001104659-24-122191")),
    ):
        sid = seed(db, t)
        for orig in originals:
            assert codes(sid, orig) == [], (t, orig)
    # ADD words: IBKR's four sales stand, the omitted 6,928 sh is added
    ibkr = seed(db, "IBKR")
    assert codes(ibkr, "0001381197-25-000131") == ["S", "S", "S", "S"]
    assert [
        float(r["shares"])
        for r in _rows(db, ibkr, asof)
        if r["accession"] == "0001381197-25-000139"
    ] == [6928.0]
    # FIX words: MRAM ×5 and BKSY ×4 replace their sales; IEHC (FIX + ADD, its rows restate every sale) too
    mram = seed(db, "MRAM")
    for orig in ("047404", "047402", "047411"):
        assert codes(mram, f"0001628280-26-{orig}") == []
    for orig in ("023225", "023218"):  # the grant beside the sale is not re-reported — it stands
        assert codes(mram, f"0001628280-26-{orig}") == ["A"]
    bksy = seed(db, "BKSY")
    for orig in (
        "0001615454-25-000011",
        "0001400954-25-000009",
        "0001349983-25-000008",
        "0001443092-25-000012",
    ):
        assert codes(bksy, orig) == []
    iehc = seed(db, "IEHC")
    assert (
        codes(iehc, "0001174947-26-000817") == []
        and codes(iehc, "0001174947-26-000849") == ["S"] * 4
    )
    # neither -> owed, the original standing
    yyai = seed(db, "YYAI")
    assert codes(yyai, "0002091629-25-000003") == ["P", "P"]
    assert review(yyai, "0002091629-25-000011") == "owed"
    # shape: BAH's eight recodes (S -> F; the grant stands); MRCY's grant added beside its sales; sums
    bah = seed(db, "BAH")
    assert {r["txn_code"] for r in _rows(db, bah, date(2024, 6, 30))} == {"A", "F"}
    mrcy = seed(db, "MRCY")
    assert codes(mrcy, "0001049521-26-000054") == ["S", "S"]
    assert codes(mrcy, "0001049521-26-000071") == ["A"]
    betr = seed(db, "BETR")
    assert codes(betr, "0001628280-24-049753") == []
    assert len(codes(betr, "0001628280-24-050110")) == 2
    bhvn = seed(db, "BHVN")
    assert sorted(
        float(r["shares"]) for r in _rows(db, bhvn, date(2025, 3, 31)) if r["txn_code"] == "P"
    ) == [900.0, 3400.0, 28400.0]


# --- the composition of several applied amendments of one original (2026-10-21) ------------------------------


def _lines(rows: list[dict]) -> list[tuple]:
    return sorted((r["accession"], r["txn_code"], float(r["shares"])) for r in rows)


def _decisions_of(db, sid, accession: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT * FROM insider_amendment_decision WHERE security_id = %s AND amendment_accession = %s",
            (sid, accession),
        )
        return cur.fetchall()


def test_coin_two_row_level_corrections_of_one_original_both_stand(db):
    """COIN's original carries seven lines; one 4/A restates its withholding (F 2,308 -> 2,247), a later one an
    exercise line (M 290). Each replaces only its own line, and BOTH amendments' rows are read — before the
    composition the newest-only rule dropped the earlier one's withholding while still suppressing the 2,308
    (the row silently gone from the tape: MEASURED on prod)."""
    sid = seed(db, "COIN")
    original = "0001415889-24-004798"
    (d1,) = _decisions_of(db, sid, "0001679788-24-000053")
    (d2,) = _decisions_of(db, sid, "0001679788-25-000033")
    assert d1["replaced_rows"] and d2["replaced_rows"]
    assert set(d1["replaced_rows"]).isdisjoint(d2["replaced_rows"])  # different lines
    got = _rows(db, sid, date(2025, 3, 31))
    assert ("0001679788-24-000053", "F", 2247.0) in _lines(got)
    assert ("0001679788-25-000033", "M", 290.0) in _lines(got)
    assert ("0001415889-24-004798", "F", 2308.0) not in _lines(got)  # replaced, not stacked
    assert (
        len([r for r in got if r["accession"] == original]) == 7 - 2
    )  # the rest of the original stands
    assert len(got) == 7  # seven lines in, seven lines out


def test_lfst_a_second_correction_of_the_same_sale_replaces_the_first(db):
    """LFST: one 4/A restates the 10,000-share 2022-06-09 sale as 5,000; a later one restates it back to
    10,000. A read pinned between them reads the first correction; after both, the second REPLACES it — never
    5,000 + 10,000. A PIT read pinned before both reads the original exactly."""
    sid = make_security(db, "LFST")
    t0 = db_now(db) - timedelta(days=3)
    t1, t2 = t0 + timedelta(days=1), t0 + timedelta(days=2)
    ingest_originals(db, sid, "LFST", recorded_at=t0)
    ingest_amendment(db, sid, "0000899243-22-023093", recorded_at=t1)
    ingest_amendment(db, sid, "0000899243-22-023602", recorded_at=t2)
    asof = date(2022, 6, 30)
    assert _lines(_rows(db, sid, asof, t1 - timedelta(seconds=1))) == [
        ("0000899243-22-022392", "S", 10000.0),
        ("0000899243-22-022392", "S", 67479.0),
    ]
    assert _lines(_rows(db, sid, asof, t1 + timedelta(seconds=1))) == [
        ("0000899243-22-022392", "S", 67479.0),
        ("0000899243-22-023093", "S", 5000.0),
    ]
    assert _lines(_rows(db, sid, asof)) == [
        ("0000899243-22-022392", "S", 67479.0),
        ("0000899243-22-023602", "S", 10000.0),
    ]


def test_amsc_amendments_of_different_trades_of_one_original_all_stand(db):
    """AMSC: ``-10-062237`` re-dates two buys of the Dec 2010 Form 4 (34,000 + 20,000 to 2010-12-17); the 2011
    4/A ``-11-004333`` — dated its own filing day, the same-day ``-11-004336`` accepted AFTER it and refused —
    re-reports the 80,000-share buy. They replace no common original line, so every row stands: the R8 re-link
    would otherwise have dropped the two re-dated buys (the newest-only rule)."""
    sid = seed(db, "AMSC")
    out = {i.accession: i for i in insider_amendment_repo.items(db, security_ids=[sid])}
    db.rollback()
    late = out["0001209191-11-004333"]
    assert late.effective.original_accessions == ("0001209191-10-061925",)
    got = [r for r in _rows(db, sid, date(2010, 12, 31))]
    assert _lines(got) == [
        ("0001209191-10-061925", "P", 66000.0),
        ("0001209191-10-062237", "P", 20000.0),
        ("0001209191-10-062237", "P", 34000.0),
        ("0001209191-11-004333", "P", 80000.0),
    ]


def test_onto_a_4a_that_supersedes_the_earlier_amendment_replaces_its_rows_entirely(db):
    """ONTO: ``-000045`` recodes the three ``D`` withholdings ``F`` and adds the RSU grant; ``-000102`` re-counts
    the withholdings and repeats the grant — "This amendment supersedes the amendment filed on February 11, 2020".
    It accounts for no ORIGINAL line by exact shape (codes and counts both differ), so by shape alone the two
    amendments' lines would stack (the grant twice, six withholdings). The filer's SUPERSEDE words name the
    earlier amendment: its rows leave entirely. A read pinned between the two reads ``-000045``'s statement.
    """
    sid = make_security(db, "ONTO")
    t0 = db_now(db) - timedelta(days=3)
    t1, t2 = t0 + timedelta(days=1), t0 + timedelta(days=2)
    ingest_originals(db, sid, "ONTO", recorded_at=t0)
    ingest_amendment(db, sid, "0001407682-20-000045", recorded_at=t1)
    ingest_amendment(db, sid, "0001407682-20-000102", recorded_at=t2)
    (early,) = _decisions_of(db, sid, "0001407682-20-000045")
    (late,) = _decisions_of(db, sid, "0001407682-20-000102")
    assert (
        len(early["replaced_rows"]) == 3 and late["replaced_rows"] == []
    )  # both applied row by row
    asof = date(2020, 3, 31)
    assert _lines(_rows(db, sid, asof, t2 - timedelta(seconds=1))) == [
        ("0001407682-20-000045", "A", 31210.0),
        ("0001407682-20-000045", "F", 1375.0),
        ("0001407682-20-000045", "F", 1391.0),
        ("0001407682-20-000045", "F", 1466.0),
    ]
    assert _lines(_rows(db, sid, asof)) == [
        ("0001407682-20-000102", "A", 31210.0),
        ("0001407682-20-000102", "F", 2730.0),
        ("0001407682-20-000102", "F", 2760.0),
        ("0001407682-20-000102", "F", 2910.0),
    ]


def test_asth_a_restatement_the_rules_cannot_account_for_stands_beside_the_earlier_line(db):
    """The known limit, stated on the real filings: ASTH ``-18-000259`` re-computes the merger-day lines as
    1,563,763 and 1,682,109 after the 1:1 CORRECTION ``-17-001403`` reported 1,664,054. No exact shape pairs them
    (the counts differ; no price), they do not SUM to it (3,245,872), and its words carry no FIX or SUPERSEDE
    sentence ("to correct a miscalculation in the number of shares … deemed to be beneficially owned" is not one
    of the phrases). With no looser owner + date + code rule, the correction's line stands beside them.
    """
    sid = seed(db, "ASTH-COMPOSE")
    out = {i.accession: i for i in insider_amendment_repo.items(db, security_ids=[sid])}
    db.rollback()
    assert out["0001553350-17-001403"].effective.disposition == "correction"
    assert out["0001553350-18-000259"].effective.original_accessions == ("0001553350-17-001361",)
    assert _lines(_rows(db, sid, date(2017, 12, 31))) == [
        ("0001553350-17-001403", "J", 1664054.0),
        ("0001553350-18-000259", "J", 1563763.0),
        ("0001553350-18-000259", "J", 1682109.0),
    ]


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
