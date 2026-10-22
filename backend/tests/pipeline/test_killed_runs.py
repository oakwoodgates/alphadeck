"""M1 + M2 + C3 end to end — a killed or partial pass never hides, its catch-up records ONLY what is
missing, and a hand-typed past ``--asof`` cannot write a scored row.

DB-backed where the claim is about the record: the real test Postgres, the real run lock, the real
``run_daily`` → ``call_for_thesis`` → ``record_if_changed`` path and the real self-audit; only the network
legs are stubbed. Every row assertion COUNTS THE TABLE. The "killed night" is built from what a hard-killed
pass genuinely leaves behind: on-time rows for the theses it reached (the cron's own writer) and its START
artifact (``write_run_start``) never finalized — no Python runs after a VM dies, so there is nothing else.
The night's ``RUN_AT`` is pinned (UTC zone, a wall time an hour before the DB clock) so every row written
by the test is genuinely on time for it; no recorded_at is forged.
"""

from __future__ import annotations

import json
import re
import time as _time
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from domain.settings import get_settings
from ingest.edgar.form4 import ingest_form4
from pipeline import cron_run_log, daily
from pipeline.cron_run_log import already_ran_live, write_run_start
from repositories import calls_repo

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")
_BACKEND = Path(__file__).resolve().parents[2]


class _Recorder:
    def __init__(self):
        self.health = []

    def notify(self, event):
        pass

    def notify_health(self, event):
        self.health.append(event)

    def notify_spac_status(self, event):
        pass


def _thesis(db, name, *, created_at: datetime, member: uuid.UUID | None = None):
    """A thesis that EXISTED before the night under test (``created_at`` set explicitly — the column's
    ``now()`` default would make it younger than the pinned night, so out of that night's roster), with
    an optional one-name basket."""
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", created_at),
        )
        if member is not None:
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, 0, "DEVCO", "—", member),
            )
    db.commit()
    return tid


def _security(db, ticker) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0001234567", "2026-01-01"),
        )
    db.commit()
    return sid


# The repo's real Form 4 fixture: a senior (CEO) open-market P-buy on 2026-06-01 (+ an older txn 05-15).
# `test_daily` proves it warms a fact-less thesis at as-of 2026-06-10. Here the night under test is pinned
# near the real clock, so the filing is moved to the SAME distance from it (every date shifted by one delta
# — the filing stays internally consistent) and ingested AFTER the night's rows: the overnight input change
# (MEASURED on 10-12: 3 of the re-recorded rows cite Form 4s SEC accepted the next day).
_FORM4 = (_BACKEND / "tests" / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)


def _ingest_p_buy(db, sid, asof: date) -> None:
    delta = asof - date(2026, 6, 10)
    xml = _FORM4
    for d in ("2026-06-01", "2026-05-15"):
        xml = xml.replace(d, (date.fromisoformat(d) + delta).isoformat())
    res = ingest_form4(db, sid, xml, f"0000000000-{asof.year % 100:02d}-000001")
    db.commit()
    assert res.appended and not res.rejected  # the facts landed


def _count(db, *, thesis_id=None, asof=None) -> int:
    db.rollback()  # a fresh snapshot — rows another connection committed must be visible
    sql, params = "SELECT count(*) AS n FROM calls WHERE true", []
    if thesis_id is not None:
        sql, params = sql + " AND thesis_id = %s", [*params, thesis_id]
    if asof is not None:
        sql, params = sql + " AND asof = %s", [*params, asof]
    with db.cursor() as cur:
        cur.execute(sql, params)
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _db_now(db) -> datetime:
    with db.cursor() as cur:
        cur.execute("SELECT clock_timestamp() AS t")
        t = cur.fetchone()["t"]
    db.rollback()
    return t.astimezone(timezone.utc)


