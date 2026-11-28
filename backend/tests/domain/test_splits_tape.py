"""S4 — the TAPE as a confirming source and the SEC-only candidate (``domain/splits.py``), on REAL inputs only:
the filings, the 2026-10-22 Massive.com rows and the bars as prod stored them (``tests/split_s4_fixtures``). The
tolerance itself is MEASURED (the constants' comment); these tests state the rule's contract on real split
nights, never fit a number to pass."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from domain.splits import (
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_POLYGON_TAPE,
    VIA_SEC_TAPE,
    PolygonEvidence,
    SecEvidence,
    TapeEvidence,
    cluster_sec,
    decide,
    decide_sec_only,
    restated,
    tape_agreement,
    with_tape,
)
from ingest.edgar.split_filing import extract_split
from pipeline.backfill_splits import FilingRef, extract_for
from tests.split_fixtures import documents
from tests.split_s4_fixtures import first_served, polygon_row, sec

GAME_8K = "0001493152-26-039213"
NXL_8K = "0001829126-26-009387"
MDCX_8K = "0001062993-26-005382"
VRME_8K = "0001214659-26-011966"


def test_the_tape_confirms_real_split_nights_on_their_first_session():
    # the as-traded close the night before vs the first session at the new price, volume falling
    for ticker, d, ratio in [
        ("GAME", date(2026, 8, 24), (8, 1)),  # x7.50 on 0.42 -> 3.17
        ("NXL", date(2026, 8, 31), (30, 1)),  # x28.9
        ("VRME", date(2026, 9, 29), (10, 1)),  # x8.22 (the 09-29 bar was first served a week late)
    ]:
        tape = first_served(ticker, d)
        assert tape.prior_served_before, ticker
        agree, why = tape_agreement(tape, Decimal(ratio[0]), Decimal(ratio[1]), d)
        assert agree, (ticker, why)


def test_the_tape_is_exact_on_the_date():
    # GAME's step lands on 08-24; the session after shows a real day's move, not the split
    tape = first_served("GAME", date(2026, 8, 25))
    agree, why = tape_agreement(tape, Decimal(8), Decimal(1), date(2026, 8, 25))
    assert not agree and "not about x8" in why
    # and a tape read at another session never answers for this one
    assert not tape_agreement(
        first_served("GAME", date(2026, 8, 24)), Decimal(8), Decimal(1), date(2026, 8, 25)
    )[0]


def test_a_vendor_that_re_based_the_session_before_does_not_confirm():
    # MDCX: the list (SEC legal date + Massive.com) says 10-12; the vendor served 10-09 already on the new scale,
    # so the first-served tape steps on 10-09 and reads x0.78 on 10-12 — never a confirmation
    agree, why = tape_agreement(
        first_served("MDCX", date(2026, 10, 12)), Decimal(50), Decimal(1), date(2026, 10, 12)
    )
    assert not agree and "x0.778" in why


def test_the_tape_never_confirms_a_forward_split_or_a_small_one():
    tape = first_served("GAME", date(2026, 8, 24))
    agree, why = tape_agreement(tape, Decimal(1), Decimal(8), date(2026, 8, 24))  # 8-for-1 forward
    assert not agree and why.startswith("the tape never confirms a forward split")
    agree, why = tape_agreement(tape, Decimal(3), Decimal(2), date(2026, 8, 24))  # 2-for-3: k = 1.5
    assert not agree and "cannot tell" in why


def test_ita_the_note_on_a_forward_split_says_the_step_and_the_factor_it_expected():
    # ITA: Massive.com lists a 3-for-1 first trading 2026-11-05 and the as-traded tape fell x0.32 that day on
    # rising volume. By design the tape never confirms a forward split (a fall on rising volume reads like a
    # crash); the note used to say "moves the price by less than x1.8 up", a reverse split's words. Only
    # the words change: the decision stays needs-review, unconfirmed, on Massive.com's values.
    poly = PolygonEvidence(
        id="ita",
        ticker="ITA",
        execution_date=date(2026, 11, 5),
        split_from=Decimal(1),
        split_to=Decimal(3),
    )
    d = decide(poly, [], asof=date(2026, 11, 6))
    assert (d.status, d.value_source) == (STATUS_NEEDS_REVIEW, SOURCE_POLYGON)
    tape = TapeEvidence(
        session=date(2026, 11, 5),
        prior=date(2026, 11, 4),
        prior_close=100.0,
        close=32.0,
        prior_volume=1_000_000.0,
        volume=2_500_000.0,
        prior_served_before=True,
    )
    out = with_tape(d, tape)
    assert replace(out, note=d.note) == d  # nothing but the note differs from the decision as made
    assert (out.status, out.confirmed_via) == (STATUS_NEEDS_REVIEW, None)
    assert (
        " The tape never confirms a forward split (a fall on rising volume reads like a crash); "
        "it stepped x0.32 on 2026-11-05, a 3-for-1 is x0.333."
    ) in out.note
    assert "does not confirm" not in out.note and "less than" not in out.note
    # the evidence stays
    assert "Tape as first served: 2026-11-04 close 100 -> 2026-11-05 32 (x0.32)" in out.note


def test_polygon_alone_plus_the_tape_confirms():
    poly = polygon_row("GAME")
    d = decide(poly, [], asof=date(2026, 8, 25))  # no SEC filing read: Massive.com only
    assert d.status == STATUS_NEEDS_REVIEW and d.value_source == SOURCE_POLYGON
    out = with_tape(d, first_served("GAME", date(2026, 8, 24)))
    assert (out.status, out.confirmed_via) == (STATUS_CONFIRMED, VIA_POLYGON_TAPE)
    assert "Tape as first served: 2026-08-21 close 0.422 -> 2026-08-24 3.165 (x7.5)" in out.note


def test_an_sec_filing_stating_the_trading_session_plus_the_tape_confirms():
    d = decide_sec_only([sec(NXL_8K)], asof=date(2026, 9, 1))
    assert (d.status, d.value_source, d.polygon) == (STATUS_NEEDS_REVIEW, SOURCE_SEC, None)
    assert (d.ratio_from, d.ratio_to, d.effective_date) == (
        Decimal(30),
        Decimal(1),
        date(2026, 8, 31),
    )
    out = with_tape(d, first_served("NXL", date(2026, 8, 31)))
    assert (out.status, out.confirmed_via) == (STATUS_CONFIRMED, VIA_SEC_TAPE)


def test_a_legal_date_is_never_confirmed_by_the_tape():
    # MDCX's 8-K states a LEGAL effective date: the first session may be a session later, and the tape is
    # never asked to pick it — it stays for review, the tape reading added as evidence
    s = sec(MDCX_8K)
    assert s.date_kind == "legal"
    d = decide_sec_only([s], asof=date(2026, 10, 16))
    out = with_tape(d, first_served("MDCX", date(2026, 10, 12)))
    assert out.status == STATUS_NEEDS_REVIEW and out.confirmed_via is None
    assert "Tape as first served" in out.note


def test_two_sources_that_disagree_are_never_arbitrated_by_the_tape():
    # GAME's real 8-K against a Massive.com row moved a session: S1 sends it to review; the tape must not settle it
    poly = polygon_row("GAME")
    moved = type(poly)(
        id=poly.id,
        ticker=poly.ticker,
        execution_date=date(2026, 8, 25),
        split_from=poly.split_from,
        split_to=poly.split_to,
    )
    d = decide(moved, [sec(GAME_8K)], asof=date(2026, 8, 26))
    assert d.status == STATUS_NEEDS_REVIEW and d.sec is not None and d.sec.has_ratio
    out = with_tape(d, first_served("GAME", date(2026, 8, 25)))
    assert out.status == STATUS_NEEDS_REVIEW and out.confirmed_via is None


def test_a_confirmed_decision_is_left_exactly_as_s1_made_it():
    d = decide(polygon_row("GAME"), [sec(GAME_8K)], asof=date(2026, 8, 25))
    assert d.status == STATUS_CONFIRMED
    assert with_tape(d, first_served("GAME", date(2026, 8, 24))) is d


def test_sec_filings_about_one_split_cluster_into_one_candidate():
    groups = cluster_sec([sec(GAME_8K), sec(NXL_8K), sec(VRME_8K)])
    assert [len(g) for g in groups] == [1, 1, 1]
    assert cluster_sec([sec(GAME_8K), sec(GAME_8K)]) == [[sec(GAME_8K), sec(GAME_8K)]]


def test_a_filing_ahead_of_its_split_says_when_it_takes_effect():
    d = decide_sec_only([sec(GAME_8K)], asof=date(2026, 8, 19))
    assert d.status == STATUS_NEEDS_REVIEW and "Takes effect 2026-08-24" in d.note
    assert d.valid_from == date(2026, 8, 19)  # public from the filing


def test_an_every_n_to_m_range_is_not_a_ratio_mtek():
    x = extract_split(documents("0001185185-26-004872.txt"))
    assert not x.has_ratio and x.abstain_reason.startswith("only an authorized range")


def test_a_past_split_restated_in_a_6k_is_listed_for_review_never_confirmed():
    # WSHP's 6-K (filed 2026-08-24) restates a 1-for-4 effective 2025-10-22 that the list does not hold: the
    # detector shows it to the operator (recall), and nothing but a second source can confirm it
    d = decide_sec_only([sec("0001493152-26-039876")], asof=date(2026, 10, 22))
    assert (d.status, d.ratio_from, d.ratio_to) == (STATUS_NEEDS_REVIEW, Decimal(4), Decimal(1))
    assert d.effective_date == date(2025, 10, 22) and d.sec.date_kind == "legal"
    assert d.valid_from == date(2025, 10, 22)


# --- F8-2 (dark, decision D-F8b): a later filing restating the split's ratio joins its candidate ----------------
#
# ``restate_days`` is the setting ``split_sec_restate_days``, read by the caller (0 = off, the shipped default). The
# shapes are the 800-day check's (2026-11-26; the F8-1 tests' filings, as that plan's lines read them): CHRN's two
# 8-Ks of one 1-for-15 filed a day apart (first session 05-27, then 06-02: the board moved it), PTN's 8-K/A (first
# session 08-12) and its 8-K 30 days later (a legal 09-10).

WSHP_6K = "0001493152-26-039876"
RESTATE_ASOF = date(2026, 11, 26)


def _filing(
    accession: str, form: str, filed: date, ratio_from: int, effective: date, kind: str = "trading"
) -> SecEvidence:
    return SecEvidence(
        accession,
        form,
        filed,
        "u",
        ratio_from=Decimal(ratio_from),
        ratio_to=Decimal(1),
        effective_date=effective,
        date_kind=kind,
        about_a_split=True,
    )


CHRN_8K = _filing("0001437749-25-017680", "8-K", date(2025, 5, 20), 15, date(2025, 5, 27))
CHRN_8K_NEXT = _filing("0001437749-25-018005", "8-K", date(2025, 5, 21), 15, date(2025, 6, 2))
PTN_8KA = _filing("0001654954-25-009330", "8-K/A", date(2025, 8, 11), 50, date(2025, 8, 12))
PTN_8K = _filing("0001654954-25-010576", "8-K", date(2025, 9, 10), 50, date(2025, 9, 10), "legal")
# one candidate by S4's own rule (each date within 2 sessions of the first filing's), its dates 4 sessions apart
SPAN = [
    _filing("0000000000-26-000001", "8-K", date(2026, 3, 2), 10, date(2026, 3, 10)),
    _filing("0000000000-26-000002", "8-K/A", date(2026, 3, 3), 10, date(2026, 3, 12)),
    _filing("0000000000-26-000003", "8-K/A", date(2026, 3, 4), 10, date(2026, 3, 6)),
]


def _accessions(groups) -> list[list[str]]:
    return [[s.accession for s in g] for g in groups]


def test_at_0_the_restate_rule_is_off_every_grouping_and_decision_as_before():
    """The shipped default: each grouping and decision exactly as S4 made them (pinned values, not a re-run)."""
    real = [sec(a) for a in (GAME_8K, NXL_8K, MDCX_8K, VRME_8K, WSHP_6K)]
    filings = [*real, CHRN_8K, CHRN_8K_NEXT, PTN_8KA, PTN_8K, *SPAN]
    groups = cluster_sec(filings, restate_days=0)
    assert groups == cluster_sec(filings)
    assert _accessions(groups) == [
        [CHRN_8K.accession],
        [CHRN_8K_NEXT.accession],
        [PTN_8KA.accession],
        [PTN_8K.accession],
        [s.accession for s in SPAN],
        [GAME_8K],
        [WSHP_6K],
        [NXL_8K],
        [VRME_8K],
        [MDCX_8K],
    ]
    decided = [decide_sec_only(g, asof=RESTATE_ASOF, restate_days=0) for g in groups]
    assert decided == [decide_sec_only(g, asof=RESTATE_ASOF) for g in groups]
    assert not any(restated(g, restate_days=0) for g in groups)
    assert [(d.effective_date, d.sec.accession) for d in decided[:5]] == [
        (date(2025, 5, 27), CHRN_8K.accession),
        (date(2025, 6, 2), CHRN_8K_NEXT.accession),
        (date(2025, 8, 12), PTN_8KA.accession),
        (date(2025, 9, 10), PTN_8K.accession),
        # the earliest filing's first session, as S4 picks it
        (date(2026, 3, 10), SPAN[0].accession),
    ]
    assert decided[0].note == (
        "SEC only: SEC 8-K 0001437749-25-017680 (filed 2025-05-20): 1-for-15 reverse split, first session "
        "2025-05-27; no Massive.com row lists this split (tonight's pull)."
    )
    assert decided[4].note == (
        "SEC only: SEC 8-K 0000000000-26-000001 (filed 2026-03-02): 1-for-10 reverse split, first session "
        "2026-03-10; no Massive.com row lists this split (tonight's pull). 3 SEC filings state it. They state "
        "different dates: 2026-03-06, 2026-03-10, 2026-03-12."
    )


def test_at_30_chrn_s_later_8k_joins_the_candidate_and_its_first_session_supersedes():
    (g,) = cluster_sec([CHRN_8K_NEXT, CHRN_8K], restate_days=30)
    assert g == [CHRN_8K, CHRN_8K_NEXT]
    assert restated(g, restate_days=30) and not restated(g, restate_days=0)
    d = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30)
    assert (d.key_date, d.effective_date, d.ratio_from, d.ratio_to) == (
        date(2025, 6, 2),
        date(2025, 6, 2),
        Decimal(15),
        Decimal(1),
    )
    assert (d.status, d.confirmed_via, d.value_source, d.polygon, d.sec) == (
        STATUS_NEEDS_REVIEW,
        None,
        SOURCE_SEC,
        None,
        CHRN_8K_NEXT,
    )
    assert d.valid_from == date(2025, 5, 20)  # S4's rule unchanged: the group's earliest filing
    assert d.note == (
        "SEC only: SEC 8-K 0001437749-25-018005 (filed 2025-05-21): 1-for-15 reverse split, first session "
        "2025-06-02; no Massive.com row lists this split (tonight's pull). 2 SEC filings state it. They state "
        "different dates: 8-K 0001437749-25-017680 (filed 2025-05-20) first session 2025-05-27; 8-K "
        "0001437749-25-018005 (filed 2025-05-21) first session 2025-06-02. A later filing restating the split "
        "supersedes: the latest stating the first session is kept."
    )


def test_at_30_ptn_keeps_the_8ka_s_first_session_over_the_later_8k_s_legal_date():
    """The latest filing that states the first TRADING session wins; a later legal date never does."""
    # filed 30 days apart: the bound is inclusive
    (g,) = cluster_sec([PTN_8KA, PTN_8K], restate_days=30)
    d = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30)
    assert (d.effective_date, d.sec, d.valid_from) == (
        date(2025, 8, 12),
        PTN_8KA,
        date(2025, 8, 11),
    )
    assert d.note == (
        "SEC only: SEC 8-K/A 0001654954-25-009330 (filed 2025-08-11): 1-for-50 reverse split, first session "
        "2025-08-12; no Massive.com row lists this split (tonight's pull). 2 SEC filings state it. They state "
        "different dates: 8-K/A 0001654954-25-009330 (filed 2025-08-11) first session 2025-08-12; 8-K "
        "0001654954-25-010576 (filed 2025-09-10) effective 2025-09-10. A later filing restating the split "
        "supersedes: the latest stating the first session is kept."
    )
    assert _accessions(cluster_sec([PTN_8KA, PTN_8K], restate_days=29)) == [
        [PTN_8KA.accession],
        [PTN_8K.accession],
    ]


def test_with_legal_dates_only_the_latest_one_is_kept_and_said_to_be_legal():
    first = _filing("0000000000-26-000011", "8-K", date(2026, 4, 1), 20, date(2026, 4, 6), "legal")
    later = _filing("0000000000-26-000012", "8-K", date(2026, 4, 9), 20, date(2026, 4, 20), "legal")
    (g,) = cluster_sec([first, later], restate_days=30)
    d = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30)
    assert (d.effective_date, d.sec) == (date(2026, 4, 20), later)
    assert d.note.endswith(
        "They state different dates: 8-K 0000000000-26-000011 (filed 2026-04-01) effective 2026-04-06; 8-K "
        "0000000000-26-000012 (filed 2026-04-09) effective 2026-04-20. A later filing restating the split "
        "supersedes: the latest effective date is kept. A legal effective date: the first session may be a "
        "session later."
    )


def test_at_30_a_group_s_dates_apart_by_s4_s_own_rule_are_settled_by_the_latest_filing_too():
    (g,) = cluster_sec(SPAN, restate_days=30)
    d = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30)
    assert (d.effective_date, d.sec, d.valid_from) == (date(2026, 3, 6), SPAN[2], date(2026, 3, 2))


def test_the_restate_join_needs_the_same_ratio_and_a_filing_within_the_days():
    other_ratio = _filing("0000000000-25-000021", "8-K", date(2025, 5, 22), 10, date(2025, 6, 9))
    assert _accessions(cluster_sec([CHRN_8K, other_ratio], restate_days=30)) == [
        [CHRN_8K.accession],
        [other_ratio.accession],
    ]
    # APUS's shape: one ratio, a first session 120 days on, filed months later: two candidates (serial splits of
    # one ratio exist), whatever the setting
    march = _filing("0001213900-26-028332", "8-K", date(2026, 3, 16), 10, date(2026, 3, 26))
    july = _filing("0000000000-26-000022", "8-K", date(2026, 7, 20), 10, date(2026, 7, 24))
    assert _accessions(cluster_sec([march, july], restate_days=30)) == [
        [march.accession],
        [july.accession],
    ]
    # the window runs from the group's NEWEST filing: a chain of restatements stays one candidate
    a = _filing("0000000000-26-000031", "8-K", date(2026, 1, 5), 8, date(2026, 1, 20))
    b = _filing("0000000000-26-000032", "8-K", date(2026, 2, 2), 8, date(2026, 2, 9))
    c = _filing("0000000000-26-000033", "8-K", date(2026, 3, 2), 8, date(2026, 3, 9))
    (g,) = cluster_sec([a, b, c], restate_days=30)
    assert g == [a, b, c]


def test_a_date_match_still_wins_over_a_restate_join():
    """A filing S4 already places by its date joins that candidate, never a newer one of the same ratio."""
    early = _filing("0000000000-26-000041", "8-K", date(2026, 1, 2), 5, date(2026, 3, 2))
    late = _filing("0000000000-26-000042", "8-K", date(2026, 2, 10), 5, date(2026, 6, 1))
    restating_early = _filing(
        "0000000000-26-000043", "8-K/A", date(2026, 2, 20), 5, date(2026, 3, 3)
    )
    groups = cluster_sec([early, late, restating_early], restate_days=30)
    assert _accessions(groups) == [
        [early.accession, restating_early.accession],
        [late.accession],
    ]
    assert groups == cluster_sec([early, late, restating_early])


# --- F3b (dark, framework completion): a NON-SESSION legal date moves to the next session ------------------------
#
# ``next_session`` is the setting ``split_legal_date_next_session``, read by the caller (False = off, the shipped
# default). SKYA's charter 8-K (filed 2025-04-30) states only a legal date, Sunday 2025-04-27 (the reader demotes its
# trading cue on a non-session to legal); SKYA first traded on the new basis on Monday 04-28. Only the chosen filing's
# LEGAL date moves, and only when it is no session (``domain.trading_days``); a trading date is never moved, and the
# SEC evidence keeps the date its passage states.

SKYA_8K = "0001641172-25-007892"
F3B_ASOF = date(2026, 11, 27)


def _skya() -> SecEvidence:
    """SKYA's 8-K read the way the pipeline reads it: a charter 8-K (5.03) through the restatement screen."""
    ref = FilingRef(accession=SKYA_8K, form="8-K", filed=date(2025, 4, 30), items=("5.03", "9.01"))
    x = extract_for(documents(f"{SKYA_8K}.txt"), ref)
    return SecEvidence(
        accession=SKYA_8K,
        form="8-K",
        filed=ref.filed,
        url="https://www.sec.gov/",
        ratio_from=x.ratio_from,
        ratio_to=x.ratio_to,
        effective_date=x.effective_date,
        date_kind=x.date_kind,
        passage=x.passage,
        document=x.document,
        abstain_reason=x.abstain_reason,
        date_note=x.date_note,
        about_a_split=x.about_a_split,
    )


