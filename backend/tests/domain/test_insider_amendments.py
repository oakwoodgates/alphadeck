"""R8-A — the PURE amendment rules on REAL filings (``tests/form4a_fixtures.py`` names each one's provenance):
the parse, the link cascade, the dispositions, the effective-state machine and the resolver's contract.

Each disposition is asserted on the real document that measured it on 2026-10-17 — the FLS cancellation is a
void, the ATCH recodes are corrections, the TDG duplicates are a 1->N void, the RPAY flag change is clerical,
the SKYT pair is ambiguous. The state-machine and resolver-contract tests state the functions' contracts
with direct inputs (no outcome is fitted: each input is the shape the ingest stores)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from domain.insider_amendments import (
    AMBIGUOUS,
    AMENDS_ACCESSION,
    CLERICAL,
    CONSOLIDATION,
    CORRECTION,
    DUPLICATES,
    METHOD_ORIG_DATE_OWNER,
    NO_ROWS,
    PARTIAL,
    STATUS_APPLIED,
    STATUS_INERT,
    STATUS_PENDING,
    VOID,
    AmendmentFacts,
    classify,
    effect_of,
    is_other_issuer,
    link_amendment,
    resolve_insider_rows,
)
from ingest.edgar.form4 import parse_form4
from ingest.edgar.form4a import _json_row, parse_form4a
from tests.form4a_fixtures import (
    FILINGS,
    amendments_of,
    filed_by_accession,
    originals_of,
    xml,
)

T0 = datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc)


def _stored(ticker: str) -> dict[str, list[dict]]:
    """The ticker's originals as the leg STORES them (``parse_form4`` -> the stored columns)."""
    out: dict[str, list[dict]] = {}
    for acc in originals_of(ticker):
        rows = []
        for i, t in enumerate(parse_form4(xml(acc))):
            rows.append(
                {
                    "accession": acc,
                    "insider_name": t["insider_name"],
                    "valid_from": t["txn_date"],
                    "txn_seq": i,
                    "txn_code": t["txn_code"],
                    "shares": t["shares"],
                    "price": t["price"],
                    "rpt_owner_cik": t["rpt_owner_cik"],
                    "accepted": FILINGS[acc].accepted,
                }
            )
        out[acc] = rows
    return out


def _facts(accession: str) -> AmendmentFacts:
    p = parse_form4a(xml(accession))
    f = FILINGS[accession]
    return AmendmentFacts(
        accession=accession,
        owners=p.owners,
        date_of_original_submission=p.date_of_original_submission,
        filed=f.filed,
        accepted=f.accepted,
        rows=p.rows,
    )


def _link(accession: str):
    t = FILINGS[accession].ticker
    return link_amendment(_facts(accession), _stored(t), filed_by_accession(t))


# --- the parse ------------------------------------------------------------------------------------------


def test_parse_form4a_reads_the_amendment_header_and_every_owner():
    p = parse_form4a(xml("0001104659-26-041960"))  # RPAY: a joint filing of four reporting owners
    assert p.document_type == "4/A"
    assert p.issuer_cik == "1720592"
    assert p.date_of_original_submission == date(2026, 4, 9)
    assert [o[1] for o in p.owners] == [
        "Forager Fund, L.P.",
        "Forager Capital Management, LLC",
        "Kissel Edward Urban",
        "MacArthur Robert Symmes",
    ]
    assert len(p.rows) == 3 and [r["txn_seq"] for r in p.rows] == [0, 1, 2]
    assert any("acquisition/disposition code" in f["text"] for f in p.footnotes)


def test_the_fls_amendment_really_has_no_table_i_and_says_cancelled():
    p = parse_form4a(xml("0001628280-26-037326"))
    assert p.rows == ()
    assert any("cancelled by the Reporting Person's broker" in f["text"] for f in p.footnotes)


# --- dispositions on the real documents --------------------------------------------------------------------


def test_fls_cancellation_links_uniquely_and_is_a_void():
    link = _link("0001628280-26-037326")
    assert link.method == METHOD_ORIG_DATE_OWNER
    assert link.original_accessions == ("0001628280-26-035913",)
    assert (link.disposition, link.status) == (VOID, STATUS_PENDING)


