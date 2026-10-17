"""M1 on the Admin surface — a killed or partial pass never reads healthy.

Against the real test DB with the router's clock pinned (``admin._now``). Every ``calls`` row is written by
the cron's own writer (``call_for_thesis`` + ``record_if_changed``) and every artifact by the real writers
(``write_run_start`` — all a hard-killed pass leaves — and ``write_cron_run_log``). Rows are recorded at the
real clock, AFTER the pinned September nights' RUN_AT, so they genuinely cover those nights (late, which
the partial line says; whether late rows count is the separate L1 policy); the theses are created with an
explicit ``created_at`` before the week, because the night's roster is the theses that existed by its
RUN_AT. The week is 2026-09-07 (Mon) .. 09-11 (Fri), EDT.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.routers import admin
from db.session import DEFAULT_TENANT_ID
from pipeline import daily
from pipeline.cron_run_log import mark_run_crashed, write_cron_run_log, write_run_start
from pipeline.daily import ThesisRunResult
from repositories import calls_repo

_NY = ZoneInfo("America/New_York")
_WEEK = [date(2026, 9, d) for d in (7, 8, 9, 10, 11)]
_BORN = datetime(2026, 8, 3, 12, 0, tzinfo=_NY)


def _et(m, d, hh, mm=0) -> datetime:
    return datetime(2026, m, d, hh, mm, tzinfo=_NY)


def _pin(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(admin, "_now", lambda: now)


def _thesis(db, name, *, created_at: datetime = _BORN):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", created_at),
        )
    db.commit()
    return tid


def _record(db, theses, nights) -> None:
    """The cron's writer, per (thesis, night)."""
    for asof in nights:
        for tid in theses:
            card = daily.call_for_thesis(db, tid, asof, record=False)
            calls_repo.record_if_changed(db, card, DEFAULT_TENANT_ID, run_kind="cron")
            db.commit()


def _finished(asof: date, at: datetime, n: int = 12, **identity):
    path = write_cron_run_log(
        [
            ThesisRunResult(thesis_id=uuid.uuid4(), name="T", recorded=True, edgar_fetches=40)
            for _ in range(n)
        ],
        asof=asof,
        allow_live=True,
        started_at=at.astimezone(timezone.utc),
        finished_at=(at + timedelta(minutes=9)).astimezone(timezone.utc),
        **identity,
    )
    assert path is not None
    return path


def _started(asof: date, at: datetime):
    path = write_run_start(asof=asof, allow_live=True, started_at=at.astimezone(timezone.utc))
    assert path is not None
    return path


def _status(client) -> dict:
    return client.get("/admin/status").json()


# --- the killed night ------------------------------------------------------------------------------------


def test_a_KILLED_pass_reads_RUNNING_in_its_timeout_then_DID_NOT_FINISH_and_its_night_PARTIAL(
    client, db, monkeypatch
):
    """The 10-12 shape on the Admin page. Thursday's 22:30 pass recorded 4 of 12 theses and the host died:
    on disk, a START artifact that never finalized; in the table, 4 rows for the night.
    - at 23:00 (30 min in, inside the 5400 s run timeout) it is a pass IN FLIGHT: `running`, no alarm, and
      its own night is not judged yet (a healthy pass mid-loop is partial by construction);
    - the next morning it is a pass that DID NOT FINISH: an alarm, `unhealthy`, and the night is listed
      PARTIAL — "4 of 12 theses". It used to read healthy for ~18 h (MEASURED, 10-12)."""
    theses = [_thesis(db, f"T{i:02d}") for i in range(12)]
    _record(db, theses, _WEEK[:3])  # Mon-Wed: full
    _record(db, theses[:4], _WEEK[3:4])  # Thu: the pass died after 4
    for d in _WEEK[:3]:
        _finished(
            d, datetime.combine(d, datetime.min.time(), tzinfo=_NY).replace(hour=22, minute=30)
        )
    _started(_WEEK[3], _et(9, 10, 22, 30))

    _pin(monkeypatch, _et(9, 10, 23, 0))  # 30 min into Thursday's pass
    body = _status(client)
    assert body["cron"]["status"] == "running", body["cron"]
    assert body["cron"]["alarms"] == []
    assert "a pass is running" in body["cron"]["detail"]
    assert body["unfinished_run"]["status"] == "running"
    assert body["unfinished_run"]["past_timeout"] is False
    assert body["record"]["partial_nights"] == []  # the in-flight night is not judged yet
    assert body["last_run"]["asof"] == "2026-09-09"  # the newest FINISHED pass

    _pin(monkeypatch, _et(9, 11, 9, 0))  # the next morning: the pass is long dead
    body = _status(client)
    assert body["cron"]["status"] == "unhealthy"
    [alarm] = body["cron"]["alarms"]
    assert alarm.startswith(
        "the last pass did not finish (started 2026-09-10 22:30 EDT; asof 2026-09-10"
    )
    assert "past the 5400 s run timeout" in alarm
    assert body["unfinished_run"]["past_timeout"] is True
    [partial] = body["record"]["partial_nights"]
    assert partial["asof"] == "2026-09-10"
    assert (partial["covered"], partial["expected"]) == (4, 12)
    assert body["record"]["missed_asofs"] == []  # ANY-row coverage alone still calls it covered
    assert "PARTIAL" in body["record"]["reason"]


