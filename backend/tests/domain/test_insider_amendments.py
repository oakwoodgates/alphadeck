"""R8-A — the PURE amendment rules on REAL filings (``tests/form4a_fixtures.py`` names each one's provenance):
the parse, the link cascade, the dispositions, the effective-state machine and the resolver's contract.

Each disposition is asserted on the real document that measured it on 2026-10-17 — the FLS cancellation is a
void, the ATCH recodes are corrections, the TDG duplicates are a 1->N void, the RPAY flag change is clerical,
the SKYT pair is ambiguous. The state-machine and resolver-contract tests state the functions' contracts
with direct inputs (no outcome is fitted: each input is the shape the ingest stores)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from domain.config import DEFAULT_CONFIG
from domain.insider_amendments import (
    ADDITION,
    AMBIGUOUS,
    AMENDS_ACCESSION,
    BASIS_FILER_CORRECTION,
    BASIS_NO_SIGNAL_EFFECT,
    CLERICAL,
    CONSOLIDATION,
    CORRECTION,
    DUPLICATES,
    FIELD_CHANGE,
    METHOD_ORIG_DATE_OWNER,
    NO_ROWS,
    PARTIAL,
    RESTATEMENT,
    REVIEW_AUTO,
    REVIEW_DECIDED,
    REVIEW_HISTORICAL,
    REVIEW_INERT,
    REVIEW_OWED,
    REVIEW_QUEUED,
    STATUS_APPLIED,
    STATUS_INERT,
    STATUS_PENDING,
    UNLINKED,
    VOID,
    AmendmentFacts,
    classify,
    effect_of,
    in_window_codes,
    is_other_issuer,
    link_amendment,
    page_text,
    resolve_insider_rows,
    review_bucket,
    signal_signature,
    standing_decisions,
    sweep_basis,
    system_basis,
    within_review_horizon,
)
from ingest.edgar.form4 import parse_form4
from ingest.edgar.form4a import _json_row, parse_form4a
from signals.horizons import insider_signal_codes
from tests.form4a_fixtures import (
    FILINGS,
    amendments_of,
    filed_by_accession,
    originals_of,
    xml,
)

T0 = datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc)
# the signal code set as the cron derives it from the registered detectors' declared txn_codes
CODES = frozenset(insider_signal_codes(DEFAULT_CONFIG))


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
                    "insider_role": t["insider_role"],
                    "aff_10b5_1": t["aff_10b5_1"],
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
    return link_amendment(_facts(accession), _stored(t), filed_by_accession(t), CODES)


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


def test_fls_cancellation_links_uniquely_and_is_a_void_the_system_applies():
    link = _link("0001628280-26-037326")
    assert link.method == METHOD_ORIG_DATE_OWNER
    assert link.original_accessions == ("0001628280-26-035913",)
    # R8-B: resolved to a definite original -> the filer's own correction, applied by the system
    assert (link.disposition, link.status) == (VOID, STATUS_APPLIED)
    assert (link.auto_resolved, link.basis) == (True, BASIS_FILER_CORRECTION)


def test_azta_power_of_attorney_amendment_is_a_void_the_system_applies():
    """AZTA's zero-row 4/A only attached a power of attorney — the CFO's buy stands in truth. Under R8-B a
    void resolved to its original is applied by default; Keep original is the operator's exact Undo (proved
    on Postgres in tests/db/test_insider_amendments.py)."""
    link = _link("0001193125-25-282945")
    assert link.original_accessions == ("0000950170-25-107291",)
    assert (link.disposition, link.status, link.basis) == (
        VOID,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )


def test_avex_never_held_is_a_void():
    assert _link("0001193125-26-166819").disposition == VOID


@pytest.mark.parametrize("accession", amendments_of("ATCH"))
def test_each_atch_recode_is_an_auto_applied_correction(accession):
    link = _link(accession)
    assert link.method == METHOD_ORIG_DATE_OWNER and len(link.original_accessions) == 1
    assert (link.disposition, link.status) == (CORRECTION, STATUS_APPLIED)
    assert link.basis == BASIS_FILER_CORRECTION  # A -> P changes a signal row


def test_arbe_share_count_fix_is_a_correction():
    link = _link("0001213900-26-107326")
    assert link.original_accessions == ("0001213900-26-106000",)
    assert link.disposition == CORRECTION


def test_rpay_flag_only_amendment_is_clerical():
    link = _link("0001104659-26-041960")
    assert link.original_accessions == ("0001104659-26-041600",)
    assert (link.disposition, link.status) == (CLERICAL, STATUS_INERT)
    assert (link.auto_resolved, link.basis) == (False, None)  # nothing to apply


def test_tdg_duplicates_keep_the_earliest_and_void_the_rest():
    link = _link("0001352573-26-000011")
    assert link.disposition == DUPLICATES and link.status == STATUS_APPLIED
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
    assert (link.auto_resolved, link.basis) == (False, None)  # no definite original: never applied


def test_iehc_addition_and_mram_restatement_are_applied_by_the_system():
    """R8-B on the real in-window sells: IEHC's 4/A restates a director's sales and adds more (more rows than
    the original), MRAM's restates the CEO's tax-withholding sale (date, price, shares) — each resolved to its
    one original, each touching a code-S row, so each is the filer's correction, applied by the system.
    """
    iehc = _link("0001174947-26-000849")
    assert iehc.original_accessions == ("0001174947-26-000817",)
    assert (iehc.disposition, iehc.status, iehc.basis) == (
        ADDITION,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )
    assert iehc.signal_effect
    mram = _link("0001628280-26-047722")
    assert mram.original_accessions == ("0001628280-26-047404",)
    assert (mram.disposition, mram.status, mram.basis) == (
        RESTATEMENT,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )


def test_eltp_two_different_originals_is_ambiguous_with_a_2012_sell_row():
    link = _link("0001465825-12-000008")
    assert link.disposition == AMBIGUOUS and link.status == STATUS_PENDING
    assert link.candidate_accessions == ("0001465825-12-000004", "0001465825-12-000005")
    assert (
        link.signal_effect and link.basis is None
    )  # a code-S candidate row (2012) — never applied


def test_classify_against_the_skyt_candidate_the_amendment_corrects_is_partial():
    stored = _stored("SKYT")
    rows = parse_form4a(xml("0001856680-26-000016")).rows
    # one corrected S row against the original's M + two S rows: a (code, date) key it omits -> partial
    assert classify(rows, stored["0001856680-26-000013"], CODES) == PARTIAL


def test_classify_no_rows_either_side_and_consolidation_rule():
    assert classify([], [], CODES) == NO_ROWS
    # the rule's contract with direct inputs (the SHAPE BHVN's 4/A had on 2026-10-17 — N rows of one
    # (code, date) restated as fewer rows of the same (code, date); the numbers here are placeholders)
    orig = [
        {"txn_code": "P", "valid_from": date(2025, 3, 4), "shares": s, "price": 30.0}
        for s in (1, 2, 3)
    ]
    amend = [{"txn_code": "P", "txn_date": "2025-03-04", "shares": 6.0, "price": 30.0}]
    assert classify(amend, orig, CODES) == CONSOLIDATION


# --- the signal-effect rule (operator-approved 2026-10-18) -------------------------------------------------


def test_the_signal_codes_are_the_detectors_codes():
    assert CODES == {"P", "S"}  # derived: insider_conviction reads P, insider_sell reads S


def test_frhc_role_restatement_is_a_field_change_never_clerical():
    """Table I identical, but the owner's role on the code-S rows goes CFO -> "Special Advisor to CEO"
    (seniority reads the role): a field change — never "clerical / no change" — and since R8-B applied by the
    system as the filer's correction (its own remarks: "solely to correct the reporting person's title").
    """
    link = _link("0000929638-26-003399")
    assert link.original_accessions == ("0000929638-26-003183",)
    assert (link.disposition, link.status) == (FIELD_CHANGE, STATUS_APPLIED)
    assert link.signal_effect and link.basis == BASIS_FILER_CORRECTION


def test_avav_grant_only_void_resolves_itself_with_no_signal_effect():
    """A zero-row 4/A voiding an original whose only rows are code A (grants): no call signal reads them."""
    link = _link("0001538012-26-000006")
    assert link.original_accessions == ("0001538012-26-000004",)
    assert link.disposition == VOID
    assert (link.signal_effect, link.auto_resolved, link.status) == (False, True, STATUS_APPLIED)
    assert link.basis == BASIS_NO_SIGNAL_EFFECT


def test_mass_addition_with_identical_signal_rows_resolves_itself():
    """Adds a code-M row beside the original's UNCHANGED sale: the signal rows are identical on both sides."""
    link = _link("0002115341-26-000008")
    assert link.disposition == ADDITION
    assert (link.signal_effect, link.auto_resolved, link.status) == (False, True, STATUS_APPLIED)
    assert link.basis == BASIS_NO_SIGNAL_EFFECT


