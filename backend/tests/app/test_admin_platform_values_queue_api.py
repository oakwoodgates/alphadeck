"""V2 W4a: the steward's QUEUE (``GET /admin/platform-values/queue``), proven with REAL sign-ins.

Built WITHOUT the suite's signed-in override (the ``world`` pattern of ``tests/app/test_workspace_isolation.py``):
plain users A (on W_A) and B (on W_B) and the steward S (super admin, on W_S), none of them the data tenant. A and
B hold the companies on cards in their own workspaces, written straight to the tables so each card carries a
recognisable secret name.

- A plain user gets 403 and nothing is written.
- One row per company with anything waiting (approved B3 (a)): the share count and/or cash/burn (B2 (a)), each with
  its state and candidate (and the value shown beside a pending one); a flag older than the value shown waits for
  nothing and is not listed, the same as ``/awaiting``.
- The order: pen-only companies last (B5 (a): every row is the Discovered link), then most cards first, then the
  longest wait first, then the id. ``total`` counts every company waiting, ``pen_only_total`` the pen-only ones.
- Paging and every query parameter are bounded (422 outside the bounds).
- The card count crosses workspaces, and the answer carries no thesis id or name, no workspace id and no user of A
  or B.
- The number of SQL statements per request does not grow with the number of rows.
- USERS' FLAGS (V2 W5c, the W5 plan's Q6): a company with an open user flag is in the queue even when no candidate
  waits, with ``open_flags`` (a number only, never whose or what note); its ``waiting_since`` is the older of its
  oldest waiting candidate and its oldest open flag; the order's keys are unchanged; a closed or withdrawn flag
  waits for nothing; the statement count is the same with 0 and 25 open flags.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import psycopg
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
from platform_values import flags as user_flags
from repositories import platform_value_repo as repo
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
QUEUE = "/admin/platform-values/queue"
OLD_REF = "https://www.sec.gov/Archives/edgar/data/7/0001-q1.htm"
NEW_REF = "https://www.sec.gov/Archives/edgar/data/7/0002-q2.htm"
TABLES = (
    "fact_shares_outstanding",
    "fact_cash_burn",
    "platform_value_decision",
    "extraction_candidate",
    "steward_pin",
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
    w_a, w_b, w_s = (_workspace(db, f"queue proof {x}") for x in ("A", "B", "S"))
    auth.create_user(db, email="a@q.test", display_name="A", password=PASSWORD, workspace_id=w_a)
    auth.create_user(db, email="b@q.test", display_name="B", password=PASSWORD, workspace_id=w_b)
    auth.create_user(
        db,
        email="s@q.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    db.commit()
    yield {
        "asof": pinned_today,
        "w_a": w_a,
        "w_b": w_b,
        "w_s": w_s,
        "a": _client("a@q.test"),
        "b": _client("b@q.test"),
        "s": _client("s@q.test"),
        "cards": [],  # (thesis id, thesis name) of every card made
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


def _card(world, db, who: str, rows: list[tuple[uuid.UUID, str | None]], *, archived=False):
    """A card in ``who``'s workspace holding ``rows`` = (company, link label or None)."""
    tid = uuid.uuid4()
    name = f"secret card of {who} {tid.hex[:8]}"
    workspace = world[f"w_{who}"]
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, 'a secret narrative', CASE WHEN %s THEN now() END)",
            (tid, workspace, name, archived),
        )
        for i, (sid, segment) in enumerate(rows):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, "
                "segment) VALUES (%s, %s, %s, 'T', 'secret role', %s, %s)",
                (workspace, tid, i, sid, segment),
            )
    db.commit()
    world["cards"].append((tid, name))
    return tid


def _flag(db, sid, d: date, *, fact_type: str = "shares_outstanding"):
    """A flagged candidate, in its own transaction (so a later one is recorded later)."""
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
        source_ref=NEW_REF,
        event_date=d,
        **values,
    )
    db.commit()
    return cid


