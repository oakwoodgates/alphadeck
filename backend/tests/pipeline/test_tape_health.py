"""G5a/F1 — the FEED recency RULE, from direct inputs (pure; no DB, no clock).

The gap being tested: a vendor series that simply STOPS (a ticker rename the vendor priced under a new
symbol, a delisting) appends zero bars with NO error, which is byte-identical to a market holiday — so every
price-driven detector for that name goes dark and nothing says so. These tests pin the predicate's
boundaries and the dedup that keeps a multi-link name from reading as several dead tapes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from pipeline.ingest_thesis import NameResult
from pipeline.tape_health import (
    StaleTape,
    is_tape_stale,
    stale_fund_shares,
    stale_label,
    stale_tapes,
)

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


# --- F1: the SAME rule over the fund-shares feed, with its own threshold and a tracked gate ----------


def test_the_fund_threshold_boundary_is_the_same_INCLUSIVE_rule():
    """One rule, two thresholds. At the fund default of 7: an edge 6 days back is fresh, 7 is stale. The
    number differs from the price tape's 5 because the feeds differ — MEASURED on dev, a healthy sleeve's
    sample sits 0-2 days behind the pass that took it (the primary source states the pull date exactly;
    the fallback stated a two-day-old date), so 7 clears that lag plus a long weekend plus two failed
    nights, while a dead sampler still surfaces inside a week."""
    assert is_tape_stale(_ASOF - timedelta(days=6), asof=_ASOF, stale_days=7) is False
    assert is_tape_stale(_ASOF - timedelta(days=7), asof=_ASOF, stale_days=7) is True


def test_stale_fund_shares_judges_ONLY_a_tracked_member():
    """THE TRAP this gate exists for: an equity member has no samples, so its fund edge is None — and None
    means STALE. Ungated, every equity in every basket would be reported as a dead fund tape every night.
    A TRACKED sleeve with a None edge is the opposite case and genuinely news: its sampler has never
    produced anything."""
    equity = _name(ticker="EQUITY", fund_shares_edge=None)  # tracked defaults to False
    sleeve_never = _name(ticker="SLEEVE", fund_shares_tracked=True, fund_shares_edge=None)
    out = stale_fund_shares([equity, sleeve_never], asof=_ASOF, stale_days=7)
    assert [s.ticker for s in out] == ["SLEEVE"]


def test_stale_fund_shares_tags_its_KIND_and_reads_the_fund_edge_not_the_tape_edge():
    """The two feeds are judged from their OWN fields: a sleeve with a healthy price tape and dead sampling
    is stale on fund shares only, and the row says which feed so the page can give the right repair.
    """
    sleeve = _name(
        ticker="SLEEVE",
        tape_edge=_ASOF,  # price fine
        fund_shares_tracked=True,
        fund_shares_edge=date(2026, 4, 1),  # sampling stopped
    )
    out = stale_fund_shares([sleeve], asof=_ASOF, stale_days=7)
    assert len(out) == 1
    assert out[0].kind == "fund_shares" and out[0].edge == date(2026, 4, 1)
    assert out[0].feed.noun == "fund-shares tape"
    # ...and its price tape is NOT reported by the price collector
    assert stale_tapes([sleeve], asof=_ASOF, stale_days=5) == ()


def test_a_name_stale_on_BOTH_feeds_is_reported_ONCE_PER_FEED():
    """The dedup is within a kind, deliberately: one security with two dead feeds is two rows, because the
    two are different problems with different repairs. Keyed on the id alone the second would vanish.
    """
    sid = uuid.uuid4()
    dead_both = _name(
        ticker="DEAD",
        security_id=sid,
        tape_edge=date(2026, 4, 1),
        fund_shares_tracked=True,
        fund_shares_edge=date(2026, 4, 1),
    )
    rows = stale_tapes([dead_both], asof=_ASOF, stale_days=5) + stale_fund_shares(
        [dead_both], asof=_ASOF, stale_days=7
    )
    assert [(s.security_id, s.kind) for s in rows] == [(sid, "price"), (sid, "fund_shares")]


def test_stale_fund_shares_DEDUPS_a_sleeve_placed_in_several_links():
    """The same multi-link dedup as the price collector — the sleeve is N basket_member rows, one dead
    sampler."""
    sid = uuid.uuid4()
    rows = [
        _name(ticker="SLEEVE", security_id=sid, fund_shares_tracked=True, fund_shares_edge=None)
        for _ in range(3)
    ]
    out = stale_fund_shares(rows, asof=_ASOF, stale_days=7)
    assert len(out) == 1 and out[0].security_id == sid


def test_stale_fund_shares_reports_a_sleeve_whose_LEG_ERRORED():
    """An unsamplable fund RAISES inside the leg, so its error is on the name's result — and the edge read
    happens outside that try, so the dead series is reported here too. A failing sampler and a stopped
    series are different facts; neither may mask the other."""
    failing = _name(
        ticker="SLEEVE",
        fund_shares_tracked=True,
        fund_shares_edge=None,
        error="fund_shares: no samplable source",
    )
    out = stale_fund_shares([failing], asof=_ASOF, stale_days=7)
    assert len(out) == 1 and out[0].kind == "fund_shares"


def test_a_stale_tape_defaults_to_the_PRICE_kind():
    """The default is what makes every row written before fund shares existed read correctly — they are
    price rows."""
    assert StaleTape(ticker="AAA", security_id=uuid.uuid4(), edge=None).kind == "price"


# --- G5b: the DELISTING classification — a stale tape WITH a delisting form reads CLOSED --------------


def test_a_stale_tape_WITH_a_delisting_date_reads_CLOSED():
    """A stopped tape whose name has a SEC delisting form (carried on NameResult.delisted_at) is a name that
    CLOSED — delisted / acquired / deregistered — not a feed gap to repair. The row carries closed_at (the
    form's filing date) and reads `closed`, so the panel can render it quietly and the page can skip it.
    """
    dead = _name(ticker="GONE", tape_edge=date(2026, 4, 1), delisted_at=date(2026, 4, 3))
    out = stale_tapes([dead], asof=_ASOF, stale_days=5)
    assert len(out) == 1
    assert out[0].closed is True and out[0].closed_at == date(2026, 4, 3)


def test_a_stale_tape_WITHOUT_a_delisting_date_stays_a_REPAIR_row():
    """The healthy common case: a stopped tape with no delisting form is a feed gap to repair — closed_at
    None, `closed` False — so it keeps the loud 'stale — repair' treatment."""
    dead = _name(ticker="DEAD", tape_edge=date(2026, 4, 1))  # delisted_at defaults None
    out = stale_tapes([dead], asof=_ASOF, stale_days=5)
    assert len(out) == 1 and out[0].closed is False and out[0].closed_at is None


def test_a_CLOSED_name_is_never_dropped_from_the_inventory():
    """#9 — recall is sacred: the marker only RECLASSIFIES, it never removes the name from the monitor. A
    stale+closed name is still emitted, with its real edge, exactly like any other stale row."""
    dead = _name(ticker="GONE", tape_edge=date(2026, 4, 1), delisted_at=date(2026, 4, 3))
    out = stale_tapes([dead], asof=_ASOF, stale_days=5)
    assert [s.ticker for s in out] == ["GONE"] and out[0].edge == date(2026, 4, 1)


def test_a_FRESH_tape_with_a_delisting_date_is_NOT_emitted():
    """A name with a delisting form but a still-fresh tape is not reported at all — there is nothing to mark
    until the tape actually stops, so an active name is never nagged."""
    fresh = _name(ticker="STILLON", tape_edge=_ASOF, delisted_at=date(2026, 4, 3))
    assert stale_tapes([fresh], asof=_ASOF, stale_days=5) == ()


def test_the_FUND_SHARES_feed_is_never_classified_closed():
    """Delisting is a LISTING (price) event: a stopped fund-shares sample is never marked closed by this
    mechanism even if the sleeve's issuer filed a delisting form, so its own repair advice is never
    suppressed. closed_at stays None on every fund-shares row."""
    sleeve = _name(
        ticker="ETF",
        fund_shares_tracked=True,
        fund_shares_edge=date(2026, 4, 1),
        delisted_at=date(2026, 4, 3),
    )
    out = stale_fund_shares([sleeve], asof=_ASOF, stale_days=7)
    assert len(out) == 1 and out[0].closed is False and out[0].closed_at is None


def test_a_StaleTape_defaults_to_NOT_closed():
    """A row built without closed_at (every pre-G5b row, and every fund-shares row) is a plain
    stale-repair tape."""
    assert StaleTape(ticker="AAA", security_id=uuid.uuid4(), edge=None).closed is False
