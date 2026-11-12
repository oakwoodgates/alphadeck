"""Rule ``2026-11-insider-rows-dated-after-filing`` — what the insider signals do with a held row the read returns
(``domain.insider_holds``), on the REAL filings' reads (``tests/held_fixtures.py``): an on-time buy fires from its
filing night, the day-low price screen skips a row dated at its filing night (and only such a row), and the label
and provenance say so (#6).
"""

from __future__ import annotations

from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.insider_holds import filing_night_clause, held_detail
from domain.market_time import known_at_for_asof
from signals import insider_conviction, insider_sell
from signals.base import PointInTimeData
from signals.display import insider_flow
from tests import held_fixtures as H

CLAUSE = "counted from the filing night because the date in the filing cannot be true"


def _rows(db, sid, asof):
    return PointInTimeData(
        db,
        asof=asof,
        known_at=known_at_for_asof(asof, H.FAR),
        clock="public",
        workspace=DEFAULT_TENANT_ID,
    ).insider_txns(sid)


def test_on_time_buys_fire_from_the_filing_night_not_the_written_date(db):
    sid = H.seed(db, "on_time_a")
    asof = date(2015, 1, 2)
    rows = _rows(db, sid, asof)
    ev = insider_conviction.score(rows, sid, asof)
    assert ev is not None and ev.asof == date(2015, 1, 2)  # the written date is 2015-12-31
    assert ev.label.endswith(f"; 3 row(s) {CLAUSE}")
    (prov,) = ev.provenance
    assert prov.ref == "0001354488-15-000004"
    assert prov.detail["date_basis"] == ["filing_night"]
    assert prov.detail["txn_date_as_filed"] == ["2015-12-31"]
    # nothing fires before the filing was public
    assert insider_conviction.score(_rows(db, sid, date(2015, 1, 1)), sid, date(2015, 1, 1)) is None


def test_an_on_time_buy_two_months_early_fires_from_its_filing_night(db):
    sid = H.seed(db, "on_time_b")
    asof = date(2010, 8, 10)
    ev = insider_conviction.score(_rows(db, sid, asof), sid, asof)
    assert ev is not None and ev.asof == date(2010, 8, 10)  # written 2010-10-09


def test_the_day_low_screen_skips_a_filing_night_row_only(db):
    sid = H.seed(db, "on_time_a")
    asof = date(2015, 1, 2)
    rows = _rows(db, sid, asof)
    lows = {
        date(2015, 1, 2): 50.0
    }  # a low far above every price: it would screen a row it could judge
    assert insider_conviction.score(rows, sid, asof, day_lows=lows) is not None
    # the same rows as if stored at that day (no date basis): the screen judges them and drops them all
    stored = [{k: v for k, v in r.items() if k != "date_basis"} for r in rows]
    assert insider_conviction.score(stored, sid, asof, day_lows=lows) is None
    for r in rows:
        assert not insider_flow._is_below_day_low(r, lows)
    for r in stored:
        assert insider_flow._is_below_day_low(r, lows)


def test_the_sell_screen_skips_a_filing_night_row_only_and_names_it(db):
    sid = H.seed(db, "impossible_year")
    asof = date(2024, 5, 28)
    rows = _rows(db, sid, asof)
    lows = {asof: 1000.0}
    placed = [r for r in rows if r.get("date_basis") == "filing_night"]
    stored = [r for r in rows if not r.get("date_basis")]
    assert len(placed) == 2 and len(stored) == 4
    # the below-low screen (ordered before the plan flag) judges the stored rows; the placed rows pass it and
    # land in the next screen, the filer's own Rule 10b5-1 box (every row of this filing ticks it)
    assert {insider_sell._screen(r, lows, None, DEFAULT_CONFIG) for r in stored} == {"below_low"}
    assert {insider_sell._screen(r, lows, None, DEFAULT_CONFIG) for r in placed} == {"planned"}
    assert {insider_sell._screen(r, {}, None, DEFAULT_CONFIG) for r in stored} == {"planned"}


def test_a_sell_cluster_holding_a_filing_night_row_names_it(db):
    """The label + provenance wiring, on the impossible-year filing BEFORE its 4/A: the stored 11-16 sale and
    the held 11-17 sale (placed at the filing night). NOT a claim about the sell signal: every sale in the
    committed held fixtures ticks the filer's Rule 10b5-1 box (the planned screen sets it aside) and has one
    seller, so to read the label this test clears that flag on its copies of the rows and relaxes the
    two-seller floor; the CEO is senior, so the senior floor stays.
    """
    sid = H.seed(db, "amended_impossible_year", amendments=False)
    asof = date(2023, 11, 20)
    rows = [{**r, "aff_10b5_1": None} for r in _rows(db, sid, asof)]
    cfg = DEFAULT_CONFIG.model_copy(update={"insider_sell_min_distinct": 1})
    lows = {date(2023, 11, 17): 100.0}  # far above the price: judged, it would set the row aside
    ev = insider_sell.score(rows, sid, asof, cfg, day_lows=lows)
    assert ev is not None and ev.asof == date(2023, 11, 17)  # the anchor: the filing night
    assert f"1 row(s) {CLAUSE}" in ev.label
    (prov,) = ev.provenance
    assert prov.ref == "0001628280-23-039558"
    assert prov.detail["date_basis"] == ["filing_night"]
    assert prov.detail["txn_date_as_filed"] == ["2027-11-17"]
    assert prov.detail["txn_count"] == 2 and prov.detail["below_low_set_aside"] == 0


def test_a_card_with_no_held_row_keeps_its_exact_label_and_provenance(db):
    sid = H.seed(db, "signed_ahead")
    rows = _rows(db, sid, date(2014, 7, 25))
    assert rows and not any(r.get("date_basis") for r in rows)
    assert held_detail(rows, "0001019687-14-002802") == {}
    assert filing_night_clause(rows) == ""