def test_azta_power_of_attorney_amendment_is_a_void_routed_never_auto_applied():
    link = _link("0001193125-25-282945")
    assert link.original_accessions == ("0000950170-25-107291",)
    assert (link.disposition, link.status) == (VOID, STATUS_PENDING)


def test_avex_never_held_is_a_void():
    assert _link("0001193125-26-166819").disposition == VOID


@pytest.mark.parametrize("accession", amendments_of("ATCH"))
def test_each_atch_recode_is_an_auto_applied_correction(accession):
    link = _link(accession)
    assert link.method == METHOD_ORIG_DATE_OWNER and len(link.original_accessions) == 1
    assert (link.disposition, link.status) == (CORRECTION, STATUS_APPLIED)


def test_arbe_share_count_fix_is_a_correction():
    link = _link("0001213900-26-107326")
    assert link.original_accessions == ("0001213900-26-106000",)
    assert link.disposition == CORRECTION


def test_rpay_flag_only_amendment_is_clerical():
    link = _link("0001104659-26-041960")
    assert link.original_accessions == ("0001104659-26-041600",)
    assert (link.disposition, link.status) == (CLERICAL, STATUS_INERT)


def test_tdg_duplicates_keep_the_earliest_and_void_the_rest():
    link = _link("0001352573-26-000011")
    assert link.disposition == DUPLICATES and link.status == STATUS_PENDING
    assert link.original_accessions == ("0001352573-26-000008",)
    assert link.voided_accessions == ("0001352573-26-000009", "0001352573-26-000010")
    assert set(link.candidate_accessions) == {
        "0001352573-26-000008",
        "0001352573-26-000009",
        "0001352573-26-000010",
    }


def test_skyt_two_different_originals_on_the_date_is_ambiguous():
    link = _link("0001856680-26-000016")
    assert link.disposition == AMBIGUOUS and link.status == STATUS_PENDING
    assert link.original_accessions == ()
    assert link.candidate_accessions == ("0001856680-26-000010", "0001856680-26-000013")


def test_classify_against_the_skyt_candidate_the_amendment_corrects_is_partial():
    stored = _stored("SKYT")
    rows = parse_form4a(xml("0001856680-26-000016")).rows
    # one corrected S row against the original's M + two S rows: a (code, date) key it omits -> partial
    assert classify(rows, stored["0001856680-26-000013"]) == PARTIAL


def test_classify_no_rows_either_side_and_consolidation_rule():
    assert classify([], []) == NO_ROWS
    # the rule's contract with direct inputs (the SHAPE BHVN's 4/A had on 2026-10-17 — N rows of one
    # (code, date) restated as fewer rows of the same (code, date); the numbers here are placeholders)
    orig = [
        {"txn_code": "P", "valid_from": date(2025, 3, 4), "shares": s, "price": 30.0}
        for s in (1, 2, 3)
    ]
    amend = [{"txn_code": "P", "txn_date": "2025-03-04", "shares": 6.0, "price": 30.0}]
    assert classify(amend, orig) == CONSOLIDATION


# --- the effective-state machine (the operator's decisions) -----------------------------------------------


def _link_rec(disposition: str, *, originals=("O",), voided=(), status=STATUS_PENDING) -> dict:
    return {
        "id": uuid4(),
        "amendment_accession": "A",
        "method": METHOD_ORIG_DATE_OWNER,
        "disposition": disposition,
        "status": status,
        "original_accessions": list(originals),
        "voided_accessions": list(voided),
        "candidate_accessions": list(originals) + list(voided),
        "recorded_at": T0,
    }


def _dec(action: str, **kw) -> dict:
    return {"id": uuid4(), "amendment_accession": "A", "action": action, "recorded_at": T0, **kw}


def test_a_pending_void_suppresses_and_ratify_keeps_it_and_keep_restores():
    link = _link_rec(VOID)
    assert effect_of(link, None, None).suppress == ("O",)  # the operator's condition
    assert effect_of(link, None, _dec("apply")).suppress == ("O",)
    kept = effect_of(link, None, _dec("keep"))
    assert kept.suppress == () and kept.status == "kept"