def test_signal_touching_resolved_amendments_are_applied_as_filer_corrections():
    """R8-B (operator decision 2026-10-20): a signal-touching amendment resolved to a definite original no
    longer waits for the operator — the signal-effect test only picks the basis."""
    for acc in (
        "0001628280-26-037326",
        "0001193125-25-282945",
        "0001352573-26-000011",
    ):  # FLS, AZTA, TDG
        link = _link(acc)
        assert link.signal_effect and link.auto_resolved and link.status == STATUS_APPLIED
        assert link.basis == BASIS_FILER_CORRECTION


def test_system_basis_covers_every_resolved_disposition_and_nothing_else():
    for disp in (
        CORRECTION,
        VOID,
        DUPLICATES,
        PARTIAL,
        CONSOLIDATION,
        ADDITION,
        RESTATEMENT,
        FIELD_CHANGE,
    ):
        assert system_basis(disp, True) == BASIS_FILER_CORRECTION
        assert system_basis(disp, False) == BASIS_NO_SIGNAL_EFFECT
    for disp in (AMBIGUOUS, NO_ROWS, CLERICAL, UNLINKED, "a-newer-writer's-kind"):
        assert system_basis(disp, True) is None and system_basis(disp, False) is None


def test_an_ambiguous_link_always_routes_even_without_a_signal_row():
    """The rule's contract with direct inputs: an ambiguous link has no target to apply, so it is never
    applied by the system even when no candidate carries a signal row (it then reads as historical — no call
    reads its rows; SKYT's real pair does carry them).
    """
    grant = {
        "txn_code": "A",
        "valid_from": date(2026, 3, 16),
        "shares": 1.0,
        "price": 0.0,
        "insider_name": "X",
        "rpt_owner_cik": "1",
    }
    stored = {"O1": [dict(grant, accession="O1")], "O2": [dict(grant, accession="O2", shares=2.0)]}
    facts = AmendmentFacts(
        "A1",
        (("1", "X"),),
        date(2026, 3, 18),
        date(2026, 3, 20),
        None,
        ({"txn_code": "A", "txn_date": date(2026, 3, 16), "shares": 3.0, "price": 0.0},),
    )
    link = link_amendment(facts, stored, {"O1": date(2026, 3, 18), "O2": date(2026, 3, 18)}, CODES)
    assert link.disposition == AMBIGUOUS and not link.signal_effect
    assert link.status == STATUS_PENDING and not link.auto_resolved


