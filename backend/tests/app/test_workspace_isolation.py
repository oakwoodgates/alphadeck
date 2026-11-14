"""The two-user isolation proof (auth PR 2, docs/TENANT_SCOPES.md): a private workspace per user, shared data.

Built WITHOUT the suite's signed-in override: real users (``app.auth.create_user``) on three provisioned
workspaces, none of them the data tenant ``...0001`` (with the data tenant equal to a user's workspace a missed
filter would read the right rows by accident), and real session cookies from ``POST /auth/login``:

- A (a plain user, workspace W_A) owns a thesis (a random synthetic name) with one member on the shared DATA
  security (the conftest ``security_id``, with DATA bars and an insider row), a ratified revenue-mix fact, a
  decision and a recorded call;
- B (a plain user, W_B) sees none of it: ``GET /theses`` is ``[]``; EVERY ``{thesis_id}`` operation in the schema
  answers 404 for A's id; every list / dashboard GET answers 200 with neither A's id nor A's name in the body; a
  dashboard page computed for A is never served to B (the cache key); B's promote carrying A's id is a 404 and
  A's row is unchanged; B's own thesis on the same security reads the DATA bars and none of A's ratified fact;
  every row written through B's session carries W_B;
- S (a super admin, W_S) sees none of A's work either: ``GET /theses`` is ``[]`` and A's id is a 404;
- the SYSTEM principal names its workspace in ``X-Alphadeck-Workspace`` (400 without it) and a user's header is
  ignored;
- ``WORKSPACE_ANY`` (the every-workspace sentinel) never appears in request-path code;
- the steward routes act on the DATA tenant, never on the caller's workspace (S sits on W_S, not DATA, so a missed
  scope cannot hide): a split in the data tenant is listed and confirmed from Admin, the onboarding readout reads
  the data tenant's queue, and ``get_workspace`` / ``get_current_tenant`` / ``DEFAULT_TENANT_ID`` appear where
  they must not.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import WORKSPACE_HEADER, get_conn
from app.main import app
from dashboards.cache import CACHE
from db.bitemporal import append_fact
from domain.scopes import DATA_TENANT_ID, SCOPE_OF, Scope
from domain.settings import get_settings
from ingest.prices.eod_loader import ingest_prices
from pipeline.call_for_thesis import call_for_thesis
from tests.auth_fixtures import sign_out

PASSWORD = "correct horse battery"
_BACKEND = Path(__file__).resolve().parents[2]
# Steward-only prefixes: a plain user is 403 there (tests/app/test_auth_gate.py proves it), so the list sweep
# below covers the workspace routes; the Admin readouts as a super admin are the one assertion left to Builder B.
_STEWARD = ("/admin", "/backtest")
_SKIP = ("/auth/", "/health")
# GETs whose dummy query values name nothing that exists: a 404 for "no such ticker / run / security" is the
# route working, not a leak (each body is still checked for A's id and name).
_NOT_FOUND_OK = {
    "/dashboards/ticker-lookup/resolve",
    "/scoreboard/editions/diff",  # the dummy edition number names no published edition
}
# a required query value the generic dummy cannot fill meaningfully (the first enum member needs a key)
_QUERY = {"/dashboards/peer-table": {"scope": "all"}}
_DUMMY_UUID = "00000000-0000-0000-0000-0000000000ff"


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


def _synthetic_bars(asof: date, n: int) -> list[dict]:
    days: list[date] = []
    d = asof
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return [
        {
            "d": day,
            "open": 10 + 0.05 * i - 0.1,
            "high": 10 + 0.05 * i + 0.3,
            "low": 10 + 0.05 * i - 0.3,
            "close": 10 + 0.05 * i,
            "volume": 500_000 + 100 * i,
        }
        for i, day in enumerate(days)
    ]


def _promote(c: TestClient, name: str, security_id: uuid.UUID, *, thesis_id=None):
    return c.post(
        "/workbench/theses",
        json={
            "id": str(thesis_id) if thesis_id else None,
            "name": name,
            "narrative": f"{name}: a synthetic narrative for the isolation proof",
            "basket": [{"ticker": "DEVCO", "role": "the name", "security_id": str(security_id)}],
            "segments": [],
        },
    )


def _ratify_mix(c: TestClient, security_id: uuid.UUID, ref: str):
    return c.post(
        "/workbench/facts",
        json={
            "fact_type": "revenue_mix",
            "security_id": str(security_id),
            "source": "10-k-segment",
            "source_ref": ref,
            "event_date": "2025-12-31",
            "segment_label": "core",
            "mix_pct": 100,
        },
    )


@pytest.fixture
def world(db, security_id, pinned_today, monkeypatch):
    """Three workspaces, three real users, A's work seeded through A's own session, DATA facts on the shared
    security. Yields a dict of everything the tests need."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute(
            "TRUNCATE app_user CASCADE"
        )  # the auth tables are not in the shared fixture's truncate
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    CACHE.clear()

    w_a, w_b, w_s = (_workspace(db, f"isolation {x}") for x in ("A", "B", "S"))
    auth.create_user(db, email="a@iso.test", display_name="A", password=PASSWORD, workspace_id=w_a)
    auth.create_user(db, email="b@iso.test", display_name="B", password=PASSWORD, workspace_id=w_b)
    auth.create_user(
        db,
        email="s@iso.test",
        display_name="S",
        password=PASSWORD,
        role="super_admin",
        workspace_id=w_s,
    )
    # the shared DATA layer: a year of bars and one open-market insider buy on the conftest security
    asof = pinned_today
    ingest_prices(db, security_id, _synthetic_bars(asof, 260))
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": DATA_TENANT_ID,
            "security_id": security_id,
            "insider_name": "Synthetic Director",
            "insider_role": "director",
            "txn_code": "P",
            "shares": 10_000,
            "price": 12.0,
            "usd": 120_000.0,
            "accession": "0000000000-26-000001",
            "txn_seq": 0,
            "valid_from": asof - timedelta(days=20),
            "accepted": datetime.combine(
                asof - timedelta(days=19), time(21, 0), tzinfo=timezone.utc
            ),
        },
    )
    db.commit()

    a = _client("a@iso.test")
    a_name = f"Synthetic card {uuid.uuid4().hex[:10]}"
    r = _promote(a, a_name, security_id)
    assert r.status_code == 200, r.text
    a_id = uuid.UUID(r.json()["id"])
    assert _ratify_mix(a, security_id, "https://example.test/a-10k.htm").status_code == 200
    r = a.post(
        f"/theses/{a_id}/decisions",
        json={"action": "pass", "decision_date": asof.isoformat(), "reason": "not yet"},
    )
    assert r.status_code == 200, r.text
    call_for_thesis(
        db, a_id, asof, record=True, workspace=w_a
    )  # a recorded call, under A's workspace
    db.commit()

    yield {
        "asof": asof,
        "sid": security_id,
        "w_a": w_a,
        "w_b": w_b,
        "w_s": w_s,
        "a": a,
        "a_id": a_id,
        "a_name": a_name,
        "b": _client("b@iso.test"),
        "s": _client("s@iso.test"),
    }
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


