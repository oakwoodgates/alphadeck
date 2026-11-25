"""T2 — the rename recommender's SPLICE PROBE, on REAL prod data (``tests/fixtures/prices/
price_feed_repairs_2026_10.json``): HLLK's stored tape against SDCO, and BRR's against SVIA (its common
stock) and SVIAW (its warrants). The probe must verify the true successors, reject the mismatched class,
make ONE vendor request per candidate into a throwaway cache, and write NOTHING (a pending recommendation —
the operator's Adopt applies it)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path

from db.session import connect
from domain.price_feed_basis import BASIS_RENAME_PIN
from pipeline.daily import ThesisRunResult
from pipeline.ingest_thesis import NameResult
from pipeline.rename_probe import (
    VERIFY_MIN_OVERLAP,
    RenameProbe,
    describe,
    describe_unpriced,
    judge,
    listing_class,
    run_auto_adopt,
    run_rename_probe,
    splice,
    unpriced_rows,
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


# --- W4-1: NEVER-PRICED names — the probe over EDGAR's OTHER symbols, with the listing-class mark -------
# The shape (MEASURED on prod 2026-11-24, named here by neutral labels per the fixture convention): an ADR
# member EDGAR files as "<name>/ADR" whose symbol 404s at the vendor every night, while EDGAR's list for the
# same CIK also carries the company's foreign ORDINARY line (a five-letter OTC symbol ending F). The member is
# not renamed (our ticker is in EDGAR's list) and has no stored bar, so it is unpriceable — and before W4-1
# nothing ever looked at the other symbol. The vendor bars below are SYNTHETIC (a fake ``PriceSource`` serves
# them; no vendor and no SEC request is made): what is under test is which symbols are fetched, how each is
# marked and what is recommended, never a price.

_ADR_NAME = "SYNTHETIC METALS PLC/ADR"


def _bars(last: date, n: int = 30) -> list[dict]:
    """``n`` synthetic daily bars ending ``last`` (the ``PriceSource`` shape)."""
    return [
        {
            "d": last - timedelta(days=i),
            "open": None,
            "high": None,
            "low": None,
            "close": 10.0,
            "volume": 1000,
        }
        for i in reversed(range(n))
    ]


def _never_priced(sid, ticker, edgar, *, acknowledged=False):
    return StaleTape(
        ticker=ticker, security_id=sid, edge=None, edgar_tickers=edgar, acknowledged=acknowledged
    )


def _ingested(sid, ticker, *, edgar_name=None, price_symbol=None, basis=None):
    return NameResult(
        ticker=ticker,
        security_id=sid,
        form4_appended=0,
        price_bars_appended=0,
        edgar_name=edgar_name,
        price_symbol=price_symbol,
        price_symbol_basis=basis,
    )


def test_listing_class_reads_the_ADR_name_first_then_the_OTC_fifth_letter():
    """The class mark is a CONVENTION, never proof: an EDGAR entity name ending "/ADR" marks the filer's
    ADR; otherwise a five-letter alphabetic OTC symbol ending Y reads ADR and one ending F a foreign
    ordinary. Anything else is unknown (``None``) — never guessed."""
    assert listing_class("TKAAY") == "adr"
    assert listing_class("TKAAF") == "ordinary"
    assert listing_class("tkaaf ") == "ordinary"  # the stored symbols are upper-case; be lenient
    assert listing_class("TKAAY", _ADR_NAME) == "adr"
    assert listing_class("TKAA", _ADR_NAME) == "adr"  # the name marks the member's own class
    assert listing_class("TKAA", "SYNTHETIC METALS PLC /ADR/") == "adr"  # EDGAR's other spelling
    for unknown in ("TKAA", "SDCO", "TKAAN", "TKA1Y", "BRK-B", "WNDR.TO", "", None):
        assert listing_class(unknown) is None, unknown
    assert (
        listing_class("TKAA", "ADRIAN SYNTHETIC INC") is None
    )  # "ADR" inside a word is not the mark


def test_unpriced_rows_are_the_never_priced_unacknowledged_names_EDGAR_lists_another_symbol_for():
    """Which rows the probe takes beside the renamed ones: a stopped PRICE row that is unpriceable, not
    acknowledged, and whose EDGAR list holds a symbol other than our ticker / price symbol — deduped by
    security, each paired with its ingest result (the EDGAR name rides it)."""
    aa, bb, cc, dd, ee, ff, gg = (uuid.uuid4() for _ in range(7))
    results = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T1",
            ingested=[
                _ingested(aa, "TKAAY", edgar_name=_ADR_NAME),
                _ingested(gg, "TKGG", price_symbol="TKGGX"),
            ],
            tape_stale=(
                _never_priced(aa, "TKAAY", ("TKAAY", "TKAAF")),  # taken
                _never_priced(bb, "TKBB", ()),  # EDGAR lists nothing: nothing to probe
                _never_priced(cc, "TKCC", ("TKCC",)),  # only our own ticker
                _never_priced(dd, "TKDDY", ("TKDDY", "TKDDF"), acknowledged=True),  # kept unpriced
                StaleTape("TKEE", ee, None, renamed=True, edgar_tickers=("TKEEN",)),  # renamed's
                StaleTape("TKFF", ff, date(2026, 11, 2), edgar_tickers=("TKFF", "TKFFF")),  # priced
                _never_priced(gg, "TKGG", ("TKGG", "TKGGX")),  # only our ticker + price symbol
            ),
        ),
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T2",
            tape_stale=(_never_priced(aa, "TKAAY", ("TKAAY", "TKAAF")),),  # the same name again
        ),
    ]

    rows = unpriced_rows(results)

    assert [(s.security_id, s.ticker) for s, _ in rows] == [(aa, "TKAAY")]
    assert rows[0][1].edgar_name == _ADR_NAME


def _boom():
    raise AssertionError("connect must not be called: an unpriced row has no stored tape to read")


def test_a_never_priced_ADR_member_is_probed_ONCE_per_symbol_and_the_ordinary_line_is_never_recommended():
    """End to end over fakes: two ADR members (one marked by its EDGAR "/ADR" name, one by its fifth letter
    alone) held by two theses, and an acknowledged one. Exactly one request per distinct OTHER symbol, live
    and force-refreshed into the throwaway dir; the acknowledged name and our own 404ing tickers are never
    fetched; no stored tape is read (no connection). Each candidate carries its bars and its class mark,
    and a foreign ORDINARY line is never recommended for an ADR member, however good its tape."""
    aa, bb, dd = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    asof = date(2026, 11, 24)
    results = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="Synthetic Minerals",
            ingested=[
                _ingested(aa, "TKAAY", edgar_name=_ADR_NAME),
                _ingested(dd, "TKDDY", edgar_name="SYNTHETIC KEPT PLC/ADR"),
            ],
            tape_stale=(
                _never_priced(aa, "TKAAY", ("TKAAY", "TKAAF")),
                _never_priced(dd, "TKDDY", ("TKDDY", "TKDDF"), acknowledged=True),
            ),
        ),
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="Synthetic Metals",
            ingested=[_ingested(bb, "TKBBY"), _ingested(aa, "TKAAY", edgar_name=_ADR_NAME)],
            tape_stale=(
                _never_priced(bb, "TKBBY", ("TKBBY", "TKBBF")),
                _never_priced(aa, "TKAAY", ("TKAAY", "TKAAF")),
            ),
        ),
    ]
    served = {"TKAAF": _bars(asof - timedelta(days=1)), "TKBBF": _bars(asof)}
    calls: list = []
    dirs: list = []

    probe = run_rename_probe(
        results=results,
        asof=asof,
        allow_live=True,
        connect=_boom,
        clock=lambda: 0.0,
        source_factory=lambda d: _FakeSource(d, served, calls, dirs),
        stale_days=5,
    )

    assert probe.evaluated and probe.error is None and probe.rows == ()
    assert sorted(c[0] for c in calls) == ["TKAAF", "TKBBF"] and probe.fetches == 2
    assert all(live and force for _, live, force in calls)
    assert len(dirs) == 1 and not dirs[0].exists()
    recs = probe.unpriced_by_security()
    assert set(recs) == {aa, bb}  # TKAAY once though two theses hold it; TKDDY never (acknowledged)
    a = recs[aa]
    assert (a.ticker, a.member_class, a.edgar_name) == ("TKAAY", "adr", _ADR_NAME)
    assert a.edgar_tickers == ("TKAAY", "TKAAF")
    (c,) = a.candidates  # our own 404ing ticker is not a candidate
    assert (c.symbol, c.bars, c.last_bar, c.error) == ("TKAAF", 30, asof - timedelta(days=1), None)
    assert c.listing_class == "ordinary"
    assert a.recommended is None  # cross-class: never recommended
    assert recs[bb].member_class == "adr"  # no EDGAR name: the fifth letter marks it
    assert recs[bb].recommended is None
    assert "cross-class" in describe_unpriced(a) and "TKAAF" in describe_unpriced(a)
    # the artifact shape: a block BESIDE `rows`, each candidate in the probe-candidate shape + its mark
    block = probe.payload()["rename_probe"]
    assert block["rows"] == [] and block["fetches"] == 2
    row = next(r for r in block["unpriced"] if r["ticker"] == "TKAAY")
    assert row == {
        "security_id": str(aa),
        "ticker": "TKAAY",
        "edgar_tickers": ["TKAAY", "TKAAF"],
        "edgar_name": _ADR_NAME,
        "member_class": "adr",
        "recommended": None,
        "candidates": [
            {
                "symbol": "TKAAF",
                "verified": False,
                "overlap": 0,
                "ratio": None,
                "bars": 30,
                "last_bar": (asof - timedelta(days=1)).isoformat(),
                "error": None,
                "listing_class": "ordinary",
            }
        ],
    }


def test_a_SAME_class_candidate_with_a_fresh_tape_is_recommended_and_a_stale_or_failed_one_is_not():
    """``recommended`` = the first candidate in EDGAR's order with bars, no fetch error, a last bar within
    ``stale_days`` of the as-of, and the member's own class mark (both unknown counts as the same).
    """
    hh, kk, mm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    asof = date(2026, 11, 24)
    results = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="T",
            ingested=[_ingested(hh, "TKHHY", edgar_name="SYNTHETIC HOLDINGS LTD/ADR")],
            tape_stale=(
                # an ADR member: the ordinary line is skipped, the second ADR line recommended
                _never_priced(hh, "TKHHY", ("TKHHY", "TKHHF", "TKHIY")),
                # both unknown: the stale and the failed candidates are skipped, nothing recommended
                _never_priced(kk, "TKKK", ("TKKK", "TKKKN", "TKKKM")),
                # both unknown, a fresh tape: recommended
                _never_priced(mm, "TKMM", ("TKMM", "TKMMN")),
            ),
        )
    ]
    served = {
        "TKHHF": _bars(asof),
        "TKHIY": _bars(asof - timedelta(days=2)),
        "TKKKN": _bars(asof - timedelta(days=5)),  # 5 days behind: stale under stale_days=5
        "TKKKM": fx.not_found("TKKKM"),
        "TKMMN": _bars(asof - timedelta(days=4)),  # 4 days behind: fresh
    }

    probe = run_rename_probe(
        results=results,
        asof=asof,
        allow_live=True,
        connect=_boom,
        clock=lambda: 0.0,
        source_factory=lambda d: _FakeSource(d, served, [], []),
        stale_days=5,
    )

    recs = probe.unpriced_by_security()
    assert recs[hh].recommended == "TKHIY"
    assert [c.listing_class for c in recs[hh].candidates] == ["ordinary", "adr"]
    assert recs[kk].recommended is None
    failed = next(c for c in recs[kk].candidates if c.symbol == "TKKKM")
    assert failed.error.startswith("HTTPStatusError: Client error '404 Not Found'")
    assert failed.bars == 0 and failed.last_bar is None
    assert recs[mm].recommended == "TKMMN" and recs[mm].member_class is None
    assert "TKMMN" in describe_unpriced(recs[mm]) and "Adopt" in describe_unpriced(recs[mm])
    assert "fetch failed" in describe_unpriced(recs[kk])


def test_the_renamed_rows_and_the_auto_adopt_are_UNCHANGED_by_the_unpriced_probe(db):
    """W4-1 adds a list; it changes nothing the rename probe already did. The renamed rows' artifact JSON
    is byte-identical with and without a never-priced row beside them (no class key rides a renamed
    candidate), and the auto-adopt still reads only the renamed rows: a pinned never-priced name opens no
    connection and adopts nothing."""
    hllk = fx.insert_security(db, "HLLK")
    aa = uuid.uuid4()
    renamed_only = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="Modern Defense",
            ingested=[_name(hllk, "HLLK")],
            tape_stale=(_stale_renamed(hllk, "HLLK"),),
        )
    ]
    with_unpriced = [
        *renamed_only,
        ThesisRunResult(
            thesis_id=uuid.uuid4(),
            name="Synthetic Minerals",
            ingested=[_ingested(aa, "TKAAY", edgar_name=_ADR_NAME, basis=BASIS_RENAME_PIN)],
            tape_stale=(_never_priced(aa, "TKAAY", ("TKAAY", "TKAAF")),),
        ),
    ]
    served = {"SDCO": fx.probe_bars("HLLK", "SDCO"), "TKAAF": _bars(_ASOF)}

    def _run(results):
        return run_rename_probe(
            results=results,
            asof=_ASOF,
            allow_live=True,
            connect=connect,
            clock=lambda: 0.0,
            source_factory=lambda d: _FakeSource(d, served, [], []),
        )

    before = _run(renamed_only).payload()["rename_probe"]
    after = _run(with_unpriced)
    assert after.payload()["rename_probe"]["rows"] == before["rows"]
    assert "listing_class" not in before["rows"][0]["candidates"][0]
    assert before["unpriced"] == [] and len(after.unpriced) == 1

    only_unpriced = run_rename_probe(
        results=with_unpriced[1:],
        asof=_ASOF,
        allow_live=True,
        connect=_boom,
        clock=lambda: 0.0,
        source_factory=lambda d: _FakeSource(d, served, [], []),
    )
    assert len(only_unpriced.unpriced) == 1
    assert (
        run_auto_adopt(only_unpriced, results=with_unpriced[1:], allow_live=True, connect=_boom)
        is only_unpriced
    )