def _offline(monkeypatch, *, ingested: list | None = None):
    """A LIVE pass with no network: the refresh / sweep / radar legs are no-ops and the per-thesis ingest
    records which thesis it was asked for. The lock, the loop, ``record_if_changed``, the artifacts and the
    self-audit all run for real."""

    def _ingest(conn, thesis_id, **k):
        if ingested is not None:
            ingested.append(thesis_id)
        return []

    monkeypatch.setattr(daily, "ingest_thesis", _ingest)
    monkeypatch.setattr("pipeline.ingest_benchmarks.ingest_benchmarks", lambda conn, **k: [])
    monkeypatch.setattr("pipeline.ingest_fundamentals.ingest_fundamentals", lambda conn, **k: [])
    monkeypatch.setattr(
        "radar.shell_sweep.run_shell_sweep",
        lambda conn, **k: SimpleNamespace(summary="stub", errors=[]),
    )
    monkeypatch.setattr(
        "radar.spac.run_spac_radar", lambda conn, **k: SimpleNamespace(summary="stub", errors=[])
    )
    rec = _Recorder()
    monkeypatch.setattr(daily, "get_notifier", lambda: rec)
    return rec


def _pin_night(monkeypatch, cutoff: datetime) -> date:
    """Pin the night: RUN_AT = ``cutoff``'s wall time in a UTC market zone, so ``cutoff`` IS the night's
    RUN_AT instant. Returns the night's as-of."""
    real = get_settings()
    # image_sha as a deployed image carries it (else the code_sha audit check pages every pass here)
    pinned = real.model_copy(
        update={"cron_run_at": cutoff.time().isoformat(), "image_sha": "test-sha"}
    )
    monkeypatch.setattr(daily, "get_settings", lambda: pinned)
    monkeypatch.setattr(daily, "market_tz", lambda: _UTC)
    return cutoff.date()


def _next_second() -> None:
    """Two passes never START in the same second in production (the run lock, and a fresh interpreter per
    pass); in-process they can, and an artifact is named by its started-at second — so a test that runs a
    second pass right after one that died fast waits for the clock to tick (< 1 s)."""
    s = datetime.now(timezone.utc).replace(microsecond=0)
    while datetime.now(timezone.utc).replace(microsecond=0) == s:
        _time.sleep(0.01)


def _artifacts(home: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(home.glob("*.json"))]


# --- M1 + M2: the killed night, end to end --------------------------------------------------------------