# --- filling an operation from the schema -------------------------------------------------------------------


def _dummy(schema: dict[str, Any], name: str, w: dict) -> str:
    if "anyOf" in schema:
        schema = next((s for s in schema["anyOf"] if s.get("type") != "null"), schema["anyOf"][0])
    if "$ref" in schema:
        ref = schema["$ref"].rsplit("/", 1)[-1]
        schema = app.openapi()["components"]["schemas"][ref]
    if "enum" in schema:
        return str(schema["enum"][0])
    t, fmt = schema.get("type"), schema.get("format")
    if name == "thesis_id":
        return str(w["a_id"])
    if name == "security_id":
        return str(w["sid"])
    if fmt == "uuid":
        return _DUMMY_UUID
    if fmt == "date":
        return w["asof"].isoformat()
    if t in ("integer", "number"):
        return str(schema.get("minimum", 1))
    if t == "boolean":
        return "false"
    if name in ("t", "ticker", "q"):
        return "DEVCO"
    return "x"


def _fill(path: str, op: dict, w: dict) -> tuple[str, dict[str, str]]:
    params = op.get("parameters", [])
    by_name = {p["name"]: p for p in params}

    def one(m: re.Match) -> str:
        p = by_name.get(m.group(1), {"schema": {"type": "string"}})
        return _dummy(p["schema"], m.group(1), w)

    url = re.sub(r"\{([^}]+)\}", one, path)
    query = {
        p["name"]: _dummy(p["schema"], p["name"], w)
        for p in params
        if p["in"] == "query" and p.get("required")
    }
    return url, query


