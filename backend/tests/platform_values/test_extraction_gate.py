"""V2 W3a: THE GATE (``platform_values.extraction``), pure: the inputs key and the "due" rule.

A company's platform values are re-extracted only when something the extractor reads has changed. The inputs key
names exactly those filings: the company's CIK plus the newest 10-Q, 10-K, 20-F and 40-F (the forms the extractor
chooses from, exact match). A company is DUE when it was never attempted, when a periodic filing became public
after the filings its last attempt read (and has been public for the hold, so companyfacts has caught up), or when
its last attempt failed and fewer than ``retry_max`` attempts in a row have failed on the same key. A company with
nothing to find is not due again until a periodic filing appears. "Public" is the SEC acceptance (else the end of
the filing date in ET): a filing not yet public at ``now`` never makes a company due (no lookahead).

No database, no network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from platform_values.extraction import (
    PERIODIC_FORMS,
    AttemptRow,
    Due,
    PeriodicFiling,
    due,
    inputs_key,
    parse_inputs_key,
)

NOW = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)  # a night: Nov 12, 22:00 ET
CIK = "0001234567"


def _f(form: str, acc: str, filed: date, accepted: datetime | None = None) -> PeriodicFiling:
    return PeriodicFiling(form=form, accession=acc, filed=filed, accepted=accepted)


Q2 = _f(
    "10-Q", "0001-26-000002", date(2026, 8, 10), datetime(2026, 8, 10, 20, 5, tzinfo=timezone.utc)
)
K1 = _f(
    "10-K", "0001-26-000001", date(2026, 3, 1), datetime(2026, 3, 1, 21, 0, tzinfo=timezone.utc)
)
BASE = [Q2, K1]


def _attempt(status: str, key: str, hours_ago: float = 48.0, *, at: datetime = NOW) -> AttemptRow:
    return AttemptRow(status=status, inputs_key=key, recorded_at=at - timedelta(hours=hours_ago))


def _due(attempts, filings, *, now: datetime = NOW, cik: str | None = CIK) -> Due:
    return due(attempts, filings, cik=cik, now=now, hold_h=24, retry_max=3)


# --- the inputs key ---------------------------------------------------------------------------------------------


def test_the_forms_are_the_four_the_extractor_reads():
    assert PERIODIC_FORMS == ("10-Q", "10-K", "20-F", "40-F")


def test_the_key_names_the_cik_and_the_newest_filing_of_each_form():
    older_q = _f("10-Q", "0001-26-000000", date(2026, 5, 10))
    key = inputs_key(CIK, [older_q, Q2, K1])
    assert key == "1234567|10-Q:0001-26-000002|10-K:0001-26-000001|20-F:|40-F:"
    assert parse_inputs_key(key) == (
        "1234567",
        {"10-Q": "0001-26-000002", "10-K": "0001-26-000001", "20-F": "", "40-F": ""},
    )
    # the CIK is normalized: zero padding never makes two keys differ
    assert inputs_key("1234567", [Q2, K1]) == inputs_key(CIK, [K1, Q2])


@pytest.mark.parametrize("form", ["10-Q/A", "10-K/A", "8-K", "6-K", "20-F/A", "4"])
def test_the_key_ignores_every_other_form(form):
    noise = _f(form, "0009-26-999999", date(2026, 11, 1))
    assert inputs_key(CIK, [*BASE, noise]) == inputs_key(CIK, BASE)


@pytest.mark.parametrize("form", PERIODIC_FORMS)
def test_the_key_changes_on_a_new_periodic_filing(form):
    new = _f(form, "0009-26-000009", date(2026, 11, 1))
    assert inputs_key(CIK, [*BASE, new]) != inputs_key(CIK, BASE)


def test_the_key_changes_with_the_cik_and_is_empty_without_filings():
    assert inputs_key("7654321", BASE) != inputs_key(CIK, BASE)
    assert inputs_key(None, []) == "|10-Q:|10-K:|20-F:|40-F:"


# --- due ---------------------------------------------------------------------------------------------------------


def test_never_attempted_is_due_never():
    assert _due([], BASE) == Due("never", "never attempted")


def test_the_same_filings_are_not_due():
    got = _due([_attempt("done", inputs_key(CIK, BASE))], BASE)
    assert got.reason is None and got.note == "up to date"


def test_a_new_filing_accepted_three_hours_ago_is_held():
    new_q = _f("10-Q", "0001-26-000003", date(2026, 11, 12), NOW - timedelta(hours=3))
    got = _due([_attempt("done", inputs_key(CIK, BASE))], [*BASE, new_q])
    assert got == Due(None, "held")


def test_a_new_filing_accepted_thirty_hours_ago_is_due_new_filing():
    new_q = _f("10-Q", "0001-26-000003", date(2026, 11, 11), NOW - timedelta(hours=30))
    got = _due([_attempt("done", inputs_key(CIK, BASE), hours_ago=72)], [*BASE, new_q])
    assert got.reason == "new_filing"


def test_a_filing_with_no_trusted_acceptance_counts_from_the_end_of_its_filing_date():
    """No acceptance: public from 23:59:59 ET of the filing date (late, never early)."""
    later = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    done = [_attempt("done", inputs_key(CIK, BASE), at=later)]
    filed_yday = _f("10-Q", "0001-26-000003", date(2026, 11, 12))  # public 2026-11-13 04:59:59 UTC
    assert _due(done, [*BASE, filed_yday], now=later) == Due(None, "held")  # public 7 hours
    # at NOW (03:00 UTC) the end of its filing date has not come: not public yet, so not even held
    assert _due(done, [*BASE, filed_yday], now=NOW) == Due(None, "up to date")
    filed_before = _f("10-Q", "0001-26-000003", date(2026, 11, 10))
    assert _due(done, [*BASE, filed_before], now=later).reason == "new_filing"


def test_a_filing_not_yet_public_at_now_never_makes_a_company_due():
    """No lookahead: a row accepted after ``now`` (a history view, a test clock) is not there yet."""
    future = _f("10-K", "0001-27-000001", date(2026, 11, 20), NOW + timedelta(days=7))
    got = _due([_attempt("done", inputs_key(CIK, BASE))], [*BASE, future], now=NOW)
    assert got.note == "up to date"


def test_an_attempt_that_read_a_newer_filing_than_the_index_knows_is_not_due():
    """The extraction reads the live index; the stored filing index may lag a night. An attempt keyed on a 10-Q the
    stored index does not have yet is newer than the index, never older."""
    fresher_q = "0001-26-000004"
    key = inputs_key(CIK, [K1, _f("10-Q", fresher_q, date(2026, 11, 12))])
    got = _due([_attempt("done", key, hours_ago=1)], BASE)
    assert got.note == "up to date"


def test_a_cik_change_is_due_new_filing():
    got = _due([_attempt("done", inputs_key("999", BASE))], BASE)
    assert got.reason == "new_filing"


def test_an_error_is_retried_until_three_in_a_row_then_waits():
    key = inputs_key(CIK, BASE)
    one = [_attempt("error", key, 24)]
    two = [_attempt("error", key, 24), _attempt("error", key, 48)]
    three = [_attempt("error", key, 24), _attempt("error", key, 48), _attempt("error", key, 72)]
    assert _due(one, BASE).reason == "retry"
    assert _due(two, BASE).reason == "retry"
    assert _due(three, BASE) == Due(None, "waiting")
    # a done attempt between them breaks the streak
    broken = [_attempt("error", key, 24), _attempt("done", key, 48), _attempt("error", key, 72)]
    assert _due(broken, BASE).reason == "retry"


def test_three_errors_then_a_new_filing_is_due_again():
    key = inputs_key(CIK, BASE)
    three = [_attempt("error", key, 30), _attempt("error", key, 54), _attempt("error", key, 78)]
    new_q = _f("10-Q", "0001-26-000003", date(2026, 11, 10))
    assert _due(three, [*BASE, new_q]).reason == "new_filing"


def test_busy_is_retried_and_never_counts_as_a_failure():
    key = inputs_key(CIK, BASE)
    assert _due([_attempt("busy", key)], BASE) == Due("retry", "busy")
    two_errors_and_busy = [
        _attempt("error", key, 24),
        _attempt("busy", key, 36),
        _attempt("error", key, 48),
    ]
    assert _due(two_errors_and_busy, BASE).reason == "retry"


def test_nothing_to_find_is_not_due_again_until_a_periodic_filing_appears():
    """A company with no periodic filing has an empty key: after one attempt it costs nothing per night."""
    empty = inputs_key(CIK, [])
    assert _due([_attempt("done", empty)], []).note == "up to date"
    assert _due([_attempt("skipped", inputs_key(None, []))], [], cik=None).note == "up to date"
    first_20f = _f("20-F", "0001-26-000020", date(2026, 10, 1))
    assert _due([_attempt("done", empty)], [first_20f]).reason == "new_filing"
