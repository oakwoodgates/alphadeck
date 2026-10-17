"""T7 — the successor-CIK block of the Admin "Price feed repairs" panel, end to end through the API over a
real DB and a run artifact written by the REAL writer (``write_cron_run_log``) from a REAL detector scan
(``run_successor_scan`` over the measured prod shapes, ``tests/fixtures/edgar/successor_cik_2026_10.json``).

Adopt and its exact Undo are exercised against the master (every column compared), each appending exactly
one change row; every refusal writes nothing; the click makes no network request."""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from db.session import connect
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from pipeline.successor_cik import SuccessorScan, run_successor_scan
from securities import master
from tests import successor_cik_fixtures as fx

_OCT16 = date(2026, 10, 16)
_AT = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)


def _night(db, *, error: str | None = None, at: datetime = _AT) -> dict:
    """Seed the four measured names, run the REAL scan over them, write the night's artifact."""
    ids = {t: fx.insert_security(db, t) for t in ("UROY", "REAX", "HLLK", "UCLE")}
    nb, ee = uuid.uuid4(), uuid.uuid4()
    results = [
        ThesisRunResult(
            thesis_id=nb,
            name="Nuclear Buildout",
            recorded=False,
            ingested=[fx.name_result("UROY", ids["UROY"]), fx.name_result("UCLE", ids["UCLE"])],
        ),
        ThesisRunResult(
            thesis_id=ee,
            name="The Everything Exchange",
            recorded=False,
            ingested=[fx.name_result("REAX", ids["REAX"]), fx.name_result("HLLK", ids["HLLK"])],
        ),
    ]
    scan = (
        SuccessorScan(error=error)
        if error
        else run_successor_scan(
            results=results,
            allow_live=True,
            connect=connect,
            previous=lambda: None,
            clock=time.perf_counter,
            live_client=fx.FixtureEdgar(fx.network_docs("UROY", "REAX")),
            cache_client=fx.FixtureEdgar(fx.cache_docs("UROY", "REAX", "HLLK", "UCLE")),
        )
    )
    write_cron_run_log(
        results,
        asof=_OCT16,
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        tape_evaluated=True,
        tape_stale_days=5,
        successor=scan,
    )
    return {**ids, "nb": nb, "ee": ee}


def _panel(client) -> dict:
    return client.get("/admin/status").json()["price_repairs"]


def _row(db, sid) -> dict:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM security_master WHERE id = %s", (sid,))
        return dict(cur.fetchone())


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — fixed table names
        return cur.fetchone()["n"]


@pytest.fixture
def no_network(monkeypatch):
    """Any EDGAR / HTTP request during the test fails it: the Adopt click must make none."""

    def _boom(*a, **k):
        raise AssertionError("the Adopt click made a network request")

    monkeypatch.setattr("ingest.edgar.client.EdgarClient._fetch", _boom)
    monkeypatch.setattr("ingest.http.polite_get", _boom)
    monkeypatch.setattr("httpx.get", _boom)


def test_the_panel_shows_EXACTLY_the_two_successors_with_their_evidence(client, db):
    ids = _night(db)

    p = _panel(client)

    assert p["successor_evaluated"] is True and p["successor_error"] is None
    assert p["successor_asof"] == "2026-10-16"
    assert [r["ticker"] for r in p["successor"]] == ["REAX", "UROY"]
    reax, uroy = p["successor"]
    assert uroy["security_id"] == str(ids["UROY"]) and uroy["stored_cik"] == "0001711570"
    assert uroy["recommended"] == "0002143673" and uroy["current_cik"] == "0001711570"
    assert uroy["theses"] == [{"id": str(ids["nb"]), "name": "Nuclear Buildout"}]
    assert uroy["old_entity_name"] == "Uranium Royalty Corp. /CA"
    assert uroy["deregistrations"] == [
        {"form": "15-12G", "filed": "2026-07-31", "accession": "0001493152-26-035517", "items": []}
    ]
    (c,) = uroy["candidates"]
    assert c["cik"] == "0002143673" and c["matched_on"] == ["UROY"]
    assert c["succession_filings"][0]["accession"] == "0001493152-26-034947"
    assert c["identity_read"] is True and c["recent_foreign_form"] is None
    assert c["files_domestic_forms"] is True and c["incorporation"] == "DE"
    assert reax["candidates"][0]["entity_name"] == "Real REMAX Group Inc."
    assert reax["theses"] == [{"id": str(ids["ee"]), "name": "The Everything Exchange"}]
    assert uroy["change"] is None and p["cik_applied"] == []
    # the night's page re-derives on the run row (benign-marked: a data gap, not a cron fault)
    problems = client.get("/admin/status").json()["last_run"]["problems"]
    assert any("now file under a NEW CIK" in x and "UROY -> CIK 0002143673" in x for x in problems)