def _value(db, sid, d: date, *, fact_type: str = "shares_outstanding"):
    common = dict(source_ref=OLD_REF, event_date=d, ratified_by="auto", tenant_id=DATA_TENANT_ID)
    if fact_type == "shares_outstanding":
        ingest_shares_outstanding(db, sid, shares=10_000_000, source="cover", **common)
    else:
        ingest_cash_burn(
            db, sid, cash_usd=5_000_000, quarterly_burn_usd=1_000_000, source="10-Q", **common
        )
    db.commit()


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in TABLES:
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — a fixed list
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _scene(world, db) -> dict[str, uuid.UUID]:
    """Six companies waiting, one not. Candidates are written in this order (so ``waiting_since`` grows):
    PEN, OLD1, BOTH (cash/burn), BIG, NEW1, BOTH (shares), ZERO."""
    asof = world["asof"]
    d_old, d_new = asof - timedelta(days=40), asof - timedelta(days=10)
    ids = {t: _company(db, t) for t in ("BIG", "OLD1", "NEW1", "BOTH", "PEN", "ZERO", "STALE")}
    _card(world, db, "a", [(ids["BIG"], "Fuel"), (ids["PEN"], "Discovered")])
    _card(world, db, "a", [(ids["BIG"], "Reactors"), (ids["OLD1"], None), (ids["STALE"], "Fuel")])
    _card(world, db, "b", [(ids["BIG"], "Discovered"), (ids["NEW1"], "Fuel")])
    _card(world, db, "b", [(ids["BOTH"], "Fuel"), (ids["STALE"], "Fuel")])
    _card(world, db, "b", [(ids["PEN"], "Fuel"), (ids["ZERO"], "Fuel")], archived=True)
    _value(db, ids["BOTH"], d_old, fact_type="cash_burn")
    _value(db, ids["STALE"], d_new)
    _flag(db, ids["PEN"], d_new)
    _flag(db, ids["OLD1"], d_new)
    _flag(db, ids["BOTH"], d_new, fact_type="cash_burn")  # newer than the value shown: pending
    _flag(db, ids["BIG"], d_new)
    _flag(db, ids["NEW1"], d_new)
    _flag(db, ids["BOTH"], d_new)
    _flag(db, ids["ZERO"], d_new)
    _flag(db, ids["STALE"], d_old)  # older than the value shown: waits for nothing
    return ids


# --- a plain user ----------------------------------------------------------------------------------------------


def test_a_plain_user_gets_403_and_nothing_is_written(world, db):
    _scene(world, db)
    before = _counts(db)
    for who in ("a", "b"):
        for params in ({}, {"pen": "only"}, {"limit": 1, "offset": 1}):
            r = world[who].get(QUEUE, params=params)
            assert r.status_code == 403, f"{who} {params}: {r.status_code} {r.text}"
    assert _counts(db) == before


# --- the steward ---------------------------------------------------------------------------------------------


def test_one_row_per_company_in_the_approved_order(world, db):
    ids = _scene(world, db)
    r = world["s"].get(QUEUE)
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["total"], got["pen_only_total"]) == (6, 1)
    rows = [(i["ticker"], i["cards"], i["pen_only"]) for i in got["items"]]
    assert rows == [
        ("BIG", 3, False),  # most cards first: two of A's and one of B's
        ("OLD1", 1, False),  # then the longest wait first
        ("BOTH", 1, False),  # its older candidate (cash/burn) is its wait
        ("NEW1", 1, False),
        ("ZERO", 0, False),  # on no live card (only an archived one): still shown, before the pen
        ("PEN", 1, True),  # only in a Discovered pen: last
    ]
    by = {i["ticker"]: i for i in got["items"]}
    assert "STALE" not in by  # a flag older than the value shown waits for nothing
    both = by["BOTH"]
    assert both["shares"]["state"] == "awaiting_steward" and both["shares"]["value"] is None
    assert both["cash_burn"]["state"] == "from_filing"
    assert (
        both["cash_burn"]["value"]["source_ref"] == OLD_REF
    )  # the value shown beside the pending flag
    assert both["cash_burn"]["candidate"]["source_ref"] == NEW_REF
    assert both["waiting_since"] == both["cash_burn"]["candidate"]["recorded_at"]
    assert by["BIG"]["cash_burn"] is None and by["BIG"]["security_id"] == str(ids["BIG"])
    assert by["BIG"]["name"] == "BIG Corp"