SKYA_OFF_NOTE = (
    "SEC only: SEC 8-K 0001641172-25-007892 (filed 2025-04-30): 1-for-300 reverse split, effective 2025-04-27; no "
    "Massive.com row lists this split (tonight's pull). A legal effective date: the first session may be a session "
    "later."
)


def test_off_skyas_sunday_legal_date_is_planned_as_stated():
    """The shipped default: the first session is the legal date as the filing states it (a Sunday), as before."""
    s = _skya()
    assert (s.ratio_from, s.ratio_to, s.effective_date, s.date_kind) == (
        Decimal(300),
        Decimal(1),
        date(2025, 4, 27),
        "legal",
    )
    d = decide_sec_only([s], asof=F3B_ASOF)
    assert decide_sec_only([s], asof=F3B_ASOF, next_session=False) == d
    assert (d.key_date, d.effective_date, d.valid_from, d.status, d.sec) == (
        date(2025, 4, 27),
        date(2025, 4, 27),
        date(2025, 4, 27),
        STATUS_NEEDS_REVIEW,
        s,
    )
    assert d.note == SKYA_OFF_NOTE


def test_on_skyas_sunday_legal_date_moves_to_mondays_session():
    s = _skya()
    off = decide_sec_only([s], asof=F3B_ASOF)
    on = decide_sec_only([s], asof=F3B_ASOF, next_session=True)
    assert (on.key_date, on.effective_date) == (date(2025, 4, 28), date(2025, 4, 28))
    # the evidence keeps the date its passage states
    assert on.sec == s and on.sec.effective_date == date(2025, 4, 27)
    assert (on.status, on.confirmed_via, on.value_source, on.polygon) == (
        STATUS_NEEDS_REVIEW,
        None,
        SOURCE_SEC,
        None,
    )
    assert on.note == (
        "SEC only: SEC 8-K 0001641172-25-007892 (filed 2025-04-30): 1-for-300 reverse split, effective 2025-04-27; "
        "no Massive.com row lists this split (tonight's pull). The stated effective date 2025-04-27 is a weekend: "
        "the first session is the next one, 2025-04-28, at the earliest."
    )
    # nothing else moves: the ratio, the status, the evidence and valid_from (the stated date) are as off
    assert (
        replace(on, key_date=off.key_date, effective_date=off.effective_date, note=off.note) == off
    )


