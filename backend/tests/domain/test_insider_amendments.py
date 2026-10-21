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
    ACTION_APPLY,
    ACTION_KEEP,
    ADDITION,
    AMBIGUOUS,
    AMENDS_ACCESSION,
    BASIS_FILER_CORRECTION,
    BASIS_HELD,
    BASIS_NO_RETRACTION,
    BASIS_NO_SIGNAL_EFFECT,
    CLERICAL,
    CONSOLIDATION,
    CORRECTION,
    DUPLICATES,
    FIELD_CHANGE,
    METHOD_ORIG_DATE_OWNER,
    METHOD_OWNER_TXN_DATE,
    NO_ROWS,
    PARTIAL,
    READ_ADD,
    READ_FIX,
    READ_UNRESOLVED,
    RELINK_ORDER,
    RELINK_TIE_BREAK,
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
    VOID_HELD,
    VOID_NO_RETRACTION,
    VOID_RETRACT,
    AmendmentFacts,
    RowMatch,
    SystemResolution,
    VoidReading,
    classify,
    effect_of,
    filer_text,
    in_window_codes,
    is_other_issuer,
    link_amendment,
    made_public_after,
    match_rows,
    originals_rows,
    page_text,
    relink_rule,
    resolve_insider_rows,
    review_bucket,
    row_tie_break,
    signal_signature,
    standing_decisions,
    sweep_resolution,
    system_basis,
    system_resolution,
    void_reading,
    within_review_horizon,
)
from ingest.edgar.form4 import parse_form4
from ingest.edgar.form4a import _json_row, parse_form4a
from signals.horizons import insider_signal_codes
from tests.form4a_fixtures import (
    FILINGS,
    accepted_by_accession,
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
        text=filer_text(p.remarks, p.footnotes),
    )


def _link(accession: str):
    t = FILINGS[accession].key
    return link_amendment(
        _facts(accession), _stored(t), filed_by_accession(t), CODES, accepted_by_accession(t)
    )


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


