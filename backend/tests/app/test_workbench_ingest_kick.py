"""The ON-PROMOTE new-member ingest kick (PR-4): a promote that ADDS members starts their back-half
ingest as a background job (the promote response's additive ``ingest`` ref + the poll endpoint), scoped
to EXACTLY the newly-added security_ids — the cost thread made testable: a re-promote of the same basket
kicks NOTHING and appends ZERO rows (COUNT THE TABLE, the CLAUDE.md idempotency rule), a no-new-members
save kicks nothing, a failure lands VISIBLE on the poll while the promote itself succeeded, and an
in-flight promote queues one follow-up (never a parallel run, never a drop).

The ingest unit itself (``pipeline.ingest_thesis``) is monkeypatched — these tests assert the KICK
seam (diff → job → poll), not EDGAR/price ingestion (its own suite covers that).
"""

from __future__ import annotations

import uuid

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline.ingest_thesis import NameResult
from workbench import ingest_jobs


@pytest.fixture(autouse=True)
def _inline_ingest_jobs(monkeypatch):
    """Run ingest jobs INLINE (synchronously) so a kicked job is terminal by the time the promote
    returns — no thread-timing flakiness, no race with the test-DB teardown. Reset the in-process
    registry per test (mirrors the draft-jobs autouse fixture)."""
    ingest_jobs.reset_state()
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda job: ingest_jobs._run_job(job))
    yield
    ingest_jobs.reset_state()


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


def _payload(members: list[dict], thesis_id: str | None = None) -> dict:
    return {
        "id": thesis_id,
        "name": "Nuclear (ingest kick)",
        "narrative": "AI power demand.",
        "ticker": None,
        "segments": [],
        "basket": members,
    }


@pytest.fixture
def fake_ingest(monkeypatch):
    """Replace the ingest unit with a recorder: appends each call's scoped id-set to ``calls`` and
    returns one clean ``NameResult`` per scoped id. Patched on the module attribute the router resolves
    at call time (``pipeline.ingest_thesis.ingest_thesis``)."""
    calls: list[set[uuid.UUID]] = []

    def fake(conn, thesis_id, *, only_security_ids=None, **kw):
        ids = set(only_security_ids or [])
        calls.append(ids)
        return [NameResult("FAKE", sid, 1, 2) for sid in ids]

    monkeypatch.setattr("pipeline.ingest_thesis.ingest_thesis", fake)
    return calls


def test_promote_kicks_a_job_for_exactly_the_new_members(client, db, security_id, fake_ingest):
    """Create (all-new → the starter-basket full ingest), then ADD one name — the second job's scope is
    EXACTLY the added id, never the established one (the cost thread: only new names pay)."""
    r1 = client.post("/workbench/theses", json=_payload([_member("DEVCO", security_id)]))
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["ingest"] is not None and body1["ingest"]["new_members"] == 1
    assert fake_ingest == [{security_id}]  # a brand-new thesis: every member is new

    added = _add_security(db, "NEWCO", "0009876543")
    r2 = client.post(
        "/workbench/theses",
        json=_payload(
            [_member("DEVCO", security_id), _member("NEWCO", added)], thesis_id=body1["id"]
        ),
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["ingest"] is not None and body2["ingest"]["new_members"] == 1
    assert fake_ingest[-1] == {added}  # ONLY the added name — DEVCO is not re-walked

    # the poll serves the done summary (the inline executor made the job terminal already)
    polled = client.get(f"/workbench/theses/{body2['id']}/ingest/jobs/{body2['ingest']['job_id']}")
    assert polled.status_code == 200, polled.text
    assert polled.json()["status"] == "done"
    assert polled.json()["result"] == {
        "members": 1,
        "form4": 1,
        "price_bars": 2,
        "form8k": 0,
        "sched13": 0,
        "fund_shares": 0,
    }


def test_re_promote_of_the_same_basket_kicks_no_job_and_appends_zero_rows(
    client, db, security_id, monkeypatch
):
    """The idempotency discipline, COUNTING THE TABLE: the fake ingest genuinely appends a price bar per
    scoped id, so a leaked second kick WOULD grow fact_price_eod — the re-promote must kick nothing and
    the table must not move (assert count(*), not just the read)."""
    from datetime import date

    from ingest.prices.eod_loader import ingest_prices

    calls: list[set[uuid.UUID]] = []

    def fake(conn, thesis_id, *, only_security_ids=None, **kw):
        ids = set(only_security_ids or [])
        calls.append(ids)
        for sid in ids:
            ingest_prices(
                conn,
                sid,
                [
                    {
                        "d": date(2026, 6, 1),
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1000,
                    }
                ],
            )
        conn.commit()
        return [NameResult("DEVCO", sid, 0, 1) for sid in ids]

    monkeypatch.setattr("pipeline.ingest_thesis.ingest_thesis", fake)

    payload = _payload([_member("DEVCO", security_id)])
    r1 = client.post("/workbench/theses", json=payload)
    assert r1.status_code == 200 and r1.json()["ingest"] is not None
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod")
        before = cur.fetchone()["n"]
    assert before == 1  # the first kick's ingest landed (the fake really writes)

    r2 = client.post("/workbench/theses", json=_payload(payload["basket"], r1.json()["id"]))
    assert r2.status_code == 200, r2.text
    assert r2.json()["ingest"] is None  # nothing added -> nothing kicked
    assert calls == [{security_id}]  # the ingest unit ran ONCE, ever
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod")
        assert cur.fetchone()["n"] == before  # the TABLE did not grow


def test_a_no_new_members_save_kicks_no_job(client, db, security_id, fake_ingest):
    """The everyday triage/edit Save (same basket, other fields moved) pays nothing: no job, no ingest
    call — the narrowing to genuinely-new members is the whole point (the cost thread)."""
    r1 = client.post("/workbench/theses", json=_payload([_member("DEVCO", security_id)]))
    tid = r1.json()["id"]
    assert len(fake_ingest) == 1  # the create's starter ingest

    edited = _payload([_member("DEVCO", security_id)], thesis_id=tid)
    edited["narrative"] = "AI power demand, refined."  # a save that adds no member
    r2 = client.post("/workbench/theses", json=edited)
    assert r2.status_code == 200, r2.text
    assert r2.json()["ingest"] is None
    assert len(fake_ingest) == 1  # no second ingest call


def test_promote_while_ingest_running_queues_one_follow_up(
    client, db, security_id, fake_ingest, monkeypatch
):
    """The in-flight design end-to-end: promote #2 lands while #1's job still runs → its new member is
    QUEUED as a single follow-up job (a real job_id on the response, 'queued' on the poll — never a
    parallel run, never a silent drop); when the running worker finishes it chains into the follow-up
    with exactly the queued ids."""
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda job: None)  # hold jobs un-run

    r1 = client.post("/workbench/theses", json=_payload([_member("DEVCO", security_id)]))
    tid = r1.json()["id"]
    job1_id = r1.json()["ingest"]["job_id"]
    assert (
        client.get(f"/workbench/theses/{tid}/ingest/jobs/{job1_id}").json()["status"] == "running"
    )

    added = _add_security(db, "NEWCO", "0009876543")
    r2 = client.post(
        "/workbench/theses",
        json=_payload([_member("DEVCO", security_id), _member("NEWCO", added)], thesis_id=tid),
    )
    job2_id = r2.json()["ingest"]["job_id"]
    assert job2_id != job1_id
    assert client.get(f"/workbench/theses/{tid}/ingest/jobs/{job2_id}").json()["status"] == "queued"
    assert fake_ingest == []  # nothing has actually run yet — held by the no-op executor

    ingest_jobs._run_job(
        ingest_jobs.get_job(job1_id)
    )  # the worker finishes -> chains the follow-up

    assert fake_ingest == [
        {security_id},
        {added},
    ]  # first the running scope, then EXACTLY the queued
    assert client.get(f"/workbench/theses/{tid}/ingest/jobs/{job1_id}").json()["status"] == "done"
    assert client.get(f"/workbench/theses/{tid}/ingest/jobs/{job2_id}").json()["status"] == "done"


