"""The admin ops surface (Slice 1) — /admin/status, /admin/runs, and the run-daily kick-off→poll job,
against the real test DB (the ``db`` fixture → alphadeck_test) with the network stubbed out.

The two invariant gates live here: **the reads write NOTHING** (proved by counting EVERY public table
before/after — a pure ops surface must own no tables), and **the trigger is idempotent to a re-click**
(the second full pass appends ZERO calls rows — COUNT the table, not the read). Staleness pins the
clock via the router's ``_now`` seam: 2026-07-17 is a Friday, 07-20 the following Monday — the
don't-cry-wolf weekend pair. The run history reads the cron's own artifacts (redirected to tmp by the
autouse ``cron_runs_dir`` fixture in this package's conftest)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.routers import admin
from db.session import DEFAULT_TENANT_ID
from pipeline import daily, daily_job
from pipeline.cron_run_log import write_cron_run_log
from pipeline.daily import ThesisRunResult
from repositories import calls_repo

_FRI = date(2026, 7, 17)
_MON = date(2026, 7, 20)

# The hole-aware read's week — the MEASURED prod shape (2026-09-08 Tue fired Wed 09:09 and recorded
# Wednesday, leaving Tuesday with no call-of-record under a CURRENT edge): 09-07 Mon … 09-10 Thu.
_S_MON = date(2026, 9, 7)
_S_TUE = date(2026, 9, 8)
_S_WED = date(2026, 9, 9)
_S_THU = date(2026, 9, 10)


@pytest.fixture(autouse=True)
def _inline_daily_jobs(monkeypatch):
    """Run daily jobs INLINE (synchronously) so a kicked-off run is terminal by the time the 202
    returns — no thread-timing flakiness, no race with the test-DB teardown. Reset the in-process
    registry per test. (The job still opens its OWN ``connect()`` to alphadeck_test and sees the
    helpers' COMMITTED rows — exactly the prod path, minus the thread.)"""
    daily_job.reset_state()
    monkeypatch.setattr(
        daily_job, "_DEFAULT_EXECUTOR", lambda job, run: daily_job._run_job(job, run)
    )
    yield
    daily_job.reset_state()


def _no_network(monkeypatch):
    """Stub the cron's ingest step so a pass never hits the network (the test_daily discipline)."""
    monkeypatch.setattr(daily, "ingest_thesis", lambda *a, **k: [])


def _pin(monkeypatch, now: datetime) -> None:
    """Pin the router's container-local clock seam (the schedule math itself is pure over it)."""
    monkeypatch.setattr(admin, "_now", lambda: now)


def _thesis(db, name):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


def _calls(db, thesis_id):
    return calls_repo.list_for_thesis(db, thesis_id)  # the full append-only history (every row)


def _tr(**kw) -> ThesisRunResult:
    return ThesisRunResult(thesis_id=uuid.uuid4(), name="T", **kw)


def _artifact(
    *, asof: date, at: datetime, allow_live: bool = True, results=None, catch_up: bool = False
):
    """Write a run-of-record artifact through the REAL writer (into the conftest-redirected tmp home)."""
    results = results if results is not None else [_tr(recorded=True, edgar_fetches=88)]
    path = write_cron_run_log(
        results,
        asof=asof,
        allow_live=allow_live,
        started_at=at,
        finished_at=at + timedelta(minutes=2),
        catch_up=catch_up,
    )
    assert path is not None
    return path


def _seed_nights(db, monkeypatch, *asofs: date) -> None:
    """Land one call-of-record per as-of through the REAL daily pass (network stubbed) — the record the
    hole-aware read scans. A night NOT in ``asofs`` has no calls row: a hole."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    for asof in asofs:
        daily.run_daily(db, asof=asof, allow_live=True)


# --- /admin/status: the freshness + health summary ---


def test_status_never_ran_and_never_begun_record_is_QUIET(client):
    """Fresh install: no calls, no artifacts. The record-never-begun state is quiet (stale False,
    days_behind None — decision #5), and the cron verdict is never_ran, not an alarm."""
    body = client.get("/admin/status").json()
    assert body["record"]["edge"] is None
    assert body["record"]["days_behind"] is None
    assert body["record"]["stale"] is False
    assert "never begun" in body["record"]["reason"]
    assert body["last_run"] is None
    assert body["cron"]["status"] == "never_ran"


def test_status_fri_edge_monday_morning_current_monday_night_stale(client, db, monkeypatch):
    """THE spec case: staleness is measured against the last EXPECTED Mon-Fri+RUN_AT run, never raw
    (today - edge) — a Friday edge Monday 09:00 is CURRENT (quiet); the same edge Monday 23:00 (past
    the 22:30 RUN_AT) is 1 behind (loud)."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    daily.run_daily(db, asof=_FRI, allow_live=True)  # the record edge lands on Friday
    _artifact(asof=_FRI, at=datetime(2026, 7, 17, 22, 30, tzinfo=timezone.utc))

    _pin(monkeypatch, datetime(2026, 7, 20, 9, 0))  # Monday morning, before RUN_AT
    body = client.get("/admin/status").json()
    assert body["record"]["edge"] == "2026-07-17"
    assert body["record"]["expected_asof"] == "2026-07-17"  # Monday's run isn't due yet
    assert body["record"]["days_behind"] == 0
    assert body["record"]["stale"] is False  # don't cry wolf over a weekend
    assert body["cron"]["status"] == "healthy"
    assert body["last_run"]["asof"] == "2026-07-17" and body["last_run"]["healthy"] is True

    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))  # Monday night, past RUN_AT
    body = client.get("/admin/status").json()
    assert body["record"]["expected_asof"] == "2026-07-20"
    assert body["record"]["days_behind"] == 1
    assert body["record"]["stale"] is True
    assert body["cron"]["status"] == "stale"
    assert "behind" in body["cron"]["detail"]


def test_status_unhealthy_when_last_run_FROZE_even_with_a_current_record(client, db, monkeypatch):
    """Decision #7 — the R1 lesson: a FROZEN run (live, 0 EDGAR fetches) appends nothing yet keeps the
    edge current-looking; it must read as LOUD as stale, never hide behind a green 'healthy'."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    daily.run_daily(db, asof=_MON, allow_live=True)  # edge = Monday (current)
    _artifact(
        asof=_MON,
        at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc),
        results=[_tr(recorded=False, edgar_fetches=0)],  # live + 0 fetches = the freeze fingerprint
    )
    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))
    body = client.get("/admin/status").json()
    assert body["record"]["stale"] is False  # the record LOOKS fine…
    assert body["cron"]["status"] == "unhealthy"  # …but the bad run is loud anyway
    assert "FROZEN" in body["cron"]["detail"]
    assert body["last_run"]["healthy"] is False
    assert any("FROZEN" in p for p in body["last_run"]["problems"])


