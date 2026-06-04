from __future__ import annotations

from pathlib import Path

import pytest

from securities import master
from securities.figi import CacheMiss

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_FIGI = _FIXTURES / "figi"
_SEC = _FIXTURES / "sec"


def _resolve(conn, ticker):
    return master.resolve(conn, ticker, figi_cache_dir=_FIGI, sec_cache_dir=_SEC, allow_live=False)


def test_resolve_from_cache_populates_master(db):
    sec = _resolve(db, "AAPL")
    assert sec.ticker == "AAPL"
    assert sec.figi == "BBG000B9XRY4"
    assert sec.cik == "0000320193"  # zero-padded to 10 digits
    assert sec.name
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master WHERE ticker = 'AAPL'")
        assert cur.fetchone()["n"] == 1


def test_resolve_is_idempotent(db):
    a = _resolve(db, "AAPL")
    b = _resolve(db, "aapl")  # case-insensitive; reads back from the master
    assert a.id == b.id
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master WHERE ticker = 'AAPL'")
        assert cur.fetchone()["n"] == 1  # not re-inserted


def test_cache_miss_raises_when_live_disabled(db):
    with pytest.raises(CacheMiss):
        _resolve(db, "ZZZZ")  # no cached fixture and live pulls disabled
