from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.scopes import DATA_TENANT_ID
from domain.thesis import BasketMember, Segment, Thesis
from domain.trading_days import closure_name
from repositories import decisions_repo, thesis_repo

_ASOF = date(2026, 6, 1)


def _knowable_on(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _seed_bars(db, security_id: uuid.UUID, n: int, end: date = _ASOF) -> None:
    days: list[date] = []
    d = end
    while len(days) < n:
        if closure_name(d) is None:
            days.append(d)
        d -= timedelta(days=1)
    for i, day in enumerate(reversed(days)):
        append_fact(
            db,
            "fact_price_eod",
            {
                "tenant_id": DATA_TENANT_ID,
                "security_id": security_id,
                "d": day,
                "close": 10.0 + i * 0.1,
                "volume": 1000.0,
                "valid_from": day,
                "recorded_at": _knowable_on(day),
            },
        )
    db.commit()


def _create_thesis(
    db,
    *,
    name: str,
    basket: list[BasketMember],
    segments: list[Segment] | None = None,
    workspace: uuid.UUID = DEFAULT_TENANT_ID,
    archived: bool = False,
) -> Thesis:
    t = Thesis(
        id=uuid.uuid4(),
        tenant_id=workspace,
        name=name,
        narrative=f"Narrative for {name}",
        basket=basket,
        segments=segments or [],
    )
    thesis_repo.upsert(db, t)
    if archived:
        thesis_repo.set_archived(db, t.id, True)
    db.commit()
    return t


def test_member_panel_resolves_ticker_and_security_id(client, db, security_id):
    _seed_bars(db, security_id, 60)
    t = _create_thesis(
        db,
        name="Alpha Card",
        basket=[BasketMember(ticker="DEVCO", role="core", security_id=security_id)],
    )
    # Log one decision on this member and one thesis-level decision (no security_id):
    # only the member's own decision should return in `decisions`.
    decisions_repo.append(
        db,
        thesis_id=t.id,
        tenant_id=DEFAULT_TENANT_ID,
        action="take",
        decision_date=_ASOF,
        security_id=security_id,
        shares=100.0,
        price=12.5,
        reason="starter",
    )
    decisions_repo.append(
        db,
        thesis_id=t.id,
        tenant_id=DEFAULT_TENANT_ID,
        action="pass",
        decision_date=_ASOF,
        security_id=None,
        reason="thesis-level pass",
    )
    db.commit()

    # Case-insensitive ticker match
    r_tk = client.get(
        f"/theses/{t.id}/member-panel",
        params={"name": "devco", "asof": _ASOF.isoformat()},
    )
    assert r_tk.status_code == 200, r_tk.text
    body = r_tk.json()
    assert body["thesis_id"] == str(t.id)
    assert body["thesis_name"] == "Alpha Card"
    assert body["asof"] == _ASOF.isoformat()
    assert body["member"]["ticker"] == "DEVCO"
    assert body["member"]["security_id"] == str(security_id)
    assert body["scored"]["security_id"] == str(security_id)
    assert body["display"]["security_id"] == str(security_id)
    assert len(body["display"]["signals"]) > 0
    assert body["shared_values"]["security_id"] == str(security_id)
    assert len(body["decisions"]) == 1
    assert body["decisions"][0]["security_id"] == str(security_id)
    assert body["position"]["security_id"] == str(security_id)
    assert body["cards"] == [
        {"thesis_id": str(t.id), "thesis_name": "Alpha Card", "ticker": "DEVCO", "archived": False}
    ]

    # Exact security_id match returns the same member
    r_sid = client.get(
        f"/theses/{t.id}/member-panel",
        params={"name": str(security_id), "asof": _ASOF.isoformat()},
    )
    assert r_sid.status_code == 200, r_sid.text
    assert r_sid.json()["member"]["security_id"] == str(security_id)


def test_member_panel_404_for_missing_member_or_thesis(client, db, security_id):
    t = _create_thesis(
        db,
        name="Single Card",
        basket=[BasketMember(ticker="DEVCO", role="core", security_id=security_id)],
    )
    r_missing = client.get(
        f"/theses/{t.id}/member-panel",
        params={"name": "NOPE", "asof": _ASOF.isoformat()},
    )
    assert r_missing.status_code == 404

    r_thesis = client.get(
        f"/theses/{uuid.uuid4()}/member-panel",
        params={"name": "DEVCO", "asof": _ASOF.isoformat()},
    )
    assert r_thesis.status_code == 404


def test_member_panel_unresolved_member_degrades_honestly(client, db):
    t = _create_thesis(
        db,
        name="Unresolved Card",
        basket=[BasketMember(ticker="MYST", role="unlinked", security_id=None)],
    )
    r = client.get(
        f"/theses/{t.id}/member-panel",
        params={"name": "MYST", "asof": _ASOF.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["member"]["ticker"] == "MYST"
    assert body["member"]["security_id"] is None
    assert body["scored"] is None
    assert body["display"] is None
    assert body["data_flags"] is None
    assert body["shared_values"] is None
    assert body["decisions"] == []
    assert body["cards"] == [
        {
            "thesis_id": str(t.id),
            "thesis_name": "Unresolved Card",
            "ticker": "MYST",
            "archived": False,
        }
    ]


def test_member_panel_cards_lists_other_live_cards_in_same_workspace_only(client, db, security_id):
    t1 = _create_thesis(
        db,
        name="Beta Card",
        basket=[BasketMember(ticker="DEVCO", role="a", security_id=security_id)],
    )
    _create_thesis(
        db,
        name="Alpha Card",
        segments=[Segment(label="Upstream"), Segment(label="Downstream")],
        basket=[
            BasketMember(ticker="DEVCO", segment="Upstream", role="a", security_id=security_id),
            BasketMember(ticker="DEVCO", segment="Downstream", role="b", security_id=security_id),
        ],
    )
    t_arch = _create_thesis(
        db,
        name="Archived Card",
        basket=[BasketMember(ticker="DEVCO", role="old", security_id=security_id)],
        archived=True,
    )
    # Another workspace holding the same shared security must NEVER appear in `cards` (invariant #12)
    other_ws = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')",
            (other_ws, "Other Workspace"),
        )
    _create_thesis(
        db,
        name="Secret Other Workspace Card",
        basket=[BasketMember(ticker="DEVCO", role="secret", security_id=security_id)],
        workspace=other_ws,
    )

    # Opening a live card lists both live cards (deduped across segments), excluding archived + other workspace
    r = client.get(
        f"/theses/{t1.id}/member-panel",
        params={"name": "DEVCO", "asof": _ASOF.isoformat()},
    )
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    assert [c["thesis_name"] for c in cards] == ["Alpha Card", "Beta Card"]
    assert all(not c["archived"] for c in cards)

    # Opening an archived card includes itself (archived=True) alongside the live cards
    r_arch = client.get(
        f"/theses/{t_arch.id}/member-panel",
        params={"name": "DEVCO", "asof": _ASOF.isoformat()},
    )
    assert r_arch.status_code == 200, r_arch.text
    arch_cards = r_arch.json()["cards"]
    assert [(c["thesis_name"], c["archived"]) for c in arch_cards] == [
        ("Alpha Card", False),
        ("Beta Card", False),
        ("Archived Card", True),
    ]
