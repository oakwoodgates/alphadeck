"""The split-basis classifier (S2) on REAL stored tapes — the acceptance key is the split-tape diagnosis's
per-company table (section 2) and its Massive.com raw spot checks. Every tape, split row and raw print here is real
(``tests/split_s2_fixtures``); nothing is synthesized.

Where these labels differ from the diagnosis's simulated date lists, the difference is the diagnosis's own
named limitation ("my simple majority-plus-exceptions classifier misses long minority runs"), and the tape
says which is right: BURU's 12-bar stale run 2024-09-05..09-20 sits x30..x40 above its neighbors with the
volume x0.09 (adjusted — the diagnosis's list would have adjusted it twice), CPOP 2026-08-07 is one of the
diagnosis table's own adjusted islands, and VRME's 23-bar stale tail (2024-09-04..10-04, x9.9 below with the
volume x66 above) is raw, as the diagnosis's caveat 6 says.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from domain.splits import applicable, pending
from pipeline import split_repair as sr
from tests import split_s2_fixtures as fx

T = fx.TODAY


def _classify(tk: str, *, confirmed=(), spot: bool = False) -> sr.Classification:
    sp = fx.splits(tk, confirmed=confirmed)
    return sr.classify(
        fx.bars(tk),
        applicable(sp, on=T),
        today=T,
        transparent=pending(sp, on=T),
        spot=fx.spots(tk) if spot else None,
    )


def _runs(c: sr.Classification) -> list[tuple[str, str, str, int, str]]:
    return [(a.isoformat(), z.isoformat(), lab, n, f) for a, z, lab, n, f in c.runs()]


def _gates(tk: str, c: sr.Classification, *, confirmed=(), spot: bool = False) -> sr.GateResult:
    before = fx.bars(tk)
    return sr.plan_gates(
        before,
        sr.apply_labels(before, c.labels),
        applicable(fx.splits(tk, confirmed=confirmed), on=T),
        asof=T,
        spot=fx.spots(tk) if spot else None,
    )


# --- the diagnosis's shapes, each on its real tape -----------------------------------------------------------


def test_vwav_two_bar_raw_island_before_its_1_for_20():
    c = _classify("VWAV")
    assert c.ok, c.refusals
    assert [(b.d.isoformat(), b.label, b.factor) for b in c.to_change] == [
        ("2026-09-15", sr.RAW, Fraction(20)),
        ("2026-09-16", sr.RAW, Fraction(20)),
    ]


def test_btoc_cliff_flip_and_stale_tail_match_the_diagnosis_runs():
    c = _classify("BTOC")
    assert c.ok, c.refusals
    assert _runs(c) == [
        (
            "2024-09-04",
            "2024-10-14",
            sr.RAW,
            29,
            "x16",
        ),  # the stale tail (older than Yahoo's 2y window)
        (
            "2024-10-15",
            "2024-10-16",
            sr.ADJUSTED,
            2,
            "x1",
        ),  # the 10-15 re-base kept on two old bars
        ("2024-10-17", "2026-10-13", sr.RAW, 498, "x16"),  # the 10-17 flip back to raw
        ("2026-10-14", "2026-10-14", sr.ADJUSTED, 1, "x1"),
    ]
    assert len(c.to_change) == 527  # the diagnosis: "B: 527 bars x16"


def test_optt_raw_history_with_the_four_adjusted_odd_days():
    c = _classify("OPTT")
    assert c.ok, c.refusals
    assert len(c.to_change) == 503  # the diagnosis: "B: 503 x30"
    islands = [b.d.isoformat() for b in c.labels if b.label == sr.ADJUSTED]
    assert islands == ["2026-07-29", "2026-08-04", "2026-08-07", "2026-08-11"]
    assert {b.factor for b in c.to_change} == {Fraction(30)}


def test_opad_misdated_vendor_boundary_two_sessions_early():
    c = _classify("OPAD")
    assert c.ok, c.refusals
    assert _runs(c) == [
        ("2024-09-04", "2026-06-04", sr.RAW, 439, "x10"),
        ("2026-06-05", "2026-06-08", sr.ADJUSTED, 2, "x1"),  # adjusted before the 06-09 split
    ]


def test_pbm_double_adjustment_is_read_only_from_raw_prints():
    """A doubled split is a lone DOWN step on the tape — a genuine crash looks the same — so without raw
    prints nothing is changed; with Massive.com's prints the 344 bars before 01-20 are over-adjusted by 6.25.
    """
    assert _classify("PBM").to_change == []
    c = _classify("PBM", spot=True)
    assert c.ok, c.refusals
    assert _runs(c) == [
        ("2024-09-04", "2026-01-16", sr.OVER_ADJUSTED, 344, "x4/25"),
        ("2026-01-20", "2026-01-30", sr.ADJUSTED, 9, "x1"),
    ]
    assert _gates("PBM", c, spot=True).spot_fail == []


def test_smx_chain_two_missing_splits_among_eight_listed():
    """SMX lists EIGHT confirmed splits; the vendor applied six. Before 2025-11-18 the bars lack 1-for-8 and
    1-for-20 (x160), from 11-18 to 05-08 only the 1-for-20 — Massive.com's prints (the diagnosis's settle) are
    needed, because the 11-18 step on the tape is x5.03 (a -37% split day against k = 8)."""
    tape_only = _classify("SMX")
    assert not tape_only.ok
    assert any("ambiguous" in r for r in tape_only.refusals)  # k disagrees with the tape's own step
    c = _classify("SMX", spot=True)
    assert c.ok, c.refusals
    assert _runs(c) == [
        ("2024-09-04", "2025-11-17", sr.RAW, 303, "x160"),
        ("2025-11-18", "2026-05-08", sr.RAW, 118, "x20"),
        (
            "2026-05-11",
            "2026-05-29",
            sr.ADJUSTED,
            14,
            "x1",
        ),  # before the 06-01 x2.285 Yahoo applied
    ]
    by = {b.d: b for b in c.labels}
    assert by[date(2025, 11, 17)].missing == (date(2025, 11, 18), date(2026, 5, 11))
    g = _gates("SMX", c, spot=True)
    assert g.ok and g.spot_checked == 49
    # Yahoo's own cumulative factors sit 0.15..1.7% off the exact ratios (its convention): reported, not a fail
    assert g.spot_near and not g.spot_fail


def test_lrhc_chain_only_the_last_of_four_listed_splits_is_missing():
    """LRHC lists 1-for-80 / 1-for-10 / 1-for-10 / 1-for-6; the vendor applied the first three, so every raw
    bar — even before 2025-07-07 — lacks exactly the 1-for-6. Its 08-10 raw bar between the 08-07 and 08-11
    adjusted islands stays raw (its x4,051 volume does not decide it)."""
    c = _classify("LRHC")
    assert c.ok, c.refusals
    assert len(c.to_change) == 499  # the diagnosis: "B: 499 x6"
    assert {b.factor for b in c.to_change} == {Fraction(6)}
    by = {b.d: b for b in c.labels}
    assert by[date(2024, 9, 4)].missing == (date(2026, 9, 8),)
    assert by[date(2026, 8, 10)].label == sr.RAW
    assert [i.dates[0].isoformat() for i in c.islands] == [
        "2026-07-29",
        "2026-08-04",
        "2026-08-07",
        "2026-08-11",
    ]


def test_atro_mis_factor_needs_its_confirm_and_its_raw_prints():
    """ATRO's 6:5 distribution still needs review on the list: nothing in force, nothing labeled. Confirmed,
    the tape alone shows nothing (a 4.2% offset is an ordinary day); Massive.com's 30 pre-split prints all say
    the vendor applied x0.957701 of the list's factor — a MIS-FACTOR, corrected by x1.04417."""
    assert _classify("ATRO").labels == []
    assert _classify("ATRO", confirmed=("ATRO",)).to_change == []
    c = _classify("ATRO", confirmed=("ATRO",), spot=True)
    assert c.ok, c.refusals
    assert _runs(c) == [("2024-09-04", "2026-06-12", sr.MIS_FACTORED, 445, "x1.04417")]
    assert c.labels[0].mis_factor == pytest.approx(0.957701, abs=1e-6)
    assert _gates("ATRO", c, confirmed=("ATRO",), spot=True).spot_fail == []


