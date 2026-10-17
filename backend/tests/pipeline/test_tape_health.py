"""G5a/F1 — the FEED recency RULE, from direct inputs (pure; no DB, no clock).

The gap being tested: a vendor series that simply STOPS (a ticker rename the vendor priced under a new
symbol, a delisting) appends zero bars with NO error, which is byte-identical to a market holiday — so every
price-driven detector for that name goes dark and nothing says so. These tests pin the predicate's
boundaries and the dedup that keeps a multi-link name from reading as several dead tapes.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from functools import partial
from pathlib import Path

from domain.settings import Settings
from ingest.edgar.client import EdgarClient
from ingest.edgar.form25 import delisted_class_is_other
from ingest.edgar.submissions import DelistingFiling, delisting_filings, edgar_tickers
from pipeline.ingest_thesis import NameResult
from pipeline.tape_health import (
    DEFAULT_CLOSED_AFTER_DAYS,
    DEFAULT_CLOSED_BEFORE_DAYS,
    StaleTape,
    classify_stop,
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


# --- G5b/T1: the stop classification — CLOSED only on a delisting form NEAR the last bar, and no rename -
#
# The fixtures below are REAL shapes, MEASURED from prod's cached EDGAR submissions on 2026-10-16: each
# name's actual `tickers` list and its actual delisting-form rows (form, filing date, accession), with the
# tape edge its stored price tape actually stops at. They go through the REAL parsers
# (`delisting_filings`, `edgar_tickers`) and the REAL collector (`stale_tapes`), so nothing is shaped to
# the classifier. Where a case needs a stop that has not happened (a still-trading multi-symbol name), the
# test says so: it states the rule's contract on that name's real EDGAR record with a hypothetical edge.

_TONIGHT = date(
    2026, 10, 15
)  # the asof of the 2026-10-16 02:30 UTC pass the measurements come from


def _edgar(tickers: list[str], rows: list[tuple[str, str, str]]) -> dict:
    """A submissions JSON in EDGAR's real shape: ``tickers`` plus the parallel, reverse-chrono
    ``filings.recent`` arrays, one row per (form, filingDate, accession). A 10-Q filler row is appended so
    every fixture also proves the walk FILTERS to the delisting forms."""
    rows = sorted(rows, key=lambda r: r[1], reverse=True) + [("10-Q", "2024-11-12", "FILLER-10Q")]
    return {
        "tickers": tickers,
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "accessionNumber": [r[2] for r in rows],
                "primaryDocument": ["primary_doc.xml"] * len(rows),
            }
        },
    }


def _member(subs: dict, *, ticker: str, edge: date | None, price_symbol: str | None = None):
    """A NameResult carrying exactly what ``ingest_thesis`` carries for a name: its edge, its master
    ticker / price_symbol, and the two EDGAR facts parsed from its submissions."""
    return _name(
        ticker=ticker,
        tape_edge=edge,
        price_symbol=price_symbol,
        delisting_filings=delisting_filings(subs),
        edgar_tickers=edgar_tickers(subs),
    )


def _classify(subs: dict, *, ticker: str, edge: date | None, price_symbol: str | None = None):
    out = stale_tapes(
        [_member(subs, ticker=ticker, edge=edge, price_symbol=price_symbol)],
        asof=_TONIGHT,
        stale_days=5,
    )
    assert (
        len(out) == 1
    )  # every case here IS a stopped tape — the classification never drops it (#9)
    return out[0]


# VRME (VerifyMe, Nasdaq): traded 82K-1.36M shares/day through 2026-10-06, then stopped. Its one delisting
# form is Nasdaq's 25-NSE for the WARRANTS ("Warrant expiring 6/23/2025"); the common stock became
# OpenWorld (OPNW) in a 2026-09-30 reverse merger + 1-for-10 reverse split.
VRME = _edgar(["OPNW"], [("25-NSE", "2025-06-23", "0001354457-25-000562")])
# AVNS: a genuine close — the 25-NSE 3 days after the last bar, the 15-12G 13 days after, EDGAR lists none.
AVNS = _edgar(
    [],
    [
        ("25-NSE", "2026-07-27", "0000876661-26-000646"),
        ("15-12G", "2026-08-06", "0001606498-26-000120"),
    ],
)
# QRVO: a genuine close whose EDGAR record still lists QRVO (the 15-12G landed 2026-10-15).
QRVO = _edgar(
    ["QRVO"],
    [
        ("25-NSE", "2026-10-05", "0001354457-26-000942"),
        ("15-12G", "2026-10-15", "0001104659-26-116241"),
    ],
)
# XOMA: three forms around a 2026-07-21 last bar — two 25-NSE before it, the 15-12G after.
XOMA = _edgar(
    [],
    [
        ("25-NSE", "2026-07-14", "0001354457-26-000691"),
        ("25-NSE", "2026-07-16", "0001354457-26-000697"),
        ("15-12G", "2026-07-27", "0001193125-26-318235"),
    ],
)
# GTLS: the closing pair sits on its 2026-07-16 last bar, beside a 2021 Form 25 and a 2025-12-15 25-NSE.
GTLS = _edgar(
    [],
    [
        ("25", "2021-01-29", "0001193125-21-022712"),
        ("25-NSE", "2025-12-15", "0000876661-25-000969"),
        ("25-NSE", "2026-07-16", "0000876661-26-000623"),
        ("15-12G", "2026-07-29", "0001193125-26-322203"),
    ],
)
WONDF = _edgar([], [])  # never priced; files no US delisting form; EDGAR lists no ticker
HLLK = _edgar(["SDCO"], [])  # renamed: EDGAR lists SDCO
BRR = _edgar(["SVIA", "SVIAW"], [])  # renamed: EDGAR lists the new common + its warrant
# Still-trading multi-symbol / vendor-symbol names (their REAL EDGAR records; the stops are hypothetical).
KYOCY = _edgar(
    ["KYOCF", "KYOAY"], [("25", "2018-06-15", "0001193125-18-193509")]
)  # priced as KYOAY
ABBNY = _edgar(["ABLZF", "ABBNY"], [("25", "2023-05-12", "0001104659-23-059601")])
WELPM = _edgar(["WELPP", "WELPM"], [])
PRTC = _edgar(["PTCHF", "PRTCY"], [("25", "2026-05-11", "0001193125-26-216746")])  # priced as PRTCY
FDCT = _edgar(["FDCT"], [])  # priced as FDCTD (the vendor's symbol), EDGAR keeps FDCT


def test_VRME_a_WARRANT_delisting_470_days_before_the_edge_plus_a_new_EDGAR_ticker_is_a_RENAMED_repair():
    """THE BUG. The old rule took the newest delisting form EVER filed and labeled VRME "closed — delisting
    filed 2025-06-23", silencing a common stock that traded through 2026-10-06. The form is 470 days
    before the edge (outside any proximity window) AND EDGAR now lists OPNW, so it is a loud repair row
    marked renamed, naming the symbol — never closed."""
    edge = date(2026, 10, 6)
    assert (edge - date(2025, 6, 23)).days == 470  # pin the measured gap
    row = _classify(VRME, ticker="VRME", edge=edge)
    assert row.closed is False and row.closed_at is None
    assert row.renamed is True and row.edgar_tickers == ("OPNW",)


def test_VRME_stays_a_repair_on_PROXIMITY_alone_even_without_the_rename_guard():
    """The two rules are independent locks: with no EDGAR ticker list at all (unread), the 470-day-old
    warrant form still cannot close the tape — proximity alone keeps VRME loud."""
    out = stale_tapes(
        [
            _name(
                ticker="VRME",
                tape_edge=date(2026, 10, 6),
                delisting_filings=delisting_filings(VRME),
                edgar_tickers=None,
            )
        ],
        asof=_TONIGHT,
        stale_days=5,
    )
    assert out[0].closed is False and out[0].renamed is False


def test_a_GENUINE_close_with_its_forms_3_and_13_days_after_the_edge_reads_CLOSED():
    """AVNS: tickers [] and its 25-NSE / 15-12G 3 and 13 days after the last bar → closed, dated by the
    filing NEAREST the edge (the 25-NSE)."""
    row = _classify(AVNS, ticker="AVNS", edge=date(2026, 7, 24))
    assert row.closed is True and row.closed_at == date(2026, 7, 27)
    assert row.renamed is False and row.edgar_tickers == ()


def test_a_close_whose_EDGAR_record_still_lists_OUR_ticker_reads_CLOSED():
    """QRVO: the form sits on the last bar and EDGAR still lists QRVO — our own symbol, so the rename guard
    does not fire and the tape is closed."""
    row = _classify(QRVO, ticker="QRVO", edge=date(2026, 10, 5))
    assert row.closed is True and row.closed_at == date(2026, 10, 5) and row.renamed is False


def test_the_filing_NEAREST_the_edge_dates_the_close_on_either_side():
    """XOMA: 25-NSEs 7 and 5 days before a 2026-07-21 last bar, the 15-12G 6 days after → the 07-16 25-NSE
    (5 days) is nearest. GTLS: the 2021 and 2025 forms are outside the window and never chosen."""
    assert _classify(XOMA, ticker="XOMA", edge=date(2026, 7, 21)).closed_at == date(2026, 7, 16)
    assert _classify(GTLS, ticker="GTLS", edge=date(2026, 7, 16)).closed_at == date(2026, 7, 16)


def test_a_NEVER_priced_name_can_never_be_closed_by_proximity():
    """WONDF: no edge (never priced), no tickers, no forms → a plain stale-repair row. Without a last bar
    there is nothing for a form to be near, so even a delisting form would not close it."""
    row = _classify(WONDF, ticker="WONDF", edge=None)
    assert row.closed is False and row.renamed is False and row.edge is None
    with_form = _edgar([], [("15-12G", "2026-10-01", "HYPOTHETICAL")])
    assert _classify(with_form, ticker="WONDF", edge=None).closed is False


def test_RENAMED_starving_names_are_repair_rows_that_name_EDGARs_symbols():
    """HLLK (EDGAR lists SDCO) and BRR (SVIA + its warrant) — the rename-starved shape: no delisting form,
    EDGAR lists a different symbol, so the repair row says where the company trades now."""
    hllk = _classify(HLLK, ticker="HLLK", edge=date(2026, 9, 24))
    brr = _classify(BRR, ticker="BRR", edge=date(2026, 9, 28))
    assert (hllk.renamed, hllk.edgar_tickers, hllk.closed) == (True, ("SDCO",), False)
    assert (brr.renamed, brr.edgar_tickers, brr.closed) == (True, ("SVIA", "SVIAW"), False)


def test_the_RENAME_guard_beats_a_proximate_delisting_form():
    """A Nasdaq exit to OTC under a new symbol (BTAI → BTAIQ is the shape) files a 25-NSE right at the edge,
    yet the company still trades: the guard runs first, so the row is a renamed repair, never closed.
    Contract test — direct inputs; BTAI's own cached record carries no delisting form."""
    subs = _edgar(["BTAIQ"], [("25-NSE", "2026-10-09", "HYPOTHETICAL")])
    row = _classify(subs, ticker="BTAI", edge=date(2026, 10, 8))
    assert row.renamed is True and row.closed is False and row.edgar_tickers == ("BTAIQ",)


