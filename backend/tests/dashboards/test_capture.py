"""Capture contract, pinned clocks, strict bounds and actual PostgreSQL isolation/read-only behavior."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus

from agent_access.references import MAX_REFERENCES, validate_references
from app.deps import get_conn
from app.main import app
from app.routers.dashboards import capture
from app.routers.dashboards.capture_adapters import adapters
from app.routers.dashboards.capture_adapters import board as board_adapter
from app.routers.dashboards.capture_contract import MAX_CARDS, response
from app.routers.dashboards.capture_links import (
    MAX_RELATIONSHIPS,
    resolve_pointer,
    validate_relationships,
)
from dashboards.cache import CACHE
from db.session import DEFAULT_TENANT_ID, connect
from domain.market_time import MarketClock, known_at_for_asof
from domain.thesis import Thesis
from repositories import thesis_repo

PIN = MarketClock(date(2026, 6, 2), date(2026, 6, 1), date(2026, 6, 2))


class StubConnection:
    def __init__(self):
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.sql = []
        self.count = 0

    @contextmanager
    def transaction(self):
        yield

    @contextmanager
    def cursor(self):
        yield self

    def execute(self, sql):
        self.sql.append(sql)

    def fetchone(self):
        return {"n": self.count}


@pytest.fixture
def client(monkeypatch):
    conn = StubConnection()
    seen = []
    monkeypatch.setattr(capture, "market_clock", lambda: PIN)
    monkeypatch.setattr(capture, "_generation", lambda c: "generation-1")

    def board(c, request):
        seen.append((request.asof, request.context))
        return [response("/theses", [], include_archived=True)]

    monkeypatch.setattr(board_adapter, "read", board)
    app.dependency_overrides[get_conn] = lambda: conn
    try:
        yield TestClient(app), conn, seen
    finally:
        app.dependency_overrides.clear()


def test_default_night_and_live_decisions_share_one_market_clock(client):
    api, conn, seen = client
    response = api.get("/dashboards/capture", params={"dashboard": "board"})
    assert response.status_code == 200
    body = response.json()
    assert body["asof"] == "2026-06-01"
    assert body["context"]["known_at"] is None
    assert body["context"]["clock"] == "record"
    assert body["context"]["decisions_asof"] == body["context"]["countdown_origin"] == "2026-06-02"
    assert body["responses"][0]["body"] == body["context"]["clock_response"]
    assert body["responses"][1]["query"] == {"include_archived": "true"}
    assert seen[0][0] == PIN.night
    assert conn.sql[:2] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "SET LOCAL statement_timeout = '120s'",
    ]


def test_past_night_uses_public_clock_and_the_edgar_cutoff(client):
    api, _, seen = client
    response = api.get("/dashboards/capture", params={"dashboard": "board", "asof": "2026-05-29"})
    assert response.status_code == 200
    asof, context = seen[0]
    assert context.known_at == known_at_for_asof(asof)
    assert context.clock == "public"
    assert context.countdown_origin == context.decisions_asof == asof


@pytest.mark.parametrize(
    "params",
    [
        {"dashboard": "unknown"},
        {"dashboard": "board", "asof": "bad"},
        {"dashboard": "rotation-map", "group_by": "mood"},
        {"dashboard": "narrative-index", "window": "5y"},
    ],
)
def test_capture_validates_allowlisted_dashboard_and_typed_options(client, params):
    api, _, seen = client
    assert api.get("/dashboards/capture", params=params).status_code == 422
    assert not seen


def test_no_partial_capture_when_generation_moves(client, monkeypatch):
    api, _, _ = client
    tokens = iter(["before", "after"])
    monkeypatch.setattr(capture, "_generation", lambda conn: next(tokens))
    response = api.get("/dashboards/capture", params={"dashboard": "board"})
    assert response.status_code == 409
    assert "responses" not in response.json()


def test_card_limit_precedes_expensive_reads_and_byte_limit_never_truncates(client, monkeypatch):
    api, conn, seen = client
    conn.count = MAX_CARDS + 1
    assert api.get("/dashboards/capture", params={"dashboard": "board"}).status_code == 413
    assert not seen
    conn.count = 0
    monkeypatch.setattr(capture, "MAX_BYTES", 10)
    response = api.get("/dashboards/capture", params={"dashboard": "board"})
    assert response.status_code == 413
    assert "responses" not in response.json()


def test_existing_transaction_is_rejected_instead_of_claiming_repeatable_read(client):
    api, conn, seen = client
    conn.info.transaction_status = TransactionStatus.INTRANS
    assert api.get("/dashboards/capture", params={"dashboard": "board"}).status_code == 503
    assert not seen


def test_total_work_budget_returns_no_partial_capture(client, monkeypatch):
    api, _, seen = client
    monkeypatch.setattr(capture, "MAX_SECONDS", -1)
    result = api.get("/dashboards/capture", params={"dashboard": "board"})
    assert result.status_code == 504 and "responses" not in result.json()
    assert not seen


def test_registered_adapter_dispatch_requires_no_new_central_switch(client, monkeypatch):
    api, _, _ = client
    adapter = SimpleNamespace(
        INCLUDE_ARCHIVED=False, read=lambda conn, request: [response("/synthetic", {"ok": True})]
    )
    monkeypatch.setitem(adapters(), "synthetic", adapter)
    result = api.get("/dashboards/capture", params={"dashboard": "synthetic"})
    assert result.status_code == 200
    assert result.json()["responses"][1]["path"] == "/synthetic"


@pytest.fixture
def db_client(db, monkeypatch):
    monkeypatch.setattr(capture, "market_clock", lambda: PIN)
    monkeypatch.setattr(capture, "_generation", lambda conn: "stable-external-generation")
    app.dependency_overrides[get_conn] = lambda: db
    CACHE.clear()
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def test_database_actually_forbids_writes_and_preserves_a_concurrent_snapshot(
    db_client, monkeypatch
):
    api, db = db_client
    thesis = Thesis(id=uuid4(), tenant_id=DEFAULT_TENANT_ID, name="before", narrative="fixture")
    thesis_repo.upsert(db, thesis)
    db.commit()
    observed = {}

    def probe(conn, request):
        observed["isolation"] = conn.execute("SHOW transaction_isolation").fetchone()[
            "transaction_isolation"
        ]
        before = thesis_repo.get(conn, thesis.id).name
        with connect() as writer:
            writer.execute("UPDATE thesis SET name = 'after' WHERE id = %s", (thesis.id,))
        after = thesis_repo.get(conn, thesis.id).name
        observed["names"] = (before, after)
        # A savepoint contains the intentional violation; the enclosing capture remains valid.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            with conn.transaction():
                conn.execute("UPDATE thesis SET name = 'forbidden' WHERE id = %s", (thesis.id,))
        return []

    monkeypatch.setattr(board_adapter, "read", probe)
    response = api.get("/dashboards/capture", params={"dashboard": "board"})
    assert response.status_code == 200
    assert observed == {"isolation": "repeatable read", "names": ("before", "before")}
    assert thesis_repo.get(db, thesis.id).name == "after"


def test_board_matches_existing_call_and_never_appends_calls(db_client):
    api, db = db_client
    thesis = Thesis(id=uuid4(), tenant_id=DEFAULT_TENANT_ID, name="Board fixture", narrative="x")
    thesis_repo.upsert(db, thesis)
    db.commit()
    response = api.get("/dashboards/capture", params={"dashboard": "board", "asof": "2026-05-29"})
    assert response.status_code == 200, response.text
    records = {r["path"]: r for r in response.json()["responses"]}
    path = f"/theses/{thesis.id}/call"
    assert records[path]["body"] == api.get(path, params={"asof": "2026-05-29"}).json()
    assert (
        records["/theses"]["body"] == api.get("/theses", params={"include_archived": "true"}).json()
    )
    assert (
        records["/data-flags"]["body"]
        == api.get("/data-flags", params={"asof": "2026-05-29"}).json()
    )
    assert db.execute("SELECT count(*) AS n FROM calls").fetchone()["n"] == 0


@pytest.mark.parametrize("dashboard", ["rotation-map", "narrative-index"])
def test_capture_bypasses_shared_cache_and_matches_canonical_empty_page(
    db_client, monkeypatch, dashboard
):
    api, db = db_client

    def no_cache(*args, **kwargs):
        raise AssertionError("a capture may never reuse a foreign database snapshot")

    with monkeypatch.context() as m:
        m.setattr(CACHE, "get", no_cache)
        result = api.get(
            "/dashboards/capture", params={"dashboard": dashboard, "asof": "2026-05-29"}
        )
    assert result.status_code == 200, result.text
    payload = result.json()["responses"][1]["body"]
    ordinary = api.get(f"/dashboards/{dashboard}", params={"asof": "2026-05-29"}).json()
    for body in (payload, ordinary):
        body.pop("compute_ms")
        body.pop("cache")
    assert payload == ordinary


def test_narrative_capture_keeps_detail_on_the_same_inputs_and_public_clock(db_client):
    # Reuse the real price fixture builders, including a later published correction, from the
    # canonical dashboard test; compare the capture and its own detail, not just mocked wiring.
    from domain.thesis import BasketMember
    from tests.dashboards.test_narrative_index import ASOF, _price, _security, bar_days

    api, db = db_client
    sid = _security(db, "CAPT")
    for i, day in enumerate(bar_days(ASOF, 40)):
        _price(
            db, sid, day, 10 + i, datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        )
    _price(db, sid, ASOF, 1000, datetime(2026, 9, 10, tzinfo=timezone.utc))
    thesis = Thesis(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Captured",
        narrative="x",
        basket=[BasketMember(ticker="CAPT", role="x", security_id=sid)],
    )
    thesis_repo.upsert(db, thesis)
    db.execute("UPDATE thesis SET created_at = '2026-01-02' WHERE id = %s", (thesis.id,))
    db.commit()
    # ASOF is the pinned live boundary; move the boundary forward so this is historical.
    capture_clock = MarketClock(date(2026, 6, 3), date(2026, 6, 2), date(2026, 6, 3))
    with pytest.MonkeyPatch.context() as m:
        m.setattr(capture, "market_clock", lambda: capture_clock)
        result = api.get(
            "/dashboards/capture",
            params={"dashboard": "narrative-index", "asof": ASOF.isoformat(), "window": "30d"},
        )
    assert result.status_code == 200, result.text
    responses = {r["path"]: r["body"] for r in result.json()["responses"]}
    page = responses["/dashboards/narrative-index"]
    detail = responses[f"/dashboards/narrative-index/{thesis.id}"]
    assert page["cards"][0] == detail["card"]
    assert page["dates"] == detail["dates"]
    assert detail["card"]["points"][-1] / detail["card"]["points"][-2] == pytest.approx(
        49 / 48, rel=1e-4
    )
    assert detail["members"][0]["ticker"] == "CAPT"


# --- Board evidence references: each active card's /theses row, call decision fields, flag entries ---

NO_BUDGET = SimpleNamespace(check_budget=lambda: None)  # relationships() only asks the work budget
ASOF_WIRE = "2026-05-29"


def _synthetic_call(thesis_id: str) -> dict:
    return {
        "thesis_id": thesis_id,
        "asof": ASOF_WIRE,
        "state": "armed",
        "verdict": "starter_entry",
        "conviction_grade": "core",
        "confirmation_grade": None,
        "entry_grade": "flip",
        "expression": "Synthetic expression",
        "exit_by": "2026-07-01",
        "arm_until": "2026-06-05",
        "triggers_fired": [{"label": "Synthetic trigger", "sources": []}],
        "risk_signals": [{"label": "Synthetic risk", "sources": []}],
        "missing": ["Synthetic missing condition"],
        "counter_case": "Synthetic counter-case",
        "armed_members": [{"security_id": str(uuid4()), "ticker": "SYNA"}],
        "watch_members": [],
    }


def _flag_entry(ticker: str | None, *holders: str) -> dict:
    return {
        "security_id": str(uuid4()),
        "ticker": ticker,
        "marked": True,
        "flags": [{"kind": "synthetic", "label": "Synthetic flag", "call_affected": False}],
        "theses": [{"id": holder, "name": "Synthetic holder"} for holder in holders],
    }


def _board_responses(theses: list[dict], calls: dict[str, dict], members: list[dict]):
    return [
        response("/clock", {}),
        response("/theses", theses, include_archived=True),
        *(response(f"/theses/{tid}/call", body, asof=ASOF_WIRE) for tid, body in calls.items()),
        response("/data-flags", {"asof": ASOF_WIRE, "members": members}, asof=ASOF_WIRE),
    ]


def test_board_references_point_each_active_card_at_its_frozen_evidence():
    a, archived, c = (str(uuid4()) for _ in range(3))
    theses = [
        {"id": a, "name": "Synthetic A", "archived": False},
        {"id": archived, "name": "Synthetic B", "archived": True},
        {"id": c, "name": "Synthetic C", "archived": False},
    ]
    call_a, call_c = _synthetic_call(a), _synthetic_call(c)
    del call_c["exit_by"]  # absent from the JSON: no reference
    call_c["conviction_grade"] = None  # null: still referenced
    members = [
        _flag_entry("SYNA", a),
        _flag_entry("SYNB", a, c),  # one entry two cards hold: ONE reference
        _flag_entry(None, c),  # no ticker: labeled by its security id
        _flag_entry("SYND", archived),  # held only by an archived card: never referenced
    ]
    responses = _board_responses(theses, {a: call_a, c: call_c}, members)
    links, refs = board_adapter.relationships(responses, NO_BUDGET)
    validate_relationships(responses, links, refs)  # every pointer resolves, IDs are unique

    # the navigation links are unchanged: a Cockpit link per active card + its headline name
    assert len(links) == 4
    assert {link.destination.settings["thesis_id"] for link in links} == {a, c}
    assert len({r.id for r in refs}) == len(refs)
    again = board_adapter.relationships(responses, NO_BUDGET)[1]
    assert [r.id for r in again] == [r.id for r in refs]  # stable IDs

    def resolved(ref):
        return resolve_pointer(responses[ref.response_index].body, ref.pointer)

    def path(ref):
        return responses[ref.response_index].path

    rows = [r for r in refs if r.category == "thesis_summary"]
    assert [(path(r), r.pointer, r.label) for r in rows] == [
        ("/theses", "/0", "theses[0]"),
        ("/theses", "/2", "theses[2]"),
    ]
    assert [resolved(r)["id"] for r in rows] == [a, c]  # the index IS the array position

    def call_refs(tid):
        return [r for r in refs if r.category == "call_result" and path(r) == f"/theses/{tid}/call"]

    fields = list(board_adapter.CALL_DECISION_FIELDS)
    assert [r.label for r in call_refs(a)] == fields
    assert [r.pointer for r in call_refs(a)] == [f"/{field}" for field in fields]
    assert [resolved(r) for r in call_refs(a)] == [call_a[field] for field in fields]
    assert [r.label for r in call_refs(c)] == [field for field in fields if field != "exit_by"]
    assert resolved(next(r for r in call_refs(c) if r.label == "conviction_grade")) is None
    assert {path(r) for r in refs if r.category == "call_result"} == {
        f"/theses/{a}/call",
        f"/theses/{c}/call",
    }

    flags = [r for r in refs if r.category == "data_flag"]
    assert [(path(r), r.pointer, r.label) for r in flags] == [
        ("/data-flags", "/members/0", "SYNA"),
        ("/data-flags", "/members/1", "SYNB"),
        ("/data-flags", "/members/2", members[2]["security_id"]),
    ]
    assert all(resolved(r) == members[int(r.pointer.rsplit("/", 1)[1])] for r in flags)

    # nothing points at the archived card: not its row, not its call, not a flag only it holds
    assert all(r.pointer != "/1" for r in rows)
    assert all(archived not in path(r) for r in refs)
    assert "/members/3" not in {r.pointer for r in flags}
    # in /theses order, one card's evidence together: its row, its call fields, its first-seen flags
    assert [r.category for r in refs] == (
        ["thesis_summary"] + ["call_result"] * 11 + ["data_flag"] * 2
    ) + (["thesis_summary"] + ["call_result"] * 10 + ["data_flag"])


def test_board_without_flags_or_cards_advertises_only_what_it_captured():
    tid = str(uuid4())
    only_theses = [response("/clock", {}), response("/theses", [{"id": tid, "archived": False}])]
    _, refs = board_adapter.relationships(only_theses, NO_BUDGET)
    assert [(r.category, r.pointer) for r in refs] == [("thesis_summary", "/0")]
    assert board_adapter.relationships([response("/clock", {})], NO_BUDGET) == ([], [])


def test_board_references_fit_both_caps_at_max_cards_with_realistic_flags():
    # Realistic flag volume: prod's /data-flags measured 162,607 characters at 66 cards (2026-11-04).
    # A member entry is at least ~394 characters (a lone quiet note), so at most ~413 entries at 66
    # cards: ~626 at MAX_CARDS. Each entry here is held by two cards; a shared entry is ONE reference.
    ids = [str(uuid4()) for _ in range(MAX_CARDS)]
    theses = [{"id": tid, "name": f"Synthetic {i}", "archived": False} for i, tid in enumerate(ids)]
    members = [
        _flag_entry(f"SY{j:03d}", ids[j % MAX_CARDS], ids[(j + 1) % MAX_CARDS]) for j in range(626)
    ]
    responses = _board_responses(theses, {tid: _synthetic_call(tid) for tid in ids}, members)
    links, refs = board_adapter.relationships(responses, NO_BUDGET)
    per_card = 1 + len(board_adapter.CALL_DECISION_FIELDS)
    assert len(refs) == MAX_CARDS * per_card + len(members) == 1826
    assert len(refs) <= MAX_RELATIONSHIPS == MAX_REFERENCES  # the two caps move in lockstep
    validate_relationships(responses, links, refs)
    envelope = {
        "responses": [r.model_dump(mode="json") for r in responses],
        "navigation": [link.model_dump(mode="json") for link in links],
        "references": [r.model_dump(mode="json") for r in refs],
    }
    validate_references(envelope)  # the MCP side accepts the same envelope


def test_board_capture_references_every_decision_field_of_the_real_call(db_client):
    api, db = db_client
    live = Thesis(id=uuid4(), tenant_id=DEFAULT_TENANT_ID, name="Board live", narrative="x")
    gone = Thesis(id=uuid4(), tenant_id=DEFAULT_TENANT_ID, name="Board archived", narrative="x")
    thesis_repo.upsert(db, live)
    thesis_repo.upsert(db, gone)
    thesis_repo.set_archived(db, gone.id, True)
    db.commit()
    result = api.get("/dashboards/capture", params={"dashboard": "board", "asof": "2026-05-29"})
    assert result.status_code == 200, result.text
    envelope = result.json()
    validate_references(envelope)
    responses = envelope["responses"]
    rows_at = next(k for k, r in enumerate(responses) if r["path"] == "/theses")
    i = next(k for k, row in enumerate(responses[rows_at]["body"]) if row["id"] == str(live.id))
    refs = envelope["references"]
    assert [
        (r["pointer"], r["label"])
        for r in refs
        if r["response_index"] == rows_at and r["category"] == "thesis_summary"
    ] == [(f"/{i}", f"theses[{i}]")]
    call_at = next(k for k, r in enumerate(responses) if r["path"] == f"/theses/{live.id}/call")
    # every listed field exists on the served call, so a misspelled field can never silently drop
    assert [r["label"] for r in refs if r["response_index"] == call_at] == list(
        board_adapter.CALL_DECISION_FIELDS
    )
    assert all(r["category"] == "call_result" for r in refs if r["response_index"] == call_at)
    assert not any(str(gone.id) in responses[r["response_index"]]["path"] for r in refs)
