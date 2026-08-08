from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.migrate import apply_migrations
from db.session import DEFAULT_TENANT_ID
from domain.enums import InstrumentKind
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


def test_primary_flag_gaps_reports_unflagged_multi_row_ciks(db):
    """The canonical-primary health check: a tenant whose master has multi-row CIKs but ZERO is_primary
    flags is the silent degraded state (ids_for_ciks falls back to an arbitrary sibling — the live 10,366-
    rows/0-flags finding). The gap reports it; stamping ANY primary clears the zero-flags condition; a
    master with no multi-row CIKs reports nothing at all (no flags needed — honest loudness)."""
    _insert(db, ticker="ASML", cik="0000937966")
    _insert(db, ticker="ASMLF", cik="0000937966")  # the multi-sibling CIK, both unflagged
    _insert(db, ticker="HIMS", cik="0001773751")  # single-row CIK — needs no flag
    gaps = master.primary_flag_gaps(db)
    assert gaps == [
        {"tenant_id": DEFAULT_TENANT_ID, "multi_row_ciks": 1, "flagged_rows": 0}
    ]  # the broken state, named

    with db.cursor() as cur:  # stamp a primary (what a populate_master re-run does) -> gap clears
        cur.execute(
            "UPDATE security_master SET is_primary = true WHERE ticker = 'ASML'",
        )
    db.commit()
    (gap,) = master.primary_flag_gaps(db)
    assert gap["flagged_rows"] == 1  # flagged_rows > 0 -> the daily guard stays quiet


def test_primary_flag_gaps_empty_when_no_multi_row_ciks(db):
    """Single-instrument-only master: nothing to rank, nothing reported (the guard never nags a state that
    needs no flags)."""
    _insert(db, ticker="HIMS", cik="0001773751")
    assert master.primary_flag_gaps(db) == []


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


def test_enrich_persists_origin_ingredients_and_reads_back_count_the_table(db):
    """The 0028 origin ingredients (raw locators for the derive-on-read chip) round-trip: enrich writes them,
    ``get``/``_row_to_security`` reads them back — the NIO shape (country NULL stays NULL: stored as-said,
    never invented). Re-enrich UPDATEs in place — COUNT THE TABLE before/after, never just the read.
    """
    sid = _insert(db, ticker="NIO", name="NIO Inc.", cik="0001736541")
    ident = SecurityIdentity(
        sector="Motor Vehicles",
        status="active",
        incorporation="Cayman Islands",
        business_city="SHANGHAI",
        business_country=None,
        files_foreign_forms=True,
    )
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]
    assert master.enrich(db, sid, ident, source="submissions:CIK0001736541") is True
    master.enrich(db, sid, ident, source="submissions:CIK0001736541")  # re-run: in place, no append
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # UPDATE-in-place, never appended
    sec = master.get(db, sid)
    assert (
        sec.incorporation,
        sec.business_city,
        sec.business_country,
        sec.files_foreign_forms,
    ) == (
        "Cayman Islands",
        "SHANGHAI",  # stored RAW — display normalization happens at derive time, not in the row
        None,
        True,
    )
    # an un-enriched row abstains on all four (the honest fallback the chip renders nothing from)
    bare = master.get(db, _insert(db, ticker="RAWX", name="Raw Co", cik="0000000042"))
    assert (
        bare.incorporation,
        bare.business_city,
        bare.business_country,
        bare.files_foreign_forms,
    ) == (
        None,
        None,
        None,
        None,
    )


def test_enrich_persists_filer_form_ingredients_and_reads_back(db):
    """The 0031 filer-form ingredients (raw inputs to the derive-on-read foreign-filer tell) round-trip:
    enrich writes ``files_domestic_forms`` / ``recent_foreign_form``, ``get``/``_row_to_security`` reads
    them back. The Cameco (CCJ) shape: a 40-F on file, no domestic forms. An un-enriched row abstains (both
    NULL — the honest fallback the tell derives ``None`` from)."""
    sid = _insert(db, ticker="CCJ", name="Cameco Corp", cik="0001009001")
    master.enrich(
        db,
        sid,
        SecurityIdentity(
            sector="Uranium",
            status="active",
            files_foreign_forms=True,
            files_domestic_forms=False,
            recent_foreign_form="40-F",
        ),
        source="submissions:CIK0001009001",
    )
    db.commit()
    sec = master.get(db, sid)
    assert (sec.files_domestic_forms, sec.recent_foreign_form) == (False, "40-F")
    # an un-enriched row abstains on both (NULL -> the tell derives None)
    bare = master.get(db, _insert(db, ticker="RAWZ", name="Raw Co", cik="0000000043"))
    assert (bare.files_domestic_forms, bare.recent_foreign_form) == (None, None)