def test_on_a_holiday_legal_date_moves_to_the_next_session():
    """A legal date on an NYSE full-day closure (Christmas Day 2026, a Friday) moves over the weekend to Monday;
    Juneteenth 2025 (a Thursday: PMEC's resolution date in the 2026-11-26 list) to the Friday."""
    xmas = _filing(
        "0000000000-26-000051", "8-K", date(2026, 12, 18), 10, date(2026, 12, 25), "legal"
    )
    d = decide_sec_only([xmas], asof=date(2026, 12, 21), next_session=True)
    assert (d.key_date, d.effective_date, d.sec.effective_date) == (
        date(2026, 12, 28),
        date(2026, 12, 28),
        date(2026, 12, 25),
    )
    assert d.note.endswith(
        "The stated effective date 2026-12-25 is a market holiday (Christmas Day): the first session is the next "
        "one, 2026-12-28, at the earliest. Takes effect 2026-12-28."
    )
    assert d.valid_from == date(2026, 12, 18)
    off = decide_sec_only([xmas], asof=date(2026, 12, 21), next_session=False)
    assert (off.key_date, off.effective_date) == (date(2026, 12, 25), date(2026, 12, 25))
    assert off.note.endswith(
        "A legal effective date: the first session may be a session later. Takes effect 2026-12-25."
    )
    june = _filing("0000000000-25-000052", "6-K", date(2025, 9, 2), 6, date(2025, 6, 19), "legal")
    d = decide_sec_only([june], asof=F3B_ASOF, next_session=True)
    assert d.effective_date == date(2025, 6, 20)
    assert "2025-06-19 is a market holiday (Juneteenth" in d.note


