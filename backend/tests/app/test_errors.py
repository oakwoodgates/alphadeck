"""The API's error contract (security hardening PR 2, ``app/errors.py``, docs/AUTH.md §Errors).

- A database error on a request answers a fixed sentence per class plus a short reference id, never the driver's
  text: ``DataError`` 422, ``UniqueViolation`` 409, ``CheckViolation`` / ``ForeignKeyViolation`` /
  ``NotNullViolation`` 422, ``ProgramLimitExceeded`` 413, ``QueryCanceled`` / ``LockNotAvailable`` /
  ``OperationalError`` 503 with ``Retry-After``; anything else 500.
- The exception is logged under that reference id.
- A steward (super admin) still sees the exception's class and message after the sentence; a plain user never does.
- FastAPI's request-validation 422 keeps its shape but no longer echoes the caller's ``input``.
"""

from __future__ import annotations

import logging
import re
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import errors
from app.auth import CurrentUser, require_super_admin, require_user
from app.deps import get_conn
from app.main import app
from db.session import DEFAULT_TENANT_ID
from tests.auth_fixtures import TEST_STEWARD, sign_out

PLAIN = CurrentUser(
    id=uuid.UUID("00000000-0000-0000-0000-00000000b001"),
    email="plain@test.invalid",
    display_name="Plain user",
    role="user",
    workspace_id=DEFAULT_TENANT_ID,
    principal="session",
)

SECRET = "SELECT secret_column FROM hidden_table WHERE x = 'C:/srv/data/file.json'"
REF = re.compile(r"Reference ([0-9a-f]{8})\.")


def as_user(user: CurrentUser) -> None:
    """Sign the suite's ``app`` in as ``user`` (both gates)."""
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_super_admin] = lambda: user


@pytest.fixture
def plain(client):
    as_user(PLAIN)
    return client


@pytest.fixture
def steward(client):
    as_user(TEST_STEWARD)
    return client


def _raise_in_search(monkeypatch, exc: BaseException) -> None:
    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr("securities.master.search", boom)


# --- the NUL character -----------------------------------------------------------------------------------------------
# Before security hardening PR 3 a NUL reached psycopg (a client-side ``DataError``) and the handler answered 422 with
# the "data" sentence. Since PR 3 the request model refuses it first: a 422 in the validation list shape, naming the
# field, with the database never asked. The handler's ``DataError`` mapping is still pinned below (an injected
# ``NumericValueOutOfRange``, a ``DataError`` subclass).


def _search_must_not_run(monkeypatch) -> None:
    def boom(*a, **kw):
        raise AssertionError("the database was asked")

    monkeypatch.setattr("securities.master.search", boom)


def test_nul_in_a_query_param_is_422_not_500_for_a_plain_user(plain, monkeypatch):
    _search_must_not_run(monkeypatch)
    r = plain.get("/workbench/securities", params={"q": "a\x00b"})
    assert r.status_code == 422, r.text
    (err,) = r.json()["detail"]
    assert err["loc"] == ["query", "q"]
    assert "control character" in err["msg"]
    assert "input" not in err  # the caller's value is never echoed (PR 2)