def test_signal_signature_reads_the_role_and_the_planned_flag_as_the_screens_do():
    base = {
        "txn_code": "S",
        "valid_from": date(2026, 8, 19),
        "shares": 500.0,
        "price": 10.0,
        "insider_name": "Ler Evgeny",
        "insider_role": "Chief Financial Officer",
        "aff_10b5_1": None,
    }
    assert signal_signature(base) == signal_signature(
        dict(base, aff_10b5_1=False)
    )  # unknown == clear
    assert signal_signature(base) != signal_signature(
        dict(base, aff_10b5_1=True)
    )  # planned is screened
    assert signal_signature(base) == signal_signature(
        dict(base, insider_role="chief  financial officer")
    )
    assert signal_signature(base) != signal_signature(
        dict(base, insider_role="Special Advisor to CEO")
    )


def test_in_window_uses_the_detectors_own_horizons():
    horizons = insider_signal_codes(DEFAULT_CONFIG)  # {'P': conviction's horizon, 'S': sell's}
    asof = date(2026, 10, 16)
    edge_p = asof - timedelta(days=horizons["P"])
    edge_s = asof - timedelta(days=horizons["S"])
    rows = [
        {"txn_code": "P", "valid_from": edge_p},
        {"txn_code": "S", "valid_from": edge_s - timedelta(days=1)},
    ]
    assert in_window_codes(rows, asof, horizons) == ("P",)
    assert in_window_codes([{"txn_code": "S", "valid_from": edge_s}], asof, horizons) == ("S",)
    assert in_window_codes([{"txn_code": "A", "valid_from": asof}], asof, horizons) == ()


def test_the_page_names_every_in_window_item_and_counts_the_rest():
    named = tuple(f"T{i}" for i in range(12))  # never collapsed, however many
    assert page_text(named, 3) == ", ".join(named) + " (+3 more in Admin)"
    assert page_text(named, 0) == ", ".join(named)
    assert page_text((), 5) == "none inside tonight's signal windows (+5 in Admin)"


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


# --- R8-B: the system's application, its Undo, the review buckets, the sweep -------------------------------


def _at(d: dict, minutes: int) -> dict:
    return {**d, "recorded_at": T0 + timedelta(minutes=minutes)}


