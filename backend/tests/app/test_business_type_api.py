"""The business-type RE-TAG endpoint (S2): POST /workbench/securities/{id}/business-type.

The operator overruling the maps for one name (#10) — and the SCORED wire carrying the two-level
read (the surface every cockpit/workbench view joins). The receipt is the post-write derived state
(#6), null clears (the visible revert, WB #1), an unknown security 404s, an archetype-era value
422s at the schema seam, and the whole sequence is count-the-table clean."""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID


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


def _rows(db) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master")
        return cur.fetchone()["n"]


def _retag(client, sid, leaf):
    return client.post(f"/workbench/securities/{sid}/business-type", json={"business_type": leaf})


def test_retag_overrides_then_null_reverts_to_derived(client, db):
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    before = _rows(db)

    r = _retag(client, sid, "utilities")
    assert r.status_code == 200
    body = r.json()
    assert body["business_type"] == "utilities"  # the effective leaf after the write
    assert body["business_supersector"] == "energy_utilities"
    assert body["business_type_override"] == "utilities"  # a standing exception
    assert body["royalty"] is False

    r = _retag(client, sid, None)  # the visible revert (WB #1)
    assert r.status_code == 200
    body = r.json()
    assert body["business_type"] == "miner"  # back to the maps-derived read
    assert body["business_supersector"] == "materials"
    assert body["business_type_override"] is None

    assert _rows(db) == before  # update-in-place across the whole sequence — never an append


def test_retag_equal_to_derived_coerces_to_no_override(client, db):
    """Picking what the maps already say stores NOTHING (store-on-diff) — the receipt shows the
    effective leaf with no standing exception."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    r = _retag(client, sid, "miner")
    assert r.status_code == 200
    body = r.json()
    assert body["business_type"] == "miner"
    assert body["business_type_override"] is None


def test_retag_unknown_security_404s(client):
    assert _retag(client, uuid.uuid4(), "miner").status_code == 404


def test_retag_rejects_an_archetype_era_value_at_the_schema_seam(client, db):
    """The enum is the contract: a legacy 'high_beta' (or any unknown leaf) is a 422, never a write."""
    sid = _insert(db, "NEM", "Newmont Corp", "Metal Mining")
    assert _retag(client, sid, "high_beta").status_code == 422


def test_scored_wire_carries_the_two_level_read(client, db):
    """The integration proof: a promoted basket's scored view joins the business type — leaf, super,
    overlay, and the null-override marker — off the master identity (never promoted onto the spine).
    """
    sid = _insert(db, "UROY", "Uranium Royalty Corp.", "Commodity Contracts Brokers & Dealers")
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Uranium theme",
            "narrative": "fuel-cycle exposure.",
            "ticker": None,
            "segments": [],
            "basket": [
                {
                    "ticker": "UROY",
                    "role": "royalty house",
                    "security_id": str(sid),
                    "segment": None,
                    "authored_by": "operator_set",
                }
            ],
        },
    )
    assert r.status_code == 200
    tid = r.json()["id"]
    scored = client.get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-01"})
    assert scored.status_code == 200
    (m,) = scored.json()["members"]
    assert m["business_type"] == "finance_brokers"  # the SIC leaf stands...
    assert m["business_supersector"] == "financials"
    assert m["royalty"] is True  # ...and the name-overlay lights beside it
    assert m["business_type_override"] is None
    assert m["instrument_kind"] == "equity"