def test_nul_in_a_query_param_is_the_same_422_for_a_steward(steward, monkeypatch):
    _search_must_not_run(monkeypatch)
    r = steward.get("/workbench/securities", params={"q": "a\x00b"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"] == ["query", "q"]


def test_nul_in_the_login_email_is_422_and_signs_nobody_in(db):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    try:
        c = TestClient(app, base_url="https://testserver")
        r = c.post("/auth/login", json={"email": "a\x00@x.test", "password": "whatever-long"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422, r.text
    (err,) = r.json()["detail"]
    assert err["loc"] == ["body", "email"] and "control character" in err["msg"]
    assert "set-cookie" not in {k.lower() for k in r.headers}


# --- each class of database error -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "status", "key"),
    [
        (psycopg.errors.UniqueViolation(SECRET), 409, "unique"),
        (psycopg.errors.CheckViolation(SECRET), 422, "constraint"),
        (psycopg.errors.ForeignKeyViolation(SECRET), 422, "constraint"),
        (psycopg.errors.NotNullViolation(SECRET), 422, "constraint"),
        (psycopg.errors.NumericValueOutOfRange(SECRET), 422, "data"),
        (psycopg.errors.ProgramLimitExceeded(SECRET), 413, "too_large"),
        (psycopg.errors.QueryCanceled(SECRET), 503, "busy"),
        (psycopg.errors.LockNotAvailable(SECRET), 503, "busy"),
        (psycopg.OperationalError(SECRET), 503, "unavailable"),
    ],
)
def test_database_errors_map_to_a_fixed_sentence(plain, monkeypatch, exc, status, key):
    _raise_in_search(monkeypatch, exc)
    r = plain.get("/workbench/securities", params={"q": "x"})
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail.startswith(errors.SENTENCES[key])
    assert REF.search(detail)
    assert "secret_column" not in detail and "C:/srv" not in detail
    if status == 503:
        assert r.headers.get("retry-after", "").isdigit()


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.errors.UniqueViolation(SECRET),
        psycopg.errors.ProgramLimitExceeded(SECRET),
        psycopg.errors.QueryCanceled(SECRET),
        psycopg.errors.CheckViolation(SECRET),
    ],
)
def test_a_steward_sees_the_underlying_error(steward, monkeypatch, exc):
    _raise_in_search(monkeypatch, exc)
    r = steward.get("/workbench/securities", params={"q": "x"})
    detail = r.json()["detail"]
    assert type(exc).__name__ in detail
    assert "secret_column" in detail


def _get_500(db, monkeypatch, user: CurrentUser):
    as_user(user)
    app.dependency_overrides[get_conn] = lambda: db
    _raise_in_search(monkeypatch, RuntimeError(SECRET))
    try:
        c = TestClient(app, raise_server_exceptions=False)
        return c.get("/workbench/securities", params={"q": "x"})
    finally:
        app.dependency_overrides.clear()


def test_any_other_exception_is_a_plain_500_with_a_reference(db, monkeypatch):
    r = _get_500(db, monkeypatch, PLAIN)
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail.startswith(errors.SENTENCES["server"])
    assert REF.search(detail)
    assert "secret_column" not in detail


def test_any_other_exception_shows_the_steward_the_error(db, monkeypatch):
    r = _get_500(db, monkeypatch, TEST_STEWARD)
    assert r.status_code == 500
    assert "RuntimeError" in r.json()["detail"]


def test_the_exception_is_logged_under_the_reference(plain, monkeypatch, caplog):
    _raise_in_search(monkeypatch, psycopg.errors.CheckViolation(SECRET))
    with caplog.at_level(logging.WARNING, logger="alphadeck.errors"):
        r = plain.get("/workbench/securities", params={"q": "x"})
    ref = REF.search(r.json()["detail"]).group(1)
    logged = [rec for rec in caplog.records if ref in rec.getMessage()]
    assert logged, [rec.getMessage() for rec in caplog.records]
    assert logged[0].exc_info is not None


# --- the request-validation 422 ---------------------------------------------------------------------------------------


def test_request_validation_422_no_longer_echoes_the_input(plain):
    big = "Z" * 5000
    r = plain.get("/workbench/securities", params={"q": "x", "limit": big})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body["detail"], list) and body["detail"]
    for item in body["detail"]:
        assert "input" not in item
        assert {"loc", "msg", "type"} <= set(item)
    assert big not in r.text


# --- the one helper the routes use ------------------------------------------------------------------------------------


def test_fail_helper_shows_detail_to_a_steward_only():
    exc = OSError(2, "No such file", "C:/srv/data/triage_sessions/x/latest.json")
    sentence = "The saved session could not be read. Try again."
    plain = errors.fail(500, sentence, exc, user=PLAIN)
    steward = errors.fail(500, sentence, exc, user=TEST_STEWARD)
    assert plain.status_code == 500
    assert plain.detail.startswith(sentence)
    assert REF.search(plain.detail)
    assert "C:/srv" not in plain.detail
    assert "C:/srv" in steward.detail and "FileNotFoundError" in steward.detail