def _operations():
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            yield method.upper(), path, op


def _call(c: TestClient, method: str, url: str, query: dict[str, str]):
    if method == "GET":
        return c.get(url, params=query)
    return c.request(method, url, params=query, json={})


# --- B sees none of A ------------------------------------------------------------------------------------------


def test_b_lists_no_theses(world):
    assert world["b"].get("/theses").json() == []
    assert world["b"].get("/theses", params={"include_archived": "true"}).json() == []
    # the control: A sees its own card
    assert [t["id"] for t in world["a"].get("/theses").json()] == [str(world["a_id"])]


def test_every_thesis_id_operation_answers_404_to_b(world):
    swept = 0
    wrong: list[str] = []
    for method, path, op in _operations():
        if "{thesis_id}" not in path:
            continue
        url, query = _fill(path, op, world)
        r = _call(world["b"], method, url, query)
        swept += 1
        if r.status_code != 404:
            wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert swept > 30  # every /theses, /workbench, /dashboards and /radar card route
    assert wrong == [], "\n".join(wrong)


def test_a_thesis_id_in_a_query_parameter_is_404_to_b(world):
    """A route that takes the thesis id as a QUERY parameter (the Scoreboard's price window, the fund radar)
    resolves it through the same ownership choke point."""
    hits = 0
    for method, path, op in _operations():
        if "{thesis_id}" in path or path.startswith(_STEWARD):
            continue
        names = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
        if "thesis_id" not in names:
            continue
        url, query = _fill(path, op, world)
        query["thesis_id"] = str(world["a_id"])
        r = _call(world["b"], method, url, query)
        hits += 1
        assert r.status_code == 404, f"{method} {path} -> {r.status_code} {r.text[:200]}"
    assert hits >= 1


def test_every_list_and_dashboard_get_is_200_and_carries_nothing_of_a(world):
    swept = 0
    wrong: list[str] = []
    a_id, a_name = str(world["a_id"]), world["a_name"]
    for method, path, op in _operations():
        if method != "GET" or "{" in path or path.startswith(_STEWARD + _SKIP):
            continue
        params = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
        if "thesis_id" in params and any(
            p["name"] == "thesis_id" and p.get("required") for p in op.get("parameters", [])
        ):
            continue  # a thesis-id route by query: the test above
        url, query = _fill(path, op, world)
        query.update(_QUERY.get(path, {}))
        r = world["b"].get(url, params=query)
        swept += 1
        ok = r.status_code == 200 or (r.status_code == 404 and path in _NOT_FOUND_OK)
        if not ok:
            wrong.append(f"GET {path} -> {r.status_code} {r.text[:160]}")
        if a_id in r.text or a_name in r.text:
            wrong.append(f"GET {path} LEAKS A's card")
    assert swept > 30
    assert wrong == [], "\n".join(wrong)