def test_the_pen_parameter_and_paging(world, db):
    _scene(world, db)
    s = world["s"]

    def tickers(**params):
        r = s.get(QUEUE, params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["total"], body["pen_only_total"]) == (
            6,
            1,
        )  # the totals never depend on the page
        return [i["ticker"] for i in body["items"]]

    assert tickers(pen="exclude") == ["BIG", "OLD1", "BOTH", "NEW1", "ZERO"]
    assert tickers(pen="only") == ["PEN"]
    assert tickers(limit=2) == ["BIG", "OLD1"]
    assert tickers(limit=2, offset=2) == ["BOTH", "NEW1"]
    assert tickers(limit=2, offset=4) == ["ZERO", "PEN"]
    assert tickers(offset=100) == []
    assert tickers(pen="only", offset=1) == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"offset": 100_001},
        {"pen": "all"},
        {"pen": "x" * 5000},
        {"limit": "many"},
    ],
)
def test_every_query_parameter_is_bounded(world, db, params):
    r = world["s"].get(QUEUE, params=params)
    assert r.status_code == 422, r.text
    (name,) = params
    assert [d["loc"] for d in r.json()["detail"]] == [
        ["query", name]
    ]  # the bound refused it, not the path


def test_the_answer_leaks_no_card_workspace_or_user(world, db):
    _scene(world, db)
    text = world["s"].get(QUEUE).text
    assert '"BIG"' in text  # the read did answer
    forbidden = [str(world[k]) for k in ("w_a", "w_b", "w_s")] + ["a@q.test", "b@q.test"]
    for tid, name in world["cards"]:
        forbidden += [str(tid), name]
    forbidden += ["secret", "Discovered", "Fuel", "Reactors"]
    for word in forbidden:
        assert word not in text, word


def test_the_queue_matches_the_awaiting_list(world, db):
    """The queue and ``/awaiting`` read the same open flags: every (company, type) on one is on the other."""
    _scene(world, db)
    s = world["s"]
    queue = s.get(QUEUE).json()["items"]
    awaiting = s.get("/admin/platform-values/awaiting").json()["items"]
    in_queue = {
        (i["security_id"], ft, i[key]["candidate"]["id"])
        for i in queue
        for key, ft in (("shares", "shares_outstanding"), ("cash_burn", "cash_burn"))
        if i[key] is not None
    }
    assert in_queue == {(a["security_id"], a["fact_type"], a["candidate"]["id"]) for a in awaiting}
    assert len(in_queue) == 7


