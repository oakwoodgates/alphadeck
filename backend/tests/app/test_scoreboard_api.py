from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest

from app.routers import scoreboard
from db.session import DEFAULT_TENANT_ID
from repositories import thesis_repo
from scoreboard.assemble import assemble_scoreboard
from tests.calls.factories import insider_event
from tests.scoreboard.helpers import bar, keys_fired, persist_thesis, record_day

# GET /scoreboard — the record served: shape, asof scrubbing, the metrics gate (matured +
# non-censored + clean-ingest only, insufficient_n below MIN_N), the archived filter,
# record-freshness (2a), record-provenance (2d), the maturity horizon (2e), and writes-nothing.

ASOF = "2026-07-15"

# The staleness pins mirror test_admin_api's: 2026-07-17 is a Friday, 07-20 the following Monday
# (the don't-cry-wolf weekend pair; RUN_AT defaults to 22:30).
_FRI = date(2026, 7, 17)


def _pin(monkeypatch, now: datetime) -> None:
    """Pin the scoreboard router's container-local clock seam (the schedule math is pure over it)."""
    monkeypatch.setattr(scoreboard, "_now", lambda: now)


def _seed_record_edge(db, security_id, edge: date):
    """One thesis with a single call-of-record at ``edge`` — so the calls-log MAX(asof) lands there."""
    thesis = persist_thesis(db, security_id)
    conv, conf = keys_fired(security_id, edge, conv_liveness=120, conf_liveness=120)
    record_day(db, thesis, [conv, conf], edge)
    return thesis


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


# --- 2d: record provenance — flagged episodes stay in the ledger, out of the aggregates ---


def _seed_matured_flagged_cycle(db, security_id):
    """One matured, NON-censored arm cycle recorded on a PARTIAL ingest (fresh=False) — June dates
    (outside the freeze window), so the run stamp alone is what flags it."""
    thesis = persist_thesis(db, security_id, thesis_id=uuid.uuid4())
    warm = [
        insider_event(security_id=security_id, liveness=3).model_copy(
            update={"asof": date(2026, 6, 14)}
        )
    ]
    record_day(db, thesis, warm, date(2026, 6, 14))  # warming first: the arm is not censored
    conv, conf = keys_fired(security_id, date(2026, 6, 15), conv_liveness=3, conf_liveness=3)
    record_day(db, thesis, [conv, conf], date(2026, 6, 15), ingest_fresh=False, ingest_errors=2)
    record_day(db, thesis, [conv], date(2026, 6, 19))  # aged out -> de-arm row
    bar(db, security_id, date(2026, 6, 15), 100.0)
    bar(db, security_id, date(2026, 6, 17), 103.0)
    return thesis


def test_flagged_episode_stays_in_ledger_but_leaves_the_metrics(client, db, security_id):
    """Recall-is-sacred cousin, proved by the COUNT: 5 matured clean + 1 matured flagged -> the
    ledger shows 6, the eligible pool and every pooled metric read 5, and the banner names the
    clean-ingest leg of the eligibility rule."""
    _seed_five_matured_cycles(db, security_id)
    _seed_matured_flagged_cycle(db, _second_security(db))

    s = client.get("/scoreboard", params={"asof": ASOF}).json()["summary"]
    assert s["n_episodes"] == 6 and s["n_matured"] == 6 and s["n_censored"] == 0
    assert s["n_eligible"] == 5  # the flagged one is ledger-only
    assert {m["name"]: m["n"] for m in s["metrics"]}["arm_timing_forward_return"] == 5
    assert "clean-ingest" in s["banner"] and "NOT A CLAIM" in s["banner"]
    result = assemble_scoreboard(db, asof=date.fromisoformat(ASOF))
    assert result.n_ingest_flagged == 1


def test_flagged_clean_and_legacy_twins_score_identically(client, db, security_id):
    """The migration-0023 rule, pinned: a clean-stamped, a partial-stamped (flagged), and a
    legacy-NULL arm over IDENTICAL bars produce IDENTICAL Outcome wire fields — provenance
    segments/annotates AFTER scoring, never inside it. Only the flags differ."""
    stamps: dict[str, dict] = {
        "clean": {"ingest_fresh": True, "ingest_errors": 0},
        "flagged": {"ingest_fresh": False, "ingest_errors": 2},
        "legacy": {},
    }
    ids: dict[str, str] = {}
    for name, stamp in stamps.items():
        t = persist_thesis(db, security_id, thesis_id=uuid.uuid4())
        ids[name] = str(t.id)
        conv, conf = keys_fired(security_id, date(2026, 6, 1), conv_liveness=5, conf_liveness=5)
        record_day(db, t, [conv, conf], date(2026, 6, 1), **stamp)
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 4), 108.0)

    body = client.get("/scoreboard", params={"asof": ASOF}).json()
    eps = {t["thesis_id"]: t["episodes"][0] for t in body["theses"]}
    clean, flagged, legacy = (eps[ids[k]] for k in ("clean", "flagged", "legacy"))
    for f in (
        "entry_close",
        "exit_close",
        "exit_date",
        "forward_return",
        "arm_until_return",
        "warm_return",
        "peak_return",
        "peak_date",
        "exit_vs_peak_days",
        "truncated",
        "insufficient_prices",
    ):
        assert clean[f] == flagged[f] == legacy[f]
    assert clean["forward_return"] == pytest.approx(0.08)  # a REAL scored number on all three
    # ...and only the provenance differs (asserted on the assembled result; the wire mirrors it)
    result = assemble_scoreboard(db, asof=date.fromisoformat(ASOF))
    by_id = {str(t.thesis_id): t.episodes[0] for t in result.theses}
    assert by_id[ids["flagged"]].ingest_flagged is True
    assert by_id[ids["clean"]].ingest_flagged is False
    assert by_id[ids["legacy"]].ingest_flagged is False
    assert by_id[ids["legacy"]].arm_ingest_fresh is None  # raw, never coerced