def test_ADOPT_then_UNDO_restores_the_master_EXACTLY(client, db, no_network):
    """The Adopt click: the CIK re-pointed in place, the identity refreshed (the 40-F flag gone, the
    domestic filer forms on), one change row; the panel shows the row's standing change with its Undo. The
    Undo click: EVERY master column back byte-for-byte; the log keeps both rows."""
    ids = _night(db)
    before = _row(db, ids["UROY"])

    r = client.post(f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "2143673"})
    assert r.status_code == 200, r.text
    change = r.json()
    assert change["action"] == "adopt" and change["basis"] == "operator:adopt-cik"
    assert (change["prior_cik"], change["new_cik"]) == ("0001711570", "0002143673")
    after = _row(db, ids["UROY"])
    assert after["id"] == ids["UROY"] and after["cik"] == "0002143673"
    assert (after["recent_foreign_form"], after["files_domestic_forms"]) == (None, True)
    assert _count(db, "security_cik_change") == 1
    row = next(x for x in _panel(client)["successor"] if x["ticker"] == "UROY")
    assert row["change"]["id"] == change["id"] and row["change"]["undoable"] is True
    assert row["current_cik"] == "0002143673"

    u = client.post(f"/admin/price-feed/cik-changes/{change['id']}/undo")
    assert u.status_code == 200, u.text
    assert u.json()["action"] == "undo" and u.json()["basis"] == "operator:undo"
    assert _row(db, ids["UROY"]) == before  # EXACT, every column
    assert _count(db, "security_cik_change") == 2
    assert next(x for x in _panel(client)["successor"] if x["ticker"] == "UROY")["change"] is None
    assert client.post(f"/admin/price-feed/cik-changes/{change['id']}/undo").status_code == 409


def test_ADOPT_is_REFUSED_for_a_CIK_the_detector_did_not_recommend(client, db, no_network):
    ids = _night(db)
    before = {t: _row(db, ids[t]) for t in ("UROY", "HLLK")}
    # another registrant's CIK (REAX's successor) for UROY
    r = client.post(f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "0002136387"})
    assert (
        r.status_code == 409 and "not the successor the detector recommended" in r.json()["detail"]
    )
    # a name the detector did not flag at all (renamed under the same CIK)
    r = client.post(f"/admin/price-feed/{ids['HLLK']}/adopt-cik", json={"cik": "0001331421"})
    assert r.status_code == 409 and "no successor-CIK recommendation" in r.json()["detail"]
    # malformed, unknown security
    assert (
        client.post(f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "UROY"}).status_code
        == 422
    )
    assert (
        client.post(
            f"/admin/price-feed/{uuid.uuid4()}/adopt-cik", json={"cik": "0002143673"}
        ).status_code
        == 404
    )
    assert _count(db, "security_cik_change") == 0
    assert {t: _row(db, ids[t]) for t in ("UROY", "HLLK")} == before