def test_azta_power_of_attorney_void_retracts_nothing_so_the_original_stands():
    """AZTA's zero-row 4/A "include[s] the Power of Attorney … No other changes have been made": no retraction
    language, so the SYSTEM keeps the original (``system:no-retraction``) — the CFO's buy stands."""
    link = _link("0001193125-25-282945")
    assert link.original_accessions == ("0000950170-25-107291",)
    assert (link.disposition, link.void_kind) == (VOID, VOID_NO_RETRACTION)
    assert (link.status, link.system_action, link.basis) == (
        STATUS_INERT,
        ACTION_KEEP,
        BASIS_NO_RETRACTION,
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


def test_skyt_tie_break_links_the_one_candidate_holding_the_restated_sale():
    """Two different Form 4s by the owner were filed on SKYT's stated date. The 4/A re-reports the 2026-03-16
    sale at $27.8398 with 9,708 fewer shares ("overstated by 9,708 shares"): ``-000013`` holds that sale (same
    owner, date, code, price — a share fix), ``-000010`` holds only a tax withholding the 4/A does not account
    for -> the row tie-break links ``-000013``; the candidates stay on the link (what it was chosen among).
    """
    link = _link("0001856680-26-000016")
    assert link.method == METHOD_ORIG_DATE_OWNER and link.tie_break
    assert link.original_accessions == ("0001856680-26-000013",)
    assert link.candidate_accessions == ("0001856680-26-000010", "0001856680-26-000013")
    # resolved to a definite original -> the filer's correction, applied by the system: only the 81,401-share
    # sale is replaced (the M / S 9,708 exercise-and-sale lines stand)
    assert (link.disposition, link.status, link.basis) == (
        PARTIAL,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )
    assert link.replaced_rows == ("0001856680-26-000013#2",)
    assert link.excluded_later == ()


def test_iehc_4a_dated_its_own_filing_day_links_to_the_july_2025_original_not_a_later_filing():
    """THE MIS-LINK (prod, 2026-10-21). IEHC 0001174947-26-000336 states ``dateOfOriginalSubmission`` 2026-03-10
    — its OWN filing date; its footnote names the real original ("filed on July 1, 2025"). The owner's only Form 4
    filed that day, ``-000338`` (two March 2026 sales), was ACCEPTED 49 seconds AFTER the 4/A — it cannot be the
    filing the 4/A amends. Refused, step 2 finds the owner's July 2025 Form 4 by the shared transaction date
    (2025-06-30) — Table I identical (the 4/A fixes only a holdings figure) -> clerical, nothing to apply. The
    other owner's Form 4 on the same transaction date (``-25-000985``) is never a candidate."""
    link = _link("0001174947-26-000336")
    assert link.excluded_later == ("0001174947-26-000338",)
    assert (link.method, link.original_accessions) == (
        METHOD_OWNER_TXN_DATE,
        ("0001174947-25-000979",),
    )
    assert (link.disposition, link.status, link.basis) == (CLERICAL, STATUS_INERT, None)
    assert "made public AFTER this amendment" in link.why


def test_without_the_index_acceptance_a_same_day_later_filing_cannot_be_told_apart():
    """The contract the ingest relies on: the order test reads the submissions index's ACCEPTANCE (the IEHC pair
    shares one filing date). With only the filing dates, ``-000338`` is not provably later and step 1 keeps it
    — which is why the leg threads ``accepted_by_accession`` from the same index as the 4/A's own ``accepted``.
    """
    t = "IEHC-MISLINK"
    link = link_amendment(_facts("0001174947-26-000336"), _stored(t), filed_by_accession(t), CODES)
    assert link.original_accessions == ("0001174947-26-000338",) and link.excluded_later == ()


def test_a_void_filed_before_the_only_same_day_filing_finds_no_original():
    """AIN 0001299383-15-000003: a zero-row 4/A whose stated date is its own filing day, accepted 19 minutes
    BEFORE ``-000004`` — the old cascade voided that later filing (suppressing its rows). Refused as made public
    after the amendment, nothing is left to link (no rows of its own -> NO_ROWS, inert): the rows stand.
    """
    link = _link("0001299383-15-000003")
    assert link.excluded_later == ("0001299383-15-000004",)
    assert (link.disposition, link.status, link.original_accessions) == (NO_ROWS, STATUS_INERT, ())


def test_fis_tie_break_links_the_withholding_and_leaves_the_buy_alone():
    """FIS: two different Form 4s by the owner on the stated date; the 4/A ("solely to correct … Column 5")
    re-reports one tax withholding, held identically by ``-099049``; ``-099107`` holds only a code-P buy the 4/A
    does not account for -> linked to ``-099049``; the buy's filing is not touched (no signal effect).
    """
    link = _link("0001193125-26-131811")
    assert link.tie_break and link.original_accessions == ("0001193125-26-099049",)
    assert link.disposition == PARTIAL and not link.signal_effect
    assert link.basis == BASIS_NO_SIGNAL_EFFECT
    assert link.replaced_rows == ("0001193125-26-099049#7",)


def test_hrow_stays_ambiguous_when_both_candidates_hold_an_accounted_row():
    """HROW: no Form 4 on the stated date (the transaction date); two of the owner's share it — the identical sale
    and the same sale at another price (a price-fix shape). The tie-break needs EXACTLY one candidate holding an
    accounted row; both do -> it stays the operator's (its remarks name the March 20 filing, but words never pick
    a filing)."""
    link = _link("0001641172-25-010338")
    assert (link.disposition, link.status, link.tie_break) == (AMBIGUOUS, STATUS_PENDING, False)
    assert link.candidate_accessions == ("0001493152-25-011028", "0001641172-25-000741")
    assert (link.auto_resolved, link.basis) == (False, None)  # no definite original: never applied


def test_mram_and_iehc_restate_their_sales_in_fix_language_so_the_system_applies_them():
    """MRAM's and IEHC's 4/As change the date AND the share count of the sales they re-report — no exact shape
    accounts for them — so the FILER'S WORDS settle the leftovers: MRAM "amended to correct the date … the
    price … and the number of shares sold" (FIX) replaces its sale; IEHC says both "correct … dates and prices"
    (FIX) and "the sale of additional shares" (ADD) — its four re-reported sales cover the original's two, so
    FIX wins the tie-break (its rows restate every original sale)."""
    mram = _link("0001628280-26-047722")
    assert (mram.disposition, mram.status, mram.basis) == (
        RESTATEMENT,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )
    assert mram.replaced_rows == ("0001628280-26-047404#0",) and not mram.unresolved
    iehc = _link("0001174947-26-000849")
    assert (iehc.disposition, iehc.status) == (ADDITION, STATUS_APPLIED)
    assert iehc.replaced_rows == ("0001174947-26-000817#0", "0001174947-26-000817#1")
    p = parse_form4a(xml("0001174947-26-000849"))
    m = match_rows(
        p.rows,
        originals_rows(["0001174947-26-000817"], _stored("IEHC")),
        CODES,
        filer_text(p.remarks, p.footnotes),
    )
    assert m.reading == READ_FIX and "dates and prices" in (m.passage or "")


def test_ibkr_omitted_sale_is_an_addition_its_real_sales_all_stand():
    """IBKR's 4/A "is filed to include shares sold on September 19, 2025, inadvertently excluded": its 6,928 sh
    on 09-19 matches none of the original's four sales by shape (a bare same-date + same-code is no match), and
    the filer's words ADD — so all four sales stand and the omitted one is added."""
    link = _link("0001381197-25-000139")
    assert (link.disposition, link.status, link.basis) == (
        PARTIAL,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )
    assert link.replaced_rows == ()
    p = parse_form4a(xml("0001381197-25-000139"))
    m = match_rows(
        p.rows,
        originals_rows(["0001381197-25-000131"], _stored("IBKR")),
        CODES,
        filer_text(p.remarks, p.footnotes),
    )
    assert (m.reading, m.added, len(m.kept)) == (READ_ADD, 1, 4)
    assert "inadvertently excluded" in (m.passage or "")


def test_yyai_neither_fixes_nor_adds_so_it_is_owed():
    """YYAI's 4/A re-reports a reverse-split share count and a new purchase in words that neither fix nor add:
    unresolved — no system decision, the original standing."""
    link = _link("0002091629-25-000011")
    assert (link.disposition, link.status, link.basis, link.system_action) == (
        RESTATEMENT,
        STATUS_PENDING,
        None,
        None,
    )
    assert link.unresolved


def test_betr_split_lots_and_bhvn_consolidation_match_by_sum():
    """BETR splits one 3,487 sh sale into 2,687 + 800 (they sum to it); BHVN restates 25,000 + 3,400 as 28,400 —
    the trust lots its 4/A does not mention (3,400 + 900) stand."""
    betr = _link("0001628280-24-050110")
    assert (betr.status, betr.replaced_rows) == (STATUS_APPLIED, ("0001628280-24-049753#0",))
    bhvn = _link("0001027035-26-000006")
    assert bhvn.disposition == CONSOLIDATION and bhvn.status == STATUS_APPLIED
    assert bhvn.replaced_rows == ("0001935979-25-000018#0", "0001935979-25-000018#1")
    m = match_rows(
        parse_form4a(xml("0001027035-26-000006")).rows,
        originals_rows(["0001935979-25-000018"], _stored("BHVN")),
        CODES,
    )
    assert m.kept == ("0001935979-25-000018#2", "0001935979-25-000018#3")


def test_voids_are_read_by_the_filers_words_and_the_originals_shape():
    """A zero-row 4/A retracts ONLY with explicit retraction language AND a single-trade original: FLS ("was
    cancelled"), AVEX ("does not hold any"), MNKD ("should not have been filed"), DVLT ×2 ("did not occur") are
    applied; FIGR's retraction ("shares withheld … did not in fact occur") faces three trades — held for the
    operator; SMR (a holdings fix) and VWAV ("All transaction data … remains correct") retract nothing.
    """
    expect = {
        "0001628280-26-037326": VOID_RETRACT,  # FLS
        "0001193125-26-166819": VOID_RETRACT,  # AVEX
        "0001193125-26-215284": VOID_RETRACT,  # MNKD
        "0001104659-24-127212": VOID_RETRACT,  # DVLT
        "0001104659-24-127204": VOID_RETRACT,  # DVLT
        "0001493152-25-024508": VOID_HELD,  # FIGR
        "0001922054-26-000007": VOID_NO_RETRACTION,  # SMR
        "0002105221-26-000002": VOID_NO_RETRACTION,  # VWAV
    }
    for acc, kind in expect.items():
        link = _link(acc)
        assert (link.disposition, link.void_kind) == (VOID, kind), acc
        want = {
            VOID_RETRACT: (STATUS_APPLIED, ACTION_APPLY, BASIS_FILER_CORRECTION),
            VOID_HELD: (STATUS_PENDING, ACTION_KEEP, BASIS_HELD),
            VOID_NO_RETRACTION: (STATUS_INERT, ACTION_KEEP, BASIS_NO_RETRACTION),
        }[kind]
        assert (link.status, link.system_action, link.basis) == want, acc


@pytest.mark.parametrize("accession", [f"0001443646-24-{n:06d}" for n in range(94, 102)])
def test_each_bah_recode_replaces_the_sale_and_keeps_the_grant(accession):
    """BAH's eight 4/As recode S -> F (same owner, date and shares): the sale is replaced, the grant stands."""
    link = _link(accession)
    assert (link.disposition, link.status) == (PARTIAL, STATUS_APPLIED)
    (orig,) = link.original_accessions
    assert link.replaced_rows == (f"{orig}#1",)


def test_mrcy_omitted_grant_keeps_both_sales_and_adds_the_grant():
    """MRCY's 4/A only ADDS the omitted RSU grant (code A, 2026-08-17): a different code and a different
    count on the sale's date is a different transaction, so neither sale is accounted for — both STAND. The
    application changes no signal row (the sales are untouched), so the system applies it as no-signal-effect.
    """
    link = _link("0001049521-26-000071")
    assert link.original_accessions == ("0001049521-26-000054",)
    assert link.disposition == PARTIAL and not link.unresolved
    assert link.replaced_rows == ()  # it accounts for no original row
    assert (link.status, link.basis, link.signal_effect) == (
        STATUS_APPLIED,
        BASIS_NO_SIGNAL_EFFECT,
        False,
    )
    m = match_rows(
        parse_form4a(xml("0001049521-26-000071")).rows,
        originals_rows(["0001049521-26-000054"], _stored("MRCY")),
        CODES,
    )
    assert m.kept == ("0001049521-26-000054#0", "0001049521-26-000054#1") and m.added == 1


def test_bah_recode_replaces_the_sale_and_keeps_the_unmentioned_grant():
    """BAH recodes S 2,142 sh -> F (same owner, date and share count: a recode, any code). The original's grant
    (code A, a different count) is not restated: it STANDS."""
    link = _link("0001443646-24-000094")
    assert (link.disposition, link.status, link.basis) == (
        PARTIAL,
        STATUS_APPLIED,
        BASIS_FILER_CORRECTION,
    )
    assert link.replaced_rows == ("0001443646-24-000061#1",)  # the S row
    m = match_rows(
        parse_form4a(xml("0001443646-24-000094")).rows,
        originals_rows(["0001443646-24-000061"], _stored("BAH")),
        CODES,
    )
    assert m.kept == ("0001443646-24-000061#0",) and m.added == 0 and not m.unresolved


def _r(code, day, shares, seq=0, owner="1", acc="O"):
    return {
        "accession": acc,
        "txn_seq": seq,
        "txn_code": code,
        "valid_from": date.fromisoformat(day),
        "shares": shares,
        "price": 10.0,
        "rpt_owner_cik": owner,
        "insider_name": f"Owner {owner}",
    }


def _a(code, day, shares, seq=0, owner="1"):
    return {
        "txn_seq": seq,
        "txn_code": code,
        "txn_date": day,
        "shares": shares,
        "price": 10.0,
        "rpt_owner_cik": owner,
        "insider_name": f"Owner {owner}",
    }


def test_match_rows_contract():
    """The matcher's contract over direct inputs (each the shape a stored / as-filed row has)."""
    # a consolidation: three sales of one date restated as their sum -> all three accounted for
    m = match_rows(
        [_a("S", "2026-03-04", 6.0)],
        [_r("S", "2026-03-04", s, i) for i, s in enumerate((1.0, 2.0, 3.0))],
        CODES,
    )
    assert m.replaced == ("O#0", "O#1", "O#2") and m.kept == () and not m.unresolved
    # a date fix (same code, same shares) — one-to-one, oldest first: the second identical sale stands
    m = match_rows(
        [_a("S", "2026-03-06", 100.0)],
        [_r("S", "2026-03-04", 100.0, 0), _r("S", "2026-03-05", 100.0, 1)],
        CODES,
    )
    assert m.replaced == ("O#0",) and m.kept == ("O#1",) and not m.unresolved
    # a share fix (same date, code and price) and a recode (same date and shares, another code)
    m = match_rows([_a("S", "2026-03-04", 150.0)], [_r("S", "2026-03-04", 100.0)], CODES)
    assert m.replaced == ("O#0",)
    m = match_rows([_a("F", "2026-03-04", 100.0)], [_r("S", "2026-03-04", 100.0)], CODES)
    assert m.replaced == ("O#0",)
    # a bare same date + same code is NO match: another count AND another price -> the words decide
    other = {**_a("S", "2026-03-04", 50.0), "price": 11.0}
    m = match_rows([other], [_r("S", "2026-03-04", 100.0)], CODES)
    assert m.unresolved and m.reading == READ_UNRESOLVED and m.kept == ("O#0",) and m.added == 1
    m = match_rows(
        [other], [_r("S", "2026-03-04", 100.0)], CODES, "to correct the price of the sale"
    )
    assert (m.reading, m.replaced, m.added) == (READ_FIX, ("O#0",), 0)
    m = match_rows([other], [_r("S", "2026-03-04", 100.0)], CODES, "a sale inadvertently omitted")
    assert (m.reading, m.kept, m.added) == (READ_ADD, ("O#0",), 1)
    # BOTH phrase sets: FIX only when the amendment re-reports at least as many rows as the original
    both = "to correct the dates of the sales, together with additional shares"
    two = [_r("S", "2026-03-04", 100.0, 0), _r("S", "2026-03-05", 70.0, 1)]
    assert match_rows([other], two, CODES, both).unresolved
    more = [other, {**_a("S", "2026-03-06", 30.0, 1), "price": 12.0}]
    assert match_rows(more, two, CODES, both).reading == READ_FIX
    # an original split into lots that sum to it
    m = match_rows(
        [_a("S", "2026-03-04", 60.0, 0), _a("S", "2026-03-04", 40.0, 1)],
        [_r("S", "2026-03-04", 100.0)],
        CODES,
    )
    assert (m.replaced, m.added) == (("O#0",), 0)
    # another owner never accounts for a row; a non-signal leftover is kept beside the addition
    m = match_rows([_a("S", "2026-03-04", 100.0, owner="2")], [_r("S", "2026-03-04", 100.0)], CODES)
    assert m.kept == ("O#0",) and not m.unresolved
    m = match_rows([_a("A", "2026-03-09", 7.0)], [_r("A", "2026-03-04", 5.0)], CODES)
    assert m.kept == ("O#0",) and not m.unresolved


def test_void_reading_contract():
    """Retraction language AND exactly one transaction, a buy or sale, on the original — else not a retraction
    (no language) or held (language, but the words cannot be mapped to one of several trades)."""
    one = [_r("P", "2026-03-04", 100.0)]
    assert void_reading("the purchase was cancelled", one, CODES).kind == VOID_RETRACT
    assert (
        void_reading("filed to include the power of attorney", one, CODES).kind
        == VOID_NO_RETRACTION
    )
    two = one + [_r("F", "2026-03-04", 40.0, 1)]
    assert void_reading("the sale did not occur", two, CODES).kind == VOID_HELD
    assert (
        void_reading("the grant did not occur", [_r("A", "2026-03-04", 9.0)], CODES).kind
        == VOID_HELD
    )
    assert void_reading("", one, CODES).kind == VOID_NO_RETRACTION


# --- the tightened retraction phrases (coordinator review, 2026-10-21) -----------------------------------------
# Three boilerplate-prone phrases could quietly hide a future filing's single real trade. Each keeps its real
# retraction and loses its boilerplate. The quoted sentences are REAL — read from prod's stored 4/As (read-only,
# 2026-10-21; the accession beside each) or the fixture documents; the joint-filer and "attributable to him"
# negatives are stated as the function's contract (the boilerplate shapes), because prod's only "attributable to
# him" is MNKD's own retraction and its only "duplicative" (FCEL 0000897069-26-000883, a no-rows 4/A) is not a
# joint-filer footnote.


def _passage_on_one_sale(text: str) -> str | None:
    """The retraction passage of ``text`` against a single-sale original (``None`` = no retraction words)."""
    v = void_reading(text, [_r("S", "2026-03-04", 100.0)], CODES)
    return v.passage if v.kind == VOID_RETRACT else None


def test_cancel_retracts_only_beside_a_trade():
    # FLS — the real document: the broker cancelled the buy inside the settlement window
    p = parse_form4a(xml("0001628280-26-037326"))
    fls = void_reading(filer_text(p.remarks, p.footnotes), [_r("P", "2026-05-18", 2000.0)], CODES)
    assert fls.kind == VOID_RETRACT and "sale on May 18, 2026 was cancelled" in fls.passage
    # CZR 0001590895-22-000086 — the cancellation of the transaction itself
    czr = _passage_on_one_sale(
        "This report on Form 4/A amends the Form 4 filed by the reporting person on March 9, 2022. This report "
        "on Form 4/A is being filed solely to report the cancellation of such transaction prior to settlement."
    )
    assert czr is not None and "cancellation of such transaction" in czr
    # option / share / earn-out cancellations — no trade beside the word: never a retraction
    for text in (
        # TLS 0001181431-08-038225 ("granted" is no "grant")
        "On June 4, 2008, the Issuer cancelled all stock options granted to the Reporting Person. In exchange "
        "for such cancelled stock options, the Issuer granted 11,250 shares of restricted stock to the "
        "Reporting Person.",
        # KITT 0001213900-22-056032 (a zero-row 4/A: earn-out boilerplate)
        "Any shares not eligible to be released within five years of December 16, 2021 will be forfeited and "
        "canceled.",
        # ACHR 0001209191-21-057863 (a merger's share conversion)
        'Upon consummation of the Merger (the "Effective Time"), each issued and outstanding share of common '
        "stock of Legacy Archer was canceled and converted into",
    ):
        assert _passage_on_one_sale(text) is None, text


def test_duplicate_retracts_only_as_a_duplicate_filing():
    # TDG 0001352573-26-000011 and ULBI 0000875657-06-000078 — real duplicate FILINGS (ULBI's spelling as filed)
    tdg = _passage_on_one_sale(
        "0001352573-26-000009 and 0001352573-26-000010, also filed on June 22, 2026, were duplicate filings "
        "made in error and are superseded by this amendment."
    )
    assert tdg is not None and "duplicate filings" in tdg
    ulbi = _passage_on_one_sale("On 12/26/06 a duplicate Form 4 was filed inadvertantly.")
    assert ulbi is not None and "duplicate Form 4" in ulbi
    # joint-filer boilerplate (the contract): another owner's report of the SAME shares is no retraction
    assert (
        _passage_on_one_sale(
            "The securities reported herein may be duplicative of the shares reported by the other reporting "
            "persons on this Form 4."
        )
        is None
    )


def test_attributable_to_him_is_ownership_boilerplate_and_mnkd_still_retracts_on_its_own_words():
    # MNKD — the real document: it still retracts, on "should not have been filed"
    p = parse_form4a(xml("0001193125-26-215284"))
    mnkd = void_reading(
        filer_text(p.remarks, p.footnotes), [_r("P", "2026-03-10", 100000.0)], CODES
    )
    assert mnkd.kind == VOID_RETRACT and "should not have been filed" in mnkd.passage
    # ownership boilerplate (the contract): never a retraction
    assert (
        _passage_on_one_sale(
            "The reporting person disclaims beneficial ownership of the shares attributable to him except to "
            "the extent of his pecuniary interest therein."
        )
        is None
    )


def test_eltp_tie_break_links_the_date_fix_and_the_2012_sale_stands():
    """ELTP: two different Form 4s filed on the stated date; the 4/A re-dates ``-000004``'s conversion (same code
    and shares, another date — a date fix) and accounts for nothing on ``-000005`` (a 2012 code-S sale) -> linked
    to ``-000004``, a correction with no signal effect: the sale stands."""
    link = _link("0001465825-12-000008")
    assert link.tie_break and link.original_accessions == ("0001465825-12-000004",)
    assert link.candidate_accessions == ("0001465825-12-000004", "0001465825-12-000005")
    assert (link.disposition, link.signal_effect, link.basis) == (
        CORRECTION,
        False,
        BASIS_NO_SIGNAL_EFFECT,
    )


def test_asth_stays_ambiguous_with_old_buy_rows():
    """ASTH 0001134366-13-000009: two different Form 4s on the stated date, the 4/A accounting for a row on BOTH (a
    date fix on ``-000002``, a price fix on ``-000004``) -> ambiguous; code-P rows of 2008 / 2012 (a signal
    effect no call reads today — "unresolved — historical")."""
    link = _link("0001134366-13-000009")
    assert (link.disposition, link.status) == (AMBIGUOUS, STATUS_PENDING)
    assert link.candidate_accessions == ("0001134366-13-000002", "0001134366-13-000004")
    assert link.signal_effect and link.basis is None  # never applied


def test_made_public_after_reads_the_index_instant_else_the_filing_date_else_keeps():
    """The order test's contract with direct inputs: the index acceptance decides when both instants are known
    (strictly later only — an equal instant is not after); else a later EDGAR filing date; unknown keeps the
    candidate (over-include, #9)."""
    t = datetime(2026, 3, 10, 21, 22, 48, tzinfo=timezone.utc)
    am = AmendmentFacts("A", (), None, date(2026, 3, 10), t, ())
    filed = {"X": date(2026, 3, 10), "Y": date(2026, 3, 11), "Z": date(2026, 3, 9)}
    acc = {"X": t + timedelta(seconds=49), "Z": t + timedelta(hours=1)}
    assert made_public_after("X", am, filed, acc)  # same day, 49 s later
    assert not made_public_after("X", am, filed, {"X": t})  # the same instant is not after
    assert made_public_after("Y", am, filed, acc)  # no instant: a later filing date
    assert made_public_after("Z", am, filed, acc)  # the instant wins over the (earlier) date
    assert not made_public_after("W", am, filed, acc)  # unknown either way: kept
    undated = AmendmentFacts("A", (), None, None, None, ())
    assert not made_public_after("Y", undated, filed, acc)


def test_row_tie_break_needs_exactly_one_candidate_holding_an_accounted_row():
    """The tie-break's contract with direct inputs: exact shape only (owner first) — one candidate holds the
    identical row -> it; two hold one -> None; a zero-row amendment -> None; a bare same-date-same-code row
    (another price AND shares) accounts for nothing."""
    base = {"insider_name": "X", "rpt_owner_cik": "1", "valid_from": date(2026, 3, 16)}
    sale = {**base, "txn_code": "S", "shares": 100.0, "price": 10.0}
    stored = {
        "O1": [{**sale, "accession": "O1", "txn_seq": 0}],
        "O2": [
            {**base, "txn_code": "F", "shares": 7.0, "price": 10.0, "accession": "O2", "txn_seq": 0}
        ],
        "O3": [{**sale, "shares": 55.0, "price": 9.0, "accession": "O3", "txn_seq": 0}],
    }
    amend = [
        {
            "insider_name": "X",
            "rpt_owner_cik": "1",
            "txn_date": "2026-03-16",
            "txn_code": "S",
            "shares": 100.0,
            "price": 10.0,
        }
    ]
    assert row_tie_break(amend, ["O1", "O2"], stored, CODES) == "O1"
    assert (
        row_tie_break(amend, ["O1", "O2", "O3"], stored, CODES) == "O1"
    )  # O3: same date + code only
    assert row_tie_break(amend, ["O1", "O1b"], {**stored, "O1b": stored["O1"]}, CODES) is None
    assert row_tie_break([], ["O1", "O2"], stored, CODES) is None
    other_owner = [{**amend[0], "insider_name": "Y", "rpt_owner_cik": "2"}]
    assert row_tie_break(other_owner, ["O1", "O2"], stored, CODES) is None


def test_relink_rule_moves_only_what_the_two_rules_explain():
    """``relink_rule`` with direct inputs: a stored link naming a filing the fresh cascade refused as made public
    after the amendment -> RELINK_ORDER; a stored AMBIGUOUS link the tie-break settles among the same candidates
    -> RELINK_TIE_BREAK; the same link -> None; any other difference (drift) -> None."""
    fresh_iehc = _link("0001174947-26-000336")
    stored_iehc = {
        "method": METHOD_ORIG_DATE_OWNER,
        "disposition": RESTATEMENT,
        "original_accessions": ["0001174947-26-000338"],
        "voided_accessions": [],
        "candidate_accessions": ["0001174947-26-000338"],
    }
    assert relink_rule(stored_iehc, fresh_iehc) == RELINK_ORDER
    fresh_skyt = _link("0001856680-26-000016")
    stored_skyt = {
        "method": METHOD_ORIG_DATE_OWNER,
        "disposition": AMBIGUOUS,
        "original_accessions": [],
        "voided_accessions": [],
        "candidate_accessions": ["0001856680-26-000010", "0001856680-26-000013"],
    }
    assert relink_rule(stored_skyt, fresh_skyt) == RELINK_TIE_BREAK
    same = {
        "method": fresh_skyt.method,
        "disposition": fresh_skyt.disposition,
        "original_accessions": list(fresh_skyt.original_accessions),
        "voided_accessions": [],
        "candidate_accessions": list(fresh_skyt.candidate_accessions),
    }
    assert relink_rule(same, fresh_skyt) is None
    drift = {**same, "original_accessions": ["0001856680-26-000010"], "disposition": PARTIAL}
    assert relink_rule(drift, fresh_skyt) is None  # not explained by either rule: never moved


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


def test_avav_grant_only_void_retracts_nothing_so_the_grants_stand():
    """A zero-row 4/A voiding an original whose only rows are code A (grants), without retraction language: the
    system keeps the original (``system:no-retraction``) — nothing a call reads either way."""
    link = _link("0001538012-26-000006")
    assert link.original_accessions == ("0001538012-26-000004",)
    assert link.disposition == VOID and not link.signal_effect
    assert (link.status, link.system_action, link.basis) == (
        STATUS_INERT,
        ACTION_KEEP,
        BASIS_NO_RETRACTION,
    )


def test_mass_addition_with_identical_signal_rows_resolves_itself():
    """Adds a code-M row beside the original's UNCHANGED sale: the signal rows are identical on both sides."""
    link = _link("0002115341-26-000008")
    assert link.disposition == ADDITION
    assert (link.signal_effect, link.auto_resolved, link.status) == (False, True, STATUS_APPLIED)
    assert link.basis == BASIS_NO_SIGNAL_EFFECT


def test_signal_touching_resolved_amendments_are_applied_as_filer_corrections():
    """R8-B (operator decision 2026-10-20): a signal-touching amendment the system resolves no longer waits for
    the operator — the signal-effect test only picks the basis."""
    for acc in (
        "0001628280-26-037326",
        "0001352573-26-000011",
    ):  # FLS (a retraction), TDG (duplicates)
        link = _link(acc)
        assert link.signal_effect and link.auto_resolved and link.status == STATUS_APPLIED
        assert link.basis == BASIS_FILER_CORRECTION


def test_system_resolution_contract():
    """What the system records per disposition (direct inputs)."""
    assert system_resolution(AMBIGUOUS, signal_effect=True) is None
    assert system_resolution(CLERICAL, signal_effect=False) is None
    assert system_resolution(DUPLICATES, signal_effect=True) == SystemResolution(
        ACTION_APPLY, BASIS_FILER_CORRECTION
    )
    assert system_resolution(
        VOID, signal_effect=True, void=VoidReading(VOID_HELD)
    ) == SystemResolution(ACTION_KEEP, BASIS_HELD)
    unresolved = RowMatch(conflicts=({"txn_code": "S"},), reading=READ_UNRESOLVED)
    assert system_resolution(PARTIAL, signal_effect=True, match=unresolved) is None
    resolved = RowMatch(replaced=("O#0",), reading=READ_FIX)
    assert system_resolution(PARTIAL, signal_effect=True, match=resolved) == SystemResolution(
        ACTION_APPLY, BASIS_FILER_CORRECTION, ("O#0",)
    )


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
    held = _at(_dec("keep", basis=BASIS_HELD), 1)
    assert _bucket(_link_rec(VOID), held, in_review=True) == REVIEW_OWED
    assert _bucket(_link_rec(VOID), held, in_review=False) == REVIEW_HISTORICAL
    no_retraction = _at(_dec("keep", basis=BASIS_NO_RETRACTION), 1)
    assert _bucket(_link_rec(VOID), no_retraction) == REVIEW_AUTO


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


def test_the_sweep_resolves_only_an_undecided_amendment():
    pend = effect_of(_link_rec(RESTATEMENT), None, None)
    res = SystemResolution(ACTION_APPLY, BASIS_FILER_CORRECTION, ("O#0",))
    assert sweep_resolution(pend, ever_decided=False, resolution=res) == res
    assert (
        sweep_resolution(pend, ever_decided=True, resolution=res) is None
    )  # anything decided stays
    assert sweep_resolution(pend, ever_decided=False, resolution=None) is None


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


def test_an_applied_match_replaces_only_its_rows_and_a_legacy_apply_the_whole_original():
    """The resolver's R8-B contract with direct inputs: a verdict carrying ``replaced_rows`` suppresses exactly
    those original rows (the rest stand) and emits the amendment's; one carrying none (every pre-R8-B decision,
    an operator's apply of an unresolved match) replaces the whole original."""
    am = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "security_id": "s",
        "accession": "A",
        "rows": [{"txn_seq": 0, "txn_code": "A", "txn_date": "2026-09-24", "shares": 5.0}],
        "accepted": T0,
        "recorded_at": T0,
    }
    link = {**_link_rec(PARTIAL, originals=("O",)), "security_id": "s"}
    rows = [_row("O", 0, code="S"), _row("O", 1, code="S")]

    def _read(decision):
        return resolve_insider_rows(
            rows, amendments=[am], links=[link], decisions=[decision], asof=date(2026, 10, 1)
        )

    matched = _dec("apply", basis=BASIS_NO_SIGNAL_EFFECT, replaced_rows=[])
    assert [(r["accession"], r["txn_code"]) for r in _read(matched)] == [
        ("O", "S"),
        ("O", "S"),
        ("A", "A"),
    ]
    one = _dec("apply", basis=BASIS_FILER_CORRECTION, replaced_rows=["O#1"])
    assert [(r["accession"], r["txn_seq"]) for r in _read(one)] == [("O", 0), ("A", 0)]
    legacy = _dec("apply", basis="operator:apply")
    assert [r["accession"] for r in _read(legacy)] == ["A"]


def test_other_issuer_screen_is_keep_when_unknown():
    assert is_other_issuer({"issuer_cik": "1894693"}, frozenset({"2038439"}))
    assert not is_other_issuer({"issuer_cik": "0002038439"}, frozenset({"2038439"}))
    assert not is_other_issuer({"issuer_cik": None}, frozenset({"2038439"}))  # unknown issuer: kept
    assert not is_other_issuer({"issuer_cik": "1894693"}, frozenset())  # no own CIK on file: kept