# --- identity_for: the scored view's display join + the DERIVED origin (the identity-lifecycle read) ---


def test_identity_for_derives_origin_from_stored_ingredients(db):
    """``identity_for`` derives ``origin`` on read from the stored 0028 ingredients (the same
    ``resolve_origin`` ladder the draft path uses): the NIO shape (country NULL, city "SHANGHAI" -> the
    city rung, display-normalized "Shanghai"), the US shape (a state abbreviation -> "US"), and an
    un-enriched row ABSTAINS to ``None`` — never a guessed origin. The RAW ingredients stay OFF the
    returned identity (derive-on-read: only the display string travels)."""
    nio = _insert(db, ticker="NIO", name="NIO Inc.", cik="0001736541")
    aapl = _insert(db, ticker="AAPL", name="Apple Inc.", cik="0000320193")
    bare = _insert(db, ticker="RAWX", name="Raw Co", cik="0000000042")  # never enriched
    master.enrich(
        db,
        nio,
        SecurityIdentity(
            sector="Motor Vehicles",
            status="active",
            incorporation="Cayman Islands",
            business_city="SHANGHAI",
            business_country=None,
            files_foreign_forms=True,
        ),
        source="submissions:CIK0001736541",
    )
    master.enrich(
        db,
        aapl,
        SecurityIdentity(
            sector="Electronic Computers",
            status="active",
            incorporation="CA",
            business_city="Cupertino",
            business_country="CA",  # the SEC quirk: US filers carry the STATE abbrev here
            files_foreign_forms=False,
        ),
        source="submissions:CIK0000320193",
    )
    db.commit()

    out = master.identity_for(db, [nio, aapl, bare])
    assert (
        out[nio]["origin"] == "Shanghai"
    )  # country NULL -> the city rung, title-cased for display
    assert out[aapl]["origin"] == "US"  # a US-state abbreviation reads "US"
    assert out[bare]["origin"] is None  # un-enriched -> honest abstain (no chip)
    # derive-on-read discipline: the raw locator ingredients never leave the accessor — only the
    # derived display strings (plus the existing identity strings) ride toward the wire
    assert set(out[nio].keys()) == {
        "name",
        "sector",
        "exchange",
        "category",
        "origin",
        "foreign_filer_form",
        "price_symbol",
    }


