"""V2 W5a: the user's read of platform values over HTTP, proven with REAL sign-ins.

``GET /platform-values/{security_id}`` and ``GET /platform-values/theses/{thesis_id}`` (``docs/PLATFORM_VALUES.md``,
"The user's read"). Built WITHOUT the suite's signed-in override (the ``world`` pattern of
``tests/app/test_workspace_isolation.py``): plain users A (W_A) and B (W_B) and the steward S (super admin, W_S),
none of them the data tenant.

- any signed-in user reads any company in the master (shared data): A reads a company only on B's card and gets
  B's answer, apart from ``my_flag``; an unknown id is 404 with the standard error body;
- the thesis read answers the card's resolved members in basket order; another workspace's thesis is 404, exactly
  as every thesis route; the steward reads both routes too;
- a steward-verified value on file: no ``@`` in any user's answer;
- ``my_flag`` is the caller's own, in the company read AND the thesis read, with A and B flagging the SAME value;
- the read is current: an ``asof`` query parameter changes nothing; the static ``theses`` path is matched before
  the dynamic ``{security_id}``.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from dashboards.cache import CACHE
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import ingest_shares_outstanding
from repositories import platform_value_repo as repo
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
BASE = "/platform-values"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
STEWARD_EMAIL = "s@pv.test"
T0 = datetime(2026, 1, 2, tzinfo=timezone.utc)


def _workspace(db, label: str) -> uuid.UUID:
    wid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace')", (wid, label)
        )
    return wid


def _client(email: str) -> TestClient:
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    r = c.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def world(db, pinned_today, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # the auth tables are not in the shared truncate
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    CACHE.clear()
    w_a, w_b, w_s = (_workspace(db, f"values read {x}") for x in ("A", "B", "S"))
    ids = {
        "a": auth.create_user(
            db, email="a@pv.test", display_name="A", password=PASSWORD, workspace_id=w_a
        ),
        "b": auth.create_user(
            db, email="b@pv.test", display_name="B", password=PASSWORD, workspace_id=w_b
        ),
        "s": auth.create_user(
            db,
            email=STEWARD_EMAIL,
            display_name="S",
            password=PASSWORD,
            role="super_admin",
            workspace_id=w_s,
        ),
    }
    db.commit()
    yield {
        "asof": pinned_today,
        "w_a": w_a,
        "w_b": w_b,
        "w_s": w_s,
        "uid": ids,
        "a": _client("a@pv.test"),
        "b": _client("b@pv.test"),
        "s": _client(STEWARD_EMAIL),
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


def _company(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, '0000000007', '2026-01-01')",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp"),
        )
    db.commit()
    return sid


def _card(world, db, who: str, sids: list[uuid.UUID]) -> uuid.UUID:
    tid = uuid.uuid4()
    workspace = world[f"w_{who}"]
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, 'n')",
            (tid, workspace, f"card of {who}"),
        )
        for i, sid in enumerate(sids):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, 'T', 'core', %s)",
                (workspace, tid, i, sid),
            )
    db.commit()
    return tid


def _shares(db, sid, d, by="auto"):
    ingest_shares_outstanding(
        db,
        sid,
        shares=10_000_000,
        source="10-q-cover",
        source_ref=OLD_REF,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()


def _cash(db, sid, d, by="auto"):
    ingest_cash_burn(
        db,
        sid,
        cash_usd=5_000_000,
        quarterly_burn_usd=1_000_000,
        source="10-Q",
        source_ref=OLD_REF,
        event_date=d,
        ratified_by=by,
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()


def _flagged(db, sid, d):
    repo.append_candidate(
        db,
        security_id=sid,
        fact_type="shares_outstanding",
        outcome="flag",
        flags=["dual-class"],
        source="10-q-cover",
        source_ref=NEW_REF,
        event_date=d,
        shares=30_000_000,
    )
    db.commit()


def _flag_row(db, uid, sid, *, note, opened, closed=None, outcome=None):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO value_flag (user_id, security_id, value_type, note, opened_at, closed_at, outcome) "
            "VALUES (%s, %s, 'shares_outstanding', %s, %s, %s, %s) RETURNING id",
            (uid, sid, note, opened, closed, outcome),
        )
        fid = cur.fetchone()["id"]
    db.commit()
    return str(fid)


def _without_my_flag(body: dict) -> dict:
    return {
        k: ({kk: vv for kk, vv in v.items() if kk != "my_flag"} if isinstance(v, dict) else v)
        for k, v in body.items()
    }


# --- any signed-in user reads any company -----------------------------------------------------------------------


def test_a_reads_a_company_only_on_bs_card_and_gets_bs_answer(world, db):
    sid = _company(db, "SHARED")
    _card(world, db, "b", [sid])
    d = world["asof"] - timedelta(days=40)
    _shares(db, sid, d)
    _cash(db, sid, d)
    got = {who: world[who].get(f"{BASE}/{sid}") for who in ("a", "b", "s")}
    for who, r in got.items():
        assert r.status_code == 200, f"{who}: {r.text}"
    assert got["a"].json() == got["b"].json() == got["s"].json()
    body = got["a"].json()
    assert (body["ticker"], body["shares"]["state"], body["shares"]["value"]["shares"]) == (
        "SHARED",
        "from_filing",
        10_000_000,
    )
    assert "card of b" not in got["a"].text  # no card, workspace or user of B in the answer
    assert str(world["w_b"]) not in got["a"].text and "b@pv.test" not in got["a"].text


def test_an_unknown_company_is_404_with_the_standard_error_body(world, db):
    for who in ("a", "s"):
        r = world[who].get(f"{BASE}/{uuid.uuid4()}")
        assert r.status_code == 404, r.text
        detail = r.json()["detail"]
        assert detail.startswith("There is no such company. Reference ")
    assert world["a"].get(f"{BASE}/not-a-uuid").status_code == 422


def test_the_route_takes_no_asof(world, db):
    sid = _company(db, "NOW")
    _shares(db, sid, world["asof"] - timedelta(days=40))
    tid = _card(world, db, "a", [sid])
    plain = world["a"].get(f"{BASE}/{sid}").json()
    dated = world["a"].get(f"{BASE}/{sid}", params={"asof": "2020-01-01"}).json()
    assert plain == dated and plain["shares"]["state"] == "from_filing"
    t_plain = world["a"].get(f"{BASE}/theses/{tid}").json()
    t_dated = world["a"].get(f"{BASE}/theses/{tid}", params={"asof": "2020-01-01"}).json()
    assert t_plain == t_dated
    params = {
        p["name"]
        for path in (f"{BASE}/{{security_id}}", f"{BASE}/theses/{{thesis_id}}")
        for p in app.openapi()["paths"][path]["get"].get("parameters", [])
    }
    assert params == {"security_id", "thesis_id"}


def test_only_the_two_paths_live_under_the_prefix_and_the_static_one_comes_first():
    paths = [
        r.path
        for r in app.routes
        if getattr(r, "path", "").startswith(BASE + "/") or getattr(r, "path", "") == BASE
    ]
    assert paths == [f"{BASE}/theses/{{thesis_id}}", f"{BASE}/{{security_id}}"]


# --- the thesis read --------------------------------------------------------------------------------------------


def test_the_thesis_read_answers_the_members_in_basket_order(world, db):
    d = world["asof"] - timedelta(days=40)
    s1, s2, s3 = (_company(db, t) for t in ("ONE", "TWO", "THREE"))
    _shares(db, s2, d)
    _flagged(db, s3, world["asof"] - timedelta(days=10))
    tid = _card(world, db, "a", [s2, s1, s3, s2])
    r = world["a"].get(f"{BASE}/theses/{tid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert [m["ticker"] for m in body["members"]] == ["TWO", "ONE", "THREE"]
    states = [m["shares"]["state"] for m in body["members"]]
    assert states == ["from_filing", "no_value", "awaiting_steward"]
    assert body["members"][2]["shares"]["awaiting"]["source_url"] == NEW_REF


def test_another_workspaces_thesis_is_404_as_every_thesis_route(world, db):
    sid = _company(db, "PRIV")
    tid = _card(world, db, "a", [sid])
    assert world["a"].get(f"{BASE}/theses/{tid}").status_code == 200
    for who in ("b", "s"):
        r = world[who].get(f"{BASE}/theses/{tid}")
        twin = world[who].get(
            f"/workbench/theses/{tid}/scored", params={"asof": world["asof"].isoformat()}
        )
        assert (r.status_code, twin.status_code) == (404, 404), r.text
        assert r.json() == twin.json()  # the same answer every thesis route gives
        assert "PRIV" not in r.text
    assert world["a"].get(f"{BASE}/theses/{uuid.uuid4()}").status_code == 404


def test_the_steward_reads_its_own_card(world, db):
    sid = _company(db, "STEW")
    tid = _card(world, db, "s", [sid])
    r = world["s"].get(f"{BASE}/theses/{tid}")
    assert r.status_code == 200, r.text
    assert [m["ticker"] for m in r.json()["members"]] == ["STEW"]


# --- no email ---------------------------------------------------------------------------------------------------


def test_a_steward_correction_on_file_puts_no_at_sign_in_any_answer(world, db):
    sid = _company(db, "VERI")
    d = world["asof"] - timedelta(days=40)
    _shares(db, sid, d, by=STEWARD_EMAIL)
    _cash(db, sid, d, by=STEWARD_EMAIL)
    tid = _card(world, db, "a", [sid])
    for who in ("a", "b"):
        r = world[who].get(f"{BASE}/{sid}")
        assert r.status_code == 200, r.text
        assert r.json()["shares"]["set_by"] == "steward"
        assert "@" not in r.text
    r = world["a"].get(f"{BASE}/theses/{tid}")
    assert r.json()["members"][0]["cash_burn"]["label"] == "Verified by the steward"
    assert "@" not in r.text


# --- my_flag: A and B flag the SAME value ---------------------------------------------------------------------


def test_each_user_sees_only_their_own_flag_in_both_reads(world, db):
    sid = _company(db, "FLAG")
    _shares(db, sid, world["asof"] - timedelta(days=40))
    tid_a = _card(world, db, "a", [sid])
    tid_b = _card(world, db, "b", [sid])
    a_flag = _flag_row(db, world["uid"]["a"], sid, note="A thinks so", opened=T0)
    b_flag = _flag_row(
        db,
        world["uid"]["b"],
        sid,
        note="B thinks so",
        opened=T0,
        closed=T0 + timedelta(days=1),
        outcome="withdrawn",
    )
    company = {who: world[who].get(f"{BASE}/{sid}") for who in ("a", "b", "s")}
    thesis = {
        "a": world["a"].get(f"{BASE}/theses/{tid_a}"),
        "b": world["b"].get(f"{BASE}/theses/{tid_b}"),
    }
    for who, mine, other, other_note in (
        ("a", a_flag, b_flag, "B thinks so"),
        ("b", b_flag, a_flag, "A thinks so"),
    ):
        for r, flag in (
            (company[who], company[who].json()["shares"]["my_flag"]),
            (thesis[who], thesis[who].json()["members"][0]["shares"]["my_flag"]),
        ):
            assert r.status_code == 200, r.text
            assert flag["id"] == mine
            assert other not in r.text and other_note not in r.text
    assert company["b"].json()["shares"]["my_flag"]["outcome"] == "withdrawn"
    assert company["s"].json()["shares"]["my_flag"] is None  # the steward raised none
    assert a_flag not in company["s"].text and b_flag not in company["s"].text
    # apart from the caller's own flag, everyone reads the same shared answer
    assert (
        _without_my_flag(company["a"].json())
        == _without_my_flag(company["b"].json())
        == _without_my_flag(company["s"].json())
    )