def test_a_cached_dashboard_page_is_never_served_to_b(world):
    asof = world["asof"].isoformat()
    for page in ("/dashboards/chain-map/cards", "/dashboards/rotation-map"):
        params = {"asof": asof} if page.endswith("cards") else {"asof": asof, "group_by": "card"}
        seen_a = world["a"].get(page, params=params)
        assert seen_a.status_code == 200
        if page.endswith("cards"):
            assert world["a_name"] in seen_a.text  # the control: A's picker lists A's card
        seen_b = world["b"].get(page, params=params)
        assert seen_b.status_code == 200
        assert str(world["a_id"]) not in seen_b.text and world["a_name"] not in seen_b.text


def test_b_cannot_overwrite_a_thesis_by_promoting_its_id(world, db):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM thesis WHERE id = %s", (world["a_id"],))
        before = cur.fetchone()
    r = _promote(world["b"], "B's takeover attempt", world["sid"], thesis_id=world["a_id"])
    assert r.status_code == 404
    assert r.json()["detail"] == "thesis not found"
    with db.cursor() as cur:
        cur.execute("SELECT * FROM thesis WHERE id = %s", (world["a_id"],))
        assert cur.fetchone() == before


def test_b_own_thesis_reads_the_shared_data_and_none_of_a_ratified_fact(world, db):
    b = world["b"]
    r = _promote(b, "B's own synthetic card", world["sid"])
    assert r.status_code == 200, r.text
    b_id = r.json()["id"]
    asof = world["asof"].isoformat()

    # the shared DATA bars: the display signals compute from them
    sig = b.get(f"/theses/{b_id}/display-signals", params={"asof": asof})
    assert sig.status_code == 200, sig.text
    (member,) = sig.json()["members"]
    assert member["signals"], "B's card must read the shared price bars"
    call = b.get(f"/theses/{b_id}/call", params={"asof": asof})
    assert call.status_code == 200, call.text

    # none of A's ratified purity: B's meter is unratified; A's reads 100% (4 pips)
    (b_m,) = b.get(f"/workbench/theses/{b_id}/scored", params={"asof": asof}).json()["members"]
    assert b_m["purity"]["pips"] is None
    (a_m,) = (
        world["a"]
        .get(f"/workbench/theses/{world['a_id']}/scored", params={"asof": asof})
        .json()["members"]
    )
    assert a_m["purity"]["pips"] == 4

    # B ratifies its own judgment and logs a decision: both land in W_B
    assert _ratify_mix(b, world["sid"], "https://example.test/b-10k.htm").status_code == 200
    r = b.post(
        f"/theses/{b_id}/decisions",
        json={"action": "pass", "decision_date": asof, "reason": "B passes"},
    )
    assert r.status_code == 200, r.text

    w_a, w_b = world["w_a"], world["w_b"]
    with db.cursor() as cur:
        for table, scope in SCOPE_OF.items():
            if scope is not Scope.WORKSPACE:
                continue
            col = "thesis_id" if table != "thesis" else "id"
            if not _has_column(cur, table, col):
                continue  # the edition tables: keyed by edition, B wrote none
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE {col} = %s AND tenant_id <> %s",  # noqa: S608
                (b_id, w_b),
            )
            assert cur.fetchone()["n"] == 0, f"{table}: a row of B's card outside W_B"
        cur.execute("SELECT tenant_id FROM thesis WHERE id = %s", (b_id,))
        assert cur.fetchone()["tenant_id"] == w_b
        cur.execute("SELECT tenant_id FROM operator_decision WHERE thesis_id = %s", (b_id,))
        assert {r["tenant_id"] for r in cur.fetchall()} == {w_b}
        cur.execute(
            "SELECT tenant_id, ratified_by FROM fact_revenue_mix WHERE source_ref = %s",
            ("https://example.test/b-10k.htm",),
        )
        row = cur.fetchone()
        assert row["tenant_id"] == w_b and row["ratified_by"] == "b@iso.test"
        # no row of any DATA-scope table sits under a user workspace (the platform tables included: V2)
        for table, scope in SCOPE_OF.items():
            if scope is not Scope.DATA:
                continue
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE tenant_id = ANY(%s)",  # noqa: S608
                ([w_a, w_b],),
            )
            assert cur.fetchone()["n"] == 0, f"{table}: a DATA-scope row under a user workspace"