def test_a_failed_ingest_is_a_failed_job_on_the_poll_but_the_promote_succeeded(
    client, db, security_id, monkeypatch
):
    """Fail-visible: the promote itself returns 200 (the spine write landed) while the background job
    lands FAILED on the poll with the per-name cause — never a silent nothing."""

    def fake(conn, thesis_id, *, only_security_ids=None, **kw):
        return [
            NameResult("DEVCO", sid, 0, 0, error="price: boom") for sid in only_security_ids or []
        ]

    monkeypatch.setattr("pipeline.ingest_thesis.ingest_thesis", fake)

    r = client.post("/workbench/theses", json=_payload([_member("DEVCO", security_id)]))
    assert r.status_code == 200, r.text  # the promote is never failed by the ingest
    ref = r.json()["ingest"]
    assert ref is not None and ref["new_members"] == 1

    polled = client.get(f"/workbench/theses/{r.json()['id']}/ingest/jobs/{ref['job_id']}").json()
    assert polled["status"] == "failed" and polled["result"] is None
    assert "DEVCO" in polled["error"] and "price: boom" in polled["error"]  # the cause travels


def test_promote_response_shape_is_backward_compatible(client, db, fake_ingest):
    """The additive field: an empty-basket create carries ``ingest: null`` and every classic
    ThesisDetail field is untouched — an old consumer of the promote response sees its exact shape.
    """
    r = client.post("/workbench/theses", json=_payload([]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingest"] is None and fake_ingest == []  # nothing added, nothing kicked
    for key in ("id", "name", "narrative", "basket", "segments", "term_set", "exclusions"):
        assert key in body  # the ThesisDetail re-snapshot is intact


def test_poll_404s_for_an_unknown_job_or_the_wrong_thesis(client, db, security_id, fake_ingest):
    r = client.post("/workbench/theses", json=_payload([_member("DEVCO", security_id)]))
    job_id = r.json()["ingest"]["job_id"]
    # unknown job id -> 404 (expired / restart-wiped: the FE shows a visible "lost from view")
    assert (
        client.get(f"/workbench/theses/{r.json()['id']}/ingest/jobs/{uuid.uuid4().hex}").status_code
        == 404
    )
    # a real job under the WRONG thesis -> 404 (the job_id must belong to the thesis)
    assert client.get(f"/workbench/theses/{uuid.uuid4()}/ingest/jobs/{job_id}").status_code == 404