def test_UNDO_is_409_when_the_master_DRIFTED_and_ADOPT_is_409_when_it_moved_since_the_pass(
    client, db, no_network
):
    ids = _night(db)
    change = client.post(
        f"/admin/price-feed/{ids['REAX']}/adopt-cik", json={"cik": "0002136387"}
    ).json()
    master.set_name(db, ids["REAX"], "Real REMAX Group Inc. (edited elsewhere)")
    db.commit()
    drifted = _row(db, ids["REAX"])
    u = client.post(f"/admin/price-feed/cik-changes/{change['id']}/undo")
    assert u.status_code == 409 and "changed since this re-point" in u.json()["detail"]
    assert _row(db, ids["REAX"]) == drifted and _count(db, "security_cik_change") == 1
    applied = _panel(client)["cik_applied"]
    assert [(x["id"], x["undoable"]) for x in applied] == [(change["id"], False)]

    # UROY's master moved (re-pointed by hand) after the pass: the recommendation is stale -> refused
    with db.cursor() as cur:
        cur.execute("UPDATE security_master SET cik = '0009999999' WHERE id = %s", (ids["UROY"],))
    db.commit()
    a = client.post(f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "0002143673"})
    assert a.status_code == 409 and "changed since the pass" in a.json()["detail"]
    assert _count(db, "security_cik_change") == 1


def test_a_successor_whose_submissions_were_NOT_READ_cannot_be_adopted(client, db, monkeypatch):
    """The rule flagged UROY but its successor's submissions failed to read that night: no identity to
    apply, so the Adopt is refused (the next pass retries the read) — never a half re-point."""
    real = run_successor_scan

    def _scan_with_failed_read(**kw):
        kw["live_client"] = fx.FixtureEdgar(
            fx.network_docs("UROY", "REAX"), fail=frozenset({"submissions/CIK0002143673.json"})
        )
        return real(**kw)

    ids = {t: fx.insert_security(db, t) for t in ("UROY",)}
    results = [
        ThesisRunResult(
            thesis_id=uuid.uuid4(), name="N", ingested=[fx.name_result("UROY", ids["UROY"])]
        )
    ]
    scan = _scan_with_failed_read(
        results=results,
        allow_live=True,
        connect=connect,
        previous=lambda: None,
        clock=time.perf_counter,
        cache_client=fx.FixtureEdgar(fx.cache_docs("UROY")),
    )
    write_cron_run_log(
        results,
        asof=_OCT16,
        allow_live=True,
        started_at=_AT,
        finished_at=_AT + timedelta(minutes=40),
        successor=scan,
    )
    (row,) = _panel(client)["successor"]
    assert row["candidates"][0]["identity_read"] is False
    r = client.post(f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "0002143673"})
    assert r.status_code == 409 and "were not read on that pass" in r.json()["detail"]
    assert _count(db, "security_cik_change") == 0


def test_an_adopted_re_point_keeps_its_Undo_after_the_name_leaves_the_inventory(client, db):
    """WB#1: once adopted, the next pass no longer flags the name (its CIK lists UROY) — the inverse must
    not vanish with the row. A newer artifact without UROY still lists the standing Adopt under
    ``cik_applied``, undoable."""
    ids = _night(db)
    change = client.post(
        f"/admin/price-feed/{ids['UROY']}/adopt-cik", json={"cik": "0002143673"}
    ).json()
    at = _AT + timedelta(days=3)
    write_cron_run_log(
        [ThesisRunResult(thesis_id=uuid.uuid4(), name="Nuclear Buildout", recorded=False)],
        asof=date(2026, 10, 19),
        allow_live=True,
        started_at=at,
        finished_at=at + timedelta(minutes=40),
        successor=SuccessorScan(evaluated=True),
    )
    p = _panel(client)
    assert p["successor"] == [] and p["successor_asof"] == "2026-10-19"
    assert [(x["id"], x["undoable"], x["ticker"]) for x in p["cik_applied"]] == [
        (change["id"], True, "UROY")
    ]


def test_a_FAILED_scan_shows_its_error_on_the_panel(client, db):
    _night(db, error="OSError: simulated fetch failure for files/company_tickers_exchange.json")
    p = _panel(client)
    assert p["successor_error"].startswith("OSError: simulated fetch failure")
    assert p["successor_evaluated"] is False and p["successor"] == []


