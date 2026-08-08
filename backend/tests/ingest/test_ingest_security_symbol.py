"""Slice B — the price leg prices under the RESOLVED vendor symbol (``price_symbol or ticker``).

A recording ``PriceSource`` proves WHICH symbol is fetched; a DB test proves the existing overlap/hole
backfill fills the missing history on the first POST-ADOPTION ingest (no separate bar-backfill needed),
and a re-run appends NOTHING — COUNT the table, not the read.
"""

from __future__ import annotations

from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices.eod_loader import ingest_prices
from ingest.prices.ingest_security import ingest_bars_for_security

D1, D2, D3, D4, D5 = (date(2026, 6, d) for d in (1, 2, 3, 4, 5))


def _bar(d: date, close: float = 1.0, volume: float = 100.0) -> dict:
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": volume}


class _BySymbol:
    """A PriceSource keyed by the requested symbol — records every symbol asked for."""

    def __init__(self, bars_by_symbol: dict[str, list[dict]]):
        self.bars_by_symbol = bars_by_symbol
        self.requested: list[str] = []

    def get_bars(self, ticker: str, *, allow_live: bool = True, force_refresh: bool = False):
        self.requested.append(ticker)
        return self.bars_by_symbol.get(ticker, [])


def _sec(security_id, *, ticker="FDCT", price_symbol=None) -> Security:
    return Security(
        id=security_id,
        tenant_id=DEFAULT_TENANT_ID,
        ticker=ticker,
        name="First Digital Corp",
        price_symbol=price_symbol,
    )


def _count(db, security_id) -> int:
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (security_id,)
        )
        return cur.fetchone()["n"]


def test_fetches_under_the_resolved_symbol_when_set(db, security_id):
    """With ``price_symbol`` set, the source is asked for the VENDOR symbol (FDCTD), never the SEC ticker."""
    src = _BySymbol({"FDCTD": [_bar(D1)]})
    ingest_bars_for_security(
        db, _sec(security_id, price_symbol="FDCTD"), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    assert src.requested == ["FDCTD"]


def test_falls_back_to_the_canonical_ticker_when_unresolved(db, security_id):
    """No ``price_symbol`` → the source is asked for the canonical ticker (unchanged behavior)."""
    src = _BySymbol({"FDCT": [_bar(D1)]})
    ingest_bars_for_security(db, _sec(security_id), tenant_id=DEFAULT_TENANT_ID, source=src)
    assert src.requested == ["FDCT"]


def test_adoption_fills_the_history_hole_once_then_rerun_appends_zero(db, security_id):
    """The post-adoption fill: FDCT stored a starved recent tail (D3-D5); after adopting FDCTD the fuller
    history (D1-D5) is fetched and the overlap/hole pass BACKFILLS the missing D1-D2 in one pass. A re-run
    over the same FDCTD series appends ZERO — COUNT the table, not the read (no separate backfill code).
    """
    # pre-adoption: FDCT gave only the recent 3 bars
    ingest_prices(db, security_id, [_bar(D3), _bar(D4), _bar(D5)])
    db.commit()
    assert _count(db, security_id) == 3

    # FDCTD carries the FULL history; the overlap (D3-D5) is IDENTICAL, so only the D1-D2 holes fill
    fdctd = [_bar(D1), _bar(D2), _bar(D3), _bar(D4), _bar(D5)]
    src = _BySymbol({"FDCTD": fdctd})
    res = ingest_bars_for_security(
        db, _sec(security_id, price_symbol="FDCTD"), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    db.commit()
    assert src.requested == ["FDCTD"]
    assert (
        res.appended == 0 and res.reversioned == 2
    )  # D1, D2 backfilled through the overlap-hole path
    assert _count(db, security_id) == 5  # the two missing bars filled — starvation cured

    res2 = ingest_bars_for_security(
        db, _sec(security_id, price_symbol="FDCTD"), tenant_id=DEFAULT_TENANT_ID, source=src
    )
    db.commit()
    assert res2.appended == 0 and res2.reversioned == 0  # idempotent
    assert _count(db, security_id) == 5  # COUNT the table: no silent growth
