from __future__ import annotations

from uuid import uuid4

from ingest.edgar.nport import Holding
from workbench.etf_overlap import classify


def _h(
    name: str,
    ticker: str | None = None,
    pct: float | None = None,
    cusip: str | None = None,
) -> Holding:
    return Holding(name=name, cusip=cusip, isin=None, ticker=ticker, val_usd=None, pct_val=pct)


def test_partition_held_available_unresolved():
    sid_held, sid_avail = uuid4(), uuid4()
    holdings = [
        _h("IN BASKET", ticker="ABC", pct=5.0),
        _h("IN MASTER ONLY", ticker="DEF", pct=3.0),
        _h("FOREIGN NAME", ticker=None, pct=9.0, cusip=None),  # no ticker → can never match
        _h("UNKNOWN TICKER", ticker="ZZZ", pct=1.0),  # ticker, but not in the master
    ]
    res = classify(holdings, {"ABC": sid_held, "DEF": sid_avail}, {sid_held})
    assert [(h.name, sid) for h, sid in res.held] == [("IN BASKET", sid_held)]
    assert [(h.name, sid) for h, sid in res.available] == [("IN MASTER ONLY", sid_avail)]
    assert [h.name for h in res.unresolved] == ["FOREIGN NAME", "UNKNOWN TICKER"]


def test_recall_every_holding_lands_in_exactly_one_bucket():
    """#9 — the partition sums to the input, whatever the match coverage: an unmatched holding is
    SHOWN as unresolved, never omitted (a dropped name is invisible; a surfaced one is prunable)."""
    sid = uuid4()
    holdings = [
        _h(f"NAME {i}", ticker=("ABC" if i == 0 else None), pct=float(i)) for i in range(25)
    ]
    res = classify(holdings, {"ABC": sid}, set())
    assert len(res.held) + len(res.available) + len(res.unresolved) == len(holdings)
    assert len(res.available) == 1  # the one match, basket empty → available
    assert len(res.unresolved) == 24


def test_zero_match_coverage_is_all_unresolved_not_empty():
    """The measured LIT shape (no equity tickers in the N-PORT): every holding must still SURFACE."""
    holdings = [_h("A", pct=2.0), _h("B", pct=1.0)]
    res = classify(holdings, {}, set())
    assert res.held == [] and res.available == []
    assert [h.name for h in res.unresolved] == ["A", "B"]


def test_buckets_sort_by_weight_desc_none_last():
    sid1, sid2, sid3 = uuid4(), uuid4(), uuid4()
    holdings = [
        _h("LIGHT", ticker="L", pct=0.5),
        _h("HEAVY", ticker="H", pct=19.5),
        _h("WEIGHTLESS", ticker="W", pct=None),  # still shown — sorts last, never hides
        _h("MID", ticker="M", pct=4.2),
    ]
    ids = {"L": sid1, "H": sid2, "W": sid3, "M": uuid4()}
    res = classify(holdings, ids, set())
    assert [h.name for h, _ in res.available] == ["HEAVY", "MID", "LIGHT", "WEIGHTLESS"]


def test_empty_holdings_is_an_empty_partition():
    res = classify([], {"ABC": uuid4()}, set())
    assert res.held == [] and res.available == [] and res.unresolved == []


def test_ticker_match_is_case_normalized():
    """`ids_for_tickers` keys are upper-cased; a lower-cased N-PORT ticker must still match (a case
    mismatch would silently unresolve a real name — the invisible-failure class)."""
    sid = uuid4()
    res = classify([_h("MIXED CASE", ticker="abc", pct=1.0)], {"ABC": sid}, set())
    assert [(h.name, s) for h, s in res.available] == [("MIXED CASE", sid)]


# --- the CUSIP→ticker leg (the overlap upgrade: most filers stamp NO ticker on equity holdings) ------


def test_cusip_resolved_holding_matches_held_and_available():
    """The SMH shape: NVIDIA rides ONLY its CUSIP in the filing — the OpenFIGI crosswalk (passed in;
    classify stays pure) turns it into the master's ticker key, so a basket name finally reads held.
    """
    nvda, avgo = uuid4(), uuid4()
    holdings = [
        _h("NVIDIA Corp", pct=19.6, cusip="67066G104"),
        _h("Broadcom Inc", pct=7.8, cusip="11135F101"),
        _h("Foreign Local", pct=2.0, cusip="999999999"),  # OpenFIGI declined -> not in the map
    ]
    res = classify(
        holdings,
        {"NVDA": nvda, "AVGO": avgo},
        {nvda},
        cusip_tickers={"67066G104": "NVDA", "11135F101": "AVGO"},
    )
    assert [(h.name, s) for h, s in res.held] == [("NVIDIA Corp", nvda)]
    assert [(h.name, s) for h, s in res.available] == [("Broadcom Inc", avgo)]
    assert [h.name for h in res.unresolved] == ["Foreign Local"]  # unmapped stays SHOWN (#9)


def test_filing_ticker_wins_over_the_cusip_map():
    """The filer's own ticker identifier is authoritative — the crosswalk only fills its absence (a
    conflicting map entry must never re-route a holding the filing already identified)."""
    sid_tick, sid_cusip = uuid4(), uuid4()
    holdings = [_h("DUAL ID", ticker="REAL", pct=1.0, cusip="123456789")]
    res = classify(
        holdings,
        {"REAL": sid_tick, "WRONG": sid_cusip},
        set(),
        cusip_tickers={"123456789": "WRONG"},
    )
    assert [(h.name, s) for h, s in res.available] == [("DUAL ID", sid_tick)]


def test_cusip_key_lookup_is_case_normalized():
    sid = uuid4()
    holdings = [_h("LOWER CUSIP", pct=1.0, cusip="67066g104")]
    res = classify(holdings, {"NVDA": sid}, set(), cusip_tickers={"67066G104": "nvda"})
    assert [(h.name, s) for h, s in res.available] == [("LOWER CUSIP", sid)]


def test_recall_partition_holds_with_the_cusip_leg():
    """#9 with both legs live: buckets still sum to the input whatever mix of ticker-carrying,
    cusip-resolved, and unmatchable holdings comes in."""
    sid = uuid4()
    holdings = [
        _h("TICKERED", ticker="ABC", pct=3.0),
        _h("CUSIP-RESOLVED", pct=2.0, cusip="67066G104"),
        _h("UNMAPPED CUSIP", pct=1.0, cusip="000000000"),
        _h("NO IDS AT ALL", pct=0.5),
    ]
    res = classify(
        holdings, {"ABC": sid, "NVDA": uuid4()}, set(), cusip_tickers={"67066G104": "NVDA"}
    )
    assert len(res.held) + len(res.available) + len(res.unresolved) == len(holdings)
    assert [h.name for h in res.unresolved] == ["UNMAPPED CUSIP", "NO IDS AT ALL"]
