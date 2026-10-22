"""The split-night hold's pure rules (``domain/split_hold.py``, split plan S5) — the CONTRACTS, on direct inputs.

The goldens on REAL split nights (GAME, NXL, MDCX, VRME held; PTIX, JAGX never; ZCSH's real forward split) run
through the real point-in-time view in ``tests/signals/test_split_hold.py``. This file pins what the rule IS:
the step shape, the three-session window, what covers a step, the card's words, and the escalation.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from domain import split_hold as sh
from domain.splits import (
    SESSION_TOLERANCE,
    STATUS_ANNOUNCED,
    STATUS_CONFIRMED,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    TAPE_BETA_MAX,
)
from pipeline import split_basis


def _bar(d: date, close: float, volume: float | None) -> dict:
    return {"d": d, "close": close, "volume": volume}


def _row(eff: date, status: str, *, rf="16", rt="1", **extra) -> dict:
    return {
        "key_date": eff,
        "effective_date": eff,
        "ratio_from": Decimal(rf),
        "ratio_to": Decimal(rt),
        "status": status,
        **extra,
    }


MON, TUE, WED, THU, FRI = (date(2026, 10, d) for d in (12, 13, 14, 15, 16))
PREV_FRI = date(2026, 10, 9)


def test_the_dials_are_pinned():
    assert sh.STEP_MIN == 3.0
    assert sh.BETA_MAX == TAPE_BETA_MAX  # S4's physical bound on the inverse volume
    assert sh.HOLD_SESSIONS == 3
    assert sh.MIN_CLOSE == split_basis.MIN_CLOSE  # T4's tick floor, one number
    assert sh.COVER_SESSIONS == SESSION_TOLERANCE  # S1's legal-date tolerance


# --- rule 1: the split-shaped step ----------------------------------------------------------------------


def test_a_reverse_split_shape_up_with_the_volume_down_is_a_step():
    s = sh.step_between(_bar(MON, 0.42, 1_486_400), _bar(TUE, 3.65, 446_494))
    assert s is not None and s.up and s.prior_d == MON and s.d == TUE
    assert s.step == pytest.approx(3.65 / 0.42) and 0 < s.beta <= 3


def test_a_forward_split_shape_down_with_the_volume_up_is_a_step_both_directions_hold():
    """An unadjusted forward split reads as a crash on rising volume — held too (operator-approved)."""
    s = sh.step_between(_bar(MON, 115.65, 882_400), _bar(TUE, 37.0, 2_210_700))
    assert s is not None and not s.up and s.describe() == "stepped down to 1/3.1 on 10-13"


@pytest.mark.parametrize(
    "a, b, why",
    [
        ((1.0, 1_000), (12.9, 383_000), "a genuine surge: the volume moved WITH the price"),
        ((4.0, 1_000), (1.03, 417), "a genuine fall: the volume fell WITH the price"),
        ((1.0, 1_000), (2.9, 100), "below x3"),
        ((1.0, 1_000), (16.0, None), "no volume: no inverse-volume evidence"),
        ((0.01, 1_000), (0.05, 10), "below the tick floor"),
        ((3.0, 1_000_000), (1.0, 1_000_000_000), "a volume move out of physical scale (beta > 3)"),
        ((1.0, 1_000), (16.0, 1_000), "flat volume: beta 0"),
    ],
)
def test_not_a_step(a, b, why):
    assert sh.step_between(_bar(MON, *a), _bar(TUE, *b)) is None, why


def test_find_steps_skips_unpriced_bars_so_a_step_is_always_between_two_priced_bars():
    bars = [_bar(MON, 0.5, 1_000), {"d": TUE, "close": None, "volume": None}, _bar(WED, 8.0, 50)]
    (s,) = sh.find_steps(bars)
    assert (s.prior_d, s.d) == (MON, WED)


# --- rule 2: the window ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "step_day, asof, held",
    [
        (MON, MON, True),  # the step day = session 1
        (MON, TUE, True),  # session 2
        (MON, WED, True),  # session 3
        (MON, THU, False),  # released
        (PREV_FRI, MON, True),  # a weekend is not a session
        (PREV_FRI, TUE, True),
        (PREV_FRI, WED, False),
    ],
)
def test_the_hold_window_is_three_sessions_counting_the_step_day(step_day, asof, held):
    s = sh.SplitStep(date(2026, 10, 1), step_day, 1.0, 16.0, 1_000, 50)
    assert sh.in_hold_window(s, asof) is held


def _tape(step_day: date) -> list[dict]:
    return [_bar(date(2026, 10, 1), 0.5, 10_000), _bar(step_day, 8.0, 600)]


def test_held_when_nothing_on_the_list_covers_the_step():
    h = sh.hold_for(_tape(MON), [], asof=TUE)
    assert h is not None and h.cut == MON
    assert h.reason == (
        "possible unconfirmed split (the price stepped x16 on 10-12 against the volume; no split filing "
        "found yet)"
    )


def test_a_confirmed_split_in_the_window_covers_the_step():
    # inside (prior bar, step bar], or within two sessions of the step bar (a vendor a session or two early)
    for eff in (MON, date(2026, 10, 8), WED):
        assert sh.hold_for(_tape(MON), [_row(eff, STATUS_CONFIRMED)], asof=WED) is None, eff


def test_a_confirmed_split_far_from_the_step_does_not_cover_it():
    far = _row(date(2026, 9, 1), STATUS_CONFIRMED)
    assert sh.hold_for(_tape(MON), [far], asof=TUE) is not None


def test_an_announced_split_covers_only_once_in_force():
    """Announced = two sources agreed; in force from its first session (``domain.splits.is_applicable``)."""
    ann = _row(MON, STATUS_ANNOUNCED)
    assert sh.hold_for(_tape(MON), [ann], asof=TUE) is None  # its first session has arrived
    early = _row(WED, STATUS_ANNOUNCED)  # the vendor stepped two sessions before the first session
    h = sh.hold_for(_tape(MON), [early], asof=TUE)
    assert h is not None and h.reason == (
        "the 1-for-16 reverse split is announced for 10-14, but the price stepped x16 on 10-12, before "
        "its first session"
    )


@pytest.mark.parametrize(
    "extra, words",
    [
        (
            {"sec_accession": "0001", "sec_form": "8-K", "sec_filed": date(2026, 10, 9)},
            "SEC 8-K filed 10-09; waiting for a second source",
        ),
        ({"polygon_id": "Eabc"}, "listed by Polygon; waiting for a second source"),
        (
            {
                "sec_accession": "0001",
                "sec_form": "6-K",
                "sec_filed": date(2026, 10, 9),
                "polygon_id": "E",
            },
            "SEC 6-K filed 10-09 and Polygon disagree; waiting for your review",
        ),
    ],
)
def test_a_needs_review_split_is_named_on_the_card(extra, words):
    h = sh.hold_for(_tape(MON), [_row(MON, STATUS_NEEDS_REVIEW, **extra)], asof=MON)
    assert h is not None
    assert h.reason == f"possible unconfirmed 1-for-16 reverse split ({words})"


def test_a_set_aside_split_does_not_cover_and_is_named():
    h = sh.hold_for(_tape(MON), [_row(MON, STATUS_REJECTED)], asof=MON)
    assert h is not None and h.reason == (
        "possible unconfirmed split (the price stepped x16 on 10-12 against the volume; the 1-for-16 reverse "
        "split listed for 10-12 was rejected)"
    )


def test_the_oldest_uncovered_step_in_the_window_is_the_cut():
    """A two-bar island (a step and its reversal): the cut sits before both."""
    bars = [
        _bar(date(2026, 10, 8), 0.5, 10_000),
        _bar(date(2026, 10, 9), 8.0, 600),
        _bar(MON, 0.5, 10_000),
    ]
    h = sh.hold_for(bars, [], asof=MON)
    assert h is not None and h.cut == date(2026, 10, 9)


def test_the_card_line():
    assert sh.note(["Breakout"], "BTOC", "why") == "Breakout on BTOC held: why"
    assert (
        sh.note(["Breakout", "52-week breakout", "Breakout"], "BTOC", "why")
        == "Breakout and 52-week breakout on BTOC held: why"
    )
    assert sh.note(["A", "B", "C"], "X", "w") == "A, B and C on X held: w"


# --- rule 3: the escalation ----------------------------------------------------------------------------


def test_a_released_step_no_source_listed_goes_to_the_operator():
    s = sh.SplitStep(PREV_FRI, MON, 0.5, 8.0, 10_000, 600)
    for asof in (MON, TUE, WED):
        assert not sh.unlisted(s, [], asof=asof)  # still held: on the cards, not yet a question
    assert sh.unlisted(s, [], asof=THU)  # three sessions passed, nothing listed
    assert sh.unlisted(
        s, [], asof=date(2026, 11, 20)
    )  # (the panel's own window bounds how long it shows)


@pytest.mark.parametrize(
    "status", [STATUS_NEEDS_REVIEW, STATUS_ANNOUNCED, STATUS_CONFIRMED, STATUS_REJECTED]
)
def test_any_list_row_near_the_step_means_a_source_named_it(status):
    s = sh.SplitStep(PREV_FRI, MON, 0.5, 8.0, 10_000, 600)
    assert not sh.unlisted(s, [_row(TUE, status)], asof=THU)
    assert sh.unlisted(
        s, [_row(date(2026, 9, 1), status)], asof=THU
    )  # a row far away names another split