def test_keep_stands_over_a_system_application_and_its_undo_restores_it():
    """The exact inverse pair: the operator's keep (newer) overrules the system's apply without revoking it;
    revoking the keep makes the system's apply the newest standing verdict again."""
    link = _link_rec(PARTIAL)
    system = _at(_dec("apply", basis=BASIS_FILER_CORRECTION), 1)
    keep = _at(_dec("keep", basis="operator:keep"), 2)
    _, verdict = standing_decisions([system])["A"]
    applied = effect_of(link, None, verdict)
    assert (applied.status, applied.suppress, applied.emit_for) == (STATUS_APPLIED, ("O",), ("O",))
    _, verdict = standing_decisions([system, keep])["A"]
    kept = effect_of(link, None, verdict)
    assert (kept.status, kept.suppress, kept.emit_for) == ("kept", (), ())
    undo = _at(_dec("revoke", revokes=keep["id"]), 3)
    _, verdict = standing_decisions([system, keep, undo])["A"]
    assert verdict["id"] == system["id"] and effect_of(link, None, verdict).status == STATUS_APPLIED


def _bucket(link, *decisions, ever=None, in_review=False):
    link_d, verdict = standing_decisions(list(decisions)).get("A", (None, None))
    return review_bucket(
        effect_of(link, link_d, verdict),
        verdict_basis=verdict.get("basis") if verdict else None,
        ever_decided=bool(decisions) if ever is None else ever,
        in_review=in_review,
    )


def test_review_buckets_the_states_an_amendment_can_hold():
    """The bucket contract over direct inputs (each the shape the ingest / sweep / operator stores)."""
    sys_apply = _at(_dec("apply", basis=BASIS_FILER_CORRECTION), 1)
    assert _bucket(_link_rec(VOID), sys_apply) == REVIEW_AUTO
    assert _bucket(_link_rec(CORRECTION, status=STATUS_APPLIED)) == REVIEW_AUTO  # by its link alone
    assert _bucket(_link_rec(VOID)) == REVIEW_QUEUED  # pre-R8-B, no decision: the sweep applies it
    keep = _at(_dec("keep", basis="operator:keep"), 2)
    assert _bucket(_link_rec(VOID), sys_apply, keep) == REVIEW_DECIDED  # kept by the operator
    undo_sys = _at(_dec("revoke", revokes=sys_apply["id"]), 2)
    assert _bucket(_link_rec(VOID), sys_apply, undo_sys) == REVIEW_OWED  # the operator took it back
    amb = _link_rec(AMBIGUOUS, originals=())
    assert _bucket(amb, in_review=True) == REVIEW_OWED
    assert _bucket(amb, in_review=False) == REVIEW_HISTORICAL
    picked = _at(
        _dec("link", disposition=PARTIAL, original_accessions=["O2"], voided_accessions=[]), 1
    )
    assert _bucket(amb, picked) == REVIEW_OWED  # operator-linked: awaits the operator's verdict
    assert _bucket(_link_rec(CLERICAL, status=STATUS_INERT)) == REVIEW_INERT


def test_within_review_horizon_reads_only_the_changed_signal_rows():
    asof = date(2026, 10, 20)
    edge = asof - timedelta(days=940)
    assert within_review_horizon([{"txn_code": "S", "valid_from": edge}], asof, 940)
    assert not within_review_horizon(
        [{"txn_code": "S", "valid_from": edge - timedelta(days=1)}], asof, 940
    )
    assert within_review_horizon([{"txn_code": "P", "txn_date": "2012-01-26"}], asof, None)
    assert not within_review_horizon(
        [], asof, None
    )  # no signal effect: outside, whatever the horizon


def test_the_sweep_applies_only_an_undecided_resolvable_amendment():
    pend = effect_of(_link_rec(RESTATEMENT), None, None)
    assert sweep_basis(pend, ever_decided=False, signal_effect=True) == BASIS_FILER_CORRECTION
    assert sweep_basis(pend, ever_decided=False, signal_effect=False) == BASIS_NO_SIGNAL_EFFECT
    assert (
        sweep_basis(pend, ever_decided=True, signal_effect=True) is None
    )  # anything decided stays
    amb = effect_of(_link_rec(AMBIGUOUS, originals=()), None, None)
    assert sweep_basis(amb, ever_decided=False, signal_effect=True) is None
    inert = effect_of(_link_rec(CLERICAL, status=STATUS_INERT), None, None)
    assert sweep_basis(inert, ever_decided=False, signal_effect=False) is None


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
