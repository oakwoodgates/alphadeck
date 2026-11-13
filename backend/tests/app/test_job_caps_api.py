"""The global job caps at the API (security hardening PR 2, item 5).

- A draft kick-off while ``Settings.draft_jobs_max_running`` drafts run (across all theses) answers 429 with a fixed
  sentence, checked BEFORE the daily allowance is charged.
- A promote that would start a fast-lane ingest while ``Settings.ingest_jobs_max_running`` ingests run starts none:
  its new names go to the onboarding queue (the same fallback as a spent allowance), and no allowance is spent.
"""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute

from app.main import app
from domain.settings import get_settings
from pipeline import onboarding
from workbench import draft_jobs, ingest_jobs

DATA = "00000000-0000-0000-0000-000000000001"


def _thesis(client) -> uuid.UUID:
    r = client.post(
        "/workbench/theses",
        json={"name": "Cap card", "narrative": "n", "basket": [], "segments": []},
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


def test_a_draft_beyond_the_global_cap_is_429(client, monkeypatch):
    from app.routers import workbench as wb

    draft_jobs.reset_state()
    monkeypatch.setattr(get_settings(), "draft_jobs_max_running", 1)
    # another card's draft is running (its worker never returns here)
    draft_jobs.start_draft_job(uuid.uuid4(), lambda: None, executor=lambda job, run: None)
    tid = _thesis(client)
    try:
        r = client.post(f"/workbench/theses/{tid}/draft-chain")
    finally:
        draft_jobs.reset_state()
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == wb.DRAFTS_AT_CAPACITY


def test_the_draft_cap_is_checked_before_the_allowance_is_charged():
    route = next(
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/workbench/theses/{thesis_id}/draft-chain"
    )
    names = [d.call.__name__ for d in route.dependant.dependencies]
    assert names.index("require_draft_room") < names.index("charge_chain_draft")


def test_a_promote_at_the_ingest_cap_queues_its_names_instead(client, db, monkeypatch):
    from app.routers import workbench as wb

    monkeypatch.setattr(get_settings(), "ingest_jobs_max_running", 1)
    monkeypatch.setattr(
        wb, "_plan_lane", lambda conn, tid, tenant, ids: onboarding.PromotePlan(frozenset(), True)
    )
    # another card's ingest is running (the suite's executor never runs it, so its worker never returns)
    ingest_jobs.start_ingest_job(uuid.uuid4(), {uuid.uuid4()}, lambda ids: {})
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
            "VALUES (%s, %s, 'CAPT', 'Cap Test Inc', '2026-01-01')",
            (sid, DATA),
        )
    db.commit()
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Cap promote",
            "narrative": "n",
            "basket": [{"ticker": "CAPT", "role": "r", "security_id": str(sid)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest"] is None
    assert body["onboarding"]["new_members"] == 1
    assert body["onboarding"]["reason"] == wb.INGEST_AT_CAPACITY


def test_a_promote_below_the_ingest_cap_still_kicks(client, db, monkeypatch):
    from app.routers import workbench as wb

    monkeypatch.setattr(get_settings(), "ingest_jobs_max_running", 2)
    monkeypatch.setattr(
        wb, "_plan_lane", lambda conn, tid, tenant, ids: onboarding.PromotePlan(frozenset(), True)
    )
    ingest_jobs.start_ingest_job(uuid.uuid4(), {uuid.uuid4()}, lambda ids: {})
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
            "VALUES (%s, %s, 'CAPU', 'Cap Under Inc', '2026-01-01')",
            (sid, DATA),
        )
    db.commit()
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Cap promote under",
            "narrative": "n",
            "basket": [{"ticker": "CAPU", "role": "r", "security_id": str(sid)}],
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ingest"] is not None