def test_a_raw_print_matching_no_listed_scale_refuses_the_company():
    """ATRO's real prints match NO product of listed factors (x0.9577). Fewer than five of them cannot
    establish a mis-factor, so the company is refused — never repaired to a guess."""
    some = dict(list(fx.spots("ATRO").items())[:4])
    sp = fx.splits("ATRO", confirmed=("ATRO",))
    c = sr.classify(fx.bars("ATRO"), applicable(sp, on=T), today=T, spot=some)
    assert not c.ok
    assert "matches no listed scale" in c.refusals[0]


@pytest.mark.parametrize(
    "tk,confirmed,n,factors",
    [
        ("DUKR", (), 376, {Fraction(25)}),
        ("GGRP", (), 504, {Fraction(8)}),
        ("MI", ("MI",), 426, {Fraction(80)}),
        ("WNRS", ("WNRS",), 423, {Fraction(300)}),
        ("CPOP", (), 503, {Fraction(15)}),
        ("BURU", (), 484, {Fraction(40)}),
        ("VRME", (), 28, {Fraction(10)}),
    ],
)
def test_the_rest_of_the_known_companies(tk, confirmed, n, factors):
    c = _classify(tk, confirmed=confirmed)
    assert c.ok, c.refusals
    assert len(c.to_change) == n
    assert {b.factor for b in c.to_change} == factors


