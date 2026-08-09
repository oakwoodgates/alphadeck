"""The business-type MASTER seam (S2): ``identity_for``'s derived read + ``set_business_type``.

DB-backed. The load-bearing checks: the derived two-level read rides the identity join (with the
overlay and the verbatim override), the re-tag write is STORE-ON-DIFF (an agreeing pick coerces to
NULL — the exception-marker discipline), idempotency COUNTS THE TABLE (update-in-place, never an
append), and the write seam is tenant-fail-closed + loud on an unknown leaf."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import BusinessSupersector, BusinessType
from securities import master


def _insert(db, ticker, name, sector) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, sector, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _stored(db, sid):
    with db.cursor() as cur:
        cur.execute(
            "SELECT business_type, business_type_basis FROM security_master WHERE id = %s", (sid,)
        )
        row = cur.fetchone()
    return row["business_type"], row["business_type_basis"]


def _rows(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        return cur.fetchone()["n"]


def test_identity_for_derives_the_two_level_read(db):
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    ident = master.identity_for(db, [sid])[sid]
    assert ident["business_type"] is BusinessType.MINER
    assert ident["business_supersector"] is BusinessSupersector.MATERIALS
    assert ident["royalty"] is False
    assert ident["business_type_override"] is None  # no re-tag stored — classified by the maps
    assert ident["instrument_kind"] == "equity"


def test_identity_for_abstains_unclassified_on_an_unenriched_row(db):
    sid = _insert(db, "DARK", "Dark Co", None)
    ident = master.identity_for(db, [sid])[sid]
    assert ident["business_type"] is None
    assert ident["business_supersector"] is None
    assert ident["royalty"] is False


def test_identity_for_lights_the_royalty_overlay_from_the_name(db):
    """The UROY class: a royalty house under an unrelated SIC — the leaf stands, the overlay lights."""
    sid = _insert(db, "UROY", "Uranium Royalty Corp.", "Commodity Contracts Brokers & Dealers")
    ident = master.identity_for(db, [sid])[sid]
    assert ident["business_type"] is BusinessType.FINANCE_BROKERS
    assert ident["royalty"] is True


def test_set_business_type_stores_the_differing_retag_and_identity_folds_it_in(db):
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    assert master.set_business_type(db, sid, "utilities", basis="operator:retag") is True
    db.commit()
    assert _stored(db, sid) == ("utilities", "operator:retag")
    ident = master.identity_for(db, [sid])[sid]
    assert ident["business_type"] is BusinessType.UTILITIES  # the re-tag wins
    assert ident["business_supersector"] is BusinessSupersector.ENERGY_UTILITIES
    assert ident["business_type_override"] == "utilities"  # verbatim, so the FE can mark + revert


def test_set_business_type_coerces_an_agreeing_pick_to_null(db):
    """STORE-ON-DIFF (the price_symbol idiom): picking exactly what the maps derive stores NULL — the
    column stays the honest exception marker; the maps remain the single source for agreement."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    master.set_business_type(db, sid, "miner", basis="operator:retag")
    db.commit()
    assert _stored(db, sid) == (None, "operator:retag")
    assert master.identity_for(db, [sid])[sid]["business_type"] is BusinessType.MINER


def test_set_business_type_none_clears_the_retag(db):
    """The visible revert (WB #1): clearing returns the name to the maps-derived classification."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    master.set_business_type(db, sid, "utilities", basis="operator:retag")
    master.set_business_type(db, sid, None, basis="operator:retag")
    db.commit()
    assert _stored(db, sid) == (None, "operator:retag")
    assert master.identity_for(db, [sid])[sid]["business_type"] is BusinessType.MINER


def test_set_business_type_is_count_the_table_idempotent(db):
    """Re-tagging is an UPDATE-in-place: re-running the same (and a different) write never appends a
    master row — assert the TABLE, not just the read (the bitemporal-dedup lesson)."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    before = _rows(db)
    for leaf in ("utilities", "utilities", "miner", "utilities"):
        master.set_business_type(db, sid, leaf, basis="operator:retag")
        db.commit()
    assert _rows(db) == before


def test_set_business_type_rejects_an_unknown_leaf_loudly(db):
    """The write seam is where out-of-contract values stop — an archetype-era value must raise, not
    store (the read would silently fall through on it; the WRITE must never let it in)."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    with pytest.raises(ValueError):
        master.set_business_type(db, sid, "high_beta", basis="operator:retag")
    assert _stored(db, sid) == (None, None)


def test_set_business_type_is_tenant_fail_closed(db):
    """A foreign/unknown id under this tenant writes NOTHING (the exists/set_price_symbol boundary)."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    assert (
        master.set_business_type(
            db, sid, "utilities", basis="operator:retag", tenant_id=uuid.uuid4()
        )
        is False
    )
    db.commit()
    assert _stored(db, sid) == (None, None)