def test_a_CRASHED_pass_alarms_at_once(client, db, monkeypatch):
    theses = [_thesis(db, f"C{i}") for i in range(3)]
    _record(db, theses, _WEEK[:4])
    for d in _WEEK[:3]:
        _finished(
            d, datetime.combine(d, datetime.min.time(), tzinfo=_NY).replace(hour=22, minute=30), 3
        )
    start = _et(9, 10, 22, 30)
    _started(_WEEK[3], start)
    mark_run_crashed(
        started_at=start.astimezone(timezone.utc),
        crashed_at=_et(9, 10, 22, 34).astimezone(timezone.utc),
        error="OperationalError: server closed the connection",
    )
    _pin(monkeypatch, _et(9, 10, 22, 40))  # inside the timeout — a crash does not wait for it
    body = _status(client)
    assert body["cron"]["status"] == "unhealthy"
    [alarm] = body["cron"]["alarms"]
    assert "the last pass CRASHED" in alarm and "OperationalError: server closed" in alarm
    assert body["unfinished_run"]["status"] == "crashed"


def test_rows_recorded_with_NO_run_artifact_for_their_night_alarm(client, db, monkeypatch):
    """(c): Thursday has post-RUN_AT rows for every thesis, but no pass left ANY artifact for Thursday — the
    newest artifact is Wednesday's. That is a pass that recorded and died before its artifact existed (the
    pre-M1 10-12 shape, where `last_run` kept showing the night before), or rows written outside the pass.
    """
    theses = [_thesis(db, f"O{i}") for i in range(3)]
    _record(db, theses, _WEEK[:4])
    _pin(monkeypatch, _et(9, 11, 9, 0))

    # on a stack WITHOUT the cron sidecar (dev: a prod-copied calls table, only hand-run artifacts) the
    # check stays silent — its rows were never this stack's passes to record
    for d in _WEEK[:3]:
        _finished(d, _et(9, d.day, 22, 30), 3, run_kind="manual")
    body = _status(client)
    assert not any("no pass left a run artifact" in a for a in body["cron"]["alarms"])

    # the cron sidecar's stack (cron-kind artifacts with a known sha): the missing artifact alarms
    for d in _WEEK[:3]:
        _finished(d, _et(9, d.day, 22, 31), 3, run_kind="cron", code_sha="2f3cdfa")
    body = _status(client)
    assert body["cron"]["status"] == "unhealthy"
    [orphan] = [a for a in body["cron"]["alarms"] if "no pass left a run artifact" in a]
    assert "for 2026-09-10 but no pass left a run artifact for that night" in orphan
    assert "the newest artifact is for asof 2026-09-09" in orphan
    assert (
        body["record"]["partial_nights"] == []
    )  # every thesis IS covered — the gap is the artifact


# --- the healthy shapes stay healthy -------------------------------------------------------------------


def test_a_FULL_week_and_its_WEEKEND_read_healthy_and_a_thesis_born_mid_week_is_not_owed_earlier_nights(
    client, db, monkeypatch
):
    theses = [_thesis(db, f"H{i}") for i in range(4)]
    late_born = _thesis(
        db, "born-thu-night", created_at=_et(9, 9, 23, 15)
    )  # after Wed's 22:30 pass
    _record(db, theses, _WEEK)
    _record(db, [late_born], _WEEK[3:])  # its first night is Thursday
    for d in _WEEK:
        _finished(
            d, datetime.combine(d, datetime.min.time(), tzinfo=_NY).replace(hour=22, minute=30), 5
        )

    for now in (_et(9, 11, 23, 0), _et(9, 13, 12, 0)):  # Friday night; Sunday noon
        _pin(monkeypatch, now)
        body = _status(client)
        assert body["cron"]["status"] == "healthy", body["cron"]
        assert body["cron"]["alarms"] == []
        assert body["record"]["partial_nights"] == [] and body["record"]["missed_asofs"] == []
        assert body["unfinished_run"] is None
        assert body["last_run"]["asof"] == "2026-09-11"


def test_a_LEGACY_artifact_without_status_is_still_the_last_run(
    client, db, monkeypatch, cron_runs_dir
):
    import json

    theses = [_thesis(db, f"L{i}") for i in range(2)]
    _record(db, theses, _WEEK[:1])
    path = _finished(_WEEK[0], _et(9, 7, 22, 30), 2)
    doc = json.loads(path.read_text(encoding="utf-8"))
    del doc["status"]  # exactly a pre-M1 artifact
    path.write_text(json.dumps(doc), encoding="utf-8")
    _pin(monkeypatch, _et(9, 7, 23, 30))
    body = _status(client)
    assert body["last_run"]["asof"] == "2026-09-07" and body["unfinished_run"] is None
    assert body["cron"]["status"] == "healthy"
    # and the run history skips an UNFINISHED pass rather than showing it as a clean empty run
    _started(_WEEK[1], _et(9, 8, 22, 30))
    runs = client.get("/admin/runs").json()["runs"]
    assert [r["asof"] for r in runs] == ["2026-09-07"]
