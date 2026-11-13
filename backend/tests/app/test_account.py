"""``/account`` — the Settings page's API (docs/AUTH.md §Settings page): profile edits and the user's own Slack webhook.

Every client here signs in through the REAL cookie flow (``POST /auth/login``) as a real ``app_user`` on its own
workspace (``tests.workspaces.new_workspace`` + ``auth.create_user``), without the suite's signed-in override, so
the route sees exactly the caller the gate resolves. Outbound Slack POSTs are captured by patching ``httpx.post``.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.settings import get_settings
from repositories import user_meta_repo
from tests.auth_fixtures import sign_out
from tests.workspaces import new_workspace

PASSWORD = "correct horse battery"
WEBHOOK = "/account/notifications/slack-webhook"
SLACK_URL = "https://hooks.slack.com/services/TEST0000/TEST0000/invented-for-tests"
OTHER_SLACK_URL = "https://hooks.slack.com/services/TEST1111/TEST1111/invented-for-tests"
_WS_PREFIX = "account-test "


@pytest.fixture
def anon(db):
    """No signed-in override; the test's connection; clean auth tables before and after."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    _wipe(db)
    auth.reset_throttle()
    get_settings.cache_clear()
    yield db
    app.dependency_overrides.clear()
    db.rollback()
    _wipe(db)
    auth.reset_throttle()
    get_settings.cache_clear()


def _wipe(db) -> None:
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # user_meta, sessions and tokens go with it
        cur.execute("DELETE FROM tenant WHERE name LIKE %s", (_WS_PREFIX + "%",))
    db.commit()


def _user(db, email: str, name: str = "Test user") -> UUID:
    ws = new_workspace(db, _WS_PREFIX + email)
    uid = auth.create_user(db, email=email, display_name=name, password=PASSWORD, workspace_id=ws)
    db.commit()
    return uid


def _client(db, email: str, name: str = "Test user") -> TestClient:
    _user(db, email, name)
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _capture_post(monkeypatch, *, status: int = 200, raises: BaseException | None = None):
    calls: list[dict] = []

    def fake_post(url, **kw):
        calls.append({"url": url, "json": kw.get("json"), "timeout": kw.get("timeout")})
        if raises is not None:
            raise raises
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


# --- the profile ---------------------------------------------------------------------------------------------


def test_a_profile_update_round_trips_into_auth_me(anon):
    c = _client(anon, "ana@example.test", "Ana")
    r = c.put(
        "/account/profile", json={"display_name": "  Ana Reyes ", "email": " ana.r@example.test "}
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Ana Reyes" and r.json()["email"] == "ana.r@example.test"
    me = c.get("/auth/me").json()  # the same session keeps working after the email change
    assert me["display_name"] == "Ana Reyes" and me["email"] == "ana.r@example.test"
    # the new email is the login key now
    c2 = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert (
        c2.post(
            "/auth/login", json={"email": "ANA.R@example.test", "password": PASSWORD}
        ).status_code
        == 200
    )


def test_another_users_email_answers_409_case_insensitively(anon):
    _user(anon, "bo@example.test", "Bo")
    c = _client(anon, "cy@example.test", "Cy")
    r = c.put("/account/profile", json={"display_name": "Cy", "email": "BO@Example.TEST"})
    assert r.status_code == 409 and r.json()["detail"] == "that email is already in use"
    assert c.get("/auth/me").json()["email"] == "cy@example.test"  # nothing changed


def test_changing_only_the_case_of_my_own_email_is_allowed(anon):
    c = _client(anon, "dee@example.test", "Dee")
    r = c.put("/account/profile", json={"display_name": "Dee", "email": "Dee@Example.test"})
    assert r.status_code == 200 and r.json()["email"] == "Dee@Example.test"


@pytest.mark.parametrize(
    "body, detail",
    [
        ({"display_name": "   ", "email": "eve@example.test"}, "a display name is required"),
        ({"display_name": "Eve", "email": "not-an-email"}, "an email address is required"),
        ({"display_name": "Eve", "email": "  "}, "an email address is required"),
    ],
)
def test_the_validation_rules_answer_400(anon, body, detail):
    c = _client(anon, "eve@example.test", "Eve")
    r = c.put("/account/profile", json=body)
    assert r.status_code == 400 and r.json()["detail"] == detail
    assert c.get("/auth/me").json()["display_name"] == "Eve"


# --- the Slack webhook ---------------------------------------------------------------------------------------


def test_the_webhook_is_unset_by_default_and_never_returned(anon):
    c = _client(anon, "fay@example.test")
    assert c.get(WEBHOOK).json() == {"set": False, "updated_at": None}
    assert c.put(WEBHOOK, json={"url": SLACK_URL}).status_code == 204
    got = c.get(WEBHOOK)
    assert got.json()["set"] is True and got.json()["updated_at"] is not None
    assert SLACK_URL not in got.text and "hooks.slack.com" not in got.text
    assert "hooks.slack.com" not in c.get("/auth/me").text


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.slack.com/services/TEST/TEST/x",  # not https
        "https://hooks.example.test/services/TEST/TEST/x",  # not Slack's host
        "https://hooks.slack.com.example.test/services/x",  # a look-alike host
        "https://hooks.slack.com/",  # the prefix alone
        "junk",
        "",
    ],
)
def test_put_refuses_anything_but_a_slack_webhook(anon, url):
    c = _client(anon, "gus@example.test")
    r = c.put(WEBHOOK, json={"url": url})
    assert r.status_code == 400
    assert c.get(WEBHOOK).json()["set"] is False


