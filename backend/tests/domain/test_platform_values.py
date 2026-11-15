"""V2 W2a: the pure rules of a platform value (``domain/platform_values.py``). No database, no I/O.

- ``pick_current`` — THE one pick every meter uses (approved decision D1 (a)): the latest ``valid_from`` wins; on
  a date tie the newest write wins (``recorded_at``, then ``id``); a value dated after ``not_after`` is never
  picked.
- ``ratifier_kind`` — who set a value: ``auto`` / NULL = machine, ``operator`` = carried (a V1 row), an email =
  steward.
- ``derive_state`` — the four states (the plan's Q1 table), with the newer pending candidate beside a value.
- ``correction_wins`` — would a steward's correction be the value shown (Q4).
- ``machine_action`` — what the machine does with a clean AUTO value (Q6, D3 (a)).
- ``annual_shares_auto_eligible`` — the annual share-count rule (the extractor plan's PR 5): an annual-only FLAG
  share count within the age cap is applied like an AUTO value.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from domain.config import DEFAULT_EXTRACTOR_CONFIG
from domain.extraction import ExtractedFact, Tier
from domain.platform_values import (
    ANNUAL_ONLY_FLAGS,
    FACT_TYPES,
    TABLE_OF,
    ValueState,
    annual_shares_auto_eligible,
    correction_wins,
    derive_state,
    machine_action,
    pick_current,
    ratifier_kind,
)

T0 = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
TODAY = date(2026, 11, 14)
A = "https://www.sec.gov/Archives/a.htm"  # sorts BEFORE b
B = "https://www.sec.gov/Archives/b.htm"


def row(ref: str, d: date, *, at: int = 0, rid: int = 1, by: str | None = "auto", **extra) -> dict:
    return {
        "id": UUID(int=rid),
        "source_ref": ref,
        "valid_from": d,
        "recorded_at": T0 + timedelta(hours=at),
        "ratified_by": by,
        "shares": 1_000 * rid,
        **extra,
    }


def flag(d: date, **extra) -> dict:
    return {
        "id": UUID(int=99),
        "outcome": "flag",
        "reason": None,
        "event_date": d,
        "source_ref": B,
        **extra,
    }


def nothing(reason: str) -> dict:
    return {"id": UUID(int=98), "outcome": "nothing", "reason": reason, "event_date": None}


# --- the vocabulary -------------------------------------------------------------------------------------------


def test_the_two_value_types_and_their_tables():
    assert FACT_TYPES == ("shares_outstanding", "cash_burn")
    assert TABLE_OF == {
        "shares_outstanding": "fact_shares_outstanding",
        "cash_burn": "fact_cash_burn",
    }
    assert [s.value for s in ValueState] == [
        "no_value",
        "from_filing",
        "awaiting_steward",
        "verified",
    ]


# --- the pick (D1 a) ------------------------------------------------------------------------------------------


def test_the_latest_date_wins():
    old, new = row(A, date(2026, 3, 31), at=5, rid=1), row(B, date(2026, 6, 30), at=0, rid=2)
    assert pick_current([new, old]) is new
    assert pick_current([old, new]) is new


def test_on_a_date_tie_the_newer_write_wins_whatever_the_urls_sort():
    """Today's inline ``max(..., key=valid_from)`` keeps the first row on a tie, and rows arrive sorted by
    ``source_ref``, so the alphabetically first URL won. Under D1 (a) the newer write wins."""
    first_url_older = row(A, date(2026, 6, 30), at=0, rid=1)
    second_url_newer = row(B, date(2026, 6, 30), at=1, rid=2)
    assert pick_current([first_url_older, second_url_newer]) is second_url_newer
    assert pick_current([second_url_newer, first_url_older]) is second_url_newer


def test_on_a_date_and_write_tie_the_greater_id_wins():
    low, high = row(B, date(2026, 6, 30), rid=1), row(A, date(2026, 6, 30), rid=2)
    assert pick_current([high, low]) is high
    assert pick_current([low, high]) is high


def test_a_public_clock_row_ties_on_its_honest_write():
    """A history read rewrites ``recorded_at`` to the public instant and keeps the store write as
    ``ingested_at``: the tie goes to the newer WRITE either way."""
    older = row(A, date(2026, 6, 30), at=9, rid=1, ingested_at=T0)
    newer = row(B, date(2026, 6, 30), at=0, rid=2, ingested_at=T0 + timedelta(hours=1))
    assert pick_current([older, newer]) is newer


def test_a_value_dated_after_today_is_never_picked():
    now, future = row(A, date(2026, 6, 30), rid=1), row(B, date(2026, 12, 31), rid=2)
    assert pick_current([now, future], not_after=TODAY) is now
    assert pick_current([future], not_after=TODAY) is None


def test_an_empty_list_picks_nothing():
    assert pick_current([]) is None


def test_a_row_without_write_columns_still_picks_by_date():
    """Some readers keep only the columns they show (the Track Record): the date still decides, and a row that
    carries its write instant wins a tie over one that does not."""
    bare = {"valid_from": date(2026, 6, 30), "shares": 1}
    full = row(A, date(2026, 6, 30), rid=2)
    assert pick_current([bare, full]) is full
    assert pick_current([{"valid_from": date(2026, 3, 31)}, bare]) is bare


# --- who set it ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratified_by", "kind"),
    [
        ("auto", "machine"),
        (None, "machine"),
        ("operator", "carried"),
        ("steward@example.test", "steward"),
        (
            "jane",
            "carried",
        ),  # a V1 free-text row (0206 is NOT VALID): a human's, not a steward's email
    ],
)
def test_ratifier_kind(ratified_by, kind):
    assert ratifier_kind(ratified_by) == kind


# --- the state (Q1's table) ----------------------------------------------------------------------------------

P_AUTO = row(A, date(2026, 6, 30), by="auto")
P_NULL = row(A, date(2026, 6, 30), by=None)
P_CARRIED = row(A, date(2026, 6, 30), by="operator")
P_STEWARD = row(A, date(2026, 6, 30), by="s@x.test")


@pytest.mark.parametrize(
    ("picked", "candidate", "decision", "state", "reason", "ratifier", "awaiting", "pending"),
    [
        # a value on file: its state comes from who set it
        (P_AUTO, None, None, ValueState.FROM_FILING, None, "machine", False, False),
        (P_NULL, None, None, ValueState.FROM_FILING, None, "machine", False, False),
        (P_CARRIED, None, None, ValueState.VERIFIED, None, "carried", False, False),
        (P_STEWARD, None, None, ValueState.VERIFIED, None, "steward", False, False),
        # a NEWER undecided FLAG beside it: the value's state, plus the pending line
        (
            P_AUTO,
            flag(date(2026, 9, 30)),
            None,
            ValueState.FROM_FILING,
            None,
            "machine",
            False,
            True,
        ),
        (
            P_STEWARD,
            flag(date(2026, 9, 30)),
            None,
            ValueState.VERIFIED,
            None,
            "steward",
            False,
            True,
        ),
        # an older or same-dated FLAG, a decided one, or a "nothing": no pending line
        (
            P_AUTO,
            flag(date(2026, 3, 31)),
            None,
            ValueState.FROM_FILING,
            None,
            "machine",
            False,
            False,
        ),
        (
            P_AUTO,
            flag(date(2026, 6, 30)),
            None,
            ValueState.FROM_FILING,
            None,
            "machine",
            False,
            False,
        ),
        (
            P_AUTO,
            flag(date(2026, 9, 30)),
            "dismiss",
            ValueState.FROM_FILING,
            None,
            "machine",
            False,
            False,
        ),
        (
            P_AUTO,
            nothing("cash-generative"),
            None,
            ValueState.FROM_FILING,
            None,
            "machine",
            False,
            False,
        ),
        # no value on file
        (None, None, None, ValueState.NO_VALUE, "not-extracted", None, False, False),
        (
            None,
            nothing("no-annual-filing"),
            None,
            ValueState.NO_VALUE,
            "no-annual-filing",
            None,
            False,
            False,
        ),
        (None, flag(date(2026, 9, 30)), None, ValueState.AWAITING_STEWARD, None, None, True, False),
        (
            None,
            flag(date(2026, 9, 30)),
            "dismiss",
            ValueState.NO_VALUE,
            "dismissed",
            None,
            False,
            False,
        ),
    ],
)
def test_derive_state(picked, candidate, decision, state, reason, ratifier, awaiting, pending):
    got = derive_state(
        picked=picked, candidate=candidate, candidate_decision=decision, pinned=False
    )
    assert (got.state, got.reason, got.ratifier) == (state, reason, ratifier)
    assert got.value is picked
    assert got.awaiting is (candidate if awaiting else None)
    assert got.pending is (candidate if pending else None)


def test_awaiting_never_reaches_a_meter():
    """The one place "awaiting the steward is not used by meters" is enforced: a candidate is never the value."""
    awaiting = derive_state(
        picked=None, candidate=flag(date(2026, 9, 30)), candidate_decision=None, pinned=False
    )
    assert awaiting.state is ValueState.AWAITING_STEWARD
    assert (awaiting.value, awaiting.meter_value) == (None, None)
    shown = derive_state(
        picked=P_AUTO, candidate=flag(date(2026, 9, 30)), candidate_decision=None, pinned=False
    )
    assert shown.meter_value is P_AUTO


def test_the_pin_rides_along():
    assert derive_state(picked=P_AUTO, candidate=None, candidate_decision=None, pinned=True).pinned


# --- the correction rule (Q4, D1 a) --------------------------------------------------------------------------

SHOWN = row(A, date(2026, 6, 30), rid=1)


def corr(ref: str, d: date) -> dict:
    return {"source_ref": ref, "valid_from": d}


@pytest.mark.parametrize(
    ("new", "wins", "reason"),
    [
        (corr(B, date(2026, 7, 31)), True, "wins"),  # a later date, another filing
        (
            corr(B, date(2026, 6, 30)),
            True,
            "wins",
        ),  # the same date, another filing: the newer write wins
        (corr(A, date(2026, 6, 30)), True, "wins"),  # the same filing, same date
        (
            corr(A, date(2026, 3, 31)),
            True,
            "wins",
        ),  # the same filing re-dated earlier: nothing else to beat
        (corr(B, date(2026, 3, 31)), False, "older_than_shown"),  # an earlier date, another filing
        (corr(B, date(2026, 11, 15)), False, "after_today"),  # dated after today: never shown
        (corr(B, TODAY), True, "wins"),  # today is allowed
    ],
)
def test_correction_wins(new, wins, reason):
    got = correction_wins([SHOWN], new, TODAY)
    assert (got.wins, got.reason) == (wins, reason)
    assert got.blocking is (SHOWN if reason == "older_than_shown" else None)


def test_a_correction_beats_a_same_filing_version_but_not_another_filings_later_date():
    later = row(B, date(2026, 9, 30), rid=2)
    got = correction_wins([SHOWN, later], corr(A, date(2026, 7, 31)), TODAY)
    assert (got.wins, got.blocking, got.reason) == (False, later, "older_than_shown")


def test_a_correction_with_nothing_on_file_wins():
    assert correction_wins([], corr(A, date(2026, 6, 30)), TODAY).wins


def test_a_winning_correction_is_what_the_pick_shows():
    """The rule IS the pick: a correction that wins is picked once written (it is the newest write)."""
    others = [SHOWN, row(B, date(2026, 6, 30), at=3, rid=2)]
    new = corr("https://www.sec.gov/c.htm", date(2026, 6, 30))
    assert correction_wins(others, new, TODAY).wins
    written = {**new, "id": UUID(int=3), "recorded_at": T0 + timedelta(days=30)}
    assert pick_current([*others, written]) is written


# --- the machine's AUTO rule (Q6, D3 a) ----------------------------------------------------------------------


def auto(ref: str, d: date, *, stored=(), shown=None, pinned=False):
    return machine_action(
        source_ref=ref,
        event_date=d,
        stored_refs=set(stored),
        shown=shown,
        pinned=pinned,
        today=TODAY,
    )


def test_machine_applies_with_nothing_on_file():
    assert tuple(auto(A, date(2026, 6, 30))) == ("apply", None, ())


def test_machine_skips_a_filing_already_stored_set_aside_or_not():
    assert tuple(auto(A, date(2026, 9, 30), stored={A}, shown=SHOWN)) == (
        "skip",
        "already_on_file",
        (),
    )
    assert auto(A, date(2026, 9, 30), stored={A}).reason == "already_on_file"


@pytest.mark.parametrize("d", [date(2026, 3, 31), date(2026, 6, 30)])
def test_machine_skips_an_older_or_equal_date(d):
    assert tuple(auto(B, d, stored={A}, shown=SHOWN)) == ("skip", "older_than_on_file", ())


def test_machine_applies_a_newer_filing():
    assert auto(B, date(2026, 9, 30), stored={A}, shown=SHOWN).action == "apply"


def test_machine_applies_a_newer_filing_over_a_verified_value():
    """D3 (a): a strictly newer filing replaces the old value, whoever set it."""
    verified = row(A, date(2026, 6, 30), by="s@x.test")
    assert auto(B, date(2026, 9, 30), stored={A}, shown=verified).action == "apply"


def test_machine_writes_a_pinned_candidate_instead():
    assert tuple(auto(B, date(2026, 9, 30), stored={A}, shown=SHOWN, pinned=True)) == (
        "candidate",
        "pinned",
        ("pinned",),
    )


def test_machine_never_writes_a_value_dated_after_today():
    assert tuple(auto(B, date(2026, 12, 31))) == ("skip", "after_today", ())


# --- the annual share-count rule (PR 5; the extractor plan's Q7) ---------------------------------------------
#
# An annual foreign filer's share count (20-F / 40-F cover) is always a FLAG from the extractor (``annual-cover``,
# plus ``stale-cover`` once older than ~6 months). The machine applies it anyway when those are its ONLY flags and
# it is no older than the age cap; anything else still waits for the steward. The truth table below is the rule.

CAP = 400


def annual(
    age_days: int = 300,
    *,
    flags=("annual-cover", "stale-cover"),
    source: str = "annual-cover",
    fact_type: str = "shares_outstanding",
    ads: tuple[int | None, str | None] = (5, "known"),
    value: float | None = 25_932_524_521.0,
) -> ExtractedFact:
    ratio, status = ads
    return ExtractedFact(
        fact_type=fact_type,
        tier=Tier.FLAG,
        source=source,
        source_ref=A,
        event_date=TODAY - timedelta(days=age_days),
        value=value,
        flags=list(flags),
        ads_ratio=ratio,
        ads_ratio_status=status,
    )


def test_the_annual_only_flag_set_is_exactly_the_two_annual_flags():
    assert ANNUAL_ONLY_FLAGS == frozenset({"annual-cover", "stale-cover"})


def test_the_age_cap_is_a_named_dial_defaulting_to_400_days():
    assert DEFAULT_EXTRACTOR_CONFIG.annual_shares_auto_max_age_days == 400


@pytest.mark.parametrize(
    "flags",
    [("annual-cover",), ("annual-cover", "stale-cover"), ("stale-cover", "annual-cover")],
)
def test_an_annual_only_count_within_the_cap_is_eligible(flags):
    assert annual_shares_auto_eligible(annual(flags=flags), TODAY, CAP)


@pytest.mark.parametrize(
    "age, ok", [(0, True), (300, True), (400, True), (401, False), (1415, False)]
)
def test_the_age_is_measured_from_the_counts_own_as_of_date(age, ok):
    assert annual_shares_auto_eligible(annual(age), TODAY, CAP) is ok


def test_a_count_dated_after_today_is_not_eligible():
    """No lookahead and no negative ages: a cover dated after today stays a candidate, as today."""
    assert not annual_shares_auto_eligible(annual(-1), TODAY, CAP)


@pytest.mark.parametrize(
    "extra",
    [
        "multi-value-cover",
        "dual-class",
        "ads-ratio-unread",
        "source-disagreement",
        "implausible-count",
        "cover-after-filing",
        "pinned",
        "a-flag-nobody-has-named-yet",  # an unknown flag queues; it never applies
    ],
)
def test_any_other_flag_beside_the_annual_ones_queues(extra):
    assert not annual_shares_auto_eligible(
        annual(flags=("annual-cover", "stale-cover", extra)), TODAY, CAP
    )
    assert not annual_shares_auto_eligible(annual(flags=("annual-cover", extra)), TODAY, CAP)


@pytest.mark.parametrize(
    "ads, ok",
    [
        ((5, "known"), True),
        ((1, "known"), True),
        ((None, None), True),
        ((None, "unread"), False),
        ((None, "known"), False),
        ((5, "guessed"), False),
    ],
)
def test_the_ads_ratio_must_be_read_or_not_applicable(ads, ok):
    assert annual_shares_auto_eligible(annual(ads=ads), TODAY, CAP) is ok


def test_stale_cover_on_a_quarterly_cover_is_never_eligible():
    """On a 10-Q cover ``stale-cover`` means the count predates the quarter's end: a different condition."""
    assert not annual_shares_auto_eligible(
        annual(source="10-q-cover", flags=("stale-cover",), ads=(None, None)), TODAY, CAP
    )
    assert not annual_shares_auto_eligible(annual(source="10-q-cover"), TODAY, CAP)


def test_annual_cash_and_burn_is_never_eligible():
    """The rule is for share counts only: annual cash and burn stays flagged (operator decision)."""
    cash = annual(fact_type="cash_burn", source="annual-statements", value=None, ads=(None, None))
    assert not annual_shares_auto_eligible(cash, TODAY, CAP)
    as_cover = annual(fact_type="cash_burn", value=None, ads=(None, None))
    assert not annual_shares_auto_eligible(as_cover, TODAY, CAP)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf")])
def test_a_count_with_no_finite_number_is_not_eligible(value):
    assert not annual_shares_auto_eligible(annual(value=value), TODAY, CAP)


def test_the_cap_is_the_callers_dial():
    assert annual_shares_auto_eligible(annual(450), TODAY, 500)
    assert not annual_shares_auto_eligible(annual(300), TODAY, 200)
