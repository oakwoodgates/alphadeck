"""X1 — the staged onboarding job's API seams: the Workbench promote's LANE (the fast lane for a few cold names,
the queue for many or inside the nightly window), the Workbench's per-thesis progress read, and the Admin
readout + Retry. The ingest unit itself is faked here (its own suite covers it); these assert the lane, the
queue rows (COUNTED) and the readout the operator sees.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings
from pipeline import onboarding
from pipeline.ingest_thesis import NameResult
from workbench import ingest_jobs

_NY = ZoneInfo("America/New_York")


@pytest.fixture
def inline_jobs(monkeypatch):
    """Run the fast lane's job INLINE (the kick suite's pattern), with the ingest unit faked: one clean
    ``NameResult`` per scoped id. Returns the scopes it was called with."""
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda job: ingest_jobs._run_job(job))
    calls: list[set[uuid.UUID]] = []

    def fake(conn, thesis_id, *, only_security_ids=None, **kw):
        ids = set(only_security_ids or [])
        calls.append(ids)
        return [NameResult("FAKE", sid, 1, 2) for sid in ids]

    monkeypatch.setattr("pipeline.ingest_thesis.ingest_thesis", fake)
    return calls


@pytest.fixture
def settings_env(monkeypatch):
    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(f"ALPHADECK_{k.upper()}", str(v))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _add_security(db, ticker: str, cik: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _member(ticker: str, sid: uuid.UUID) -> dict:
    return {"ticker": ticker, "role": "the name", "security_id": str(sid), "segment": None}


def _payload(members: list[dict], name: str = "Onboarding thesis", thesis_id=None) -> dict:
    return {
        "id": thesis_id,
        "name": name,
        "narrative": "n",
        "ticker": None,
        "segments": [],
        "basket": members,
    }


def _queue(db) -> dict[uuid.UUID, str]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT security_id, status FROM security_onboarding")
        out = {r["security_id"]: r["status"] for r in cur.fetchall()}
    db.rollback()
    return out


# --- the promote's lane --------------------------------------------------------------------------------------


def test_a_FEW_cold_names_take_the_FAST_LANE_and_land_done(client, db, inline_jobs):
    a = _add_security(db, "AAA", "0001000001")
    b = _add_security(db, "BBB", "0001000002")

    r = client.post("/workbench/theses", json=_payload([_member("AAA", a), _member("BBB", b)]))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest"] is not None and body["ingest"]["new_members"] == 2
    assert body["onboarding"] is None
    assert inline_jobs == [{a, b}]  # ingested at once, exactly as before X1
    assert _queue(db) == {
        a: "done",
        b: "done",
    }  # ... and recorded warm, so the nightly walks them in full


def test_MORE_cold_names_than_the_fast_lane_are_QUEUED_not_ingested(
    client, db, inline_jobs, settings_env
):
    settings_env(onboard_fast_lane_max=1)
    a = _add_security(db, "AAA", "0001000001")
    b = _add_security(db, "BBB", "0001000002")

    r = client.post("/workbench/theses", json=_payload([_member("AAA", a), _member("BBB", b)]))

    body = r.json()
    assert body["ingest"] is None
    assert body["onboarding"]["queued"] == 2 and body["onboarding"]["new_members"] == 2
    assert "more than the 1 the fast lane loads at once" in body["onboarding"]["reason"]
    assert inline_jobs == []  # nothing ingested inside the request's background
    assert _queue(db) == {a: "queued", b: "queued"}

    # the Workbench's progress read, scoped to this thesis
    p = client.get(f"/workbench/theses/{body['id']}/onboarding")
    assert p.status_code == 200, p.text
    out = p.json()
    assert out["counts"]["queued"] == 2 and out["counts"]["done"] == 0
    assert {n["ticker"] for n in out["pending"]} == {"AAA", "BBB"}
    assert out["history_legs"] == ["form4", "form8k", "schedule13"]

    # a re-promote of the same basket queues nothing new (COUNT THE TABLE) and kicks nothing
    again = client.post(
        "/workbench/theses",
        json=_payload([_member("AAA", a), _member("BBB", b)], thesis_id=body["id"]),
    ).json()
    assert again["ingest"] is None and again["onboarding"] is None
    assert len(_queue(db)) == 2


def test_a_promote_INSIDE_the_nightly_window_queues_even_one_name(
    client, db, inline_jobs, settings_env, monkeypatch
):
    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    monkeypatch.setattr(onboarding, "market_now", lambda: datetime(2026, 6, 10, 22, 5, tzinfo=_NY))
    a = _add_security(db, "AAA", "0001000001")

    body = client.post("/workbench/theses", json=_payload([_member("AAA", a)])).json()

    assert body["ingest"] is None and inline_jobs == []
    assert "inside the nightly window (21:30-23:30" in body["onboarding"]["reason"]
    assert _queue(db) == {a: "queued"}


def test_a_name_ANOTHER_live_thesis_holds_is_warm_no_queue_row_and_the_pre_X1_kick(
    client, db, inline_jobs
):
    a = _add_security(db, "AAA", "0001000001")
    first = client.post("/workbench/theses", json=_payload([_member("AAA", a)], "first")).json()
    assert _queue(db) == {a: "done"}  # loaded by the first promote's fast lane
    db.execute(
        "DELETE FROM security_onboarding"
    )  # a LEGACY-warm name: loaded before X1, no row at all
    db.commit()

    second = client.post("/workbench/theses", json=_payload([_member("AAA", a)], "second")).json()

    assert first["id"] != second["id"]
    assert second["ingest"] is not None and second["onboarding"] is None
    assert _queue(db) == {}  # warm: the nightly already walks it every night — never queued


def test_a_queue_FAULT_never_fails_the_promote_it_falls_back_to_the_pre_X1_kick(
    client, db, inline_jobs, monkeypatch
):
    def broken(*a, **k):
        raise RuntimeError("the queue table is gone")

    monkeypatch.setattr(onboarding, "enqueue_cold", broken)
    a = _add_security(db, "AAA", "0001000001")

    r = client.post("/workbench/theses", json=_payload([_member("AAA", a)]))

    assert r.status_code == 200 and r.json()["ingest"] is not None
    assert inline_jobs == [{a}]


# --- the Admin readout + Retry -------------------------------------------------------------------------------


def _seed(db, *rows):
    """``rows`` = (sid, status, attempts, last_error, enqueued_hours_ago)."""
    for sid, status, attempts, err, ago in rows:
        db.execute(
            "INSERT INTO security_onboarding (tenant_id, security_id, status, attempts, last_error, "
            "source, enqueued_at, legs_done) VALUES (%s, %s, %s, %s, %s, 'promote', now() - %s, %s)",
            (
                DEFAULT_TENANT_ID,
                sid,
                status,
                attempts,
                err,
                timedelta(hours=ago),
                ["form4"] if status != "queued" else [],
            ),
        )
    db.commit()


def _basket(db, *sids):
    tid = uuid.uuid4()
    db.execute(
        "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'live', 'n')",
        (tid, DEFAULT_TENANT_ID),
    )
    for i, s in enumerate(sids):
        db.execute(
            "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, %s, %s, 'T', '—', %s)",
            (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, s),
        )
    db.commit()


def test_the_ADMIN_readout_counts_orders_failed_first_and_Retry_requeues(client, db, settings_env):
    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    f = _add_security(db, "FAIL", "0001000001")
    d = _add_security(db, "DONE", "0001000002")
    q = _add_security(db, "WAIT", "0001000003")
    p = _add_security(db, "PARK", "0001000004")
    _basket(db, f, d, q)  # PARK is in no live basket
    _seed(
        db,
        (f, "failed", 3, "form4: EDGAR said 503", 1),
        (d, "done", 0, None, 2),
        (q, "queued", 0, None, 1),
        (p, "queued", 0, None, 1),
    )

    out = client.get("/admin/onboarding").json()

    assert out["counts"] == {
        "queued": 2,
        "in_progress": 0,
        "failed": 1,
        "done": 1,
        "parked": 1,
    }
    assert [n["ticker"] for n in out["pending"]][0] == "FAIL"  # loud first
    assert out["pending"][0]["last_error"] == "form4: EDGAR said 503"
    assert {n["ticker"]: n["in_live_basket"] for n in out["pending"]}["PARK"] is False
    assert [n["ticker"] for n in out["recent_done"]] == ["DONE"]
    assert out["stalled"] is None and out["last_run"] is None
    assert out["window"].startswith("21:30-23:30")

    r = client.post(f"/admin/onboarding/{f}/retry")
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["failed"] == 0 and _queue(db)[f] == "queued"
    assert client.post(f"/admin/onboarding/{q}/retry").status_code == 409  # only a failed name
    assert client.post(f"/admin/onboarding/{uuid.uuid4()}/retry").status_code == 404


def test_the_ADMIN_readout_says_STALLED_when_names_wait_and_no_run_finished(
    client, db, onboard_runs_dir, settings_env
):
    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    q = _add_security(db, "WAIT", "0001000003")
    _basket(db, q)
    _seed(db, (q, "queued", 0, None, 7))  # waiting 7 h > the 6 h default

    stalled = client.get("/admin/onboarding").json()
    assert stalled["stalled"] and "no onboarding run has finished ever" in stalled["stalled"]

    # a run that finished an hour ago: the job is alive (just behind) -> not stalled; the readout shows it
    onboard_runs_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    (onboard_runs_dir / f"{started:%Y%m%dT%H%M%SZ}.json").write_text(
        json.dumps(
            {
                "pass_kind": "onboard",
                "run_kind": "cron",
                "status": "finished",
                "started_at": started.isoformat(),
                "finished_at": (started + timedelta(minutes=25)).isoformat(),
                "stopped": "budget_time",
                "detail": "budget_time: the run's 1500s budget is spent",
                "counts": {"done": 12, "busy": 1},
            }
        ),
        encoding="utf-8",
    )
    ok = client.get("/admin/onboarding").json()
    assert ok["stalled"] is None
    assert ok["last_run"]["stopped"] == "budget_time" and ok["last_run"]["counts"] == {
        "done": 12,
        "busy": 1,
    }