def test_on_a_trading_date_on_a_non_session_is_never_moved():
    """Only a LEGAL date moves. A trading date the reader left on a non-session is decided exactly as off."""
    for effective in (date(2026, 3, 1), date(2026, 12, 25)):  # a Sunday, a closure
        s = _filing("0000000000-26-000053", "8-K", date(2026, 2, 20), 10, effective, "trading")
        off = decide_sec_only([s], asof=F3B_ASOF)
        assert decide_sec_only([s], asof=F3B_ASOF, next_session=True) == off
        assert off.effective_date == effective


def test_on_a_legal_date_on_a_session_and_a_trading_filing_in_the_group_are_unchanged():
    # MDCX's real legal reading (a session), and a legal weekday
    mdcx = sec(MDCX_8K)
    assert mdcx.date_kind == "legal"
    weekday = _filing(
        "0000000000-26-000054", "8-K", date(2026, 4, 1), 20, date(2026, 4, 6), "legal"
    )
    # SKYA's shape on prod: a sibling 8-K stating the first TRADING session wins over the Sunday-legal one
    sibling = _filing("0001641172-25-006045", "8-K", date(2025, 4, 25), 300, date(2025, 4, 28))
    for group, asof in (
        ([mdcx], date(2026, 10, 16)),
        ([weekday], F3B_ASOF),
        ([sibling, _skya()], F3B_ASOF),
        ([sec(a) for a in (GAME_8K, NXL_8K, VRME_8K, WSHP_6K)], F3B_ASOF),
    ):
        for g in cluster_sec(group):
            assert decide_sec_only(g, asof=asof, next_session=True) == decide_sec_only(g, asof=asof)
    (g,) = cluster_sec([sibling, _skya()])
    assert decide_sec_only(g, asof=F3B_ASOF, next_session=True).effective_date == date(2025, 4, 28)