def test_a_KILLED_night_is_caught_up_and_the_catch_up_records_ONLY_the_missing_theses(
    db, monkeypatch, tmp_path
):
    """The 10-12 shape, MEASURED: the 22:30 pass recorded 4 of 12 theses (22:42-22:43) and the VM died; the
    next day's boot catch-up re-ran all 12 and — its inputs having changed overnight (restated bars, and
    Form 4s SEC accepted the next day) — appended NEW versions over the 4 honest on-time rows. Now:
    - M1: the killed pass's START artifact (``running``) does NOT satisfy the --catch-up guard, so the
      catch-up runs at all (the trap: a killed night must never block its own recovery);
    - M2: the catch-up appends rows for exactly the 8 missing theses and ZERO for the 4 on-time ones —
      even though the 4's inputs CHANGED overnight (a senior open-market P-buy ingested after the night,
      which warms them), and the control at the end proves that change would otherwise have re-recorded
      all 4."""
    home = tmp_path / "cron_runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    cutoff = _db_now(db) - timedelta(hours=1)  # the night's RUN_AT, an hour ago
    asof = _pin_night(monkeypatch, cutoff)
    born = cutoff - timedelta(days=30)
    sid = _security(db, "DEVCO")
    theses = [
        _thesis(db, f"T{i:02d}", created_at=born, member=(sid if i < 4 else None))
        for i in range(12)
    ]
    ingested: list = []
    _offline(monkeypatch, ingested=ingested)

    # --- the killed pass: started at RUN_AT, recorded the first 4 through the cron's writer, then died ---
    write_run_start(
        asof=asof, allow_live=True, started_at=cutoff + timedelta(seconds=1), run_kind="cron"
    )
    on_time = theses[:4]
    for tid in on_time:
        card = daily.call_for_thesis(db, tid, asof, record=False)
        assert card.state is State.INCUBATING  # no facts yet
        assert calls_repo.record_if_changed(db, card, DEFAULT_TENANT_ID, run_kind="cron") is True
        db.commit()
    assert _count(db, asof=asof) == 4
    assert already_ran_live(asof, run_at=cutoff.time(), tz=_UTC) is False  # THE GUARD TRAP

    # --- overnight, the 4's inputs change: their member's Form 4 lands (accepted after the night) ---
    _ingest_p_buy(db, sid, asof)

    # --- the sidecar's boot catch-up, its exact argv ---
    daily.main(["--run-kind", "cron", "--catch-up", "--asof", asof.isoformat()])

    assert _count(db, asof=asof) == 12  # +8: exactly the missing theses
    for tid in on_time:
        assert _count(db, thesis_id=tid, asof=asof) == 1  # ZERO re-records of an on-time row
        assert calls_repo.latest_for_thesis(db, tid)[0].state is State.INCUBATING  # it stands
    db.rollback()
    for tid in theses[4:]:
        assert _count(db, thesis_id=tid, asof=asof) == 1
    assert set(ingested) == set(theses[4:])  # skipped means skipped: no ingest for the 4

    docs = _artifacts(home)
    assert [d["status"] for d in docs] == [
        "running",
        "finished",
    ]  # the killed pass's, the catch-up's
    caught = docs[1]
    assert caught["catch_up"] is True and caught["summary"]["skipped"] == 4
    assert caught["summary"]["appended"] == 8
    skipped = {t["id"] for t in caught["theses"] if t["skipped"]}
    assert skipped == {str(t) for t in on_time}
    reasons = [t["skipped"] for t in caught["theses"] if t["skipped"]]
    assert all(r.startswith("on-time call-of-record already recorded") for r in reasons)
    audit = {a["check"]: a["ok"] for a in caught["audit"]}
    assert audit["on_time_rows"] is True and audit["every_thesis_finished"] is True
    assert already_ran_live(asof, run_at=cutoff.time(), tz=_UTC) is True  # the night is credited

    # --- the control: a pass that is NOT a catch-up re-versions exactly the 4 whose inputs changed ---
    before = _count(db, asof=asof)
    daily.run_daily(db, asof=asof, allow_live=True, run_kind="manual")
    assert _count(db, asof=asof) == before + 4
    for tid in on_time:
        assert calls_repo.latest_for_thesis(db, tid)[0].state is State.WARMING
    db.rollback()


def test_a_catch_up_does_NOT_skip_a_thesis_whose_only_row_is_LATE(db, monkeypatch, tmp_path):
    """On time means inside ``[RUN_AT, the next weekday's 06:00 EDGAR open)``. A thesis whose only row for
    the night landed after that (a next-day catch-up's) has no on-time record, so a catch-up still owes it
    the night: it is processed exactly as before M2. The night is ten days back, so a row written now is
    genuinely late."""
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    cutoff = _db_now(db) - timedelta(days=10)
    born = cutoff - timedelta(days=30)
    late, missing = _thesis(db, "late", created_at=born), _thesis(db, "missing", created_at=born)
    asof = _pin_night(monkeypatch, cutoff)
    card = daily.call_for_thesis(db, late, asof, record=False)
    calls_repo.record_if_changed(
        db, card, DEFAULT_TENANT_ID, run_kind="cron"
    )  # recorded NOW = late
    db.commit()
    ingested: list = []
    _offline(monkeypatch, ingested=ingested)

    results = daily.run_daily(db, asof=asof, allow_live=True, run_kind="cron", catch_up=True)

    by_id = {r.thesis_id: r for r in results}
    assert by_id[late].skipped is None and by_id[missing].skipped is None
    assert set(ingested) == {late, missing}  # both processed
    assert by_id[late].recorded is False  # unchanged card -> no row (record_if_changed)
    assert by_id[missing].recorded is True
    assert (
        _count(db, thesis_id=late, asof=asof) == 1 and _count(db, thesis_id=missing, asof=asof) == 1
    )