# --- 2a: the record-freshness (staleness) line on the summary ---


def test_scoreboard_staleness_fri_edge_monday_morning_current_monday_night_stale(
    client, db, security_id, monkeypatch
):
    """THE spec case, mirrored on the Scoreboard summary: freshness is measured against the last
    EXPECTED Mon-Fri+RUN_AT run, never raw (today - edge) — a Friday edge Monday 09:00 is CURRENT
    (quiet); the same edge Monday 23:00 (past the 22:30 RUN_AT) is 1 behind (loud)."""
    _seed_record_edge(db, security_id, _FRI)

    _pin(monkeypatch, datetime(2026, 7, 20, 9, 0))  # Monday morning, before RUN_AT
    s = client.get("/scoreboard", params={"asof": "2026-07-20"}).json()["summary"]
    assert s["record_edge"] == "2026-07-17"
    assert s["expected_asof"] == "2026-07-17"  # Monday's run isn't due yet
    assert s["days_behind"] == 0
    assert s["stale"] is False  # don't cry wolf over a weekend
    assert s["today"] == "2026-07-20"

    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))  # Monday night, past RUN_AT
    s = client.get("/scoreboard", params={"asof": "2026-07-20"}).json()["summary"]
    assert s["expected_asof"] == "2026-07-20"
    assert s["days_behind"] == 1
    assert s["stale"] is True


def test_scoreboard_staleness_never_begun_is_quiet(client, db, security_id, monkeypatch):
    """A thesis with NO call-of-record → the record edge is None: the QUIET never-begun state
    (days_behind None, stale False), never an alarm on a fresh install."""
    persist_thesis(db, security_id)  # a thesis, but no call recorded
    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))
    s = client.get("/scoreboard", params={"asof": "2026-07-20"}).json()["summary"]
    assert s["record_edge"] is None
    assert s["days_behind"] is None
    assert s["stale"] is False


def test_scoreboard_staleness_is_asof_independent(client, db, security_id, monkeypatch):
    """The staleness answers "is the record current NOW" — it must be IDENTICAL whether the view is
    scrubbed to the past or to today (only the FE suppresses it on a past view, decision #2). Clock
    pinned Monday night → 1 behind, regardless of the request asof; record_edge is the UNCAPPED
    calls-log MAX(asof), not the asof-capped view."""
    _seed_record_edge(db, security_id, _FRI)
    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))

    today_view = client.get("/scoreboard", params={"asof": "2026-07-20"}).json()["summary"]
    past_view = client.get("/scoreboard", params={"asof": "2026-07-09"}).json()["summary"]
    for key in ("record_edge", "expected_asof", "days_behind", "stale", "today"):
        assert today_view[key] == past_view[key]
    assert today_view["stale"] is True and today_view["days_behind"] == 1
    # and the asof genuinely scrubbed the VIEW (only the freshness fields are shared) — the past view
    # sees no record, proving the freshness edge is not the capped read
    assert past_view["n_with_record"] == 0 and today_view["n_with_record"] == 1


def test_scoreboard_get_writes_nothing(client, db, security_id, monkeypatch):
    _seed_one_open_censored(db, security_id)
    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))  # a pinned clock so the freshness read runs

    def counts():
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM calls) AS c,"
                " (SELECT count(*) FROM operator_decision) AS d,"
                " (SELECT count(*) FROM fact_price_eod) AS p,"
                " (SELECT count(*) FROM fact_insider_txn) AS i"  # the 2d thaw-lag read path
            )
            r = cur.fetchone()
            return (r["c"], r["d"], r["p"], r["i"])

    before = counts()
    resp = client.get("/scoreboard", params={"asof": ASOF})
    assert resp.status_code == 200
    # the compute-on-read freshness fields were traversed (record_edge is a pure SELECT) …
    assert "stale" in resp.json()["summary"] and "record_edge" in resp.json()["summary"]
    assert counts() == before  # … and the read wrote NOTHING
