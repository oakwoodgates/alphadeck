"""G5a — the price-tape recency RULE, from direct inputs (pure; no DB, no clock).

The gap being tested: a vendor series that simply STOPS (a ticker rename the vendor priced under a new
symbol, a delisting) appends zero bars with NO error, which is byte-identical to a market holiday — so every
price-driven detector for that name goes dark and nothing says so. These tests pin the predicate's
boundaries and the dedup that keeps a multi-link name from reading as several dead tapes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from pipeline.ingest_thesis import NameResult
from pipeline.tape_health import StaleTape, is_tape_stale, stale_label, stale_tapes

_ASOF = date(2026, 6, 10)  # a Wednesday


def _name(**kw) -> NameResult:
    base = dict(ticker="AAA", security_id=uuid.uuid4(), form4_appended=0, price_bars_appended=0)
    return NameResult(**{**base, **kw})


# --- the predicate's boundaries ----------------------------------------------------------------------


def test_a_tape_ending_today_or_yesterday_is_FRESH():
    assert is_tape_stale(_ASOF, asof=_ASOF, stale_days=5) is False
    assert is_tape_stale(date(2026, 6, 9), asof=_ASOF, stale_days=5) is False


def test_the_threshold_is_INCLUSIVE_at_stale_days():
    """The exact boundary, because an off-by-one here either cries wolf over a long weekend or lets a dead
    tape sit a week longer: at stale_days=5, an edge 4 days back is fresh and 5 days back is stale.
    """
    assert is_tape_stale(_ASOF - timedelta(days=4), asof=_ASOF, stale_days=5) is False
    assert is_tape_stale(_ASOF - timedelta(days=5), asof=_ASOF, stale_days=5) is True


def test_a_WEEKEND_and_a_single_holiday_stay_fresh_at_the_default_threshold():
    """The day math the default rests on, counted out rather than asserted in prose (an earlier docstring got
    this off by one). From a Friday close, with stale_days=5: Monday's pass is 3 days and Tuesday's is 4 —
    both FRESH — and Wednesday's is 5, STALE. So an ordinary weekend, and a weekend plus a Monday holiday
    (last bar Friday, next bar Tuesday, worst pass 3 days) or a Friday holiday (last bar Thursday, Monday
    pass = 4 days), all sit inside the window."""
    friday = date(2026, 6, 5)
    thursday = date(2026, 6, 4)
    assert (friday.weekday(), thursday.weekday()) == (4, 3)  # pin the fixture's own calendar claim
    assert is_tape_stale(friday, asof=date(2026, 6, 8), stale_days=5) is False  # Monday: 3 days
    assert is_tape_stale(friday, asof=date(2026, 6, 9), stale_days=5) is False  # Tuesday: 4 days
    assert is_tape_stale(friday, asof=date(2026, 6, 10), stale_days=5) is True  # Wednesday: 5 days
    # a FRIDAY holiday: the last bar is Thursday and the next session is Monday — still fresh
    assert is_tape_stale(thursday, asof=date(2026, 6, 8), stale_days=5) is False  # Monday: 4 days


def test_a_TWO_SESSION_closure_beside_a_weekend_trips_for_ONE_night_accepted():
    """THE ACCEPTED EDGE, pinned so nobody is surprised by it and nobody silently "fixes" it: a rare
    two-session market closure adjacent to a weekend (a Thursday+Friday shutdown) leaves a WEDNESDAY edge and
    the next pass is MONDAY — 5 calendar days, so it reads stale for that one night and clears on Tuesday's
    pass once a bar lands. Without a trading calendar this is unavoidable at any threshold tight enough to
    catch a dead tape inside a week; the lever is ALPHADECK_TAPE_STALE_DAYS."""
    wednesday, monday, tuesday = date(2026, 6, 3), date(2026, 6, 8), date(2026, 6, 9)
    assert (wednesday.weekday(), monday.weekday()) == (2, 0)
    assert (
        is_tape_stale(wednesday, asof=monday, stale_days=5) is True
    )  # the one-night false positive
    assert is_tape_stale(monday, asof=tuesday, stale_days=5) is False  # ...cleared by the next bar
    assert (
        is_tape_stale(wednesday, asof=monday, stale_days=6) is False
    )  # the operator's lever works


def test_a_tape_with_NO_BARS_AT_ALL_is_stale():
    """``edge is None`` = the security has never had a bar stored. That is the most complete version of the
    failure (a name that has NEVER priced), so it must never read as fresh by omission."""
    assert is_tape_stale(None, asof=_ASOF, stale_days=5) is True


def test_stale_days_ZERO_disables_the_monitor_entirely():
    """The documented off-switch: nothing is stale at 0, not even a never-priced name."""
    assert is_tape_stale(None, asof=_ASOF, stale_days=0) is False
    assert is_tape_stale(date(2020, 1, 1), asof=_ASOF, stale_days=0) is False


# --- the per-thesis collector ------------------------------------------------------------------------


def test_stale_tapes_selects_only_the_stale_and_keeps_first_seen_order():
    fresh = _name(ticker="FRESH", tape_edge=_ASOF)
    dead = _name(ticker="DEAD", tape_edge=date(2026, 4, 1))
    never = _name(ticker="NEVER", tape_edge=None)
    out = stale_tapes([fresh, dead, never], asof=_ASOF, stale_days=5)
    assert [s.ticker for s in out] == ["DEAD", "NEVER"]
    assert out[0].edge == date(2026, 4, 1) and out[1].edge is None


def test_stale_tapes_DEDUPS_a_name_placed_in_several_links_by_security_id():
    """THE load-bearing dedup: a name the draft placed in N value-chain links is N basket_member rows with
    the SAME security_id, so ingest_thesis returns N NameResults for it. Without the dedup one dead tape
    would page N times and the panel would list it N times."""
    sid = uuid.uuid4()
    rows = [_name(ticker="DEAD", security_id=sid, tape_edge=date(2026, 4, 1)) for _ in range(3)]
    out = stale_tapes(rows, asof=_ASOF, stale_days=5)
    assert len(out) == 1 and out[0].security_id == sid


def test_stale_tapes_keeps_a_TICKER_LESS_name_and_labels_it_by_id():
    """#9 — recall is sacred applies to a monitor's output: a member with no ticker and a dead tape is
    exactly the case an operator most needs to see, so it is reported and rendered by id, never dropped.
    """
    sid = uuid.uuid4()
    out = stale_tapes(
        [_name(ticker=None, security_id=sid, tape_edge=None)], asof=_ASOF, stale_days=5
    )
    assert len(out) == 1 and out[0].ticker is None
    assert out[0].label == str(sid)  # the id stands in for the missing ticker


def test_stale_tapes_reports_a_name_whose_price_leg_also_ERRORED():
    """A failing leg and a dead tape are different facts and must not mask each other: the edge read is
    independent of the leg's outcome, so this name shows up BOTH as an errored name (on its own result) and
    here as a stale tape."""
    dead_and_failing = _name(ticker="DEAD", tape_edge=date(2026, 4, 1), error="price: boom")
    out = stale_tapes([dead_and_failing], asof=_ASOF, stale_days=5)
    assert len(out) == 1 and out[0].ticker == "DEAD"


def test_stale_tapes_is_EMPTY_when_the_monitor_is_disabled():
    out = stale_tapes([_name(tape_edge=None)], asof=_ASOF, stale_days=0)
    assert out == ()


def test_the_label_rule_is_shared_and_prefers_the_ticker():
    sid = uuid.uuid4()
    assert stale_label("AAA", sid) == "AAA"
    assert stale_label(None, sid) == str(sid)
    assert StaleTape(ticker="AAA", security_id=sid, edge=None).label == "AAA"