def test_a_catch_up_that_skips_EVERY_thesis_exits_0_and_never_pages_a_retry(
    db, monkeypatch, tmp_path, capsys
):
    """A night whose every thesis already holds an on-time row (the guard could not see it — say the
    artifact write failed): the catch-up skips all of them, appends NOTHING, prints one quiet line, and
    exits 0 — the night HAS its record (``_failed_to_record`` counts a skipped thesis)."""
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path / "cron_runs")
    cutoff = _db_now(db) - timedelta(hours=1)
    theses = [_thesis(db, f"S{i}", created_at=cutoff - timedelta(days=30)) for i in range(3)]
    asof = _pin_night(monkeypatch, cutoff)
    for tid in theses:
        card = daily.call_for_thesis(db, tid, asof, record=False)
        calls_repo.record_if_changed(db, card, DEFAULT_TENANT_ID, run_kind="cron")
        db.commit()
    rec = _offline(monkeypatch)
    before = _count(db)

    daily.main(["--run-kind", "cron", "--catch-up", "--asof", asof.isoformat()])  # no SystemExit

    assert _count(db) == before
    out = capsys.readouterr().out
    assert "3 thesis(es) skipped — an on-time call-of-record for the night already stands" in out
    assert "· 3 skipped (on-time)" in out
    assert rec.health == []  # nothing to page


def test_a_pass_that_CRASHES_marks_its_start_artifact_crashed_and_the_catch_up_still_runs(
    db, monkeypatch, tmp_path
):
    """H3 + M1: an exception escaping the pass pages AND re-marks the start artifact ``crashed`` (with the
    reason); the guard never credits it, so the next --catch-up runs the night for real."""
    home = tmp_path / "cron_runs"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", home)
    cutoff = _db_now(db) - timedelta(hours=1)
    tid = _thesis(db, "crashy", created_at=cutoff - timedelta(days=30))
    asof = _pin_night(monkeypatch, cutoff)
    rec = _offline(monkeypatch)
    real_run_daily = daily.run_daily

    def _boom(*a, **k):
        raise RuntimeError("loop exploded")

    monkeypatch.setattr(daily, "run_daily", _boom)
    with pytest.raises(RuntimeError, match="loop exploded"):
        daily.run_daily_pass(asof=asof, allow_live=True, notifier=rec, run_kind="cron")
    [doc] = _artifacts(home)
    assert doc["status"] == "crashed" and doc["error"] == "RuntimeError: loop exploded"
    assert [h.crashed for h in rec.health] == ["RuntimeError: loop exploded"]

    monkeypatch.setattr(daily, "run_daily", real_run_daily)
    _next_second()
    daily.main(["--run-kind", "cron", "--catch-up", "--asof", asof.isoformat()])
    assert _count(db, thesis_id=tid, asof=asof) == 1
    assert [d["status"] for d in _artifacts(home)] == ["crashed", "finished"]


# --- C3: a hand-typed past --asof is refused ---------------------------------------------------------