def test_MULTI_symbol_and_VENDOR_symbol_names_are_NOT_flagged_renamed():
    """MEMBERSHIP, never tickers[0]: EDGAR lists every class side by side. KYOCY is priced as KYOAY (EDGAR's
    SECOND symbol), ABBNY is EDGAR's second, WELPM its second, PRTC is priced as PRTCY, FDCT's vendor symbol
    is FDCTD while EDGAR keeps FDCT. A tickers[0] read would flag four of the five (MEASURED on prod too:
    ABBNY, KYOCY, PRTC, WELPM). Hypothetical stops on the names' REAL EDGAR records (all five trade
    today)."""
    edge = date(2026, 10, 6)
    cases = [
        (KYOCY, "KYOCY", "KYOAY"),
        (ABBNY, "ABBNY", None),
        (WELPM, "WELPM", None),
        (PRTC, "PRTC", "PRTCY"),
        (FDCT, "FDCT", "FDCTD"),
    ]
    for subs, ticker, ps in cases:
        row = _classify(subs, ticker=ticker, edge=edge, price_symbol=ps)
        assert row.renamed is False, ticker
    # ...and the first-element read the rule refuses WOULD have mis-flagged four of them
    first_elem_flags = [
        ticker
        for subs, ticker, ps in cases
        if edgar_tickers(subs)[0] not in {s for s in (ticker, ps) if s}
    ]
    assert first_elem_flags == ["KYOCY", "ABBNY", "WELPM", "PRTC"]