def test_duplicates_suppress_only_the_voided_accessions():
    link = _link_rec(DUPLICATES, originals=("O1",), voided=("O2", "O3"))
    eff = effect_of(link, None, None)
    assert eff.suppress == ("O2", "O3") and eff.emit_for == ()


def test_a_correction_applies_itself_and_keep_overrules_it():
    link = _link_rec(CORRECTION, status=STATUS_APPLIED)
    eff = effect_of(link, None, None)
    assert (eff.suppress, eff.emit_for, eff.status) == (("O",), ("O",), STATUS_APPLIED)
    assert effect_of(link, None, _dec("keep")).suppress == ()


def test_a_partial_does_nothing_until_applied():
    link = _link_rec(PARTIAL)
    assert effect_of(link, None, None).suppress == ()
    applied = effect_of(link, None, _dec("apply"))
    assert applied.suppress == ("O",) and applied.emit_for == ("O",)


def test_an_ambiguous_link_does_nothing_until_the_operator_links_it():
    link = _link_rec(AMBIGUOUS, originals=())
    eff = effect_of(link, None, None)
    assert (eff.suppress, eff.emit_for, eff.status) == ((), (), STATUS_PENDING)
    picked = _dec(
        "link",
        target_accession="O2",
        disposition=VOID,
        original_accessions=["O2"],
        voided_accessions=[],
    )
    eff2 = effect_of(link, picked, None)
    assert eff2.method == "operator" and eff2.suppress == (
        "O2",
    )  # a linked void is now pending: suppress


# --- the resolver's contract --------------------------------------------------------------------------------


def _row(acc: str, seq: int, code: str = "P", issuer: str | None = "1963088") -> dict:
    return {
        "accession": acc,
        "txn_seq": seq,
        "txn_code": code,
        "valid_from": date(2026, 9, 24),
        "issuer_cik": issuer,
        "usd": 100.0,
    }


def test_nothing_to_resolve_returns_the_same_list():
    rows = [_row("O", 0)]
    assert (
        resolve_insider_rows(rows, amendments=[], links=[], decisions=[], asof=date(2026, 10, 1))
        is rows
    )


def test_an_ambiguous_link_suppresses_nothing():
    rows = [_row("O1", 0), _row("O2", 0)]
    link = {
        **_link_rec(AMBIGUOUS, originals=()),
        "candidate_accessions": ["O1", "O2"],
        "security_id": "s",
    }
    out = resolve_insider_rows(
        rows, amendments=[], links=[link], decisions=[], asof=date(2026, 10, 1)
    )
    assert out == rows


def test_a_correction_emits_the_amended_rows_in_the_fact_shape():
    atch = "0001493152-26-044628"
    amend_rows = [_json_row(r) for r in parse_form4a(xml(atch)).rows]
    am = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "security_id": "s",
        "accession": atch,
        "rows": amend_rows,
        "accepted": FILINGS[atch].accepted,
        "recorded_at": T0,
    }
    link = {**_link_rec(CORRECTION, originals=("0001493152-26-044467",), status=STATUS_APPLIED)}
    link["amendment_accession"] = atch
    rows = [_row("0001493152-26-044467", 0, code="A")]
    out = resolve_insider_rows(
        rows, amendments=[am], links=[link], decisions=[], asof=date(2026, 10, 1)
    )
    assert [(r["accession"], r["txn_code"], r[AMENDS_ACCESSION]) for r in out] == [
        (atch, "P", "0001493152-26-044467")
    ]
    assert (
        out[0]["valid_from"] == date(2026, 9, 24) and out[0]["accepted"] == FILINGS[atch].accepted
    )
    # the emitted row is dated by its own transaction date: an asof before it does not see it
    assert (
        resolve_insider_rows(
            rows, amendments=[am], links=[link], decisions=[], asof=date(2026, 9, 23)
        )
        == []
    )


def test_other_issuer_screen_is_keep_when_unknown():
    assert is_other_issuer({"issuer_cik": "1894693"}, frozenset({"2038439"}))
    assert not is_other_issuer({"issuer_cik": "0002038439"}, frozenset({"2038439"}))
    assert not is_other_issuer({"issuer_cik": None}, frozenset({"2038439"}))  # unknown issuer: kept
    assert not is_other_issuer({"issuer_cik": "1894693"}, frozenset())  # no own CIK on file: kept
