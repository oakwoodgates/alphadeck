"""The stale-tape machinery AFTER a company-list rename (2026-10-24): renamed-not-closed, the pin, the auto-adopt.

1. PURE — the classifier calls a stop RENAMED, not CLOSED, when SEC's ticker map lists the CIK under another
   ticker even with a delisting form at the last bar and an empty EDGAR list (the PSKY -> SKYD shape); and a
   tape PINNED to its old ticker counts only the pin as "ours".
2. PURE — the strict auto-adopt rule (>= 20 shared bars, x1.000).
3. DB — ``run_auto_adopt`` adopts SEC's new ticker for a pinned name through the existing repair log, and never
   for anything else (a manual symbol, a weak splice, an undone auto-adopt, a candidate that is not the ticker).
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

from db.session import DEFAULT_TENANT_ID, connect
from domain.price_feed_basis import BASIS_AUTO_ADOPT, BASIS_RENAME_PIN
from ingest.edgar.submissions import DelistingFiling
from pipeline.ingest_thesis import NameResult
from pipeline.rename_probe import (
    AUTO_ADOPT_MIN_OVERLAP,
    CandidateProbe,
    RenameProbe,
    RenameRecommendation,
    auto_adoptable,
    run_auto_adopt,
)
from pipeline.tape_health import classify_stop, stale_tapes
from repositories import price_feed_repo

_ASOF = date(2026, 10, 23)
_EDGE = date(2026, 10, 10)
_FORM25 = DelistingFiling(form="25-NSE", filed=date(2026, 10, 12), accession="0000000000-26-000025")


# --- 1. renamed, not closed --------------------------------------------------------------------------------------


def test_PSKY_shape_SEC_map_lists_the_CIK_under_a_new_ticker_so_the_stop_is_RENAMED_not_CLOSED():
    """A Form 25 for the old ticker two days after the last bar, EDGAR's own list EMPTY — the old rule read this
    CLOSED (quiet, never paged). SEC's current ticker map lists the CIK under SKYD: it is a rename.
    """
    closed_before = classify_stop(
        edge=_EDGE,
        filings=[_FORM25],
        edgar_tickers=(),
        ticker="PSKY",
        price_symbol=None,
        asof=_ASOF,
    )
    assert closed_before.closed_at == _FORM25.filed  # the misread, reproduced without the map
    c = classify_stop(
        edge=_EDGE,
        filings=[_FORM25],
        edgar_tickers=(),
        ticker="PSKY",
        price_symbol=None,
        asof=_ASOF,
        sec_map_tickers=("SKYD",),
    )
    assert c.renamed is True and c.closed_at is None
    assert c.edgar_tickers == ("SKYD",)  # the probe splices the map's ticker


def test_the_map_listing_OUR_ticker_still_lets_a_real_close_read_closed():
    c = classify_stop(
        edge=_EDGE,
        filings=[_FORM25],
        edgar_tickers=(),
        ticker="PSKY",
        price_symbol=None,
        asof=_ASOF,
        sec_map_tickers=("PSKY",),
    )
    assert c.closed_at == _FORM25.filed and c.renamed is False
    gone = classify_stop(
        edge=_EDGE,
        filings=[_FORM25],
        edgar_tickers=(),
        ticker="PSKY",
        price_symbol=None,
        asof=_ASOF,
        sec_map_tickers=(),  # the map lists NOTHING for the CIK: delisted, not renamed
    )
    assert gone.closed_at == _FORM25.filed


def test_a_PINNED_tape_counts_only_the_pin_as_ours():
    """After the refresh renamed PSKY -> SKYD in place and pinned the price leg to PSKY: the master ticker is
    SKYD (which EDGAR lists) but the leg reads PSKY. Pinned, the stopped tape is a RENAME to splice; the same
    row read without the pin flag would hide behind the ticker match and could read CLOSED."""
    kw = dict(
        edge=_EDGE,
        filings=[_FORM25],
        edgar_tickers=("SKYD",),
        ticker="SKYD",
        price_symbol="PSKY",
        asof=_ASOF,
    )
    assert classify_stop(**kw).renamed is False  # unpinned reading: the ticker is listed
    pinned = classify_stop(**kw, rename_pinned=True)
    assert pinned.renamed is True and pinned.edgar_tickers == ("SKYD",)


def test_stale_tapes_threads_the_map_and_the_pin_from_the_ingest_result():
    sid = uuid.uuid4()
    r = NameResult(
        "SKYD",
        sid,
        0,
        0,
        tape_edge=_EDGE,
        delisting_filings=(_FORM25,),
        edgar_tickers=(),
        price_symbol="PSKY",
        price_symbol_basis=BASIS_RENAME_PIN,
        sec_map_tickers=("SKYD",),
    )
    [s] = stale_tapes([r], asof=_ASOF, stale_days=5)
    assert s.renamed is True and not s.closed and s.edgar_tickers == ("SKYD",)


# --- 2. the strict rule ------------------------------------------------------------------------------------------


def test_auto_adoptable_needs_20_shared_bars_and_x1_000():
    ok = CandidateProbe(symbol="SKYD", verified=True, overlap=20, ratio=1.0002)
    assert auto_adoptable(ok)
    assert not auto_adoptable(CandidateProbe(symbol="S", verified=True, overlap=19, ratio=1.0))
    assert not auto_adoptable(CandidateProbe(symbol="S", verified=True, overlap=30, ratio=0.999))
    assert not auto_adoptable(CandidateProbe(symbol="S", verified=True, overlap=30, ratio=1.0006))
    assert not auto_adoptable(CandidateProbe(symbol="S", overlap=30, ratio=1.0, error="404"))
    assert AUTO_ADOPT_MIN_OVERLAP == 20


# --- 3. the auto-adopt -----------------------------------------------------------------------------------------


def _master_row(db, *, ticker, price_symbol=None, basis=None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, cik, ticker, name, is_primary, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, TRUE, %s)",
            (sid, DEFAULT_TENANT_ID, f"{uuid.uuid4().int % 10**9:010d}", ticker, "Co", _EDGE),
        )
    db.commit()
    if price_symbol is not None:
        price_feed_repo.apply_change(db, sid, action="set", symbol=price_symbol, basis=basis)
        db.commit()
    return sid


def _probe(sid, ticker, *, symbol, overlap=30, ratio=1.0):
    cand = CandidateProbe(symbol=symbol, verified=True, overlap=overlap, ratio=ratio, bars=60)
    rec = RenameRecommendation(
        security_id=sid, ticker=ticker, edge=_EDGE, edgar_tickers=(symbol,), candidates=(cand,)
    )
    return RenameProbe(evaluated=True, rows=(rec,))


def _pinned(*sids):
    """Tonight's ingest results claiming each name is priced under the refresh's pin (the master is re-read)."""
    rows = [NameResult("X", s, 0, 0, price_symbol_basis=BASIS_RENAME_PIN) for s in sids]
    return [SimpleNamespace(ingested=rows)]


def _state(db, sid):
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT ticker, price_symbol, price_symbol_basis FROM security_master WHERE id = %s",
            (sid,),
        )
        return tuple(cur.fetchone().values())


def test_a_pinned_name_whose_probe_splice_verifies_the_new_ticker_is_ADOPTED_automatically(db):
    sid = _master_row(db, ticker="SKYD", price_symbol="PSKY", basis=BASIS_RENAME_PIN)
    out = run_auto_adopt(
        _probe(sid, "SKYD", symbol="SKYD"), results=_pinned(sid), allow_live=True, connect=connect
    )
    [a] = out.auto_adopted
    assert a.error is None and a.symbol == "SKYD" and a.from_symbol == "PSKY"
    assert _state(db, sid) == ("SKYD", None, BASIS_AUTO_ADOPT)  # priced under the ticker now
    change = price_feed_repo.standing_change_for(db, sid)
    assert (
        change.id == a.change_id and change.action == "adopt" and change.basis == BASIS_AUTO_ADOPT
    )
    assert "adopted SKYD automatically" in a.describe()
    assert out.payload()["rename_probe"]["auto_adopted"][0]["symbol"] == "SKYD"


def test_an_UNDONE_auto_adopt_is_never_re_applied(db):
    sid = _master_row(db, ticker="SKYD", price_symbol="PSKY", basis=BASIS_RENAME_PIN)
    [a] = run_auto_adopt(
        _probe(sid, "SKYD", symbol="SKYD"), results=_pinned(sid), allow_live=True, connect=connect
    ).auto_adopted
    price_feed_repo.undo_change(db, a.change_id, basis="operator:undo")
    db.commit()
    assert _state(db, sid) == ("SKYD", "PSKY", BASIS_RENAME_PIN)  # the pin is back
    again = run_auto_adopt(
        _probe(sid, "SKYD", symbol="SKYD"), results=_pinned(sid), allow_live=True, connect=connect
    )
    assert again.auto_adopted == ()  # the operator's undo stands
    assert _state(db, sid) == ("SKYD", "PSKY", BASIS_RENAME_PIN)


def test_nothing_else_is_ever_adopted_on_its_own(db):
    manual = _master_row(db, ticker="BTLN", price_symbol="BTLNX", basis="operator:set")
    weak = _master_row(db, ticker="DRK", price_symbol="ANY", basis=BASIS_RENAME_PIN)
    other = _master_row(db, ticker="VIP", price_symbol="GREE", basis=BASIS_RENAME_PIN)
    unpinned = _master_row(db, ticker="CNSY")
    before = {s: _state(db, s) for s in (manual, weak, other, unpinned)}
    n = price_feed_repo.standing_changes(db)
    probe = RenameProbe(
        evaluated=True,
        rows=(
            *_probe(manual, "BTLN", symbol="BTLN").rows,  # a manual price symbol: never touched
            *_probe(
                weak, "DRK", symbol="DRK", overlap=12
            ).rows,  # verified (>=5) but < 20 shared bars
            *_probe(
                other, "VIP", symbol="VIPW"
            ).rows,  # the verified candidate is not the SEC ticker
            *_probe(unpinned, "CNSY", symbol="CNSY").rows,  # no pin at all
        ),
    )
    # every result CLAIMS a pin: the master re-read is what keeps each of these from being adopted
    out = run_auto_adopt(
        probe,
        results=_pinned(manual, weak, other, unpinned),
        allow_live=True,
        connect=connect,
    )
    assert out.auto_adopted == () and out.auto_adopt_error is None
    assert {s: _state(db, s) for s in before} == before
    assert len(price_feed_repo.standing_changes(db)) == len(n)


def test_a_no_live_pass_adopts_nothing_and_opens_no_connection():
    def boom():
        raise AssertionError("no connection on a --no-live pass")

    sid = uuid.uuid4()
    probe = _probe(sid, "SKYD", symbol="SKYD")
    assert run_auto_adopt(probe, results=_pinned(sid), allow_live=False, connect=boom) is probe
    empty = RenameProbe(evaluated=True)
    assert run_auto_adopt(empty, results=_pinned(sid), allow_live=True, connect=boom) is empty
    # the COMMON night: renamed rows, none pinned -> the probe comes back unchanged, no connection opened
    assert run_auto_adopt(probe, results=[], allow_live=True, connect=boom) is probe
