"""Synthetic Cockpit capture parity, provenance pointers and fresh-navigation date context."""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent_access import references as mcp_references
from app.deps import get_conn
from app.main import app
from app.routers import theses
from app.routers.dashboards import capture
from app.routers.dashboards.capture_adapters import board, cockpit
from app.routers.dashboards.capture_contract import CaptureContext, CaptureRequest, response
from app.routers.dashboards.capture_links import (
    CONTEXT_HASH_EXCLUDED,
    context_hash,
    navigation,
    reference,
    resolve_pointer,
    validate_relationships,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain import market_time
from domain.market_time import MarketClock, known_at_for_asof
from domain.thesis import BasketMember, Evidence, Thesis
from repositories import decisions_repo, thesis_repo
from tests.dashboards.test_capture import StubConnection

PIN = MarketClock(date(2026, 6, 2), date(2026, 6, 1), date(2026, 6, 2))
ASOF = date(2026, 5, 29)


def context(asof=ASOF):
    known_at = known_at_for_asof(asof) if asof < PIN.night else None
    return CaptureContext(
        clock_response={
            "current_night": PIN.night,
            "action_day": PIN.action_day,
            "market_today": PIN.today,
            "market_tz": "America/New_York",
        },
        clock="public" if known_at else "record",
        known_at=known_at,
        decisions_asof=market_time.serve_decisions_asof(asof, PIN),
        countdown_origin=market_time.countdown_origin(asof, PIN),
        limitations=[],
    )


def request(thesis_id):
    return CaptureRequest(ASOF, context(), PIN, "link", False, "1y", float("inf"), thesis_id)


@pytest.fixture
def stub_client(monkeypatch):
    conn = StubConnection()
    seen = []
    monkeypatch.setattr(capture, "market_clock", lambda: PIN)
    monkeypatch.setattr(capture, "_generation", lambda conn: "stable")

    def read(conn, request):
        seen.append(request)
        return []

    monkeypatch.setattr(cockpit, "read", read)
    app.dependency_overrides[get_conn] = lambda: conn
    try:
        yield TestClient(app), conn, seen
    finally:
        app.dependency_overrides.clear()


def test_thesis_scope_validation_and_unrelated_deployment_card_limit(stub_client):
    client, conn, seen = stub_client
    conn.count = 1001
    tid = str(uuid4())
    for params in (
        {"dashboard": "cockpit"},
        {"dashboard": "cockpit", "thesis_id": "bad"},
        {"dashboard": "board", "thesis_id": tid},
    ):
        assert client.get("/dashboards/capture", params=params).status_code == 422
    assert not seen
    result = client.get("/dashboards/capture", params={"dashboard": "cockpit", "thesis_id": tid})
    assert result.status_code == 200
    assert len(seen) == 1 and str(seen[0].thesis_id) == tid
    assert not any("count(*)" in sql for sql in conn.sql)


def test_expected_context_matches_wire_canonicalization_and_rejects_before_work(
    stub_client, monkeypatch
):
    client, conn, seen = stub_client
    payload = {
        "asof": ASOF.isoformat(),
        "clock_response": {
            "current_night": "2026-06-01",
            "action_day": "2026-06-02",
            "market_today": "2026-06-02",
            "market_tz": "America/New_York",
        },
        "clock": "public",
        "known_at": "2026-06-01T09:59:59.999999Z",
        "decisions_asof": ASOF.isoformat(),
        "countdown_origin": ASOF.isoformat(),
        "scope": "deployment_visibility_not_authorization",
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    assert context_hash(ASOF.isoformat(), context()) == expected
    params = {
        "dashboard": "cockpit",
        "thesis_id": str(uuid4()),
        "asof": ASOF.isoformat(),
        "expected_context_hash": expected,
    }
    assert client.get("/dashboards/capture", params=params).status_code == 200
    seen.clear()
    conn.sql.clear()
    # Same explicit date becomes historical as the live boundary rolls; its clock context must not drift.
    monkeypatch.setattr(
        capture,
        "market_clock",
        lambda: MarketClock(date(2026, 6, 3), date(2026, 6, 2), date(2026, 6, 3)),
    )
    result = client.get("/dashboards/capture", params=params)
    assert result.status_code == 409 and result.json()["detail"] == "context_changed"
    assert not seen and not conn.sql
    params["expected_context_hash"] = "not-a-hash"
    assert client.get("/dashboards/capture", params=params).status_code == 422


# One valid change per CaptureContext field. A new field fails the test below until it has an
# entry here, and then it is guarded by the hash with no edit to the hash itself.
CONTEXT_CHANGES = {
    "clock_response": lambda c: c.clock_response.model_copy(
        update={"market_today": c.clock_response.market_today + timedelta(days=1)}
    ),
    "clock": lambda c: "record" if c.clock == "public" else "public",
    "known_at": lambda c: (c.known_at or datetime(2026, 5, 30, tzinfo=timezone.utc))
    + timedelta(microseconds=1),
    "decisions_asof": lambda c: c.decisions_asof - timedelta(days=1),
    "countdown_origin": lambda c: c.countdown_origin + timedelta(days=1),
    "scope": lambda c: "synthetic_other_scope",
    "limitations": lambda c: [*c.limitations, "Synthetic per-surface note."],
}


def mcp_hash(asof: str, context: CaptureContext) -> str:
    """The MCP's hash over the context exactly as it crosses the wire."""
    wire = json.loads(context.model_dump_json())
    return mcp_references.context_hash({"asof": asof, "context": wire})


@pytest.mark.parametrize("field", list(CaptureContext.model_fields))
def test_context_hash_guards_every_field_except_limitations(field):
    assert field in CONTEXT_CHANGES, f"add a synthetic change for the new context field {field!r}"
    base = context()
    changed = base.model_copy(update={field: CONTEXT_CHANGES[field](base)})
    assert changed.model_dump()[field] != base.model_dump()[field]
    asof = ASOF.isoformat()
    unchanged = context_hash(asof, changed) == context_hash(asof, base)
    assert unchanged is (field == "limitations")
    assert mcp_hash(asof, changed) == context_hash(asof, changed)


@pytest.mark.parametrize("asof", [ASOF, PIN.night])  # public clock, then record clock
def test_backend_and_mcp_context_hashes_agree_on_the_serialized_context(asof):
    ctx = context(asof).model_copy(update={"limitations": ["Synthetic per-surface note."]})
    assert ctx.clock == ("public" if asof < PIN.night else "record")
    assert mcp_hash(asof.isoformat(), ctx) == context_hash(asof.isoformat(), ctx)
    assert mcp_references.CONTEXT_HASH_EXCLUDED == CONTEXT_HASH_EXCLUDED == ("limitations",)


@pytest.fixture
def db_client(db, monkeypatch):
    monkeypatch.setattr(capture, "market_clock", lambda: PIN)
    monkeypatch.setattr(market_time, "market_clock", lambda *args, **kwargs: PIN)
    monkeypatch.setattr(theses, "market_clock", lambda: PIN)
    monkeypatch.setattr(theses, "market_today", lambda: PIN.today)
    monkeypatch.setattr(capture, "_generation", lambda conn: "stable")
    app.dependency_overrides[get_conn] = lambda: db
    try:
        yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("asof", [ASOF, PIN.night])
def test_cockpit_all_reads_equal_existing_api_and_no_persistence(db_client, asof):
    client, db = db_client
    thesis = Thesis(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Synthetic capture",
        narrative="Synthetic current metadata",
        evidence=[
            Evidence(id=uuid4(), kind="DATA", label="Synthetic evidence", ref="synthetic:source")
        ],
    )
    thesis_repo.upsert(db, thesis)
    decision = decisions_repo.append(
        db, thesis_id=thesis.id, action="take", decision_date=PIN.today, shares=1, price=10
    )
    db.commit()
    result = client.get(
        "/dashboards/capture",
        params={"dashboard": "cockpit", "thesis_id": str(thesis.id), "asof": asof.isoformat()},
    )
    assert result.status_code == 200, result.text
    captured = result.json()
    assert len(captured["responses"]) == 7
    for record in captured["responses"]:
        if record["path"] != "/clock":
            canonical = client.get(record["path"], params=record["query"])
            assert canonical.status_code == 200, canonical.text
            assert record["body"] == canonical.json()
    assert any("decision log are current" in text for text in captured["context"]["limitations"])
    records = {item["path"]: item["body"] for item in captured["responses"]}
    assert records[f"/theses/{thesis.id}/decisions"][0]["id"] == str(decision["id"])
    assert records[f"/theses/{thesis.id}"]["position"]["opened_on"] == PIN.today.isoformat()
    assert db.execute("SELECT count(*) AS n FROM calls").fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM operator_decision").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM basket_snapshot").fetchone()["n"] == 1
    for item in captured["references"]:
        assert (
            resolve_pointer(captured["responses"][item["response_index"]]["body"], item["pointer"])
            is not None
        )


def test_missing_cockpit_thesis_is_404(db_client):
    client, _ = db_client
    result = client.get(
        "/dashboards/capture", params={"dashboard": "cockpit", "thesis_id": str(uuid4())}
    )
    assert result.status_code == 404


def test_historical_display_and_its_evidence_exclude_a_later_price_correction(
    db_client, security_id
):
    client, db = db_client
    thesis = Thesis(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Synthetic price history",
        narrative="fixture",
        basket=[BasketMember(ticker="DEVCO", role="fixture", security_id=security_id)],
    )
    thesis_repo.upsert(db, thesis)
    for close, recorded_at in (
        (10, datetime(2026, 5, 29, tzinfo=timezone.utc)),
        (99, datetime(2026, 6, 15, tzinfo=timezone.utc)),
    ):
        append_fact(
            db,
            "fact_price_eod",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": security_id,
                "d": ASOF,
                "valid_from": ASOF,
                "close": close,
                "volume": 1000,
                "recorded_at": recorded_at,
            },
        )
    db.commit()
    result = client.get(
        "/dashboards/capture",
        params={"dashboard": "cockpit", "thesis_id": str(thesis.id), "asof": ASOF.isoformat()},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    display = next(item for item in body["responses"] if item["path"].endswith("/display-signals"))
    signal = next(
        item for item in display["body"]["members"][0]["signals"] if item["kind"] == "price_path"
    )
    assert signal["series"][0]["values"][-1] == 10
    reference_objects = [
        resolve_pointer(body["responses"][item["response_index"]]["body"], item["pointer"])
        for item in body["references"]
        if item["category"] == "display_basis"
    ]
    assert signal in reference_objects
    assert signal["basis"]["window_end"] == ASOF.isoformat()


def test_provenance_relationships_point_to_exact_sources_and_are_bounded_stable_ids():
    tid, sid = uuid4(), uuid4()
    base = f"/theses/{tid}"
    provenance = {
        "source": "synthetic",
        "ref": "source/with~characters",
        "url": "https://example.invalid/source",
        "detail": {"raw": 7, "formula": "a/b"},
    }
    trigger = {"label": "Synthetic trigger", "sources": [provenance]}
    responses = [
        response("/clock", {}),
        response(
            base,
            {
                "basket": [{"security_id": str(sid), "ticker": "SYN"}] * 2,
                "evidence": [{"label": "Authored", "ref": "synthetic:ref"}],
            },
        ),
        response(
            base + "/call",
            {
                "triggers_fired": [trigger],
                "risk_signals": [trigger],
                "armed_members": [{"triggers": [trigger]}],
                "watch_members": [{"triggers": [trigger]}],
            },
            asof=ASOF,
        ),
        response(
            base + "/display-signals",
            {"members": [{"signals": [{"label": "Display", "basis": {"params": {"window": 20}}}]}]},
            asof=ASOF,
        ),
        response(
            base + "/data-flags",
            {
                "members": [
                    {
                        "flags": [
                            {
                                "label": "Flag",
                                "evidence": [
                                    {"label": "Source", "url": "https://example.invalid/flag"}
                                ],
                            }
                        ]
                    }
                ]
            },
            asof=ASOF,
        ),
        response(
            "/workbench" + base + "/scored",
            {"members": [{"ticker": "SYN", "purity": {"provenance": [provenance]}}]},
            asof=ASOF,
        ),
    ]
    links, refs = cockpit.relationships(responses, request(tid))
    validate_relationships(responses, links, refs)
    assert len(links) == 1  # one member placed twice remains one navigation destination
    assert links[0].destination.settings["selected_name"] == str(sid)
    assert len({r.id for r in refs}) == len(refs) and all(len(r.id) == 28 for r in refs)
    assert all(
        r.id == again.id
        for r, again in zip(refs, cockpit.relationships(responses, request(tid))[1], strict=True)
    )
    call_sources = [r for r in refs if r.category == "call_input"]
    assert len(call_sources) == 4
    assert all(
        resolve_pointer(responses[r.response_index].body, r.pointer) == provenance
        for r in call_sources
    )
    assert {r.category for r in refs} == {
        "call_result",
        "call_signal",
        "call_input",
        "thesis_metadata",
        "authored_evidence",
        "display_basis",
        "data_flag",
        "scored_member",
        "scored_provenance",
    }


def test_board_links_are_explicit_fresh_captures_and_archive_rows_have_no_link():
    tid, archived, sid = uuid4(), uuid4(), uuid4()
    responses = [
        response(
            "/theses",
            [{"id": str(tid), "archived": False}, {"id": str(archived), "archived": True}],
        ),
        response(
            f"/theses/{tid}/call",
            {"thesis_id": str(tid), "armed_members": [{"security_id": str(sid)}]},
        ),
    ]
    links, refs = board.relationships(responses, request(tid))
    assert len(links) == 2 and refs == []
    assert all(
        link.kind == "fresh_capture" and link.destination.settings["thesis_id"] == str(tid)
        for link in links
    )


def test_relationship_limits_invalid_pointers_and_duplicate_ids_fail_closed():
    responses = [response("/synthetic", {"a/b": {"~key": [7]}})]
    good = reference(responses, 0, "/a~1b/~0key/0", category="call_input")
    assert resolve_pointer(responses[0].body, good.pointer) == 7
    validate_relationships(responses, [], [good])
    for links, refs, expected in (
        ([navigation("cockpit", thesis_id=str(uuid4())) for _ in range(2049)], [], 413),
        ([], [good, good], 500),
        ([], [reference(responses, 0, "/missing", category="call_input")], 500),
        ([], [reference(responses, 0, "/a~2b", category="call_input")], 500),
    ):
        with pytest.raises(HTTPException) as error:
            validate_relationships(responses, links, refs)
        assert error.value.status_code == expected