def test_status_a_benign_no_live_dev_run_is_NOT_unhealthy(client, db, monkeypatch):
    """Honest loudness: a hand-run --no-live pass writes an artifact whose assessor note is benign
    ('not an error') — the cron verdict must not cry 'unhealthy' over a dev run."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    daily.run_daily(db, asof=_MON, allow_live=True)  # edge = Monday (current)
    _artifact(
        asof=_MON,
        at=datetime(2026, 7, 20, 22, 40, tzinfo=timezone.utc),
        allow_live=False,
        results=[_tr(withheld_reason="no-live", edgar_fetches=0)],
    )
    _pin(monkeypatch, datetime(2026, 7, 20, 23, 0))
    body = client.get("/admin/status").json()
    assert body["last_run"]["mode"] == "no-live"
    assert body["last_run"]["healthy"] is False  # the assessor notes it…
    assert any("not an error" in p for p in body["last_run"]["problems"])
    assert body["cron"]["status"] == "healthy"  # …but it is a note, not an alarm


# --- the hole-aware read: missed nights under a CURRENT edge (the wrong-day shape) ---


def test_status_GAPPY_when_a_weekday_inside_the_window_has_no_record(client, db, monkeypatch):
    """THE measured shape: Mon, Wed, Thu recorded — Tuesday's run fired Wednesday morning and recorded
    Wednesday, so Tuesday has NO call-of-record. The edge (Thu) is CURRENT Thursday night and the last run
    is clean, so every existing check reads green — the hole read must be loud: missed 1, the date listed,
    the verdict `gappy`."""
    _seed_nights(db, monkeypatch, _S_MON, _S_WED, _S_THU)
    _artifact(asof=_S_THU, at=datetime(2026, 9, 10, 22, 30, tzinfo=timezone.utc))
    _pin(monkeypatch, datetime(2026, 9, 10, 23, 0))  # Thursday night, past RUN_AT
    body = client.get("/admin/status").json()
    assert body["record"]["edge"] == "2026-09-10"
    assert body["record"]["days_behind"] == 0 and body["record"]["stale"] is False  # edge: current
    assert body["record"]["missed"] == 1
    assert body["record"]["missed_asofs"] == ["2026-09-08"]
    assert body["record"]["window_days"] == 10
    assert (
        "no call-of-record" in body["record"]["reason"]
    )  # the reason no longer says "no run is missing"
    assert body["last_run"]["healthy"] is True
    assert body["cron"]["status"] == "gappy"
    assert "2026-09-08" in body["cron"]["detail"] and "hole" in body["cron"]["detail"]


def test_status_a_CLEAN_window_is_healthy_with_no_holes_mentioned(client, db, monkeypatch):
    """Honest loudness: a record with every scheduled night present reads healthy — missed 0, an empty
    list, and no mention of holes anywhere (a control that doesn't discriminate stays silent)."""
    _seed_nights(db, monkeypatch, _S_MON, _S_TUE, _S_WED)
    _artifact(asof=_S_WED, at=datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc))
    _pin(monkeypatch, datetime(2026, 9, 9, 23, 0))  # Wednesday night
    body = client.get("/admin/status").json()
    assert body["record"]["missed"] == 0 and body["record"]["missed_asofs"] == []
    assert body["record"]["reason"] == "current — no scheduled run is missing"
    assert body["cron"]["status"] == "healthy"
    assert "hole" not in body["cron"]["detail"]


