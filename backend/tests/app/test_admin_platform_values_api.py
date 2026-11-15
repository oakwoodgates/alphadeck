"""V2 W2c: the steward's platform-value routes (``/admin/platform-values``), proven with REAL sign-ins.

Built WITHOUT the suite's signed-in override (the ``world`` pattern of ``tests/app/test_workspace_isolation.py``):
three provisioned workspaces, none of them the data tenant, real users and real session cookies:

- A (a plain user, W_A) and B (a plain user, W_B) each own a card holding the shared DATA security;
- S (the steward, super admin, on W_S — not the data tenant, so a write aimed at the caller's workspace cannot
  hide).

A plain user gets 403 on EVERY route and writes nothing in any table; the steward's actions land in the data
tenant, each answer is the company's new state, and both A and B read the steward's value on their own cards.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.errors import PLATFORM_VALUE_NOT_FOUND, PLATFORM_VALUE_SENTENCES
from app.main import app
from dashboards.cache import CACHE
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from domain.text_types import PROSE_MAX, URL_MAX
from ingest.shares import ingest_shares_outstanding
from repositories import platform_value_repo as repo
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
BASE = "/admin/platform-values"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
TABLES = (
    "fact_shares_outstanding",
    "fact_cash_burn",
    "platform_value_decision",
    "extraction_candidate",
    "steward_pin",
    "fact_revenue_mix",
)


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


def _promote(c: TestClient, name: str, security_id: uuid.UUID) -> uuid.UUID:
    r = c.post(
        "/workbench/theses",
        json={
            "name": name,
            "narrative": f"{name}: a synthetic narrative for the steward proof",
            "basket": [{"ticker": "DEVCO", "role": "the name", "security_id": str(security_id)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


@pytest.fixture
def world(db, security_id, pinned_today, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # the auth tables are not in the shared truncate
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    CACHE.clear()
    w_a, w_b, w_s = (_workspace(db, f"steward proof {x}") for x in ("A", "B", "S"))
    auth.create_user(db, email="a@pv.test", display_name="A", password=PASSWORD, workspace_id=w_a)
    auth.create_user(db, email="b@pv.test", display_name="B", password=PASSWORD, workspace_id=w_b)
    auth.create_user(
        db,
        email="s@pv.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    db.commit()
    a, b = _client("a@pv.test"), _client("b@pv.test")
    a_id = _promote(a, f"A card {uuid.uuid4().hex[:8]}", security_id)
    b_id = _promote(b, f"B card {uuid.uuid4().hex[:8]}", security_id)
    db.commit()
    yield {
        "asof": pinned_today,
        "sid": security_id,
        "w_a": w_a,
        "w_b": w_b,
        "w_s": w_s,
        "a": a,
        "b": b,
        "s": _client("s@pv.test"),
        "a_id": a_id,
        "b_id": b_id,
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — a fixed list
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _flag(db, sid, d: date, *, fact_type: str = "shares_outstanding", ref: str = NEW_REF):
    values = (
        {"shares": 30_000_000}
        if fact_type == "shares_outstanding"
        else {"cash_usd": 9_000_000, "quarterly_burn_usd": 1_500_000}
    )
    cid = repo.append_candidate(
        db,
        security_id=sid,
        fact_type=fact_type,
        outcome="flag",
        flags=["dual-class"],
        source="cover",
        source_ref=ref,
        event_date=d,
        located_passages=[
            {"kind": "cover", "source_ref": ref, "anchor": "Class A", "excerpt": "Class A common"}
        ],
        **values,
    )
    db.commit()
    return cid


def _shares_on_file(db, sid, ref: str, d: date, count: float):
    fid = ingest_shares_outstanding(
        db,
        sid,
        shares=count,
        source="cover",
        source_ref=ref,
        event_date=d,
        ratified_by="auto",
        tenant_id=DATA_TENANT_ID,
    )
    db.commit()
    return fid


def _correct_body(asof: date, **over) -> dict:
    return {
        "fact_type": "cash_burn",
        "cash_usd": 30_000_000,
        "quarterly_burn_usd": 3_000_000,
        "source_ref": NEW_REF,
        "event_date": (asof - timedelta(days=30)).isoformat(),
        "note": "read the 10-Q's cash flow statement",
        **over,
    }


# --- a plain user: 403 everywhere, nothing written -----------------------------------------------------------


def test_a_plain_user_gets_403_on_every_route_and_writes_nothing(world, db):
    sid, asof = world["sid"], world["asof"]
    cid = _flag(db, sid, asof - timedelta(days=10))
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO platform_value_decision (tenant_id, security_id, fact_type, action, candidate_id, "
            "reason, decided_by) VALUES (%s, %s, 'shares_outstanding', 'dismiss', %s, 'x', 's@pv.test') "
            "RETURNING id",
            (DATA_TENANT_ID, sid, cid),
        )
        did = cur.fetchone()["id"]
    db.commit()
    calls = [
        ("get", f"{BASE}/awaiting", None),
        ("get", f"{BASE}/queue", None),
        ("get", f"{BASE}/{sid}", None),
        ("post", f"{BASE}/candidates/{cid}/approve", {}),
        ("post", f"{BASE}/{sid}/correct", _correct_body(asof)),
        ("post", f"{BASE}/candidates/{cid}/dismiss", {"reason": "no"}),
        ("post", f"{BASE}/decisions/{did}/undo", {}),
        ("post", f"{BASE}/{sid}/pin", {"fact_type": "shares_outstanding"}),
        ("post", f"{BASE}/{sid}/unpin", {"fact_type": "shares_outstanding"}),
    ]
    # every route this router mounts is in the list
    mounted = {
        (m.lower(), r.path)
        for r in app.routes
        if getattr(r, "path", "").startswith(BASE)
        for m in r.methods
    }
    assert len(mounted) == len(calls)
    before = _counts(db)
    for who in ("a", "b"):
        for method, url, body in calls:
            r = getattr(world[who], method)(url, **({"json": body} if body is not None else {}))
            assert r.status_code == 403, f"{who} {method} {url}: {r.status_code} {r.text}"
    assert _counts(db) == before


# --- the steward: each action answers the company's new state ------------------------------------------------


def test_the_steward_approves_a_flag_and_it_lands_in_the_data_tenant(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    cid = _flag(db, sid, asof - timedelta(days=10))
    listed = s.get(f"{BASE}/awaiting").json()
    assert (listed["total"], [i["candidate"]["id"] for i in listed["items"]]) == (1, [str(cid)])
    r = s.post(f"{BASE}/candidates/{cid}/approve", json={"note": "read the cover"})
    assert r.status_code == 200, r.text
    shares = r.json()["shares"]
    assert (shares["state"], shares["ratifier"], shares["awaiting"]) == (
        "verified",
        "steward",
        None,
    )
    assert shares["value"]["ratified_by"] == "s@pv.test"
    assert shares["value"]["shares"] == 30_000_000
    (decision,) = shares["decisions"]
    assert (decision["action"], decision["candidate_id"], decision["decided_by"]) == (
        "approve",
        str(cid),
        "s@pv.test",
    )
    assert shares["candidates"][0]["decision"]["id"] == decision["id"]
    assert shares["candidates"][0]["located_passages"][0]["anchor"] == "Class A"
    db.rollback()
    with db.cursor() as cur:
        for table in ("fact_shares_outstanding", "platform_value_decision"):
            cur.execute(
                f"SELECT tenant_id FROM {table} WHERE security_id = %s", (sid,)  # noqa: S608
            )
            assert {x["tenant_id"] for x in cur.fetchall()} == {DATA_TENANT_ID}, table
    db.rollback()
    assert s.get(f"{BASE}/awaiting").json() == {"items": [], "total": 0}


def test_a_stewards_correction_is_read_by_both_workspaces(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    r = s.post(f"{BASE}/{sid}/correct", json=_correct_body(asof))
    assert r.status_code == 200, r.text
    cash = r.json()["cash_burn"]
    assert (cash["state"], cash["value"]["source"], cash["value"]["ratified_by"]) == (
        "verified",
        "ratified",
        "s@pv.test",
    )
    for who, tid in (("a", world["a_id"]), ("b", world["b_id"])):
        (m,) = (
            world[who]
            .get(f"/workbench/theses/{tid}/scored", params={"asof": asof.isoformat()})
            .json()["members"]
        )
        assert m["runway"]["value"] == 30.0, who  # 30M / (3M a quarter / 3) = 30 months


def test_the_correction_rule_refuses_with_its_sentence_and_the_blocking_date(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    shown = asof - timedelta(days=10)
    _shares_on_file(db, sid, OLD_REF, shown, 10_000_000)
    before = _counts(db)
    older = {
        "fact_type": "shares_outstanding",
        "shares": 12_000_000,
        "source_ref": NEW_REF,
        "event_date": (shown - timedelta(days=1)).isoformat(),
    }
    r = s.post(f"{BASE}/{sid}/correct", json=older)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail.startswith(PLATFORM_VALUE_SENTENCES["older_than_shown"])
    assert shown.isoformat() in detail and OLD_REF in detail  # the steward sees what blocks it
    r = s.post(
        f"{BASE}/{sid}/correct",
        json={**older, "event_date": (asof + timedelta(days=1)).isoformat()},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"].startswith(PLATFORM_VALUE_SENTENCES["after_today"])
    assert _counts(db) == before


def test_dismiss_then_undo_makes_the_candidate_awaiting_again(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    cid = _flag(db, sid, asof - timedelta(days=10))
    r = s.post(f"{BASE}/candidates/{cid}/dismiss", json={"reason": "the other class"})
    assert r.status_code == 200, r.text
    shares = r.json()["shares"]
    assert (shares["state"], shares["reason"]) == ("no_value", "dismissed")
    did = shares["decisions"][0]["id"]
    assert s.get(f"{BASE}/awaiting").json()["total"] == 0
    r = s.post(f"{BASE}/decisions/{did}/undo", json={"reason": "dismissed by mistake"})
    assert r.status_code == 200, r.text
    shares = r.json()["shares"]
    assert (shares["state"], shares["awaiting"]["id"]) == ("awaiting_steward", str(cid))
    undo_id = shares["decisions"][0]["id"]
    assert [d["undone"] for d in shares["decisions"]] == [False, True]
    assert s.get(f"{BASE}/awaiting").json()["total"] == 1
    # an undo cannot be undone, and a decision is undone once
    r = s.post(f"{BASE}/decisions/{undo_id}/undo", json={})
    assert r.status_code == 409 and r.json()["detail"].startswith(
        PLATFORM_VALUE_SENTENCES["undo_of_undo"]
    )
    r = s.post(f"{BASE}/decisions/{did}/undo", json={})
    assert r.status_code == 409 and r.json()["detail"].startswith(
        PLATFORM_VALUE_SENTENCES["already_undone"]
    )


def test_undo_an_approve_reads_the_earlier_value(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    old = _shares_on_file(db, sid, OLD_REF, asof - timedelta(days=40), 10_000_000)
    cid = _flag(db, sid, asof - timedelta(days=10))
    approved = s.post(f"{BASE}/candidates/{cid}/approve", json={}).json()["shares"]
    assert approved["value"]["shares"] == 30_000_000
    r = s.post(f"{BASE}/decisions/{approved['decisions'][0]['id']}/undo", json={})
    assert r.status_code == 200, r.text
    shares = r.json()["shares"]
    assert (shares["state"], shares["value"]["id"], shares["pending"]["id"]) == (
        "from_filing",
        str(old),
        str(cid),
    )
    assert [v["set_aside"] for v in shares["versions"]] == [False, True]


def test_pin_and_unpin(world, db):
    s, sid = world["s"], world["sid"]
    body = {"fact_type": "cash_burn", "note": "an annual filer: review each filing"}
    r = s.post(f"{BASE}/{sid}/pin", json=body)
    assert r.status_code == 200 and r.json()["cash_burn"]["pinned"] is True
    assert r.json()["shares"]["pinned"] is False
    r = s.post(f"{BASE}/{sid}/pin", json=body)
    assert r.status_code == 409 and r.json()["detail"].startswith(
        PLATFORM_VALUE_SENTENCES["already_pinned"]
    )
    r = s.post(f"{BASE}/{sid}/unpin", json={"fact_type": "cash_burn"})
    assert r.status_code == 200 and r.json()["cash_burn"]["pinned"] is False
    r = s.post(f"{BASE}/{sid}/unpin", json={"fact_type": "cash_burn"})
    assert r.status_code == 409 and r.json()["detail"].startswith(
        PLATFORM_VALUE_SENTENCES["not_pinned"]
    )


def test_the_company_read_says_who_pinned_when_and_why(world, db):
    """The company read carries the newest pin event (W4a): who, when and the note; after an unpin, the unpin."""
    s, sid = world["s"], world["sid"]
    got = s.get(f"{BASE}/{sid}").json()
    assert (got["shares"]["pin"], got["cash_burn"]["pin"]) == (None, None)  # never pinned
    note = "an annual filer: review each filing"
    r = s.post(f"{BASE}/{sid}/pin", json={"fact_type": "cash_burn", "note": note})
    assert r.status_code == 200, r.text
    pin = r.json()["cash_burn"]["pin"]
    assert (pin["action"], pin["decided_by"], pin["note"]) == ("pin", "s@pv.test", note)
    assert pin["recorded_at"]
    assert r.json()["shares"]["pin"] is None
    assert s.get(f"{BASE}/{sid}").json()["cash_burn"]["pin"] == pin  # the read says the same
    r = s.post(f"{BASE}/{sid}/unpin", json={"fact_type": "cash_burn"})
    unpin = r.json()["cash_burn"]["pin"]
    assert (unpin["action"], unpin["decided_by"], unpin["note"]) == ("unpin", "s@pv.test", None)
    assert unpin["recorded_at"] >= pin["recorded_at"]


def test_the_company_read_and_unknown_ids(world, db):
    s, sid = world["s"], world["sid"]
    r = s.get(f"{BASE}/{sid}")
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["security_id"], got["ticker"]) == (str(sid), "DEVCO")
    assert (got["shares"]["state"], got["shares"]["reason"]) == ("no_value", "not-extracted")
    nobody = uuid.uuid4()
    for method, url, body in (
        ("get", f"{BASE}/{nobody}", None),
        ("post", f"{BASE}/{nobody}/correct", _correct_body(world["asof"])),
        ("post", f"{BASE}/candidates/{nobody}/approve", {}),
        ("post", f"{BASE}/candidates/{nobody}/dismiss", {"reason": "x"}),
        ("post", f"{BASE}/decisions/{nobody}/undo", {}),
        ("post", f"{BASE}/{nobody}/pin", {"fact_type": "cash_burn"}),
    ):
        r = getattr(s, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, f"{url}: {r.text}"
        assert r.json()["detail"].startswith(PLATFORM_VALUE_NOT_FOUND)


def test_request_limits(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    before = _counts(db)
    r = s.post(f"{BASE}/{sid}/correct", json=_correct_body(asof, note="x" * (PROSE_MAX + 1)))
    assert r.status_code == 422, r.text
    r = s.post(
        f"{BASE}/{sid}/correct",
        json=_correct_body(asof, source_ref="https://x.test/" + "a" * URL_MAX),
    )
    assert r.status_code == 422, r.text
    cid = _flag(db, sid, asof - timedelta(days=10))
    before = _counts(db)
    r = s.post(f"{BASE}/candidates/{cid}/dismiss", json={"reason": "   "})
    assert r.status_code == 422 and r.json()["detail"].startswith(
        PLATFORM_VALUE_SENTENCES["blank_reason"]
    )
    assert _counts(db) == before


def test_the_awaiting_list_is_newest_first_and_omits_decided(world, db):
    s, sid, asof = world["s"], world["sid"], world["asof"]
    other = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, 'OTHR', 'Other Corp', '0000000009', '2026-01-01')",
            (other, DATA_TENANT_ID),
        )
    db.commit()
    first = _flag(db, sid, asof - timedelta(days=10))
    second = _flag(db, other, asof - timedelta(days=5), fact_type="cash_burn")
    decided = _flag(db, other, asof - timedelta(days=5))
    assert s.post(f"{BASE}/candidates/{decided}/dismiss", json={"reason": "no"}).status_code == 200
    got = s.get(f"{BASE}/awaiting", params={"limit": 1}).json()
    assert got["total"] == 2
    assert [i["candidate"]["id"] for i in got["items"]] == [str(second)]
    got = s.get(f"{BASE}/awaiting").json()
    assert [(i["candidate"]["id"], i["ticker"], i["fact_type"]) for i in got["items"]] == [
        (str(second), "OTHR", "cash_burn"),
        (str(first), "DEVCO", "shares_outstanding"),
    ]
