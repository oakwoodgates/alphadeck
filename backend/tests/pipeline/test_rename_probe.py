"""T2 — the rename recommender's SPLICE PROBE, on REAL prod data (``tests/fixtures/prices/
price_feed_repairs_2026_10.json``): HLLK's stored tape against SDCO, and BRR's against SVIA (its common
stock) and SVIAW (its warrants). The probe must verify the true successors, reject the mismatched class,
make ONE vendor request per candidate into a throwaway cache, and write NOTHING (a pending recommendation —
the operator's Adopt applies it)."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from db.session import connect
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.rename_probe import (
    VERIFY_MIN_OVERLAP,
    RenameProbe,
    describe,
    judge,
    run_rename_probe,
    splice,
)
from pipeline.tape_health import StaleTape
from tests import price_feed_fixtures as fx

_ASOF = date(2026, 10, 16)


def test_the_true_successor_VERIFIES_on_the_real_overlap():
    """HLLK -> SDCO (an OTC rename; SDR Drone, Inc. was HALLMARK VENTURE GROUP until 2026-06-16): SDCO's
    vendor tape carries HLLK's history under the new symbol, so every shared close matches."""
    c = judge("SDCO", fx.probe_bars("HLLK", "SDCO"), fx.stored_closes("HLLK"))
    assert c.verified is True
    assert c.overlap == 30  # every extracted stored date is in SDCO's tape
    assert c.ratio == 1.0
    assert c.last_bar == date(2026, 10, 15)  # adopting it extends our tape past the 09-24 edge


def test_the_common_stock_verifies_and_the_WARRANTS_are_REJECTED():
    """BRR -> EDGAR lists SVIA and SVIAW side by side. SVIA (the common) splices at x1.000; SVIAW (the
    warrants) shares every date but trades at ~x0.12 — the mismatched candidate a blind tickers[0]-style
    adoption could have filed under the member."""
    stored = fx.stored_closes("BRR")
    svia = judge("SVIA", fx.probe_bars("BRR", "SVIA"), stored)
    sviaw = judge("SVIAW", fx.probe_bars("BRR", "SVIAW"), stored)
    assert svia.verified is True and abs(svia.ratio - 1.0) < 1e-6 and svia.overlap == 30
    assert sviaw.verified is False and sviaw.overlap == 30
    assert 0.10 < sviaw.ratio < 0.14  # MEASURED median 0.121 — an order of magnitude off


def test_a_splice_needs_at_least_FIVE_shared_dates():
    """The overlap floor, stated with real values: SDCO restricted to the first 4 stored dates matches to
    the cent and still does NOT verify — too few dates to rule out a coincidence; the 5th makes it.
    """
    stored = fx.stored_closes("HLLK")
    first = sorted(stored)[:VERIFY_MIN_OVERLAP]
    bars = [b for b in fx.probe_bars("HLLK", "SDCO") if b["d"] in set(first)]
    four = judge("SDCO", [b for b in bars if b["d"] != first[-1]], stored)
    five = judge("SDCO", bars, stored)
    assert four.overlap == 4 and four.verified is False
    assert five.overlap == 5 and five.verified is True


def test_splice_ignores_dates_without_a_positive_close_on_BOTH_sides():
    stored = {date(2026, 9, 1): 2.0, date(2026, 9, 2): 0.0}
    bars = [
        {"d": date(2026, 9, 1), "close": 2.0},
        {"d": date(2026, 9, 2), "close": 2.0},  # our close is 0 -> not a shared date
        {"d": date(2026, 9, 3), "close": None},  # theirs is missing
    ]
    assert splice(bars, stored) == (1, 1.0)


class _FakeSource:
    """A ``PriceSource`` serving the extracted vendor bars (or the extracted 404) per symbol, counting
    requests and recording the cache dir it was built over."""

    def __init__(self, cache_dir: Path, served: dict, calls: list, dirs: list):
        self._served = served
        self._calls = calls
        dirs.append(cache_dir)

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        self._calls.append((ticker, allow_live, force_refresh))
        got = self._served[ticker]
        if isinstance(got, Exception):
            raise got
        return got


def _stale_renamed(sid, ticker):
    c = fx.case(ticker)
    return StaleTape(
        ticker=ticker,
        security_id=sid,
        edge=fx.last_bar(ticker),
        renamed=True,
        edgar_tickers=tuple(c["edgar_tickers"]),
    )


def _name(sid, ticker):
    c = fx.case(ticker)
    return NameResult(
        ticker=ticker,
        security_id=sid,
        form4_appended=0,
        price_bars_appended=0,
        edgar_name=c["edgar_name"],
        former_names=fx.former(ticker),
        name_change_filings=fx.name_changes(ticker),
    )


def _counts(db) -> dict:
    with db.cursor() as cur:
        out = {}
        for t in ("fact_price_eod", "price_symbol_change", "price_feed_ack"):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — fixed table names
            out[t] = cur.fetchone()["n"]
        cur.execute("SELECT count(*) AS n FROM security_master WHERE price_symbol IS NOT NULL")
        out["price_symbols_set"] = cur.fetchone()["n"]
    return out


