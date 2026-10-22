"""S4 — the TAPE as a confirming source and the SEC-only candidate (``domain/splits.py``), on REAL inputs only:
the filings, the 2026-10-22 Polygon rows and the bars as prod stored them (``tests/split_s4_fixtures``). The
tolerance itself is MEASURED (the constants' comment); these tests state the rule's contract on real split
nights, never fit a number to pass."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from domain.splits import (
    SOURCE_POLYGON,
    SOURCE_SEC,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    VIA_POLYGON_TAPE,
    VIA_SEC_TAPE,
    cluster_sec,
    decide,
    decide_sec_only,
    tape_agreement,
    with_tape,
)
from ingest.edgar.split_filing import extract_split
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
    # MDCX: the list (SEC legal date + Polygon) says 10-12; the vendor served 10-09 already on the new scale,
    # so the first-served tape steps on 10-09 and reads x0.78 on 10-12 — never a confirmation
    agree, why = tape_agreement(
        first_served("MDCX", date(2026, 10, 12)), Decimal(50), Decimal(1), date(2026, 10, 12)
    )
    assert not agree and "x0.778" in why


def test_the_tape_never_confirms_a_forward_split_or_a_small_one():
    tape = first_served("GAME", date(2026, 8, 24))
    agree, why = tape_agreement(tape, Decimal(1), Decimal(8), date(2026, 8, 24))  # 8-for-1 forward
    assert not agree and "cannot tell" in why
    agree, _ = tape_agreement(tape, Decimal(3), Decimal(2), date(2026, 8, 24))  # 2-for-3: k = 1.5
    assert not agree


def test_polygon_alone_plus_the_tape_confirms():
    poly = polygon_row("GAME")
    d = decide(poly, [], asof=date(2026, 8, 25))  # no SEC filing read: Polygon only
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
    # GAME's real 8-K against a Polygon row moved a session: S1 sends it to review; the tape must not settle it
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