def test_identity_for_still_carries_the_enrichment_strings(db):
    """The pre-existing identity strings (name / sector / exchange / category) are unchanged by the origin
    extension — the same join, one more derived field."""
    sid = _insert(db, ticker="OKLO", name="Oklo Inc.", cik="0001849056")
    master.enrich(
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
    out = master.identity_for(db, [sid])
    assert out[sid] == {
        "name": "Oklo Inc.",
        "sector": "Electric Services",
        "exchange": "NYSE",
        "category": "Large accelerated filer",
        "origin": None,  # no locator ingredients stored -> the ladder abstains
        "foreign_filer_form": None,  # no filer-form ingredients stored -> the tell abstains
        "price_symbol": None,  # no resolved symbol -> priced under the canonical ticker
    }


def test_identity_for_derives_foreign_filer_form_with_the_domestic_veto(db):
    """``identity_for`` derives ``foreign_filer_form`` on read from the stored 0031 ingredients (the same
    discipline as origin): the Cameco (CCJ) shape fires "40-F"; the Energy-Fuels (UUUU) shape — a legacy
    40-F BUT recent domestic forms — is VETOED to ``None`` (the key false-positive kill); a plain domestic
    filer and an un-enriched row both abstain. The RAW ingredients stay OFF the returned identity.
    """
    ccj = _insert(db, ticker="CCJ", name="Cameco Corp", cik="0001009001")
    uuuu = _insert(db, ticker="UUUU", name="Energy Fuels Inc", cik="0001385849")
    uec = _insert(db, ticker="UEC", name="Uranium Energy Corp", cik="0001334933")
    bare = _insert(db, ticker="RAWZ", name="Raw Co", cik="0000000043")  # never enriched
    master.enrich(
        db,
        ccj,
        SecurityIdentity(status="active", files_domestic_forms=False, recent_foreign_form="40-F"),
        source="submissions:CIK0001009001",
    )
    master.enrich(
        db,
        uuuu,
        # the veto shape: a 40-F is on file, but so are recent 10-K/10-Q filings -> DOES file Form 4
        SecurityIdentity(status="active", files_domestic_forms=True, recent_foreign_form="40-F"),
        source="submissions:CIK0001385849",
    )
    master.enrich(
        db,
        uec,
        SecurityIdentity(status="active", files_domestic_forms=True, recent_foreign_form=None),
        source="submissions:CIK0001334933",
    )
    db.commit()

    out = master.identity_for(db, [ccj, uuuu, uec, bare])
    assert out[ccj]["foreign_filer_form"] == "40-F"  # foreign + no domestic forms -> fires
    assert out[uuuu]["foreign_filer_form"] is None  # THE VETO: 40-F present but domestic-vetoed
    assert out[uec]["foreign_filer_form"] is None  # no foreign form at all
    assert out[bare]["foreign_filer_form"] is None  # un-enriched -> honest abstain
    # derive-on-read discipline: the raw ingredients never leave the accessor, only the derived string
    assert "recent_foreign_form" not in out[ccj]
    assert "files_domestic_forms" not in out[ccj]


# --- all_cik_primary_ids: the --universe scope resolver (identity lifecycle) ---


def test_all_cik_primary_ids_picks_the_primary_sibling_and_skips_cikless(db):
    """The universe-wide CIK->id map resolves each CIK to its CANONICAL row (the ``ids_for_ciks`` pick:
    ``is_primary`` first), so a multi-sibling CIK enriches the instrument the operator trades — and a
    CIK-less row (an OpenFIGI-era insert / a fund) simply isn't in the map (no submissions doc exists).
    """
    asml = _insert(db, ticker="ASML", cik="0000937966")
    _insert(db, ticker="ASMLF", cik="0000937966")  # the OTC foreign ordinary — must NOT be picked
    hims = _insert(db, ticker="HIMS", cik="0001773751")
    _insert(db, ticker="LIT", cik=None)  # CIK-less: nothing to parse, not in the map
    with db.cursor() as cur:
        cur.execute("UPDATE security_master SET is_primary = true WHERE id = %s", (asml,))
    db.commit()
    assert master.all_cik_primary_ids(db) == {"0000937966": asml, "0001773751": hims}


def test_all_cik_primary_ids_is_tenant_scoped(db):
    """Tenant isolation (#5): the map covers ONLY the asked tenant's rows — another tenant's CIK never
    leaks into a universe scope (a leaked id would let the enrich write under the wrong tenant)."""
    from pipeline.provision_tenant import provision_tenant

    other = uuid.UUID("00000000-0000-0000-0000-0000000000ad")
    provision_tenant(db, "other", tenant_id=other)
    db.commit()
    mine = _insert(db, ticker="OKLO", cik="0001849056")
    _insert(db, ticker="FRGN", cik="0009999999", tenant_id=other)
    assert master.all_cik_primary_ids(db) == {"0001849056": mine}
    out_other = master.all_cik_primary_ids(db, tenant_id=other)
    assert list(out_other.keys()) == ["0009999999"]


# --- instrument_kind: the ETF-sleeve foundation brick (migration 0026 + resolve/mark, Slice 1) ---


def test_instrument_kind_migration_defaults_equity_and_is_idempotent(db):
    """The 0026 column is present, NOT NULL, DEFAULT 'equity' — an existing/legacy row (inserted without the
    column) reads back the default, never NULL; and re-applying migrations is a no-op (schema_migrations
    tracks 0026 as done; the SQL is ADD COLUMN IF NOT EXISTS belt-and-braces too)."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'security_master' AND column_name = 'instrument_kind'"
        )
        col = cur.fetchone()
    assert col is not None  # the column exists
    assert col["is_nullable"] == "NO"  # NOT NULL
    assert "equity" in col["column_default"]  # DEFAULT 'equity'::text

    # a row inserted WITHOUT naming instrument_kind takes the default (the no-backfill path — every
    # pre-migration row stays 'equity')
    sid = _insert(db, ticker="HIMS", cik="0001773751")
    assert master.get(db, sid).instrument_kind is InstrumentKind.EQUITY

    # re-apply migrations: idempotent — 0026 is already recorded, so nothing re-runs and nothing errors
    assert "0026_master_instrument_kind.sql" not in apply_migrations(db)
    assert master.get(db, sid).instrument_kind is InstrumentKind.EQUITY  # unchanged


def test_resolve_marks_new_row_etf_with_null_cik(db):
    """The surface-ETF CREATE path: a thematic ETF absent from SEC (LIT — not in the SEC fixture, so
    ``cik_for`` returns None) is INSERTED marked 'etf' with cik=None (fine for a price-only sleeve); OpenFIGI
    (the LIT fixture) supplies name + figi. Count-the-table idempotent: a re-resolve reads the row back, never
    re-inserts, and it stays 'etf'."""
    sec = master.resolve(
        db,
        "LIT",
        figi_cache_dir=_FIGI,
        sec_cache_dir=_SEC,
        allow_live=False,
        instrument_kind=InstrumentKind.ETF,
    )
    assert sec.instrument_kind is InstrumentKind.ETF
    assert (
        sec.cik is None
    )  # a fund-trust series, not an operating-company CIK — SEC-absent is expected
    assert sec.figi == "BBG000QN0N15" and sec.name  # OpenFIGI named the row
    # the stored row round-trips 'etf' through the read path (_row_to_security)
    assert master.get(db, sec.id).instrument_kind is InstrumentKind.ETF
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master WHERE ticker = 'LIT'")
        assert cur.fetchone()["n"] == 1

    again = master.resolve(
        db,
        "LIT",
        figi_cache_dir=_FIGI,
        sec_cache_dir=_SEC,
        allow_live=False,
        instrument_kind=InstrumentKind.ETF,
    )
    assert again.id == sec.id  # read back, not re-inserted
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master WHERE ticker = 'LIT'")
        assert cur.fetchone()["n"] == 1  # count-the-table: no duplicate append


def test_resolve_default_kind_is_equity(db):
    """resolve() with no instrument_kind arg inserts the default 'equity' — the equity path is unchanged by
    the new parameter (AAPL is a normal operating company)."""
    sec = master.resolve(db, "AAPL", figi_cache_dir=_FIGI, sec_cache_dir=_SEC, allow_live=False)
    assert sec.instrument_kind is InstrumentKind.EQUITY
    assert master.get(db, sec.id).instrument_kind is InstrumentKind.EQUITY


def test_mark_instrument_kind_flips_existing_row_in_place(db):
    """The surface-ETF HIT path: a ticker already present as an equity (SPY/GLD — the few mega-ETFs in the
    master) is flipped to 'etf' UPDATE-in-place — the id is stable, the row COUNT never grows (count the
    table, not the read), and a re-mark is idempotent."""
    sid = _insert(db, ticker="SPY", name="SPDR S&P 500 ETF Trust", cik="0000884394")
    assert master.get(db, sid).instrument_kind is InstrumentKind.EQUITY  # starts at the default
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]

    assert master.mark_instrument_kind(db, sid, InstrumentKind.ETF) is True
    db.commit()
    assert master.get(db, sid).instrument_kind is InstrumentKind.ETF  # flipped
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # UPDATE-in-place, never appended

    # idempotent re-mark (still one row, still etf)
    assert master.mark_instrument_kind(db, sid, InstrumentKind.ETF) is True
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before


def test_mark_instrument_kind_unknown_id_updates_nothing(db):
    """A foreign/unknown id under this tenant updates nothing (fail-closed, the same write-side boundary as
    ``exists`` / ``enrich``) — never conjures a row."""
    assert master.mark_instrument_kind(db, uuid.uuid4(), InstrumentKind.ETF) is False


# --- price_symbol: the resolved vendor-symbol writer + column (migration 0032, the OTC fix) ---


def test_price_symbol_column_defaults_null_and_reads_back(db):
    """The 0032 columns are additive + nullable — a row inserted without naming them reads back NULL
    (the healthy "priced under the canonical ticker" default), never a guessed value."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'security_master' AND column_name = 'price_symbol'"
        )
        assert cur.fetchone()["is_nullable"] == "YES"
    sid = _insert(db, ticker="FDCT", name="First Digital Corp", cik="0001111111")
    sec = master.get(db, sid)
    assert sec.price_symbol is None and sec.price_symbol_basis is None


