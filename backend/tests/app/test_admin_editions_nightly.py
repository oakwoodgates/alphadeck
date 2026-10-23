"""Editions S5 — the Admin status's one line for the Scoreboard editions' nightly page job: the newest APPLIED
run artifact's own ``admin_line`` (a directory read, no DB), ``null`` until the job has run, a dry run never
shown, ``attention`` passed through (a real self-check mismatch or a crash)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline import edition_run_log


def _artifact(edition_runs_dir, started: datetime, *, mode="apply", line="x", attention=False):
    edition_run_log.write_edition_run(
        {
            "kind": edition_run_log.KIND,
            "mode": mode,
            "status": "done",
            "started_at": started.isoformat(),
            "finished_at": (started + timedelta(seconds=70)).isoformat(),
            "end": "2026-10-23",
            "admin_line": line,
            "attention": attention,
        },
        base_dir=edition_runs_dir,
    )


def test_the_line_is_null_until_the_job_has_run(client):
    assert client.get("/admin/status").json()["editions_nightly"] is None


def test_the_newest_applied_run_is_the_line_and_a_dry_run_never_shows(client, edition_runs_dir):
    t = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    line = "Edition 1 · page for Oct 23 added · 10 theses · self-check passed"
    _artifact(edition_runs_dir, t, line=line)
    _artifact(edition_runs_dir, t + timedelta(hours=1), mode="dry_run", line="a dry run")
    got = client.get("/admin/status").json()["editions_nightly"]
    assert got["line"] == line and got["attention"] is False and got["night"] == "2026-10-23"
    assert got["ran_at"].startswith("2026-10-24T03:01:10")


def test_attention_rides_through(client, edition_runs_dir):
    t = datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc)
    _artifact(edition_runs_dir, t, line="self-check: 1 thesis differs", attention=True)
    assert client.get("/admin/status").json()["editions_nightly"]["attention"] is True