def test_a_stale_unrenamed_name_with_only_an_OLD_form_stays_a_REPAIR_row():
    """The 192-name class: a still-listed company with an old, unrelated delisting form (ABBNY's 2023 Form
    25, KYOCY's 2018 one). If its tape stopped tonight it is a feed gap — loud, not closed, not renamed.
    """
    for subs, ticker, ps in [(ABBNY, "ABBNY", None), (KYOCY, "KYOCY", "KYOAY")]:
        row = _classify(subs, ticker=ticker, edge=date(2026, 10, 6), price_symbol=ps)
        assert (row.closed, row.renamed) == (False, False), ticker


def test_the_window_edges_are_INCLUSIVE_and_settable():
    """Defaults [edge − 30, edge + 45], inclusive; one day past either side is not a close. Both sides are
    parameters (the nightly pass passes the Settings values)."""
    edge = date(2026, 8, 1)

    def closed(filed: date, **kw) -> bool:
        f = DelistingFiling(form="25-NSE", filed=filed, accession="A")
        return (
            classify_stop(
                edge=edge,
                filings=[f],
                edgar_tickers=(),
                ticker="X",
                price_symbol=None,
                asof=_TONIGHT,
                **kw,
            ).closed_at
            is not None
        )

    assert closed(edge - timedelta(days=30)) and not closed(edge - timedelta(days=31))
    assert closed(edge + timedelta(days=45)) and not closed(edge + timedelta(days=46))
    assert closed(edge - timedelta(days=31), before_days=31)  # the dial moves the boundary
    assert not closed(edge + timedelta(days=1), after_days=-5)  # a negative side reads as 0
    assert closed(edge, after_days=-5, before_days=-5)  # ...the edge day itself still counts