def test_status_STALE_wins_over_gappy(client, db, monkeypatch):
    """Priority: a stale edge AND a hole → `stale` (the louder, more actionable verdict) — but the hole
    list still carries both the interior hole and the missing expected day (deliberately in both reads).
    """
    _seed_nights(db, monkeypatch, _S_MON, _S_WED)  # Tuesday hole; Thursday never recorded
    _artifact(asof=_S_WED, at=datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc))
    _pin(monkeypatch, datetime(2026, 9, 10, 23, 0))  # Thursday night: Thursday's run is expected
    body = client.get("/admin/status").json()
    assert body["record"]["days_behind"] == 1 and body["record"]["stale"] is True
    assert body["record"]["missed"] == 2
    assert body["record"]["missed_asofs"] == ["2026-09-08", "2026-09-10"]
    assert body["cron"]["status"] == "stale"
    assert "behind" in body["record"]["reason"]  # the stale wording keeps its exact meaning


def test_runs_a_CATCH_UP_row_with_zero_fetches_is_healthy_and_tagged(client, db, monkeypatch):
    """The Python side of the sidecar's late-wake catch-up: a `--catch-up` pass runs inside the EDGAR TTL
    and legitimately fetches ~0. Its artifact carries catch_up=true, the history re-derives health WITHOUT
    the freeze predicate (healthy, tagged), and — as the LAST run — it must not paint the cron unhealthy.
    The SAME numbers on a scheduled artifact still read FROZEN (the existing contract is untouched).
    """
    _seed_nights(db, monkeypatch, _S_TUE, _S_WED)
    zero = [_tr(recorded=False, edgar_fetches=0)]
    _artifact(asof=_S_TUE, at=datetime(2026, 9, 9, 3, 0, tzinfo=timezone.utc), results=zero)
    _artifact(
        asof=_S_WED,
        at=datetime(2026, 9, 10, 3, 5, tzinfo=timezone.utc),
        results=zero,
        catch_up=True,
    )
    runs = client.get("/admin/runs").json()["runs"]
    assert [r["asof"] for r in runs] == ["2026-09-09", "2026-09-08"]
    caught_up, scheduled = runs
    assert caught_up["catch_up"] is True and caught_up["healthy"] is True
    assert caught_up["problems"] == []
    assert scheduled["catch_up"] is False and scheduled["healthy"] is False
    assert any("FROZEN" in p for p in scheduled["problems"])

    _pin(monkeypatch, datetime(2026, 9, 9, 23, 0))  # Wednesday night: edge Wed is current
    body = client.get("/admin/status").json()
    assert body["last_run"]["catch_up"] is True
    assert body["cron"]["status"] == "healthy"  # the catch-up's ~0 fetches are not an alarm


# --- /admin/runs: the run history ---


def test_runs_newest_first_skip_unreadable_and_limit(client, cron_runs_dir):
    _artifact(asof=date(2026, 7, 16), at=datetime(2026, 7, 16, 22, 30, tzinfo=timezone.utc))
    _artifact(asof=_FRI, at=datetime(2026, 7, 17, 22, 30, tzinfo=timezone.utc))
    _artifact(asof=_MON, at=datetime(2026, 7, 20, 22, 30, tzinfo=timezone.utc))
    # a corrupt artifact that SORTS between the real ones — skipped fail-open, never a failed history
    (cron_runs_dir / "20260718T000000Z.json").write_text("{ not json", encoding="utf-8")

    body = client.get("/admin/runs").json()
    assert [r["asof"] for r in body["runs"]] == ["2026-07-20", "2026-07-17", "2026-07-16"]
    assert all(r["healthy"] is True for r in body["runs"])

    body = client.get("/admin/runs", params={"limit": 2}).json()
    assert [r["asof"] for r in body["runs"]] == ["2026-07-20", "2026-07-17"]