def test_on_the_tape_is_read_on_the_moved_session_and_never_confirms_a_legal_date():
    """The detector reads the tape on the decision's first session: on, Monday's bar (a Sunday has none). A legal
    date is never confirmed by it: the reading is evidence on the note, the decision stays for review.
    """
    s = _skya()
    on = decide_sec_only([s], asof=F3B_ASOF, next_session=True)
    tape = TapeEvidence(
        session=date(2025, 4, 28),
        prior=date(2025, 4, 25),
        prior_close=0.05,
        close=15.0,
        prior_volume=3_000_000.0,
        volume=10_000.0,
        prior_served_before=True,
    )
    out = with_tape(on, tape)
    assert (out.status, out.confirmed_via) == (STATUS_NEEDS_REVIEW, None)
    assert out.note == f"{on.note} {tape.describe()}."
    # off, the decision's first session is the Sunday: a reading of another session never answers for it
    off = decide_sec_only([s], asof=F3B_ASOF)
    assert with_tape(off, tape) is off


def test_on_with_the_restate_rule_the_latest_legal_date_kept_moves_too():
    """F8-2's restated candidate keeps its latest filing's legal date; a Saturday one moves to Monday."""
    first = _filing("0000000000-26-000055", "8-K", date(2026, 4, 1), 20, date(2026, 4, 6), "legal")
    later = _filing("0000000000-26-000056", "8-K", date(2026, 4, 9), 20, date(2026, 4, 18), "legal")
    (g,) = cluster_sec([first, later], restate_days=30)
    off = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30)
    on = decide_sec_only(g, asof=RESTATE_ASOF, restate_days=30, next_session=True)
    assert (off.effective_date, on.effective_date, on.sec) == (
        date(2026, 4, 18),
        date(2026, 4, 20),
        later,
    )
    assert on.note.endswith(
        "the latest effective date is kept. The stated effective date 2026-04-18 is a weekend: the first session "
        "is the next one, 2026-04-20, at the earliest."
    )
