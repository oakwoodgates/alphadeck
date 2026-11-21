"""V2 W5a: the words a user reads about a platform value (``platform_values/words.py``), pure.

One module holds them, so the user read (W5a) and the server-written lines (W5d) say the same thing. It sits under
``platform_values/``, never ``domain/``, so the editions fingerprint does not move when a word changes. The words
never carry a steward's email.
"""

from __future__ import annotations

from datetime import date

import pytest

from domain.platform_values import ValueState
from platform_values import words

STEWARD = "steward@example.test"


# --- every (state, reason) -> its label ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "reason", "set_by", "label"),
    [
        (ValueState.FROM_FILING, None, "machine", "From the filing"),
        (ValueState.VERIFIED, None, "steward", "Verified by the steward"),
        (ValueState.VERIFIED, None, "carried", "Verified (carried over from V1)"),
        (ValueState.AWAITING_STEWARD, None, None, "Awaiting the steward"),
        (
            ValueState.NO_VALUE,
            "not-extracted",
            None,
            "No value yet: the platform has not read this company's filings yet",
        ),
        (
            ValueState.NO_VALUE,
            "no-annual-filing",
            None,
            "No value yet: no 10-K, 10-Q, 20-F or 40-F on EDGAR",
        ),
        (
            ValueState.NO_VALUE,
            "cover-not-located",
            None,
            "No value yet: the cover count could not be read",
        ),
        (
            ValueState.NO_VALUE,
            "no-candidate",
            None,
            "No value yet: the latest filing gave no value of this kind",
        ),
        (
            ValueState.NO_VALUE,
            "dismissed",
            None,
            "No value yet: the steward found no usable value in the latest filing",
        ),
        (
            ValueState.NO_VALUE,
            "cash-generative",
            None,
            "Runway does not apply: the company generated cash",
        ),
        (
            ValueState.NO_VALUE,
            "financials-in-exhibit",
            None,
            "No value yet: the statements were in an exhibit the platform did not read",
        ),
        (
            ValueState.NO_VALUE,
            "exhibit-ambiguous",
            None,
            "No value yet: the statements were in more than one exhibit",
        ),
        (
            ValueState.NO_VALUE,
            "statements-not-located",
            None,
            "No value yet: the statements were not found in the filing",
        ),
        (ValueState.NO_VALUE, "not-shown", None, "No value yet"),
        (ValueState.NO_VALUE, None, None, "No value yet"),
        (ValueState.NO_VALUE, "a-reason-not-named-yet", None, "No value yet"),
    ],
)
def test_every_state_and_reason_has_its_label(state, reason, set_by, label):
    assert words.state_label(state, reason, set_by) == label
    assert words.state_label(state.value, reason, set_by) == label  # the stored text reads the same


def test_every_named_reason_is_in_the_table():
    """The reasons the extractor and the state produce today each have their own words (an unknown one reads the
    plain "No value yet", never its slug)."""
    assert set(words.NO_VALUE_WORDS) == {
        "not-extracted",
        "no-annual-filing",
        "cover-not-located",
        "no-candidate",
        "dismissed",
        "cash-generative",
        "financials-in-exhibit",
        "exhibit-ambiguous",
        "statements-not-located",
        "not-shown",
    }


# --- who set it: never an email --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratified_by", "kind", "said"),
    [
        ("auto", "machine", "from the filing"),
        (None, "machine", "from the filing"),
        ("operator", "carried", "verified (carried over from V1)"),
        (STEWARD, "steward", "verified by the steward"),
    ],
)
def test_set_by_is_a_kind_and_words_never_the_email(ratified_by, kind, said):
    assert words.set_by(ratified_by) == kind
    assert words.set_by_words(ratified_by) == said
    assert "@" not in words.set_by_words(ratified_by)


# --- the annual line, the pending line, the awaiting line ----------------------------------------------------


def test_the_annual_line_only_for_an_annual_cover_count():
    assert (
        words.annual_line("annual-cover", date(2026, 3, 31))
        == "annual figure from the 20-F or 40-F cover, as of Mar 31, 2026"
    )
    for source in ("10-q-cover", "cover", "ratified", "annual-statements", None):
        assert words.annual_line(source, date(2026, 3, 31)) is None


def test_the_pending_line_names_the_newer_filing_by_date_and_no_figure():
    assert (
        words.pending_line(date(2026, 12, 31))
        == "A newer filing (dated Dec 31, 2026) is awaiting the steward"
    )
    assert words.pending_line(None) == "A newer filing is awaiting the steward"


def test_the_awaiting_line_says_which_meter_waits():
    assert words.awaiting_line("shares_outstanding", date(2026, 3, 31)) == (
        "The steward has not checked the share count read from the filing dated Mar 31, 2026. "
        "Market cap is not shown until a count is verified."
    )
    assert words.awaiting_line("cash_burn", date(2026, 7, 26)) == (
        "The steward has not checked the cash and burn read from the filing dated Jul 26, 2026. "
        "Runway is not shown until the steward checks it."
    )
    assert words.awaiting_line("cash_burn", None) == (
        "The steward has not checked the cash and burn read from the filing. "
        "Runway is not shown until the steward checks it."
    )


def test_a_day_reads_as_a_month_name_without_a_leading_zero():
    assert words.day_words(date(2026, 9, 5)) == "Sep 5, 2026"


def test_the_words_live_outside_domain():
    """The editions fingerprint hashes ``domain/``: the words must not live there (the W5 plan, Q2)."""
    assert words.__name__ == "platform_values.words"


# --- the server-written lines (W5d) -------------------------------------------------------------------------------


def test_the_set_by_line_names_a_kind_and_never_an_email():
    assert (
        words.set_by_line("steward", date(2026, 11, 18))
        == "Verified by the steward on Nov 18, 2026"
    )
    assert words.set_by_line("steward") == "Verified by the steward"
    assert words.set_by_line("carried", date(2026, 11, 18)) == "Verified (carried over from V1)"
    assert words.set_by_line("machine") == "From the filing (applied automatically)"


def test_the_market_cap_notes_never_tell_a_user_to_ratify_a_shared_value():
    notes = (words.CAP_NO_PRICE, words.CAP_NO_SHARES, words.CAP_ADS_UNREAD)
    assert not any("ratif" in n.lower() for n in notes)
    assert words.CAP_NO_SHARES == "Price on file; no shared share count yet (see Shared values)."
    assert words.CAP_ADS_UNREAD.endswith("The steward sets the ratio.")