def test_set_price_symbol_stores_and_is_update_in_place_count_the_table(db):
    """The writer stamps the resolved symbol UPDATE-in-place (the ``mark_instrument_kind`` pattern): the id
    is stable, the row COUNT never grows (count the table, not the read), and a re-write of the same value
    is idempotent. NEVER touches the SEC ticker — it stays canon."""
    sid = _insert(db, ticker="FDCT", name="First Digital Corp", cik="0001111111")
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        before = cur.fetchone()["n"]

    assert master.set_price_symbol(db, sid, "FDCTD", basis="resolver:auto 251/16") is True
    db.commit()
    sec = master.get(db, sid)
    assert sec.price_symbol == "FDCTD" and sec.price_symbol_basis == "resolver:auto 251/16"
    assert sec.ticker == "FDCT"  # the SEC form is untouched — canon

    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # UPDATE-in-place, never appended

    assert master.set_price_symbol(db, sid, "FDCTD", basis="resolver:auto 251/16") is True
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        assert cur.fetchone()["n"] == before  # idempotent re-write — still one row


def test_set_price_symbol_coerces_a_self_match_to_null(db):
    """A symbol equal to the canonical ticker is NOT an exception — it coerces to NULL (the healthy state),
    so the column stays the honest exception marker (never a redundant "priced under FDCT" on FDCT).
    """
    sid = _insert(db, ticker="FDCT", name="First Digital Corp", cik="0001111111")
    assert (
        master.set_price_symbol(db, sid, "fdct", basis="operator:adopt") is True
    )  # case-insensitive
    db.commit()
    assert master.get(db, sid).price_symbol is None


