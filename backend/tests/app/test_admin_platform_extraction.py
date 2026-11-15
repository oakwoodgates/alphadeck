"""V2 W3c — the Admin status's one line for background extraction (``AdminStatusOut.platform_extraction``): the
newer of the nightly pass's ``platform_extraction`` block (a finished run artifact) and the newest backfill /
onboarding / by-hand run file, ``null`` until one has run, ``attention`` passed through. Steward-only, proved with
REAL sign-ins: a plain user in either of two workspaces is refused.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from dashboards.cache import CACHE
from domain.settings import get_settings
from pipeline import extraction_run_log
from tests.auth_fixtures import sign_out
from tests.workspaces import new_workspace

PASSWORD = "correct horse battery"
T = datetime(2026, 11, 14, 3, 40, tzinfo=timezone.utc)


def _summary(at: datetime, *, run_kind: str, line: str, attention: bool = False) -> dict:
    return {
        "kind": "platform_extraction",
        "run_kind": run_kind,
        "status": "ran",
        "started_at": at.isoformat(),
        "finished_at": (at + timedelta(seconds=192)).isoformat(),
        "admin_line": line,
        "attention": attention,
    }


def _nightly(cron_runs_dir, at: datetime, block: dict, *, status: str = "finished") -> None:
    cron_runs_dir.mkdir(parents=True, exist_ok=True)
    doc = {"status": status, "started_at": at.isoformat(), "mode": "live", "asof": "2026-11-13"}
    doc["platform_extraction"] = block
    (cron_runs_dir / f"{at.strftime('%Y%m%dT%H%M%SZ')}.json").write_text(json.dumps(doc))


def test_the_line_is_null_until_a_run_exists(client):
    assert client.get("/admin/status").json()["platform_extraction"] is None


def test_the_nightly_block_is_the_line_and_its_attention_rides_through(client, cron_runs_dir):
    line = "Shared facts · Nov 13: 3 extracted: 2 applied, 1 to the steward, 0 nothing found · 9 SEC requests · 41s"
    _nightly(cron_runs_dir, T, _summary(T, run_kind="nightly", line=line))
    got = client.get("/admin/status").json()["platform_extraction"]
    assert got == {
        "line": line,
        "ran_at": got["ran_at"],
        "run_kind": "nightly",
        "attention": False,
    }
    assert got["ran_at"].startswith("2026-11-14T03:43:12")

    loud = "Shared facts · Nov 14: 0 extracted · 2 failed (ACME: SEC unreachable, BETA: not cached)"
    later = T + timedelta(days=1)
    _nightly(cron_runs_dir, later, _summary(later, run_kind="nightly", line=loud, attention=True))
    got = client.get("/admin/status").json()["platform_extraction"]
    assert (got["line"], got["attention"]) == (loud, True)


def test_an_unfinished_pass_s_block_is_never_shown(client, cron_runs_dir):
    _nightly(
        cron_runs_dir, T, _summary(T, run_kind="nightly", line="half a night"), status="running"
    )
    assert client.get("/admin/status").json()["platform_extraction"] is None


def test_the_newer_of_the_nightly_and_a_backfill_run_wins(
    client, cron_runs_dir, extraction_runs_dir
):
    _nightly(cron_runs_dir, T, _summary(T, run_kind="nightly", line="the nightly"))
    later = T + timedelta(hours=10)
    extraction_run_log.write_extraction_run(
        _summary(later, run_kind="backfill", line="the backfill"), base_dir=extraction_runs_dir
    )
    got = client.get("/admin/status").json()["platform_extraction"]
    assert (got["line"], got["run_kind"]) == ("the backfill", "backfill")

    newest = T + timedelta(days=1)
    _nightly(cron_runs_dir, newest, _summary(newest, run_kind="nightly", line="tonight"))
    assert client.get("/admin/status").json()["platform_extraction"]["line"] == "tonight"


@pytest.fixture
def signed_in(db):
    """Three real users on three workspaces (a plain user in W_A and in W_B, the steward in W_S), signed in through
    ``POST /auth/login``; the suite's steward override removed."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()
    for who, role in (("a", "user"), ("b", "user"), ("s", "super_admin")):
        auth.create_user(
            db,
            email=f"{who}@w3c.test",
            display_name=who.upper(),
            password=PASSWORD,
            role=role,
            workspace_id=new_workspace(db, f"W3c {who}"),
        )
    db.commit()

    def login(who: str) -> TestClient:
        c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        r = c.post("/auth/login", json={"email": f"{who}@w3c.test", "password": PASSWORD})
        assert r.status_code == 200, r.text
        return c

    yield login
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()
    CACHE.clear()


def test_the_line_is_steward_only(signed_in, cron_runs_dir):
    line = "Shared facts · Nov 13: 1 extracted: 1 applied, 0 to the steward, 0 nothing found · 2 SEC requests · 3s"
    _nightly(cron_runs_dir, T, _summary(T, run_kind="nightly", line=line))
    steward = signed_in("s").get("/admin/status")
    assert steward.status_code == 200 and steward.json()["platform_extraction"]["line"] == line
    for who in ("a", "b"):
        r = signed_in(who).get("/admin/status")
        assert r.status_code == 403, (who, r.text)
        assert "Shared facts" not in r.text