def test_the_nightly_probe_recommends_ONE_fetch_per_candidate_and_WRITES_NOTHING(db):
    """End to end over a real DB: two renamed names held by two theses (HLLK twice — a value-chain
    duplicate), three distinct EDGAR symbols. Exactly three vendor requests, each live + force-refreshed
    into a THROWAWAY temp dir (gone afterwards); SDCO and SVIA verified, SVIAW rejected; the EDGAR evidence
    rides each row; and not one row anywhere changed — nothing is adopted until the operator clicks.
    """
    hllk = fx.insert_security(db, "HLLK")
    brr = fx.insert_security(db, "BRR")
    before = _counts(db)
    results = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="Modern Defense",
            ingested=[_name(hllk, "HLLK")],
            tape_stale=(_stale_renamed(hllk, "HLLK"),),
        ),
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="The Everything Exchange",
            ingested=[_name(brr, "BRR"), _name(hllk, "HLLK")],
            tape_stale=(_stale_renamed(brr, "BRR"), _stale_renamed(hllk, "HLLK")),
        ),
    ]
    served = {
        "SDCO": fx.probe_bars("HLLK", "SDCO"),
        "SVIA": fx.probe_bars("BRR", "SVIA"),
        "SVIAW": fx.probe_bars("BRR", "SVIAW"),
    }
    calls: list = []
    dirs: list = []

    probe = run_rename_probe(
        results=results,
        asof=_ASOF,
        allow_live=True,
        connect=connect,
        clock=lambda: 0.0,
        source_factory=lambda d: _FakeSource(d, served, calls, dirs),
    )

    assert probe.evaluated and probe.error is None
    assert sorted(c[0] for c in calls) == ["SDCO", "SVIA", "SVIAW"] and probe.fetches == 3
    assert all(live and force for _, live, force in calls)
    assert len(dirs) == 1 and not dirs[0].exists()  # a throwaway dir, removed on return
    assert "price_cache" not in str(dirs[0])  # never the shared cache
    recs = probe.by_security()
    assert set(recs) == {hllk, brr}  # HLLK once, though two theses hold it
    assert recs[hllk].recommended == "SDCO"
    assert recs[hllk].edgar_name == "SDR Drone, Inc."
    assert recs[hllk].former_names[0].name == "HALLMARK VENTURE GROUP, INC."
    assert recs[hllk].name_change_filings[0].filed == date(2026, 7, 16)
    assert recs[brr].recommended == "SVIA"
    by_sym = {c.symbol: c for c in recs[brr].candidates}
    assert by_sym["SVIA"].verified and not by_sym["SVIAW"].verified
    assert "splice-verified SVIA (30 shared bars, x1.000" in describe(recs[brr])
    # the artifact shape
    block = probe.payload()["rename_probe"]
    assert block["evaluated"] is True and block["fetches"] == 3
    row = next(r for r in block["rows"] if r["ticker"] == "HLLK")
    assert row["recommended"] == "SDCO" and row["edgar_tickers"] == ["SDCO"]
    assert row["candidates"][0]["verified"] is True and row["candidates"][0]["overlap"] == 30
    # NOTHING auto-applied: every table the repair could touch is exactly as it was
    assert _counts(db) == before


def test_a_candidate_whose_fetch_404s_is_recorded_and_never_verified(db):
    """A real vendor 404 (the extracted WONDF error) on a candidate: recorded on the row, not verified, the
    probe still evaluated — one candidate's fetch never fails the probe."""
    hllk = fx.insert_security(db, "HLLK")
    stale = StaleTape(
        ticker="HLLK",
        security_id=hllk,
        edge=fx.last_bar("HLLK"),
        renamed=True,
        edgar_tickers=("SDCO",),
    )
    probe = run_rename_probe(
        results=[ThesisRunResult(thesis_id=uuid.uuid4(), name="T", tape_stale=(stale,))],
        asof=_ASOF,
        allow_live=True,
        connect=connect,
        clock=lambda: 0.0,
        source_factory=lambda d: _FakeSource(d, {"SDCO": fx.not_found("SDCO")}, [], []),
    )
    (rec,) = probe.rows
    assert probe.evaluated and rec.recommended is None
    assert rec.candidates[0].error.startswith("HTTPStatusError: Client error '404 Not Found'")
    assert "no candidate splice-verified (SDCO: fetch failed)" == describe(rec)


def test_nothing_to_probe_opens_no_connection_and_makes_no_request():
    """The common night: no renamed tape -> evaluated, empty, and neither the DB nor the vendor touched."""

    def _boom():
        raise AssertionError("connect must not be called")

    probe = run_rename_probe(
        results=[ThesisRunResult(thesis_id=uuid.uuid4(), name="T")],
        asof=_ASOF,
        allow_live=True,
        connect=_boom,
        clock=lambda: 0.0,
        source_factory=lambda d: (_ for _ in ()).throw(AssertionError("no source")),
    )
    assert probe.evaluated and probe.rows == () and probe.fetches == 0


def test_a_no_live_pass_never_probes():
    probe = run_rename_probe(
        results=[], asof=_ASOF, allow_live=False, connect=lambda: None, clock=lambda: 0.0
    )
    assert probe == RenameProbe()  # not evaluated — and never a baseline for anything


def test_a_stored_tape_read_fault_fails_OPEN_with_the_error():
    stale = StaleTape(
        ticker="HLLK",
        security_id=uuid.uuid4(),
        edge=date(2026, 9, 24),
        renamed=True,
        edgar_tickers=("SDCO",),
    )

    def _down():
        raise RuntimeError("server closed the connection")

    probe = run_rename_probe(
        results=[ThesisRunResult(thesis_id=uuid.uuid4(), name="T", tape_stale=(stale,))],
        asof=_ASOF,
        allow_live=True,
        connect=_down,
        clock=lambda: 0.0,
    )
    assert probe.evaluated is False
    assert probe.error == "RuntimeError: server closed the connection"
