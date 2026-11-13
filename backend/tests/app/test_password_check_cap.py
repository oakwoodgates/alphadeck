"""The cap on concurrent password checks (security PR 5 step 3, plan D7).

Each argon2 check costs ~64 MB and real CPU. At most ``password_check_concurrency`` run at once (sign-in, the
throwaway check for an unknown email, change-password); a check that finds no slot within
``password_check_wait_s`` answers 503 "Sign-in is busy. Try again in a moment." with ``Retry-After``. The test holds
the only slot itself, through the same context manager the checks use. DB-backed (the shared ``db`` fixture).
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.scopes import FOUNDING_WORKSPACE_ID
from domain.settings import get_settings
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
BUSY = "Sign-in is busy. Try again in a moment."


@pytest.fixture
def client(db, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    db.commit()
    auth.reset_throttle()
    monkeypatch.setenv("ALPHADECK_PASSWORD_CHECK_CONCURRENCY", "1")
    monkeypatch.setenv("ALPHADECK_PASSWORD_CHECK_WAIT_S", "0.1")
    get_settings.cache_clear()
    auth.create_user(
        db,
        email="cap@test.invalid",
        display_name="cap",
        password=PASSWORD,
        workspace_id=FOUNDING_WORKSPACE_ID,
    )
    db.commit()
    yield TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


def _login(c: TestClient, email: str = "cap@test.invalid", password: str = PASSWORD):
    return c.post("/auth/login", json={"email": email, "password": password})


def test_a_login_with_every_slot_taken_answers_503_in_time(client):
    client.get("/health")  # warm the app, so the timing below is the wait alone
    with auth.password_check_slot():
        started = time.monotonic()
        r = _login(client)
        took = time.monotonic() - started
    assert r.status_code == 503
    assert r.json()["detail"] == BUSY
    assert r.headers.get("retry-after")
    assert "set-cookie" not in r.headers
    assert took < 5  # the wait is 0.1 s, not the default 10 s


def test_an_unknown_email_waits_for_a_slot_too(client):
    with auth.password_check_slot():
        r = _login(client, "nobody@test.invalid")
    assert r.status_code == 503 and r.json()["detail"] == BUSY


def test_a_busy_answer_is_not_a_failed_sign_in(client):
    """The 503 is the server's, not the caller's mistake: it never counts toward the address's pause."""
    with auth.password_check_slot():
        for _ in range(get_settings().login_max_failures + 1):
            assert _login(client, password="wrong password!!").status_code == 503
    assert _login(client).status_code == 200


def test_with_the_slot_free_a_login_answers_normally(client):
    assert _login(client).status_code == 200
    assert _login(client, password="wrong password!!").status_code == 401


def test_change_password_waits_for_a_slot(client):
    assert _login(client).status_code == 200
    with auth.password_check_slot():
        r = client.post(
            "/auth/change-password", json={"current": PASSWORD, "new": "a brand new passphrase"}
        )
    assert r.status_code == 503 and r.json()["detail"] == BUSY
    ok = client.post(
        "/auth/change-password", json={"current": PASSWORD, "new": "a brand new passphrase"}
    )
    assert ok.status_code == 204


def test_a_cap_of_zero_means_no_cap(client, monkeypatch):
    monkeypatch.setenv("ALPHADECK_PASSWORD_CHECK_CONCURRENCY", "0")
    get_settings.cache_clear()
    with auth.password_check_slot():
        assert _login(client).status_code == 200
