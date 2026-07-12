from __future__ import annotations

import uuid
from datetime import date, timedelta

from db.session import DEFAULT_TENANT_ID
from repositories import thesis_repo
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

# GET /scoreboard — the record served: shape, asof scrubbing, the metrics gate (matured +
# non-censored only, insufficient_n below MIN_N), the archived filter, and writes-nothing.

ASOF = "2026-07-15"


def _seed_one_open_censored(db, security_id):
    """The launch shape: the record's first card is already armed (censored), still armed at the
    edge (open), exit_by far out (immature)."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, date(2026, 7, 1), conv_liveness=120, conf_liveness=120)
    record_day(db, thesis, [conv, conf], date(2026, 7, 10))
    bar(db, security_id, date(2026, 7, 10), 100.0)
    bar(db, security_id, date(2026, 7, 14), 104.0)
    return thesis


def _seed_five_matured_cycles(db, security_id):
    """Five arm->aged-out cycles on one thesis, a warming row first (so cycle 1 isn't censored):
    5 matured, non-censored episodes — enough to clear MIN_N for the pooled metrics."""
    thesis = persist_thesis(db, security_id)
    warm = [
        insider_event(security_id=security_id, liveness=3).model_copy(
            update={"asof": date(2026, 5, 29)}
        )
    ]
    record_day(db, thesis, warm, date(2026, 5, 29))
    closes = [(100.0, 105.0), (100.0, 98.0), (100.0, 110.0), (100.0, 99.0), (100.0, 104.0)]
    for i, (entry, exit_) in enumerate(closes):
        fire = date(2026, 6, 1) + timedelta(days=7 * i)
        conv, conf = keys_fired(security_id, fire, conv_liveness=3, conf_liveness=3)
        record_day(db, thesis, [conv, conf], fire)  # armed; exit_by = fire+3
        record_day(db, thesis, [conv], fire + timedelta(days=4))  # aged out -> de-arm row
        bar(db, security_id, fire, entry)
        bar(db, security_id, fire + timedelta(days=2), exit_)
    return thesis


def test_scoreboard_shape_counts_and_provenance(client, db, security_id):
    thesis = _seed_one_open_censored(db, security_id)

    r = client.get("/scoreboard", params={"asof": ASOF})
    assert r.status_code == 200
    body = r.json()

    assert body["asof"] == ASOF
    s = body["summary"]
    assert s["n_theses"] == 1 and s["n_with_record"] == 1
    assert s["n_episodes"] == 1 and s["n_open"] == 1 and s["n_censored"] == 1
    assert s["n_eligible"] == 0  # censored + immature: ledger-only, never a metric input
    assert s["record_began"] == "2026-07-10"
    assert s["min_n"] >= 1 and "NOT A CLAIM" in s["banner"]
    assert {m["name"] for m in s["metrics"]} >= {"arm_timing_forward_return", "false_arm_rate"}
    assert all(m["insufficient_n"] for m in s["metrics"])

    (t,) = body["theses"]
    assert t["thesis_id"] == str(thesis.id) and t["record_error"] is None
    (ep,) = t["episodes"]
    assert ep["status"] == "open" and ep["censored_start"] is True and ep["matured"] is False
    assert ep["arm_date"] == "2026-07-10" and ep["ticker"] == "DEVCO"
    assert ep["entry_close"] == 100.0 and ep["exit_close"] == 104.0
    assert ep["truncated"] is True  # running to the last bar <= asof
    assert len(ep["triggers_at_arm"]) >= 1  # the WHY rides the row
    assert all("label" in tr and "kind" in tr for tr in ep["triggers_at_arm"])


def test_scoreboard_asof_scrub_is_point_in_time(client, db, security_id):
    _seed_one_open_censored(db, security_id)

    before = client.get("/scoreboard", params={"asof": "2026-07-09"}).json()
    assert before["summary"]["n_with_record"] == 0
    assert before["summary"]["n_episodes"] == 0
    assert before["summary"]["record_began"] is None
    (t,) = before["theses"]
    assert t["first_call_asof"] is None and t["episodes"] == []


def test_metrics_clear_the_gate_at_min_n_matured_episodes(client, db, security_id):
    _seed_five_matured_cycles(db, security_id)

    s = client.get("/scoreboard", params={"asof": ASOF}).json()["summary"]
    assert s["n_episodes"] == 5 and s["n_matured"] == 5 and s["n_censored"] == 0
    assert s["n_eligible"] == 5
    by_name = {m["name"]: m for m in s["metrics"]}
    arm = by_name["arm_timing_forward_return"]
    assert arm["n"] == 5 and arm["insufficient_n"] is False
    assert arm["summary"]["median"] is not None
    false_arm = by_name["false_arm_rate"]
    assert false_arm["n"] == 5 and false_arm["summary"]["adverse"] == 2.0
    # calibration stays gated: one grade bucket can never establish monotonicity
    assert by_name["grade_confidence_calibration"]["insufficient_n"] is True


def _second_security(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "OTHRCO", "0007654321", "2026-01-01"),
        )
    db.commit()
    return sid


def test_running_and_censored_episodes_never_enter_metrics(client, db, security_id):
    _seed_five_matured_cycles(db, security_id)
    _seed_one_open_censored(db, _second_security(db))

    s = client.get("/scoreboard", params={"asof": ASOF}).json()["summary"]
    assert s["n_episodes"] == 6 and s["n_eligible"] == 5  # the open censored one is ledger-only
    assert {m["name"]: m["n"] for m in s["metrics"]}["arm_timing_forward_return"] == 5


def test_include_archived_param(client, db, security_id):
    thesis = _seed_one_open_censored(db, security_id)
    thesis_repo.set_archived(db, thesis.id, True)
    db.commit()

    body = client.get("/scoreboard", params={"asof": ASOF}).json()
    (t,) = body["theses"]
    assert t["archived"] is True and len(t["episodes"]) == 1  # the record is not erased

    body = client.get("/scoreboard", params={"asof": ASOF, "include_archived": "false"}).json()
    assert body["theses"] == [] and body["summary"]["n_episodes"] == 0


def test_scoreboard_get_writes_nothing(client, db, security_id):
    _seed_one_open_censored(db, security_id)

    def counts():
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM calls) AS c,"
                " (SELECT count(*) FROM operator_decision) AS d,"
                " (SELECT count(*) FROM fact_price_eod) AS p"
            )
            r = cur.fetchone()
            return (r["c"], r["d"], r["p"])

    before = counts()
    assert client.get("/scoreboard", params={"asof": ASOF}).status_code == 200
    assert counts() == before