def test_a_filing_dated_AFTER_the_runs_asof_is_not_evidence_for_that_night():
    """#1 — a past-asof pass (a catch-up) reads today's cached submissions; a form filed after that night
    could not have been known then, so it cannot close that night's tape."""
    f = DelistingFiling(form="15-12G", filed=date(2026, 10, 15), accession="A")
    c = classify_stop(
        edge=date(2026, 10, 5),
        filings=[f],
        edgar_tickers=(),
        ticker="X",
        price_symbol=None,
        asof=date(2026, 10, 14),
    )
    assert c.closed_at is None


def test_the_window_defaults_match_the_SETTINGS_defaults():
    """Two homes for one number (the pure module cannot read Settings) — pinned so they cannot drift."""
    fields = Settings.model_fields  # the declared defaults, independent of the process env
    assert (
        (
            fields["tape_closed_before_days"].default,
            fields["tape_closed_after_days"].default,
        )
        == (
            DEFAULT_CLOSED_BEFORE_DAYS,
            DEFAULT_CLOSED_AFTER_DAYS,
        )
        == (30, 45)
    )


def test_no_EDGAR_facts_at_all_reads_as_a_plain_REPAIR_row():
    """No CIK / a failed submissions read → no filings, tickers None: the LOUD reading, never the quiet
    one (the safe default, #7/#9)."""
    row = stale_tapes([_name(ticker="DEAD", tape_edge=date(2026, 4, 1))], asof=_ASOF, stale_days=5)[
        0
    ]
    assert (row.closed, row.renamed, row.edgar_tickers) == (False, False, None)


def test_a_CLOSED_name_is_never_dropped_from_the_inventory():
    """#9 — recall is sacred: the marker only RECLASSIFIES, it never removes the name from the monitor. A
    stale+closed name is still emitted, with its real edge, exactly like any other stale row."""
    row = _classify(AVNS, ticker="AVNS", edge=date(2026, 7, 24))
    assert row.ticker == "AVNS" and row.edge == date(2026, 7, 24) and row.closed


def test_a_FRESH_tape_with_a_delisting_form_or_a_new_EDGAR_ticker_is_NOT_emitted():
    """Nothing to mark until the tape actually stops: a still-priced name (CTGL/AIXC — renamed in EDGAR but
    still priced today, plan T2's territory) is never emitted, so an active name is never nagged."""
    fresh = [
        _member(QRVO, ticker="QRVO", edge=_TONIGHT),
        _member(_edgar(["SKTG"], []), ticker="CTGL", edge=_TONIGHT),
    ]
    assert stale_tapes(fresh, asof=_TONIGHT, stale_days=5) == ()