def test_across_a_skipped_thesis_CATCH_UP_the_panel_lists_EXACTLY_the_pages_baseline(client, db):
    """The panel reads the SAME per-thesis carry-forward as the page (``successor_cik``'s baseline). After
    every pass of the real sequence — a full pass (REAX pages), a catch-up that SKIPS The Everything
    Exchange and ingests Nuclear Buildout with UROY newly in it (UROY pages), the next full pass (nothing
    pages) — the panel's rows are exactly the names the next pass treats as already told, it reads as of
    the newest pass, and the night's run row names exactly what paged."""
    from pipeline.successor_cik import previous_successor_keys

    ids = {t: fx.insert_security(db, t) for t in ("UROY", "REAX", "HLLK", "UCLE")}
    nb, ee = uuid.uuid4(), uuid.uuid4()

    def thesis(tid, name, *tickers):
        if not tickers:  # the catch-up SKIPPED it (its on-time row stood): nothing ingested
            return ThesisRunResult(thesis_id=tid, name=name, skipped="on-time row stood")
        return ThesisRunResult(
            thesis_id=tid,
            name=name,
            recorded=False,
            ingested=[fx.name_result(t, ids[t]) for t in tickers],
        )

    def run(results, *, asof: date, at: datetime, catch_up: bool = False) -> list[str]:
        scan = run_successor_scan(
            results=results,
            allow_live=True,
            connect=connect,
            previous=previous_successor_keys,  # the conftest-redirected run home
            clock=time.perf_counter,
            live_client=fx.FixtureEdgar(fx.network_docs("UROY", "REAX")),
            cache_client=fx.FixtureEdgar(fx.cache_docs("UROY", "REAX", "HLLK", "UCLE")),
        )
        write_cron_run_log(
            results,
            asof=asof,
            allow_live=True,
            started_at=at,
            finished_at=at + timedelta(minutes=5),
            catch_up=catch_up,
            successor=scan,
        )
        p = _panel(client)
        told = {k.split(":")[0] for k in previous_successor_keys()}
        assert {r["security_id"] for r in p["successor"]} == told
        assert p["successor_asof"] == asof.isoformat()
        problems = client.get("/admin/runs?limit=1").json()["runs"][0]["problems"]
        paged = [line for line in problems if "now file under a NEW CIK" in line]
        if scan.new:
            head = (
                f"{len(scan.new)} name(s) now file under a NEW CIK: {', '.join(scan.new_labels)} —"
            )
            assert len(paged) == 1 and paged[0].startswith(head)
        else:
            assert paged == []
        return sorted(r["ticker"] for r in p["successor"])

    first = run(
        [thesis(nb, "Nuclear Buildout", "UCLE"), thesis(ee, "The Everything Exchange", "REAX")],
        asof=date(2026, 10, 14),
        at=datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc),
    )
    assert first == ["REAX"]
    caught = run(
        [thesis(nb, "Nuclear Buildout", "UROY", "UCLE"), thesis(ee, "The Everything Exchange")],
        asof=date(2026, 10, 15),
        at=datetime(2026, 10, 16, 13, 0, tzinfo=timezone.utc),
        catch_up=True,
    )
    assert caught == [
        "REAX",
        "UROY",
    ]  # REAX carried for the skipped thesis; UROY found by the catch-up
    nxt = run(
        [
            thesis(nb, "Nuclear Buildout", "UROY", "UCLE"),
            thesis(ee, "The Everything Exchange", "REAX", "HLLK"),
        ],
        asof=date(2026, 10, 16),
        at=datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc),
    )
    assert nxt == ["REAX", "UROY"]


def test_the_status_read_still_WRITES_NOTHING_with_the_successor_block(client, db):
    ids = _night(db)
    client.post(
        f"/admin/price-feed/{ids['REAX']}/adopt-cik", json={"cik": "0002136387"}
    ).raise_for_status()
    counts = {t: _count(db, t) for t in ("security_cik_change", "security_master")}
    for _ in range(2):
        assert client.get("/admin/status").status_code == 200
    assert {t: _count(db, t) for t in counts} == counts
