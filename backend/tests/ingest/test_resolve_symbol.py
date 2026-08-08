"""``ingest/prices/resolve_symbol.py`` — the resolver runtime (search + history probe + pure decide).
Monkeypatches the two live legs so the orchestration is exercised offline."""

from __future__ import annotations

from datetime import date

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from ingest.prices import resolve_symbol
from ingest.prices.resolve_symbol import resolve_price_symbol


def _sec(ticker="FDCT", name="First Digital Corp"):
    import uuid

    return Security(id=uuid.uuid4(), tenant_id=DEFAULT_TENANT_ID, ticker=ticker, name=name)


def _bars(n: int) -> list[dict]:
    return [{"d": date(2026, 1, 1), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * n


def _patch(monkeypatch, *, searches, bars):
    """searches: {query: payload}; bars: {symbol: bar_count}."""
    monkeypatch.setattr(
        resolve_symbol,
        "search_quotes",
        lambda q, **kw: searches.get(q, {"quotes": []}),
    )
    monkeypatch.setattr(
        resolve_symbol,
        "fetch_eod",
        lambda sym, **kw: _bars(bars.get(sym.upper(), 0)),
    )


def test_ticker_search_auto(monkeypatch):
    _patch(
        monkeypatch,
        searches={
            "FDCT": {
                "quotes": [
                    {"symbol": "FDCTD", "shortname": "First Digital Corp", "quoteType": "EQUITY"}
                ]
            }
        },
        bars={"FDCT": 16, "FDCTD": 251},
    )
    p = resolve_price_symbol(_sec(), allow_live=True)
    assert (p.tier, p.proposed_symbol) == ("AUTO", "FDCTD")


def test_name_fallback_when_ticker_search_empty(monkeypatch):
    _patch(
        monkeypatch,
        searches={
            # ticker search (VREOD) empty; the NAME search resolves VREOF
            "Vireo Growth Inc.": {
                "quotes": [
                    {"symbol": "VREOF", "shortname": "Vireo Growth Inc.", "quoteType": "EQUITY"}
                ]
            }
        },
        bars={"VREOD": 8, "VREOF": 251},
    )
    p = resolve_price_symbol(_sec(ticker="VREOD", name="Vireo Growth Inc."), allow_live=True)
    assert (p.tier, p.proposed_symbol) == ("AUTO", "VREOF")


def test_probe_failure_is_guarded_to_flag_not_crash(monkeypatch):
    """A history-probe error (offline / network) yields 0 bars = unverified → the lone match is FLAG, the
    resolution never crashes."""
    monkeypatch.setattr(
        resolve_symbol,
        "search_quotes",
        lambda q, **kw: (
            {
                "quotes": [
                    {"symbol": "FDCTD", "shortname": "First Digital Corp", "quoteType": "EQUITY"}
                ]
            }
            if q == "FDCT"
            else {"quotes": []}
        ),
    )

    def _boom(sym, **kw):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(resolve_symbol, "fetch_eod", _boom)
    p = resolve_price_symbol(_sec(), allow_live=True)
    assert (p.tier, p.proposed_symbol) == ("FLAG", "FDCTD")  # candidate found, unverified


def test_shortened_name_fallback_resolves_curaleaf(monkeypatch):
    """THE RECALL FIX end-to-end: the ticker (CURLD) and the FULL name both search to nothing, but a
    SHORTENED query ("Curaleaf") surfaces CURLF — the resolver issues that shorter query and adopts it AUTO.
    The full-name query returns empty (the measured miss); only the shortened one returns the US listing.
    """
    searches = {
        # "CURLD" and the full legal name both miss
        "Curaleaf Holdings, Inc.": {"quotes": []},
        # the suffix-stripped and first-token shortened queries surface the US listing
        "Curaleaf Holdings": {
            "quotes": [
                {"symbol": "CURA.TO", "shortname": "Curaleaf Holdings, Inc.", "quoteType": "EQUITY"}
            ]
        },  # foreign only → filtered out
        "Curaleaf": {
            "quotes": [
                {"symbol": "CURLF", "shortname": "Curaleaf Holdings, Inc.", "quoteType": "EQUITY"}
            ]
        },
    }
    _patch(monkeypatch, searches=searches, bars={"CURLD": 12, "CURLF": 251})
    p = resolve_price_symbol(_sec(ticker="CURLD", name="Curaleaf Holdings, Inc."), allow_live=True)
    assert (p.tier, p.proposed_symbol) == ("AUTO", "CURLF")
    assert "shortened-name search" in p.why


def test_tickerless_is_none(monkeypatch):
    _patch(monkeypatch, searches={}, bars={})
    p = resolve_price_symbol(_sec(ticker=None), allow_live=True)
    assert p.tier == "NONE" and p.proposed_symbol is None
