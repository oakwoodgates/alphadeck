"""V2 W6b: the calendar list keeps its row ids across saves (``PUT /theses/{id}/catalysts``).

A counting entry (``fact_thesis_catalyst``) is keyed by its calendar row's id, so a save that re-mints every id would
orphan it. A row sent WITH an id that is on this card keeps it; a row sent without one is a new row; an id that is
not on this card (another card's, another workspace's, or made up) is never adopted: the row gets a fresh id.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from dashboards.cache import CACHE
from domain.settings import get_settings
from tests.auth_fixtures import sign_out
from tests.thesis_catalyst_fixtures import card

PASSWORD = "correct horse battery"


def _rows(db, thesis_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, label FROM catalyst WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,)
        )
        return cur.fetchall()


def test_a_save_that_sends_ids_keeps_them(client, db, pinned_today):
    tid, _sids, entry = card(db)
    db.commit()
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[
            {"id": str(entry), "label": "DOE loan (renamed)", "kind": "regulatory"},
            {"label": "a new row"},
        ],
    )
    assert r.status_code == 200, r.text
    got = r.json()["catalysts"]
    assert got[0]["id"] == str(entry) and got[0]["label"] == "DOE loan (renamed)"
    new_id = got[1]["id"]
    assert new_id != str(entry)
    # a second save that sends both ids changes neither
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[{"id": str(entry), "label": "DOE loan (renamed)"}, {"id": new_id, "label": "a new row"}],
    )
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["catalysts"]] == [str(entry), new_id]
    assert [row["id"] for row in _rows(db, tid)] == [entry, uuid.UUID(new_id)]


def test_a_row_without_an_id_is_a_new_row(client, db, pinned_today):
    tid, _sids, entry = card(db)
    db.commit()
    r = client.put(f"/theses/{tid}/catalysts", json=[{"label": "DOE loan"}])
    assert r.status_code == 200, r.text
    (only,) = r.json()["catalysts"]
    assert only["id"] != str(entry)  # no id sent: the old row (nothing counts on it) is replaced


def test_duplicate_ids_are_refused(client, db, pinned_today):
    tid, _sids, entry = card(db)
    db.commit()
    before = _rows(db, tid)
    r = client.put(
        f"/theses/{tid}/catalysts",
        json=[{"id": str(entry), "label": "a"}, {"id": str(entry), "label": "b"}],
    )
    assert r.status_code == 422, r.text
    assert "more than once" in r.json()["detail"]
    assert _rows(db, tid) == before


def test_an_id_from_another_card_is_never_adopted(client, db, pinned_today):
    t1, _s1, e1 = card(db)
    t2, _s2, _e2 = card(db)
    db.commit()
    made_up = uuid.uuid4()
    r = client.put(
        f"/theses/{t2}/catalysts",
        json=[{"id": str(e1), "label": "stolen"}, {"id": str(made_up), "label": "made up"}],
    )
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json()["catalysts"]]
    assert str(e1) not in ids and str(made_up) not in ids
    # the other card's row is untouched
    assert _rows(db, t1) == [{"id": e1, "label": "DOE loan"}]


def _workspace(db, label: str) -> uuid.UUID:
    wid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, label)
        )
    return wid


def _signed_in(email: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def test_another_workspaces_row_id_is_never_adopted_and_its_card_is_404(
    db, pinned_today, monkeypatch
):
    """Two workspaces, two real sign-ins: B cannot write A's list (404, as a missing card) and B sending A's row id
    on B's own card gets a fresh id; A's row is untouched."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    CACHE.clear()
    try:
        w_a, w_b = _workspace(db, "ids A"), _workspace(db, "ids B")
        auth.create_user(db, email="a@ids.test", display_name="A", password=PASSWORD, workspace_id=w_a)
        auth.create_user(db, email="b@ids.test", display_name="B", password=PASSWORD, workspace_id=w_b)
        ta, _sa, ea = card(db, workspace=w_a)
        tb, _sb, _eb = card(db, workspace=w_b)
        db.commit()
        b = _signed_in("b@ids.test")
        r = b.put(f"/theses/{ta}/catalysts", json=[{"id": str(ea), "label": "x"}])
        assert r.status_code == 404 and r.json()["detail"] == "thesis not found"
        r = b.put(f"/theses/{tb}/catalysts", json=[{"id": str(ea), "label": "B's row"}])
        assert r.status_code == 200, r.text
        assert r.json()["catalysts"][0]["id"] != str(ea)
        assert _rows(db, ta) == [{"id": ea, "label": "DOE loan"}]
    finally:
        app.dependency_overrides.clear()
        auth.reset_throttle()
        get_settings.cache_clear()
        CACHE.clear()
