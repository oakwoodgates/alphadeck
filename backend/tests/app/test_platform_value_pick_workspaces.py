"""V2 W2a with real logins: the one pick and the state read across TWO workspaces and the data tenant.

A share count is platform data: one value per company for everyone. Two plain users (A on W_A, B on W_B, the
``world`` fixture of ``test_workspace_isolation.py``: real users, real session cookies, none on the data tenant)
each have a card on the same company. The data tenant holds two counts dated the same day, the OLDER write under
the alphabetically FIRST filing URL; workspace A holds a stray count (a V1 leftover) dated later. Both users'
scored reads show the data tenant's NEWER write (D1 (a)), never the stray row, and the same value; the state read
agrees with the meter.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from db.bitemporal import append_fact
from domain.platform_values import ValueState
from domain.scopes import DATA_TENANT_ID
from platform_values.state import read_state
from tests.app.test_workspace_isolation import _promote, world  # noqa: F401 — the shared fixture

FIRST_URL = "https://www.sec.gov/Archives/edgar/data/1/a-older.htm"  # sorts first, written first
SECOND_URL = "https://www.sec.gov/Archives/edgar/data/1/b-newer.htm"  # sorts second, written later
STRAY_URL = "https://www.sec.gov/Archives/edgar/data/1/c-stray.htm"


def _count(db, sid, tenant, url: str, shares: float, valid_from, written: datetime) -> None:
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": tenant,
            "security_id": sid,
            "shares": shares,
            "source": "cover",
            "source_ref": url,
            "valid_from": valid_from,
            "ratified_by": "auto",
            "recorded_at": written,
        },
    )


def _shares_on(client, thesis_id, asof) -> list[tuple[str, float]]:
    r = client.get(f"/workbench/theses/{thesis_id}/scored", params={"asof": asof.isoformat()})
    assert r.status_code == 200, r.text
    (member,) = r.json()["members"]
    return [
        (p["ref"], p["detail"]["shares"])
        for p in member["market_cap"]["provenance"]
        if "shares" in p["detail"]
    ]


def test_both_workspaces_read_the_data_tenants_newer_write(world, db):  # noqa: F811
    sid, asof = world["sid"], world["asof"]
    day = asof - timedelta(days=40)
    t0 = datetime.now(timezone.utc) - timedelta(days=10)
    _count(db, sid, DATA_TENANT_ID, FIRST_URL, 10_000_000, day, t0)
    _count(db, sid, DATA_TENANT_ID, SECOND_URL, 20_000_000, day, t0 + timedelta(hours=1))
    stray_day = day + timedelta(days=5)
    _count(db, sid, world["w_a"], STRAY_URL, 99_000_000, stray_day, t0 + timedelta(hours=2))
    db.commit()

    r = _promote(world["b"], "B's own card", sid)
    assert r.status_code == 200, r.text
    b_id = r.json()["id"]

    seen_by_a = _shares_on(world["a"], world["a_id"], asof)
    seen_by_b = _shares_on(world["b"], b_id, asof)
    assert seen_by_a == seen_by_b == [(SECOND_URL, 20_000_000.0)]

    db.rollback()
    st = read_state(db, sid, "shares_outstanding", asof=asof)
    assert (st.state, st.value["source_ref"], st.value["tenant_id"]) == (
        ValueState.FROM_FILING,
        SECOND_URL,
        DATA_TENANT_ID,
    )