def test_the_FUND_SHARES_feed_is_never_classified_closed_or_renamed():
    """Delisting and renames are LISTING (price) events: a stopped fund-shares sample is never marked by
    this mechanism even if the sleeve's issuer filed a delisting form near the edge, so its own repair
    advice is never suppressed."""
    sleeve = _name(
        ticker="ETF",
        fund_shares_tracked=True,
        fund_shares_edge=date(2026, 7, 24),
        delisting_filings=delisting_filings(AVNS),
        edgar_tickers=("OTHER",),
    )
    out = stale_fund_shares([sleeve], asof=_TONIGHT, stale_days=7)
    assert len(out) == 1 and out[0].closed is False and out[0].renamed is False


def test_a_StaleTape_defaults_to_NOT_closed_and_NOT_renamed():
    """A row built without the classification (every pre-G5b row, and every fund-shares row) is a plain
    stale-repair tape."""
    row = StaleTape(ticker="AAA", security_id=uuid.uuid4(), edge=None)
    assert row.closed is False and row.renamed is False and row.edgar_tickers is None


# --- Q6: the Form 25 CLASS check — a Form 25 that removed ANOTHER class is not a close -------------------
#
# REAL rows, MEASURED 2026-10-17: each name's window delisting forms exactly as `delisting_filings` reads
# them from prod's cached submissions (form, filing date, accession, primary document, the doc's own cik),
# the tickers EDGAR lists, and the edge prod's stored tape stops at. The Form 25 documents are the real ones
# in tests/fixtures/edgar/form25_cache (provenance: tests/ingest/test_form25.py), read through the REAL
# client and the REAL hook the nightly pass binds. For the 7 still-trading names the stop has NOT happened:
# each case states the rule's contract on the name's real record with a tonight-stop (their real last bar,
# judged at a pass five days later) — the same hypothetical the T1 measurement counted them under.

_Q6_ASOF = date(2026, 10, 21)  # a pass at which a 2026-10-16 last bar reads stale (5 calendar days)
_F25_CACHE = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form25_cache"
_XSL = "xslF25X02/primary_doc.xml"


def _q6_subs(cik: str, tickers: list[str], rows: list[tuple[str, str, str, str]]) -> dict:
    """EDGAR's real submissions shape WITH its ``cik`` and the real ``primaryDocument`` per row."""
    rows = sorted(rows, key=lambda r: r[1], reverse=True)
    return {
        "cik": cik,
        "tickers": tickers,
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "accessionNumber": [r[2] for r in rows],
                "primaryDocument": [r[3] for r in rows],
            }
        },
    }


def _nse(filed: str, accession: str) -> tuple[str, str, str, str]:
    return ("25-NSE", filed, accession, _XSL)


def _f15(filed: str, accession: str, doc: str) -> tuple[str, str, str, str]:
    return ("15-12G", filed, accession, doc)