# --- V2: share counts and cash/burn are platform data the steward sets; the company-catalyst form is retired ----


def _platform_value(c: TestClient, sid, asof: date, fact_type: str):
    common = {
        "security_id": str(sid),
        "source": "10-q-cover",
        "event_date": (asof - timedelta(days=30)).isoformat(),
    }
    if fact_type == "shares_outstanding":
        body = {"source_ref": "https://example.test/10q.htm#cover", "shares": 50_000_000}
    else:
        body = {
            "source_ref": "https://example.test/10q.htm#cash",
            "cash_usd": 30_000_000,
            "quarterly_burn_usd": 3_000_000,
        }
    return c.post("/workbench/facts", json={**common, **body, "fact_type": fact_type})


def _all_rows(db, table: str) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a fixed pair
        return cur.fetchone()["n"]


def test_a_plain_users_share_count_or_cash_confirm_is_refused(world, db):
    """V2 (operator change to D1): only the steward sets a share count or a cash/burn value by hand. A plain
    user's confirm answers 403 with a fixed sentence and writes NO row in ANY tenant."""
    from app.errors import STEWARD_SETS_PLATFORM_VALUE

    for fact_type, table in (
        ("shares_outstanding", "fact_shares_outstanding"),
        ("cash_burn", "fact_cash_burn"),
    ):
        before = _all_rows(db, table)
        r = _platform_value(world["b"], world["sid"], world["asof"], fact_type)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == STEWARD_SETS_PLATFORM_VALUE
        assert _all_rows(db, table) == before, table


def test_the_stewards_confirm_lands_in_the_data_tenant_and_every_workspace_reads_it(world, db):
    """The steward (S, on W_S — not the data tenant) confirms a share count and a cash/burn figure: each row
    lands under the DATA tenant with S's email as the ratifier, and A (on W_A) reads both on A's own card.
    """
    s, sid, asof = world["s"], world["sid"], world["asof"]
    for fact_type in ("shares_outstanding", "cash_burn"):
        r = _platform_value(s, sid, asof, fact_type)
        assert r.status_code == 200, r.text
    db.rollback()
    with db.cursor() as cur:
        for table in ("fact_shares_outstanding", "fact_cash_burn"):
            cur.execute(
                f"SELECT tenant_id, ratified_by FROM {table} WHERE security_id = %s",  # noqa: S608
                (sid,),
            )
            rows = cur.fetchall()
            assert [(x["tenant_id"], x["ratified_by"]) for x in rows] == [
                (DATA_TENANT_ID, "s@iso.test")
            ], table
    (a_m,) = (
        world["a"]
        .get(f"/workbench/theses/{world['a_id']}/scored", params={"asof": asof.isoformat()})
        .json()["members"]
    )
    assert a_m["market_cap"]["value"] is not None  # A reads the steward's count (x A's price)
    assert any(
        p["detail"].get("ratified_by") == "s@iso.test" for p in a_m["market_cap"]["provenance"]
    )
    assert a_m["runway"]["value"] == 30.0  # 30M cash / (3M a quarter / 3) = 30 months


def test_a_plain_users_purity_confirm_works_and_stays_private(world, db):
    """Purity is unchanged by V2's steward rule: B's confirm is a 200, lands in W_B, and A never reads it."""
    b, sid = world["b"], world["sid"]
    assert _ratify_mix(b, sid, "https://example.test/b-only-10k.htm").status_code == 200
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT tenant_id FROM fact_revenue_mix WHERE source_ref = %s",
            ("https://example.test/b-only-10k.htm",),
        )
        assert [r["tenant_id"] for r in cur.fetchall()] == [world["w_b"]]
    (a_m,) = (
        world["a"]
        .get(
            f"/workbench/theses/{world['a_id']}/scored",
            params={"asof": world["asof"].isoformat()},
        )
        .json()["members"]
    )
    assert [p["ref"] for p in a_m["purity"]["provenance"]] == ["https://example.test/a-10k.htm"]


