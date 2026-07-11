from __future__ import annotations

from datetime import date

from repositories import decisions_repo
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

# GET /scoreboard, the operator track on the wire: the episode's operator slot, off-record
# override spans (with resolved tickers + the frozen stance), and the summary counts.

ASOF = "2026-07-15"


def test_operator_track_on_the_wire(client, db, security_id):
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 7, 1), conv_liveness=120, conf_liveness=120)
    record_day(db, thesis, [conv, conf], date(2026, 7, 1))
    decisions_repo.append(
        db,
        thesis_id=thesis.id,
        action="take",
        decision_date=date(2026, 7, 2),
        security_id=security_id,
        price=100.0,
        reason="acting on the arm",
        tenant_id=thesis.tenant_id,
        call_state="armed",
        call_verdict="core_entry",
    )
    db.commit()
    bar(db, security_id, date(2026, 7, 10), 108.0)

    body = client.get("/scoreboard", params={"asof": ASOF}).json()
    s = body["summary"]
    assert s["n_takes"] == 1 and s["n_passes"] == 0 and s["n_overrides"] == 0
    (t,) = body["theses"]
    (ep,) = t["episodes"]
    op = ep["operator"]
    assert op["action"] == "took" and op["decision_date"] == "2026-07-02"
    assert op["entry_price"] == 100.0 and op["entry_inferred"] is False
    assert op["running"] is True and op["exit_inferred"] is True
    assert abs(op["operator_return"] - 0.08) < 1e-9
    assert t["operator_spans"] == [] and t["decision_anomaly"] is None


def test_override_span_on_the_wire_with_ticker_and_stance(client, db, security_id):
    thesis = persist_thesis(db, security_id)
    record_day(db, thesis, [], date(2026, 7, 1))  # incubating record
    decisions_repo.append(
        db,
        thesis_id=thesis.id,
        action="take",
        decision_date=date(2026, 7, 2),
        security_id=security_id,
        price=125.0,
        tenant_id=thesis.tenant_id,
        call_state="incubating",
        call_verdict="watching",
    )
    db.commit()

    body = client.get("/scoreboard", params={"asof": ASOF}).json()
    assert body["summary"]["n_overrides"] == 1
    (t,) = body["theses"]
    (span,) = t["operator_spans"]
    assert span["override"] is True and span["ticker"] == "DEVCO"
    assert span["call_state_at_take"] == "incubating"
    assert span["call_verdict_at_take"] == "watching"
    assert span["running"] is True and span["entry_price"] == 125.0