def _statements(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = psycopg.Cursor.execute

    def counting(self, query, *args, **kwargs):
        seen.append(str(query)[:60])
        return real(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", counting)
    return seen


def test_the_statement_count_does_not_grow_with_the_rows(world, db, monkeypatch):
    _scene(world, db)
    s = world["s"]
    assert s.get(QUEUE).status_code == 200  # warm-up: the session's first touch
    seen = _statements(monkeypatch)
    assert len(s.get(QUEUE).json()["items"]) == 6
    few = len(seen)
    d = world["asof"] - timedelta(days=10)
    for n in range(12):  # twelve more companies, each with both values waiting, on cards of A and B
        sid = _company(db, f"MORE{n}")
        _card(world, db, "a" if n % 2 else "b", [(sid, "Fuel")])
        _value(db, sid, d - timedelta(days=30), fact_type="cash_burn")
        _flag(db, sid, d)
        _flag(db, sid, d, fact_type="cash_burn")
    seen.clear()
    assert len(s.get(QUEUE).json()["items"]) == 18
    assert len(seen) == few, seen


# --- users' flags (V2 W5c) -------------------------------------------------------------------------------------


def _user_id(db, who: str) -> uuid.UUID:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE email = %s", (f"{who}@q.test",))
        uid = cur.fetchone()["id"]
    db.rollback()
    return uid


def _user_flag(db, who: str, sid, value_type: str = "shares_outstanding", note: str | None = None):
    """A user's open flag, raised and committed through ``platform_values.flags`` (the route's own path after its
    own-card check)."""
    row = user_flags.raise_flag(
        db,
        user_id=_user_id(db, who),
        security_id=sid,
        value_type=value_type,
        note=note,
        charge=lambda: True,
    ).flag
    db.commit()
    return row


def test_a_company_with_only_a_user_flag_is_in_the_queue(world, db):
    ids = _scene(world, db)
    only = _company(db, "ONLYFLAG")
    _card(world, db, "a", [(only, "Fuel")])
    first = _user_flag(db, "a", only, note="a secret note of A")
    _user_flag(db, "b", only, "cash_burn")
    _user_flag(db, "b", ids["BIG"])  # beside a waiting candidate
    got = world["s"].get(QUEUE).json()
    assert (got["total"], got["pen_only_total"]) == (7, 1)
    by = {i["ticker"]: i for i in got["items"]}
    row = by["ONLYFLAG"]
    assert (row["open_flags"], row["shares"], row["cash_burn"], row["cards"]) == (2, None, None, 1)
    assert (
        datetime.fromisoformat(row["waiting_since"]) == first["opened_at"]
    )  # its oldest open flag
    assert by["BIG"]["open_flags"] == 1
    # BIG's candidate is older than its flag: its wait is unchanged
    assert by["BIG"]["waiting_since"] == by["BIG"]["shares"]["candidate"]["recorded_at"]
    assert by["OLD1"]["open_flags"] == 0
    # the order's keys are unchanged: ONLYFLAG has one card and the newest wait, so it sorts after the other
    # one-card companies and before the company on no live card and the pen-only one
    assert [i["ticker"] for i in got["items"]] == [
        "BIG",
        "OLD1",
        "BOTH",
        "NEW1",
        "ONLYFLAG",
        "ZERO",
        "PEN",
    ]


def test_a_flag_older_than_the_candidates_sets_the_wait(world, db):
    sid = _company(db, "EARLY")
    _card(world, db, "b", [(sid, "Fuel")])
    flag = _user_flag(db, "b", sid)
    _flag(db, sid, world["asof"] - timedelta(days=10))
    (row,) = world["s"].get(QUEUE).json()["items"]
    assert row["ticker"] == "EARLY" and row["open_flags"] == 1
    assert row["shares"] is not None
    assert datetime.fromisoformat(row["waiting_since"]) == flag["opened_at"]


def test_a_closed_or_withdrawn_flag_waits_for_nothing(world, db):
    kept, withdrawn = _company(db, "KEPT"), _company(db, "GONE")
    _card(world, db, "a", [(kept, "Fuel"), (withdrawn, "Fuel")])
    k = _user_flag(db, "a", kept)
    w = _user_flag(db, "a", withdrawn)
    assert world["s"].get(QUEUE).json()["total"] == 2
    a_id = _user_id(db, "a")  # (it ends the read's transaction: before the writes below)
    user_flags.close(
        db, k["id"], outcome="kept", note="right", decision_id=None, closed_by="s@q.test"
    )
    user_flags.withdraw(db, w["id"], user_id=a_id)
    db.commit()
    assert world["s"].get(QUEUE).json() == {"items": [], "total": 0, "pen_only_total": 0}


def test_the_queue_names_no_flagging_user_or_note(world, db):
    sid = _company(db, "QUIET")
    _card(world, db, "a", [(sid, "Fuel")])
    f = _user_flag(db, "a", sid, note="a secret note of A")
    text = world["s"].get(QUEUE).text
    assert '"QUIET"' in text
    for word in ("a secret note", "a@q.test", str(f["id"]), str(_user_id(db, "a"))):
        assert word not in text, word


def test_the_statement_count_is_the_same_with_0_and_25_flags(world, db, monkeypatch):
    _scene(world, db)
    s = world["s"]
    assert s.get(QUEUE).status_code == 200  # warm-up
    seen = _statements(monkeypatch)
    assert s.get(QUEUE).json()["total"] == 6
    none = len(seen)
    for n in range(
        25
    ):  # 25 open flags: 20 on companies with only a flag, 5 beside a waiting candidate
        sid = _company(db, f"FLAGGED{n}")
        _card(world, db, "a" if n % 2 else "b", [(sid, "Fuel")])
        if n >= 20:
            _flag(db, sid, world["asof"] - timedelta(days=10))
        _user_flag(db, "a" if n % 2 else "b", sid)
    seen.clear()
    assert s.get(QUEUE).json()["total"] == 31
    assert len(seen) == none, seen
