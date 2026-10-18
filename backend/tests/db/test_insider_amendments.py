"""R8-A — the Form 4/A amendments END TO END on Postgres, on REAL filings (``tests/form4a_fixtures.py``):
ingest + link (COUNT the tables), the read-path resolution through ``db.bitemporal`` (both the scoped
``as_of`` and the basket ``as_of_many``), the operator's decisions and their Undo, the point-in-time gate on
the link's ``recorded_at``, and the other-issuer screen.

Every behavior is asserted on the call's OWN detector (``signals.insider_conviction``) or on counted rows —
never on a fitted number: the ATCH CORE is the five real 4/As' own arithmetic, the FLS loss of CORE is the
real cancellation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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


def test_fls_pending_void_suppresses_the_cancelled_buy(db):
    sid = make_security(db, "FLS")
    ingest_originals(db, sid, "FLS")
    asof = date(2026, 6, 15)
    before = _signal(db, sid, asof)
    assert before is not None and before.grade == Grade.CORE  # two directors, $231,615
    out = ingest_amendment(db, sid, "0001628280-26-037326")
    assert (out.link.disposition, out.link.status) == ("void", "pending")
    assert _signal(db, sid, asof) is None  # PENDING already suppresses (the operator's condition)
    assert "0001628280-26-035913" not in {r["accession"] for r in _rows(db, sid, asof)}
    assert _count(db, "fact_insider_txn", sid) == 3  # suppressed at read, never deleted


def test_ratify_void_keeps_it_suppressed_and_its_undo_too(db):
    sid = seed(db, "FLS")
    acc, asof = "0001628280-26-037326", date(2026, 6, 15)
    d = insider_amendment_repo.decide(
        db, sid, acc, "apply", reason="cancelled", basis="operator:apply"
    )
    db.commit()
    assert _signal(db, sid, asof) is None
    insider_amendment_repo.revoke(db, d["id"], reason="undo", basis="operator:revoke")
    db.commit()
    assert _signal(db, sid, asof) is None  # back to PENDING — still suppressing


def test_azta_keep_original_restores_the_buy_and_undo_suppresses_again(db):
    sid = make_security(db, "AZTA")
    ingest_originals(db, sid, "AZTA")
    asof = date(2025, 8, 15)
    fired = _signal(db, sid, asof)
    assert fired is not None  # the CFO's open-market buy
    ingest_amendment(db, sid, "0001193125-25-282945")
    assert (
        _signal(db, sid, asof) is None
    )  # the power-of-attorney 4/A reads as a void: pending suppresses
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