def test_set_price_symbol_clears_with_none(db):
    """``None`` clears a prior resolution (the correction path) — back to the canonical-ticker default."""
    sid = _insert(db, ticker="FDCT", name="First Digital Corp", cik="0001111111")
    master.set_price_symbol(db, sid, "FDCTD", basis="resolver:auto")
    db.commit()
    assert master.set_price_symbol(db, sid, None, basis="operator:clear") is True
    db.commit()
    assert master.get(db, sid).price_symbol is None


def test_set_price_symbol_unknown_id_updates_nothing(db):
    """A foreign/unknown id updates nothing (fail-closed, the same write-side boundary as the other writers)."""
    assert master.set_price_symbol(db, uuid.uuid4(), "FDCTD", basis="x") is False


def test_identity_for_carries_the_resolved_price_symbol(db):
    """``identity_for`` carries ``price_symbol`` verbatim beside origin / foreign_filer_form — the exception
    symbol when set, ``None`` when priced under the canonical ticker (the FE renders the note only when set).
    """
    fdct = _insert(db, ticker="FDCT", name="First Digital Corp", cik="0001111111")
    hims = _insert(db, ticker="HIMS", name="Hims & Hers Health, Inc.", cik="0001773751")
    master.set_price_symbol(db, fdct, "FDCTD", basis="resolver:auto")
    db.commit()
    out = master.identity_for(db, [fdct, hims])
    assert out[fdct]["price_symbol"] == "FDCTD"  # the exception symbol travels
    assert out[hims]["price_symbol"] is None  # priced under the canonical ticker