def test_the_company_catalyst_form_is_refused(world, db):
    """V2: a company catalyst is no longer written from the Workbench form. The route answers 409 with a fixed
    sentence and writes nothing (no row under any tenant)."""
    from app.errors import CATALYST_FORM_RETIRED

    r = world["b"].post(
        "/workbench/facts",
        json={
            "fact_type": "catalyst",
            "security_id": str(world["sid"]),
            "catalyst_type": "contract",
            "grade": "core",
            "label": "a signed offtake",
            "source": "ratified",
            "source_ref": "https://example.test/pr/offtake",
            "event_date": world["asof"].isoformat(),
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == CATALYST_FORM_RETIRED
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_catalyst")
        assert cur.fetchone()["n"] == 0


def _has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return cur.fetchone() is not None


# --- the super admin --------------------------------------------------------------------------------------------


def test_the_super_admin_sees_no_other_workspace_theses(world):
    s = world["s"]
    assert s.get("/theses").json() == []
    assert s.get(f"/theses/{world['a_id']}").status_code == 404
    assert (
        s.get(
            f"/theses/{world['a_id']}/call", params={"asof": world["asof"].isoformat()}
        ).status_code
        == 404
    )


def test_admin_readouts_never_name_another_workspace_thesis(world, db):
    """As the super admin, no Admin readout carries another workspace's thesis NAME (ids only, B3). A's card has
    a LATE-ONLY night (a row recorded after its night's next EDGAR open), which ``/admin/status`` lists by name.
    """
    night = world["asof"] - timedelta(days=1)
    while night.weekday() >= 5:
        night -= timedelta(days=1)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, recorded_at) "
            "SELECT tenant_id, thesis_id, %s, state, verdict, card, %s FROM calls WHERE thesis_id = %s "
            "ORDER BY seq DESC LIMIT 1",
            (
                night,
                datetime.combine(night + timedelta(days=3), time(18, 0), tzinfo=timezone.utc),
                world["a_id"],
            ),
        )
    db.commit()
    s = world["s"]
    for page in ("/admin/status", "/admin/runs", "/admin/onboarding"):
        r = s.get(page)
        assert r.status_code == 200, f"{page}: {r.status_code}"
        assert world["a_name"] not in r.text, f"{page} names another workspace's thesis"
    # ...and the late-only night lists it as ids: the workspace and the thesis, 8 hex each
    late = s.get("/admin/status").json()["record"]["late_only_nights"]
    mine = [n for n in late if n["thesis_id"] == str(world["a_id"])]
    assert mine, "the late-only night should list the thesis by id"
    assert mine[0]["thesis_name"] == (
        f"workspace {str(world['w_a'])[:8]} \u00b7 thesis {str(world['a_id'])[:8]}"
    )


# --- the system principal ---------------------------------------------------------------------------------------


def test_the_system_principal_names_its_workspace_in_the_header(world, monkeypatch):
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "system-token-for-the-isolation-suite")
    get_settings.cache_clear()
    sys_client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    bearer = {"Authorization": "Bearer system-token-for-the-isolation-suite"}
    page, params = "/dashboards/chain-map/cards", {"asof": world["asof"].isoformat()}

    missing = sys_client.get(page, params=params, headers=bearer)
    assert missing.status_code == 400
    assert WORKSPACE_HEADER in missing.json()["detail"]

    as_b = sys_client.get(
        page, params=params, headers={**bearer, WORKSPACE_HEADER: str(world["w_b"])}
    )
    assert as_b.status_code == 200 and world["a_name"] not in as_b.text
    as_a = sys_client.get(
        page, params=params, headers={**bearer, WORKSPACE_HEADER: str(world["w_a"])}
    )
    assert as_a.status_code == 200 and world["a_name"] in as_a.text

    # a USER's header is ignored: B naming A's workspace still reads B's own
    sneaky = world["b"].get(page, params=params, headers={WORKSPACE_HEADER: str(world["w_a"])})
    assert sneaky.status_code == 200 and world["a_name"] not in sneaky.text


