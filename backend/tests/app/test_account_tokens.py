"""``/account/agents`` and ``/account/tokens`` (docs/AUTH.md §The routes): my own access tokens, nobody else's.

Real users and real bearer tokens (no signed-in override): A and B on two workspaces, each signed in with a
session cookie; the tokens are written through ``app.auth`` like the CLI's. The 401 sweep in
``tests/app/test_auth_gate.py`` picks the four routes up from the schema by itself. DB-backed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.settings import get_settings
from tests.auth_fixtures import sign_out
from tests.workspaces import new_workspace

PASSWORD = "correct horse battery"
SYSTEM = "system-token-for-the-account-tokens-suite"


@pytest.fixture
def two(db, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", SYSTEM)
    monkeypatch.delenv("ALPHADECK_PUBLIC_ORIGIN", raising=False)
    get_settings.cache_clear()
    ids = {}
    for key, role in (("a", "user"), ("b", "user"), ("s", "super_admin")):
        ids[key] = auth.create_user(
            db,
            email=f"{key}@tokens.test",
            display_name=key.upper(),
            password=PASSWORD,
            role=role,
            workspace_id=new_workspace(db, f"tokens {key}"),
        )
    db.commit()
    clients = {}
    for key in ids:
        c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        assert (
            c.post(
                "/auth/login", json={"email": f"{key}@tokens.test", "password": PASSWORD}
            ).status_code
            == 200
        )
        clients[key] = c
    yield {"ids": ids, **clients}
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


def _bare() -> TestClient:
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def _me(token: str):
    return _bare().get("/auth/me", headers={"authorization": f"Bearer {token}"})


def test_create_shows_the_token_once_stores_only_its_hash_and_it_signs_in(two, db):
    r = two["a"].post("/account/tokens", json={"name": "  laptop  "})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "laptop" and body["token"] and body["created_at"]
    with db.cursor() as cur:
        cur.execute("SELECT name, token_hash, user_id FROM api_token WHERE id = %s", (body["id"],))
        row = cur.fetchone()
    assert row["token_hash"] == auth.token_hash(body["token"]) != body["token"]
    assert row["user_id"] == two["ids"]["a"]
    me = _me(body["token"])
    assert me.status_code == 200 and me.json()["email"] == "a@tokens.test"
    # the list never carries the token again
    listed = two["a"].get("/account/tokens")
    assert listed.status_code == 200
    assert body["token"] not in listed.text
    assert [t["name"] for t in listed.json()["tokens"]] == ["laptop"]


def test_each_user_lists_only_their_own_live_first(two):
    a, b = two["a"], two["b"]
    first = a.post("/account/tokens", json={"name": "one"}).json()
    a.post("/account/tokens", json={"name": "two"})
    b.post("/account/tokens", json={"name": "b's"})
    assert a.delete(f"/account/tokens/{first['id']}").status_code == 204
    tokens = a.get("/account/tokens").json()["tokens"]
    assert [(t["name"], t["revoked_at"] is not None) for t in tokens] == [
        ("two", False),
        ("one", True),
    ]
    assert [t["name"] for t in b.get("/account/tokens").json()["tokens"]] == ["b's"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_a_bad_name_is_400(two, name):
    r = two["a"].post("/account/tokens", json={"name": name})
    assert r.status_code == 400
    assert "1 to 60" in r.json()["detail"]


def test_sixty_characters_is_fine(two):
    assert two["a"].post("/account/tokens", json={"name": "x" * 60}).status_code == 201


def test_the_twenty_first_live_token_is_409_and_a_revoke_makes_room(two):
    a = two["a"]
    made = [a.post("/account/tokens", json={"name": f"t{i}"}) for i in range(20)]
    assert all(r.status_code == 201 for r in made)
    r = a.post("/account/tokens", json={"name": "t20"})
    assert r.status_code == 409
    assert r.json()["detail"] == "too many tokens; revoke one first"
    assert a.delete(f"/account/tokens/{made[0].json()['id']}").status_code == 204
    assert a.post("/account/tokens", json={"name": "t20"}).status_code == 201


def test_revoke_own_is_204_idempotent_and_the_token_stops_working(two):
    a = two["a"]
    made = a.post("/account/tokens", json={"name": "x"}).json()
    assert _me(made["token"]).status_code == 200
    assert a.delete(f"/account/tokens/{made['id']}").status_code == 204
    assert _me(made["token"]).status_code == 401
    assert a.delete(f"/account/tokens/{made['id']}").status_code == 204  # already revoked


def test_a_token_may_revoke_itself(two):
    made = two["a"].post("/account/tokens", json={"name": "self"}).json()
    bearer = {"authorization": f"Bearer {made['token']}"}
    assert _bare().delete(f"/account/tokens/{made['id']}", headers=bearer).status_code == 204
    assert _me(made["token"]).status_code == 401


def test_another_users_token_and_an_unknown_id_are_404_alike(two):
    b_token = two["b"].post("/account/tokens", json={"name": "b"}).json()
    r = two["a"].delete(f"/account/tokens/{b_token['id']}")
    assert r.status_code == 404 and r.json()["detail"] == "no such token"
    assert _me(b_token["token"]).status_code == 200  # B's token still works
    unknown = two["a"].delete("/account/tokens/00000000-0000-0000-0000-0000000000ff")
    assert unknown.status_code == 404 and unknown.json() == r.json()


def test_the_system_principal_is_403_on_all_four(two):
    bearer = {"authorization": f"Bearer {SYSTEM}"}
    c = _bare()
    for method, path, body in (
        ("GET", "/account/agents", None),
        ("GET", "/account/tokens", None),
        ("POST", "/account/tokens", {"name": "x"}),
        ("DELETE", "/account/tokens/00000000-0000-0000-0000-0000000000ff", None),
    ):
        r = c.request(method, path, json=body, headers=bearer)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"


def test_agents_unhosted(two):
    r = two["a"].get("/account/agents")
    assert r.status_code == 200
    assert r.json() == {
        "hosted": False,
        "connector_url": None,
        "steward": False,
        "stdio_server": True,
        "oauth": False,
    }
    assert two["s"].get("/account/agents").json()["steward"] is True


def test_agents_hosted(two, monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("ALPHADECK_PUBLIC_ORIGIN", "https://deck.example.com/")
    get_settings.cache_clear()
    assert two["a"].get("/account/agents").json() == {
        "hosted": True,
        "connector_url": "https://deck.example.com/mcp",
        "steward": False,
        "stdio_server": True,
        "oauth": False,  # the AS is mounted only at boot (app.state.oauth): not in this test
    }