def test_runs_empty_when_no_artifacts(client):
    assert client.get("/admin/runs").json() == {"runs": []}


# --- the pure-ops invariant: the reads WRITE NOTHING ---


def _table_counts(db) -> dict[str, int]:
    """COUNT every public base table — the writes-nothing gate counts the tables, never trusts a read."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        counts: dict[str, int] = {}
        for n in names:
            cur.execute(f'SELECT COUNT(*) AS n FROM "{n}"')  # names come from the catalog, quoted
            counts[n] = cur.fetchone()["n"]
        return counts


def test_admin_reads_write_NOTHING(client, db, monkeypatch):
    """The slice's structural bound: /admin/status + /admin/runs are a pure ops READ surface — every
    public table holds exactly as many rows after the reads as before (over real seeded state, so the
    reads actually traverse data)."""
    _no_network(monkeypatch)
    _thesis(db, "T")
    daily.run_daily(db, asof=_FRI, allow_live=True)
    _artifact(asof=_FRI, at=datetime(2026, 7, 17, 22, 30, tzinfo=timezone.utc))
    before = _table_counts(db)
    assert client.get("/admin/status").status_code == 200
    assert client.get("/admin/runs").status_code == 200
    assert _table_counts(db) == before


# --- POST /admin/run-daily + the poll: the one explicit trigger ---


def test_run_daily_202_poll_done_shows_in_history_and_reclick_is_idempotent(
    client, db, monkeypatch
):
    """The full trigger loop: 202 + job_id → poll done with the run's counts → the pass wrote the SAME
    artifact the cron writes (it shows in /admin/runs) and the call-of-record landed. Then THE
    idempotency gate: a re-click runs the full pass again and appends ZERO calls rows — asserted by
    COUNTING the table (the read dedups, so a duplicate would hide behind it)."""
    _no_network(monkeypatch)
    tid = _thesis(db, "T")

    kicked = client.post("/admin/run-daily")
    assert kicked.status_code == 202
    job_id = kicked.json()["job_id"]
    assert kicked.json()["status"] == "running"

    polled = client.get(f"/admin/run-daily/jobs/{job_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "done" and body["error"] is None
    run = body["result"]
    assert run["mode"] == "live" and run["theses"] == 1
    assert run["appended"] == 1 and run["unchanged"] == 0
    assert len(_calls(db, tid)) == 1  # the call-of-record landed

    # the manual run is in the history exactly like a nightly one (same artifact, same shape)
    hist = client.get("/admin/runs").json()["runs"]
    assert len(hist) == 1 and hist[0]["appended"] == 1 and hist[0]["asof"] == run["asof"]

    # …and the record edge advanced to the pass's asof (today)
    status = client.get("/admin/status").json()
    assert status["record"]["edge"] == run["asof"]

    # THE re-click: a second full pass on unchanged facts appends NOTHING — count the table
    kicked2 = client.post("/admin/run-daily")
    assert kicked2.status_code == 202  # the slot freed on completion; a re-click is allowed
    body2 = client.get(f"/admin/run-daily/jobs/{kicked2.json()['job_id']}").json()
    assert body2["status"] == "done"
    assert body2["result"]["appended"] == 0 and body2["result"]["unchanged"] == 1
    assert len(_calls(db, tid)) == 1  # STILL one row — the table did not grow


def test_run_daily_409_while_a_run_is_in_flight(client, monkeypatch):
    """The concurrent-run guard: while a job holds the single slot, a second kick-off is 409 — a
    double-click can never stack a second live pass."""
    monkeypatch.setattr(daily_job, "_DEFAULT_EXECUTOR", lambda job, run: None)  # stays running
    assert client.post("/admin/run-daily").status_code == 202
    second = client.post("/admin/run-daily")
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]


def test_run_daily_failure_is_a_visible_failed_job(client, monkeypatch):
    """A pass that raises becomes a VISIBLE failed job on the poll (operator-facing message, no result)
    — never a silent 500, and the slot frees so the operator can retry."""

    def boom(**kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(admin, "run_daily_pass", boom)
    job_id = client.post("/admin/run-daily").json()["job_id"]
    body = client.get(f"/admin/run-daily/jobs/{job_id}").json()
    assert body["status"] == "failed" and body["result"] is None
    assert "db exploded" in body["error"]
    # the slot was freed — the trigger isn't bricked
    assert client.post("/admin/run-daily").status_code == 202


def test_poll_unknown_job_is_404(client):
    r = client.get("/admin/run-daily/jobs/no-such-job")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]