def test_put_replaces_and_stamps_updated_at(anon):
    c = _client(anon, "hal@example.test")
    uid = auth.find_user_by_email(anon, "hal@example.test")["id"]
    c.put(WEBHOOK, json={"url": SLACK_URL})
    first = user_meta_repo.get(anon, uid)
    c.put(WEBHOOK, json={"url": OTHER_SLACK_URL})
    second = user_meta_repo.get(anon, uid)
    anon.commit()
    assert first.slack_webhook_url == SLACK_URL and second.slack_webhook_url == OTHER_SLACK_URL
    assert second.updated_at >= first.updated_at


def test_delete_clears_keeps_the_row_and_is_a_noop_when_unset(anon):
    c = _client(anon, "ivy@example.test")
    uid = auth.find_user_by_email(anon, "ivy@example.test")["id"]
    assert c.delete(WEBHOOK).status_code == 204  # nothing set: a no-op
    assert user_meta_repo.get(anon, uid) is None
    c.put(WEBHOOK, json={"url": SLACK_URL})
    assert c.delete(WEBHOOK).status_code == 204
    assert c.get(WEBHOOK).json() == {"set": False, "updated_at": None}
    row = user_meta_repo.get(anon, uid)
    anon.commit()
    assert row is not None and row.slack_webhook_url is None  # the row stays


def test_one_users_webhook_never_touches_another_users(anon):
    a = _client(anon, "jo@example.test")
    b = _client(anon, "kit@example.test")
    assert a.put(WEBHOOK, json={"url": SLACK_URL}).status_code == 204
    assert b.get(WEBHOOK).json() == {"set": False, "updated_at": None}
    uid_b = auth.find_user_by_email(anon, "kit@example.test")["id"]
    assert user_meta_repo.get(anon, uid_b) is None
    b.put(WEBHOOK, json={"url": OTHER_SLACK_URL})
    b.delete(WEBHOOK)
    uid_a = auth.find_user_by_email(anon, "jo@example.test")["id"]
    assert user_meta_repo.get(anon, uid_a).slack_webhook_url == SLACK_URL
    anon.commit()


# --- the test message ----------------------------------------------------------------------------------------


def test_the_test_message_answers_404_when_no_webhook_is_saved(anon, monkeypatch):
    calls = _capture_post(monkeypatch)
    c = _client(anon, "lu@example.test")
    assert c.post(WEBHOOK + "/test").status_code == 404
    c.put(WEBHOOK, json={"url": SLACK_URL})
    c.delete(WEBHOOK)
    assert c.post(WEBHOOK + "/test").status_code == 404
    assert calls == []


def test_the_test_message_posts_one_plain_line_to_my_url(anon, monkeypatch):
    calls = _capture_post(monkeypatch)
    c = _client(anon, "mo@example.test", "Mo Test")
    c.put(WEBHOOK, json={"url": SLACK_URL})
    r = c.post(WEBHOOK + "/test")
    assert r.status_code == 200 and r.json()["delivered"] is True
    assert SLACK_URL not in r.text
    assert calls == [
        {
            "url": SLACK_URL,
            "json": {"text": "Alpha Deck test message for Mo Test"},
            "timeout": 10.0,
        }
    ]


def test_the_test_message_carries_a_hostile_display_name_inert(anon, monkeypatch):
    """Security PR 4a: the display name is user-authored, so Slack markup in it arrives escaped and a newline in
    it cannot start a line of its own."""
    calls = _capture_post(monkeypatch)
    c = _client(anon, "hx@example.test", "<!channel> <https://evil.example|click> & co\nFORGED")
    c.put(WEBHOOK, json={"url": SLACK_URL})
    assert c.post(WEBHOOK + "/test").status_code == 200
    assert [call["json"]["text"] for call in calls] == [
        "Alpha Deck test message for "
        "&lt;!channel&gt; &lt;https://evil.example|click&gt; &amp; co FORGED"
    ]


def test_a_5xx_is_not_delivered(anon, monkeypatch):
    _capture_post(monkeypatch, status=500)
    c = _client(anon, "ned@example.test")
    c.put(WEBHOOK, json={"url": SLACK_URL})
    r = c.post(WEBHOOK + "/test")
    assert r.status_code == 200
    assert r.json() == {"delivered": False, "detail": "Slack answered 500"}


def test_a_network_error_is_not_delivered_and_never_leaks_the_url(anon, monkeypatch):
    _capture_post(monkeypatch, raises=httpx.ConnectError(f"cannot connect to {SLACK_URL}"))
    c = _client(anon, "oz@example.test")
    c.put(WEBHOOK, json={"url": SLACK_URL})
    r = c.post(WEBHOOK + "/test")
    assert r.status_code == 200
    assert r.json() == {"delivered": False, "detail": "could not reach Slack (ConnectError)"}
    assert "hooks.slack.com" not in r.text


# --- the system principal ------------------------------------------------------------------------------------


def test_the_system_token_cannot_reach_the_account_routes(anon, monkeypatch):
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "sys-token-for-tests")
    get_settings.cache_clear()
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    h = {"Authorization": "Bearer sys-token-for-tests"}
    assert c.get(WEBHOOK, headers=h).status_code == 403
    assert (
        c.put(
            "/account/profile", headers=h, json={"display_name": "x", "email": "x@example.test"}
        ).status_code
        == 403
    )
    assert c.put(WEBHOOK, headers=h, json={"url": SLACK_URL}).status_code == 403