# name -> (submissions, last bar). The 7 still trading, then the 8 genuine closes.
Q6_NAMES = {
    "ACHR": (
        _q6_subs("0001824502", ["ACHR"], [_nse("2026-09-16", "0000876661-26-000755")]),
        date(2026, 10, 16),
    ),
    "ARBE": (
        _q6_subs("0001861841", ["ARBE", "ARBEW"], [_nse("2026-10-06", "0001354457-26-000957")]),
        date(2026, 10, 16),
    ),
    "BKKT": (
        _q6_subs("0001820302", ["BKKT", "BKKT-WT"], [_nse("2026-10-15", "0000876661-26-000837")]),
        date(2026, 10, 16),
    ),
    "IONQ": (
        _q6_subs("0001824920", ["IONQ"], [_nse("2026-09-30", "0000876661-26-000804")]),
        date(2026, 10, 16),
    ),
    "KTTA": (
        _q6_subs("0001841330", ["KTTA"], [_nse("2026-09-16", "0001354457-26-000883")]),
        date(2026, 10, 16),
    ),
    "PDYN": (
        _q6_subs("0001826681", ["PDYN", "PDYNW"], [_nse("2026-09-28", "0001354457-26-000917")]),
        date(2026, 10, 16),
    ),
    "VSTD": (
        _q6_subs("0001898604", ["VSTD"], [_nse("2026-10-09", "0001354457-26-000973")]),
        date(2026, 10, 15),
    ),
    "SKYT": (
        _q6_subs(
            "0001819974",
            [],
            [
                _nse("2026-07-31", "0001354457-26-000745"),
                _f15("2026-08-10", "0000950142-26-002290", "eh260818332_1512g-sky.htm"),
            ],
        ),
        date(2026, 8, 7),
    ),
    "QRVO": (
        _q6_subs(
            "0001604778",
            ["QRVO"],
            [
                _nse("2026-10-05", "0001354457-26-000942"),
                _f15("2026-10-15", "0001104659-26-116241", "tm2627544d1_1512g.htm"),
            ],
        ),
        date(2026, 10, 5),
    ),
    "GTLS": (
        _q6_subs(
            "0000892553",
            [],
            [
                _nse("2026-07-16", "0000876661-26-000623"),
                _f15("2026-07-29", "0001193125-26-322203", "d135037d1512g.htm"),
            ],
        ),
        date(2026, 7, 16),
    ),
    "ATAI": (
        _q6_subs(
            "0002081043",
            [],
            [
                _nse("2026-09-11", "0001354457-26-000870"),
                _f15("2026-09-21", "0001140361-26-037188", "ef20082445_1512g.htm"),
            ],
        ),
        date(2026, 9, 11),
    ),
    "XOMA": (
        _q6_subs(
            "0000791908",
            [],
            [
                _nse("2026-07-14", "0001354457-26-000691"),
                _nse("2026-07-16", "0001354457-26-000697"),
                _f15("2026-07-27", "0001193125-26-318235", "d368035d1512g.htm"),
            ],
        ),
        date(2026, 7, 21),
    ),
    "CPRX": (
        _q6_subs(
            "0001369568",
            [],
            [
                _nse("2026-07-15", "0001354457-26-000694"),
                _f15("2026-07-24", "0001193125-26-315836", "d152809d1512g.htm"),
            ],
        ),
        date(2026, 7, 21),
    ),
    "AVNS": (
        _q6_subs(
            "0001606498",
            [],
            [
                _nse("2026-07-27", "0000876661-26-000646"),
                _f15("2026-08-06", "0001606498-26-000120", "avns2026form15.htm"),
            ],
        ),
        date(2026, 7, 24),
    ),
    "RMAX": (
        _q6_subs(
            "0001581091",
            [],
            [
                _nse("2026-08-25", "0000876661-26-000703"),
                _f15("2026-09-04", "0001104659-26-105182", "tm2623567d11_1512g.htm"),
            ],
        ),
        date(2026, 8, 24),
    ),
}
_STILL_TRADING = {"ACHR", "ARBE", "BKKT", "IONQ", "KTTA", "PDYN", "VSTD"}


def _hook():
    """The REAL hook the nightly pass binds, on the real cached Form 25 documents (no network)."""
    return partial(delisted_class_is_other, EdgarClient(cache_dir=_F25_CACHE, allow_live=False))


def _q6(name: str, *, hook=None) -> StaleTape:
    subs, edge = Q6_NAMES[name]
    out = stale_tapes(
        [_member(subs, ticker=name, edge=edge)],
        asof=_Q6_ASOF,
        stale_days=5,
        removes_other_class=hook,
    )
    assert len(out) == 1  # every case IS a stopped tape — the classification never drops it (#9)
    return out[0]


def test_Q6_a_WARRANT_25_NSE_no_longer_closes_a_stopped_common_tape():
    """The measured residual: under proximity alone each of these six would read CLOSED (quiet, never
    paged) the night its tape stopped — on a 25-NSE that removed its WARRANTS. With the class check each is
    the loud stale-REPAIR row a stopped, still-listed common tape is."""
    for name in ("ACHR", "ARBE", "BKKT", "IONQ", "KTTA", "PDYN"):
        filed = Q6_NAMES[name][0]["filings"]["recent"]["filingDate"][0]
        assert _q6(name).closed_at == date.fromisoformat(filed), name  # T1 alone: silenced
        row = _q6(name, hook=_hook())
        assert (row.closed, row.renamed) == (False, False), name


