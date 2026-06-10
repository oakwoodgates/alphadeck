from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from securities import master
from securities.figi import CacheMiss

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_FIGI = _FIXTURES / "figi"
_SEC = _FIXTURES / "sec"


def _insert(db, *, ticker, name=None, cik=None, tenant_id=DEFAULT_TENANT_ID, recorded_at=None):
    """Insert one master row directly (the `db` fixture truncates security_master)."""
    sid = uuid.uuid4()
    cols = "id, tenant_id, ticker, name, cik, valid_from"
    vals = [sid, tenant_id, ticker, name, cik, date(2026, 1, 1)]
    if recorded_at is not None:
        cols += ", recorded_at"
        vals.append(recorded_at)
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO security_master ({cols}) VALUES ({', '.join(['%s'] * len(vals))})",
            vals,
        )
    db.commit()
    return sid


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


# --- search: the Workbench add-a-name discovery net (Slice 4b) ---


def test_search_finds_by_ticker_or_name_substring(db):
    oklo = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    leu = _insert(db, ticker="LEU", name="Centrus Energy Corp.")
    assert [s.id for s in master.search(db, "OK")] == [oklo]  # ticker substring
    assert [s.id for s in master.search(db, "centrus")] == [leu]  # name substring, case-insensitive
    hit = master.search(db, "OKLO")[0]
    assert (hit.ticker, hit.name, hit.cik) == ("OKLO", "Oklo Inc.", "0001849056")


def test_search_no_match_is_empty_and_read_only(db):
    """An unknown name resolves to nothing — never guessed, never ingested (INVARIANT #2). The search is
    read-only: no master row is conjured into existence (unlike resolve's allow_live ingest path).
    """
    _insert(db, ticker="OKLO", name="Oklo Inc.")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]
    assert master.search(db, "NOTAREALNAME") == []
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before


def test_search_returns_latest_row_per_ticker(db):
    """A name correction appends a new row for the same ticker; search dedups to one — the latest-recorded
    (the same latest-wins read the rest of the master uses)."""
    _insert(
        db, ticker="OKLO", name="Old Name", recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    _insert(
        db, ticker="OKLO", name="Oklo Inc.", recorded_at=datetime(2026, 6, 1, tzinfo=timezone.utc)
    )
    assert [h.name for h in master.search(db, "OKLO")] == ["Oklo Inc."]