# --- the grep guard ---------------------------------------------------------------------------------------------


def test_workspace_any_never_appears_in_request_path_code():
    """``WORKSPACE_ANY`` (no workspace filter) is for system code only. Under ``app/`` and ``dashboards/`` and in the
    radar modules a route reaches it can never appear: a request always names the caller's workspace.
    """
    roots = [_BACKEND / "app", _BACKEND / "dashboards"]
    files = [p for r in roots for p in r.rglob("*.py")]
    files += [
        _BACKEND / "radar" / n for n in ("fund_cards.py", "repo.py", "fund_repo.py", "funds.py")
    ]
    offenders = [str(p) for p in files if "WORKSPACE_ANY" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_default_tenant_id_appears_nowhere_in_the_request_path_packages():
    """``db.session.DEFAULT_TENANT_ID`` is for tests and the demo seed only: under ``app/``, ``dashboards/`` and
    ``radar/`` a read names ``DATA_TENANT_ID`` (a DATA read) or the caller's workspace."""
    files = [p for r in ("app", "dashboards", "radar") for p in (_BACKEND / r).rglob("*.py")]
    offenders = [str(p) for p in files if "DEFAULT_TENANT_ID" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_steward_routes_never_act_on_the_callers_workspace():
    """A steward route acts on the DATA tenant or on every workspace by id, never on "the caller's workspace":
    ``get_workspace`` appears in none of the admin routers, and the retired ``get_current_tenant`` nowhere.
    """
    for name in ("admin.py", "admin_splits.py", "admin_onboarding.py"):
        text = (_BACKEND / "app" / "routers" / name).read_text(encoding="utf-8")
        assert "get_workspace" not in text, name
    offenders = [
        str(p)
        for p in (_BACKEND / "app").rglob("*.py")
        if "get_current_tenant" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --- the steward routes read and write the DATA tenant (S sits on W_S) ---------------------------------------


def test_the_steward_confirms_a_data_tenant_split_and_reads_the_onboarding_queue(world, db):
    from domain.splits import STATUS_NEEDS_REVIEW
    from pipeline import onboarding
    from repositories import split_repo

    sid, s = world["sid"], world["s"]
    key = date(2026, 5, 18)
    seeded = split_repo.append_version(
        db,
        tenant_id=DATA_TENANT_ID,
        security_id=sid,
        key_date=key,
        values={
            "status": STATUS_NEEDS_REVIEW,
            "polygon_split_from": 1,
            "polygon_split_to": 8,
            "polygon_execution_date": key,
            "effective_date": key,
            "value_source": "polygon",
            "ratio_from": 1,
            "ratio_to": 8,
            "valid_from": key,
        },
        action=split_repo.ACTION_DETECT,
        author=split_repo.AUTHOR_DETECT,
    )
    onboarding.enqueue(db, [sid], tenant_id=DATA_TENANT_ID, source="test")
    db.commit()

    def count(tenant):
        with db.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM security_split WHERE security_id = %s AND tenant_id = %s",
                (sid, tenant),
            )
            return cur.fetchone()["n"]

    before_data, before_ws = count(DATA_TENANT_ID), count(world["w_s"])
    listed = s.get("/admin/splits")
    assert listed.status_code == 200, listed.text
    assert [x["version_id"] for x in listed.json()["needs_review"]] == [str(seeded.id)]
    assert s.get("/admin/status").json()["splits_needs_review"] == 1

    r = s.post(f"/admin/splits/{seeded.id}/confirm", json={"source": "polygon"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("confirmed", "announced")
    # exactly one new version, under the DATA tenant, none under the steward's workspace
    assert count(DATA_TENANT_ID) == before_data + 1
    assert count(world["w_s"]) == before_ws == 0

    onb = s.get("/admin/onboarding")
    assert onb.status_code == 200, onb.text
    assert sum(onb.json()["counts"].values()) >= 1  # the data tenant's queued row is read
