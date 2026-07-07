from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.security import SecurityIdentity
from ingest import CacheMiss
from securities import master

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


# --- ids_for_ciks: the EDGAR-first discovery resolution (CIK -> security id, the cleanest #2) ---


def test_ids_for_ciks_maps_cik_to_id_and_omits_missing(db):
    oklo = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    leu = _insert(db, ticker="LEU", name="Centrus Energy Corp.", cik="0001065059")
    out = master.ids_for_ciks(db, ["0001849056", "0001065059", "9999999999"])
    assert out == {"0001849056": oklo, "0001065059": leu}  # a CIK with no master row is omitted


def test_ids_for_ciks_pads_unpadded_input(db):
    """A format mismatch would silently match NOTHING (the invisible-failure class) — so an unpadded numeric
    CIK is zero-padded to the master's 10-digit storage. (EFTS already sends the padded form; this guards a
    careless caller.)"""
    oklo = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    assert master.ids_for_ciks(db, ["1849056"]) == {"0001849056": oklo}


def test_ids_for_ciks_latest_row_per_cik(db):
    """One id per CIK — the latest-recorded (a CIK's share classes / name corrections collapse to its primary
    row, the same latest-wins the rest of the master uses)."""
    _insert(
        db,
        ticker="ATAI",
        name="Old Atai",
        cik="0002081043",
        recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    new = _insert(
        db,
        ticker="ATAI",
        name="AtaiBeckley Inc.",
        cik="0002081043",
        recorded_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert master.ids_for_ciks(db, ["0002081043"]) == {"0002081043": new}


def test_ids_for_ciks_empty_and_blank_input(db):
    """No CIKs / blanks -> no query, empty map (blanks are filtered before the lookup). Tenant-scoping is the
    same ``WHERE tenant_id`` pattern as ``ids_for_tickers``/``search``."""
    assert master.ids_for_ciks(db, []) == {}
    assert master.ids_for_ciks(db, ["", None]) == {}


# --- enrich: machine-parsed identity onto the master, UPDATE-in-place (Workbench enrichment, Slice 1) ---


def test_enrich_sets_identity_and_reads_back(db):
    sid = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    updated = master.enrich(
        db,
        sid,
        SecurityIdentity(
            sector="Electric Services",
            exchange="NYSE",
            status="active",
            category="Large accelerated filer",
        ),
        source="submissions:CIK0001849056",
    )
    db.commit()
    assert updated is True
    sec = master.get(db, sid)
    assert (sec.sector, sec.exchange, sec.status, sec.category) == (
        "Electric Services",
        "NYSE",
        "active",
        "Large accelerated filer",  # the filer-category tell round-trips
    )


def test_enrich_is_update_in_place_not_append(db):
    """Re-enrichment UPDATEs in place (the master is identity-mutable) — the row COUNT never grows (count the
    table, not the read), the id is stable (FK'd facts never orphan), and the latest values win — EXCEPT
    ``exchange``, which only FILLS a NULL: the submissions value is COMPANY-level (``exchanges[0]``), while
    the populate path writes the SEC table's PER-INSTRUMENT venue, which is authoritative (the company-level
    overwrite is how the ASMLF foreign ordinary got stamped "Nasdaq" — the canonical-primary slice).
    """
    sid = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]
    master.enrich(
        db, sid, SecurityIdentity(sector="A", exchange="NYSE", status="active"), source="s1"
    )
    master.enrich(
        db, sid, SecurityIdentity(sector="B", exchange="Nasdaq", status="inactive"), source="s2"
    )
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # UPDATE-in-place, never appended
    sec = master.get(db, sid)
    assert (sec.sector, sec.status) == ("B", "inactive")  # latest wins
    assert (
        sec.exchange == "NYSE"
    )  # fill-if-null: the first fill sticks — never clobbered company-level


def test_enrich_unknown_id_updates_nothing(db):
    """A foreign/unknown id under this tenant updates nothing (fail-closed, the same write-side boundary as
    ``exists``) — never conjures a row."""
    assert master.enrich(db, uuid.uuid4(), SecurityIdentity(sector="X"), source="s") is False