def test_needs_review_splits_wait_and_label_nothing():
    for tk in ("MI", "WNRS", "ATRO"):
        sp = fx.splits(tk)
        assert applicable(sp, on=T) == [] and pending(sp, on=T)
        assert _classify(tk).labels == []


@pytest.mark.parametrize("tk", ["SEGG", "YYAI"])
def test_a_tape_the_vendor_adjusted_needs_at_most_its_stale_bar(tk):
    c = _classify(tk)
    assert c.ok, c.refusals
    assert [b.d.isoformat() for b in c.to_change] == ([] if tk == "SEGG" else ["2024-09-04"])


# --- the gates on the repaired tapes, and the independent raw prints -----------------------------------------


@pytest.mark.parametrize(
    "tk,confirmed",
    [
        ("BTOC", ()),
        ("BURU", ()),
        ("CPOP", ()),
        ("DUKR", ()),
        ("GGRP", ()),
        ("LRHC", ()),
        ("MI", ("MI",)),
        ("OPAD", ()),
        ("OPTT", ()),
        ("VRME", ()),
        ("VWAV", ()),
        ("WNRS", ("WNRS",)),
    ],
)
def test_repaired_tape_is_t4_clean_and_agrees_with_polygon(tk, confirmed):
    c = _classify(tk, confirmed=confirmed, spot=True)
    assert c.ok, c.refusals
    g = _gates(tk, c, confirmed=confirmed, spot=True)
    assert g.t4_after == [] and g.k_steps_after == [] and g.spot_fail == [], g.reasons()
    assert g.spot_checked > 0


def test_t4_flagged_these_tapes_before_the_repair():
    for tk in ("BTOC", "DUKR", "GGRP", "OPAD", "OPTT", "VWAV"):
        c = _classify(tk)
        assert _gates(tk, c).t4_before, tk


def test_the_correction_is_exact_on_the_stored_decimal():
    by = {r["d"]: r for r in fx.rows("BTOC")}
    lab = next(b for b in _classify("BTOC").labels if b.d == date(2026, 10, 13))
    new = sr.repaired_bar(by[lab.d], sr.correction(lab))
    assert new["close"] == by[lab.d]["close"] * 16
    assert new["volume"] == by[lab.d]["volume"] / Decimal(16)