def test_Q6_VSTD_whose_25_NSE_removes_its_COMMON_still_closes():
    """Not every recent 25-NSE is a warrant: VSTD's 2026-10-09 filing removes its "Class A Common Stock"
    (a Nasdaq 12d2-2(b) strike), so if its tape stops it IS a close — the class check keeps it one.
    """
    assert _q6("VSTD", hook=_hook()).closed_at == date(2026, 10, 9)


def test_Q6_the_8_genuine_closes_STAY_closed():
    """Every genuine close's Form 25 removes the common, so each stays closed. One date moves: XOMA's
    nearest form (2026-07-16) removed its PREFERRED depositary shares, so its close dates from the
    next-nearest form, the 2026-07-27 15-12G (nearer than the common's 07-14 25-NSE: +6 days vs -7).
    """
    t1 = {name: _q6(name).closed_at for name in Q6_NAMES if name not in _STILL_TRADING}
    q6 = {name: _q6(name, hook=_hook()).closed_at for name in t1}
    assert q6 == {
        "SKYT": date(2026, 8, 10),
        "QRVO": date(2026, 10, 5),
        "GTLS": date(2026, 7, 16),
        "ATAI": date(2026, 9, 11),
        "XOMA": date(2026, 7, 27),
        "CPRX": date(2026, 7, 24),
        "AVNS": date(2026, 7, 27),
        "RMAX": date(2026, 8, 25),
    }
    assert {n for n in t1 if t1[n] != q6[n]} == {"XOMA"} and t1["XOMA"] == date(2026, 7, 16)


class _Asked:
    """A recording hook: which filings ``classify_stop`` asked about, answering from the real reader."""

    def __init__(self) -> None:
        self.real = _hook()
        self.asked: list[tuple[str, str]] = []

    def __call__(self, label, filing) -> bool:
        self.asked.append((label, filing.accession))
        return self.real(label, filing)


def test_Q6_the_hook_is_asked_NEAREST_first_ONLY_for_a_Form_25_and_ONLY_until_one_counts():
    """The cost bound: SKYT's nearest form is its 15-12G (+3 days), which counts without a read, so its
    25-NSE is never opened; XOMA's nearest is the preferred's 25-NSE (-5), read once and passed over, and
    the 15-12G (+6) then counts — its common's 25-NSE (-7) is never opened."""
    skyt, xoma = _Asked(), _Asked()
    assert _q6("SKYT", hook=skyt).closed_at == date(2026, 8, 10)
    assert skyt.asked == []
    assert _q6("XOMA", hook=xoma).closed_at == date(2026, 7, 27)
    assert xoma.asked == [
        ("XOMA", "0001354457-26-000697")
    ]  # the label rides along for the log line


def test_Q6_the_hook_is_never_asked_for_a_RENAMED_a_NEVER_PRICED_or_a_FRESH_tape():
    """No document read where the class cannot matter: the rename guard decides first (VRME's real record,
    EDGAR lists OPNW), a never-priced name has no last bar to be near, and a live tape is never classified.
    """
    asked = _Asked()
    vrme = stale_tapes(
        [_member(VRME, ticker="VRME", edge=date(2026, 10, 6))],
        asof=_TONIGHT,
        stale_days=5,
        removes_other_class=asked,
    )
    assert vrme[0].renamed is True
    subs, _ = Q6_NAMES["ACHR"]
    never = stale_tapes(
        [_member(subs, ticker="ACHR", edge=None)],
        asof=_Q6_ASOF,
        stale_days=5,
        removes_other_class=asked,
    )
    assert never[0].closed is False
    fresh = stale_tapes(
        [_member(subs, ticker="ACHR", edge=_Q6_ASOF)],
        asof=_Q6_ASOF,
        stale_days=5,
        removes_other_class=asked,
    )
    assert fresh == () and asked.asked == []


def test_Q6_an_UNKNOWN_class_still_COUNTS_and_is_logged(capsys, tmp_path):
    """The fail-safe Q6 kept: a Form 25 whose class cannot be read (here: the document is not in the cache
    and live pulls are off) counts as this security's delisting exactly as before — and says so."""
    blind = partial(delisted_class_is_other, EdgarClient(cache_dir=tmp_path, allow_live=False))
    assert _q6("ACHR", hook=blind).closed_at == date(2026, 9, 16)
    assert "counted as a delisting of this security (fail-safe)" in capsys.readouterr().out