def _cli(monkeypatch, now: datetime):
    """The CLI with the market clock at ``now`` (22:30 ET RUN_AT, the prod default) and the pass stubbed:
    returns a dict recording whether the lock was taken and with which as-of the pass ran."""
    monkeypatch.setattr(daily, "market_now", lambda: now)
    monkeypatch.setattr(
        daily, "get_settings", lambda: get_settings().model_copy(update={"cron_run_at": "22:30"})
    )
    monkeypatch.setattr(daily, "market_tz", lambda: _NY)
    monkeypatch.setattr(daily, "get_notifier", lambda: _Recorder())
    monkeypatch.setattr(daily, "already_ran_live", lambda asof, **kw: False)
    seen: dict = {"locked": 0}

    class _Lock:
        def release(self):
            pass

    def _acquire(**kw):
        seen["locked"] += 1
        return _Lock()

    def _pass(**kw):
        seen["asof"] = kw["asof"]
        t = datetime(2026, 10, 17, 2, 31, tzinfo=timezone.utc)
        return daily.DailyPassOutcome(
            results=[],
            asof=kw["asof"],
            allow_live=kw["allow_live"],
            started_at=t,
            finished_at=t,
            log_path=None,
        )

    monkeypatch.setattr(daily, "acquire_pass_lock", _acquire)
    monkeypatch.setattr(daily, "run_daily_pass", _pass)
    return seen


_FRI_NIGHT = datetime(
    2026, 10, 16, 23, 0, tzinfo=_NY
)  # Friday after RUN_AT: last expected = Fri 10-16


