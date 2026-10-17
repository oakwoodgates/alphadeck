"""T3 + Q8 — which price 404s are EXPECTED (``pipeline/price_feed.py``), stated with the REAL errors and
classifications measured on prod (``tests/fixtures/prices/price_feed_repairs_2026_10.json``): GTLS is a
genuine close, WONDF never priced, HLLK a rename the operator has not adopted."""

from __future__ import annotations

import uuid
from datetime import date

from pipeline.ingest_thesis import NameResult
from pipeline.price_feed import (
    EXPECTED_ACKNOWLEDGED,
    EXPECTED_CLOSED,
    excuse_expected_misses,
    expected_miss_reasons,
    mark_acknowledged,
)
from pipeline.tape_health import StaleTape, classify_stop, is_unpriceable
from tests import price_feed_fixtures as fx

_ASOF = date(2026, 10, 16)


def _failing(sid, ticker, *legs, not_found=True) -> NameResult:
    """A name whose listed legs raised, shaped exactly as ``ingest_thesis`` builds it (``error`` = the
    ``"<leg>: <message>"`` pairs joined by ``"; "``)."""
    leg_errors = tuple(legs)
    return NameResult(
        ticker=ticker,
        security_id=sid,
        form4_appended=0,
        price_bars_appended=0,
        error="; ".join(f"{leg}: {m}" for leg, m in leg_errors) or None,
        leg_errors=leg_errors,
        price_not_found=not_found,
    )


def test_the_measured_shapes_classify_as_closed_renamed_and_unpriceable():
    """The three readings on the real EDGAR facts: GTLS closed (its 25-NSE sits on its last bar), HLLK
    renamed (EDGAR lists SDCO, not HLLK), WONDF unpriceable (no bar ever, EDGAR lists nothing)."""
    gtls = classify_stop(
        edge=fx.last_bar("GTLS"),
        filings=fx.delisting("GTLS"),
        edgar_tickers=(),
        ticker="GTLS",
        price_symbol=None,
        asof=_ASOF,
    )
    hllk = classify_stop(
        edge=fx.last_bar("HLLK"),
        filings=(),
        edgar_tickers=("SDCO",),
        ticker="HLLK",
        price_symbol=None,
        asof=_ASOF,
    )
    wondf = classify_stop(
        edge=None, filings=(), edgar_tickers=(), ticker="WONDF", price_symbol=None, asof=_ASOF
    )
    assert gtls.closed_at == date(2026, 7, 16) and not gtls.renamed
    assert hllk.renamed and hllk.closed_at is None
    assert wondf.closed_at is None and not wondf.renamed
    assert is_unpriceable(kind="price", edge=None, closed_at=None, renamed=False)
    assert not is_unpriceable(kind="price", edge=fx.last_bar("GTLS"), closed_at=None, renamed=False)
    assert not is_unpriceable(kind="price", edge=None, closed_at=None, renamed=True)
    assert not is_unpriceable(kind="fund_shares", edge=None, closed_at=None, renamed=False)


def test_a_CLOSED_names_404_is_excused_and_its_other_legs_errors_are_kept_exactly():
    """GTLS's real vendor 404 is EXPECTED (closed): the price entry leaves `leg_errors`, `error` is rebuilt
    from the remaining legs exactly as the ingest builds it, and the excused message is kept on the
    ExpectedPriceMiss. A form4 failure on the same name is NOT excused — only the 404 is expected.
    """
    sid = uuid.uuid4()
    msg = fx.probe_error("GTLS", "GTLS").split(": ", 1)[
        1
    ]  # the extracted message after the class name
    r = _failing(sid, "GTLS", ("form4", "boom"), ("price", msg))
    out, misses = excuse_expected_misses([r], {sid: EXPECTED_CLOSED})
    (n,) = out
    assert n.leg_errors == (("form4", "boom"),)
    assert n.error == "form4: boom"
    assert misses[0].reason == "closed" and misses[0].ticker == "GTLS"
    assert misses[0].error.startswith("Client error '404 Not Found'")
    assert r.error == f"form4: boom; price: {msg}"  # the input is never mutated


def test_a_404_that_was_the_ONLY_failure_leaves_no_error_at_all():
    sid = uuid.uuid4()
    out, misses = excuse_expected_misses(
        [_failing(sid, "WONDF", ("price", fx.probe_error("WONDF", "WONDF")))],
        {sid: EXPECTED_ACKNOWLEDGED},
    )
    assert out[0].error is None and out[0].leg_errors == ()
    assert misses[0].reason == "acknowledged"


def test_a_NON_404_price_failure_is_never_excused_even_on_a_closed_name():
    sid = uuid.uuid4()
    r = _failing(sid, "GTLS", ("price", "ReadTimeout: timed out"), not_found=False)
    out, misses = excuse_expected_misses([r], {sid: EXPECTED_CLOSED})
    assert out == [r] and misses == ()


def test_a_name_NOT_classified_expected_keeps_its_404():
    """HLLK (renamed, not adopted) and an unacknowledged WONDF are absent from the reasons: their 404s are
    the news, so they stay errors."""
    hllk, wondf = uuid.uuid4(), uuid.uuid4()
    names = [
        _failing(hllk, "HLLK", ("price", "Client error '404 Not Found'")),
        _failing(wondf, "WONDF", ("price", "Client error '404 Not Found'")),
    ]
    out, misses = excuse_expected_misses(names, {})
    assert out == names and misses == ()


def test_a_value_chain_duplicate_is_ONE_excused_miss():
    sid = uuid.uuid4()
    r = _failing(sid, "XOMA", ("price", "Client error '404 Not Found'"))
    out, misses = excuse_expected_misses([r, r], {sid: EXPECTED_CLOSED})
    assert [n.error for n in out] == [None, None] and len(misses) == 1


def test_only_an_UNPRICEABLE_row_can_be_marked_acknowledged():
    """An acknowledgment silences only the never-priced class: a closed, a renamed and a plain feed-gap row
    with the same acknowledged id stay as they were."""
    sid = uuid.uuid4()
    rows = (
        StaleTape(ticker="WONDF", security_id=sid, edge=None, edgar_tickers=()),
        StaleTape(ticker="GAP", security_id=sid, edge=date(2026, 9, 1)),
        StaleTape(ticker="HLLK", security_id=sid, edge=None, renamed=True, edgar_tickers=("SDCO",)),
        StaleTape(ticker="ETF", security_id=sid, edge=None, kind="fund_shares"),
    )
    marked = mark_acknowledged(rows, {sid})
    assert [s.acknowledged for s in marked] == [True, False, False, False]
    assert marked[0].quiet and not marked[1].quiet
    assert mark_acknowledged(rows, set()) == rows


def test_expected_reasons_are_closed_or_acknowledged_price_rows_only():
    closed, acked, gap = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = (
        StaleTape(
            ticker="GTLS", security_id=closed, edge=date(2026, 7, 16), closed_at=date(2026, 7, 16)
        ),
        StaleTape(ticker="WONDF", security_id=acked, edge=None, acknowledged=True),
        StaleTape(ticker="GAP", security_id=gap, edge=date(2026, 9, 1)),
    )
    assert expected_miss_reasons(rows) == {closed: "closed", acked: "acknowledged"}