def test_C3_a_hand_typed_PAST_asof_is_REFUSED_before_anything_runs(monkeypatch, capsys):
    seen = _cli(monkeypatch, _FRI_NIGHT)
    with pytest.raises(SystemExit) as exc:
        daily.main(["--asof", "2026-10-12"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--asof 2026-10-12 is older than the last expected night (2026-10-16" in err
    assert "python -m pipeline.backfill --asof 2026-10-12` (a PUBLIC-CLOCK reconstruction" in err
    assert seen["locked"] == 0 and "asof" not in seen  # no lock, no pass, no artifact, no row


@pytest.mark.parametrize(
    "argv",
    [
        ["--catch-up", "--asof", "2026-10-12"],  # the sidecar's guarded catch-up
        ["--late-ok", "--asof", "2026-10-12"],  # a deliberate late record
        ["--no-live", "--asof", "2026-10-12"],  # records nothing, so it cannot score anything
    ],
)
def test_C3_the_exemptions_RUN_a_past_asof(monkeypatch, argv):
    seen = _cli(monkeypatch, _FRI_NIGHT)
    daily.main(argv)
    assert seen["asof"] == date(2026, 10, 12) and seen["locked"] == 1


@pytest.mark.parametrize(
    "now, argv, asof",
    [
        # a DAYTIME manual pass for TODAY (Friday 14:00, before RUN_AT: last expected = Thu 10-15)
        (datetime(2026, 10, 16, 14, 0, tzinfo=_NY), ["--asof", "2026-10-16"], date(2026, 10, 16)),
        # the last expected night itself, typed the next morning
        (datetime(2026, 10, 16, 9, 0, tzinfo=_NY), ["--asof", "2026-10-15"], date(2026, 10, 15)),
        # bare: today, never older
        (_FRI_NIGHT, [], date(2026, 10, 16)),
    ],
)
def test_C3_TODAY_and_the_last_expected_night_stay_ALLOWED(monkeypatch, now, argv, asof):
    monkeypatch.setattr(daily, "market_today", lambda: now.date())
    seen = _cli(monkeypatch, now)
    daily.main(argv)
    assert seen["asof"] == asof


def test_C3_the_rule_on_a_WEEKEND():
    sun = datetime(2026, 10, 18, 12, 0, tzinfo=_NY)  # last expected = Fri 10-16
    ra = time(22, 30)
    assert daily.past_asof_refusal(date(2026, 10, 16), now=sun, run_at=ra) is None
    assert daily.past_asof_refusal(date(2026, 10, 17), now=sun, run_at=ra) is None  # Saturday
    assert "older than the last expected night (2026-10-16" in daily.past_asof_refusal(
        date(2026, 10, 15), now=sun, run_at=ra
    )


# --- C3 vs the sidecar: every `pipeline.daily` invocation in daily_cron.sh --------------------------------


def _script() -> str:
    return (_BACKEND / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")


def test_every_SIDECAR_invocation_carries_catch_up_or_late_ok():
    """Static: the sidecar reaches `pipeline.daily` ONLY through `run_pass`, and every `run_pass` call is
    either a `--catch-up` (the boot catch-up, its retry, the crossed-RUN_AT catch-up, the late-wake
    `catch_up_between`, the scheduled run's retry) or the scheduled run with `--late-ok`. So C3 can never
    refuse the cron's own passes, whatever the clock says when they fire."""
    lines = [ln.strip() for ln in _script().splitlines() if not ln.strip().startswith("#")]
    direct = [ln for ln in lines if "pipeline.daily" in ln and "python -m pipeline.daily" in ln]
    assert len(direct) == 1 and direct[0].startswith("timeout ")  # only inside run_pass
    calls = [ln for ln in lines if re.search(r"\brun_pass --", ln)]
    assert len(calls) == 5, calls  # boot, boot retry, catch_up_between, scheduled, scheduled retry
    for ln in calls:
        assert "--run-kind cron" in ln, ln
        assert "--catch-up" in ln or "--late-ok" in ln, ln
    scheduled = [ln for ln in calls if "--catch-up" not in ln]
    assert len(scheduled) == 1 and '--late-ok --asof "${target}"' in scheduled[0]


@pytest.mark.parametrize(
    "fired_at, target, needs_late_ok",
    [
        # on time: Friday's pass fires at 22:30:05 for Friday — the flag is inert
        (datetime(2026, 10, 16, 22, 30, 5, tzinfo=_NY), date(2026, 10, 16), False),
        # a suspend overshoot past midnight (the MEASURED 00:24 shape): still the last expected night
        (datetime(2026, 10, 14, 0, 24, tzinfo=_NY), date(2026, 10, 13), False),
        # the overshoot PAST the next weekday's RUN_AT (target Tue, wake Wed 22:40): late by construction
        (datetime(2026, 10, 14, 22, 40, tzinfo=_NY), date(2026, 10, 13), True),
    ],
)
def test_the_SCHEDULED_argv_runs_at_any_fire_time_and_late_ok_is_what_carries_an_overshoot(
    monkeypatch, fired_at, target, needs_late_ok
):
    seen = _cli(monkeypatch, fired_at)
    daily.main(
        ["--run-kind", "cron", "--late-ok", "--asof", target.isoformat()]
    )  # the sidecar's argv
    assert seen["asof"] == target
    refusal = daily.past_asof_refusal(target, now=fired_at, run_at=time(22, 30))
    assert (refusal is not None) is needs_late_ok


def test_the_RUN_TIMEOUT_the_Admin_ages_a_pass_by_is_the_one_the_sidecar_enforces():
    """M1's "did not finish" alarm is only honest if the backend ages a `running` artifact by the SAME
    ceiling the sidecar's `timeout` kills it at: one host var feeds both services (compose), and every
    fallback — the backend's, the sidecar's, the shell's own and the code default — must agree."""
    from domain.settings import Settings

    compose = (_BACKEND.parent / "docker-compose.yml").read_text(encoding="utf-8")
    fallbacks = re.findall(r"\$\{ALPHADECK_CRON_RUN_TIMEOUT_S:-(\d+)\}", compose)
    assert len(fallbacks) == 2  # the backend's mirror and the sidecar's RUN_TIMEOUT_S
    assert "ALPHADECK_CRON_RUN_TIMEOUT_S: ${ALPHADECK_CRON_RUN_TIMEOUT_S:-" in compose  # backend
    shell = re.search(r'RUN_TIMEOUT_S="\$\{RUN_TIMEOUT_S:-(\d+)\}"', _script())
    assert shell is not None
    default = Settings.model_fields["cron_run_timeout_s"].default
    assert {int(x) for x in fallbacks} == {int(shell.group(1))} == {int(default)}
