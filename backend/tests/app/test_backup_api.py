"""The Backups admin surface (Slice 4) — POST /admin/backup (202 + job), the poll, GET /admin/backups,
and the /admin/status last_backup join, against the real test DB (the ``client`` -> ``db`` -> alphadeck_test)
with ``pg_dump`` STUBBED (a fake that writes a dummy ``.sql`` — no binary needed).

The load-bearing gates live here: the create path WRITES NOTHING (proved by counting every public table
before/after — pg_dump is read-only, the runner touches no row), a failed dump is a LOUD ``failed`` job
(honest loudness), and the single-slot 409 guard holds. The backups dir is redirected to tmp by the
autouse ``backups_dir`` fixture in this package's conftest."""

from __future__ import annotations

import uuid

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline import backup, backup_job


@pytest.fixture(autouse=True)
def _inline_backup_jobs(monkeypatch):
    """Run snapshot jobs INLINE so a kicked-off create is terminal by the time the 202 returns — no
    thread-timing flakiness. Reset the in-process registry per test (the test_admin_api.py shape).
    """
    backup_job.reset_state()
    monkeypatch.setattr(
        backup_job, "_DEFAULT_EXECUTOR", lambda job, run: backup_job._run_job(job, run)
    )
    yield
    backup_job.reset_state()


@pytest.fixture(autouse=True)
def _stub_pg_dump(monkeypatch):
    """Stub the pg_dump subprocess so no endpoint test needs the binary. ``run_backup`` resolves
    ``_pg_dump`` at CALL TIME (``runner = dump_runner or _pg_dump``), so this monkeypatch is honored; the
    fake writes a dummy ``.sql`` at the tmp path the runner is handed."""

    def _fake(url, dest, *, timeout_s):
        dest.write_text("-- dummy dump\nSELECT 1;\n", encoding="utf-8")

    monkeypatch.setattr(backup, "_pg_dump", _fake)


def _thesis(db, name):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


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
            cur.execute(f'SELECT COUNT(*) AS n FROM "{n}"')
            counts[n] = cur.fetchone()["n"]
        return counts


# --- POST /admin/backup + the poll: the one explicit trigger ---


def test_create_202_poll_done_shows_in_list_and_status_age(client):
    """The full loop: 202 + job_id → poll done with the BackupOut → it shows in /admin/backups
    newest-first → /admin/status last_backup is set (the age join)."""
    kicked = client.post("/admin/backup")
    assert kicked.status_code == 202
    assert kicked.json()["status"] == "running"
    job_id = kicked.json()["job_id"]

    polled = client.get(f"/admin/backup/jobs/{job_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "done" and body["error"] is None
    result = body["result"]
    assert result["bytes"] > 0 and result["labeled"] is False
    assert result["name"].startswith("alphadeck-") and result["name"].endswith(".sql")

    listing = client.get("/admin/backups").json()["backups"]
    assert len(listing) == 1 and listing[0]["name"] == result["name"]

    status = client.get("/admin/status").json()
    assert status["last_backup"] is not None
    assert status["last_backup"]["name"] == result["name"]


def test_labeled_snapshot_is_named_and_flagged(client):
    """A label makes a NAMED, prune-exempt snapshot; the slug rides the filename."""
    ref = client.post("/admin/backup", json={"label": "Pre Migration"})
    assert ref.status_code == 202
    result = client.get(f"/admin/backup/jobs/{ref.json()['job_id']}").json()["result"]
    assert result["labeled"] is True
    assert "pre-migration" in result["name"]


def test_second_create_while_running_is_409(client, monkeypatch):
    """The single-slot guard: while a job holds the slot, a second create is 409 — a double-click can
    never stack a second pg_dump."""
    monkeypatch.setattr(backup_job, "_DEFAULT_EXECUTOR", lambda job, run: None)  # stays running
    assert client.post("/admin/backup").status_code == 202
    second = client.post("/admin/backup")
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"]


def test_dump_failure_is_a_visible_failed_job(client, monkeypatch):
    """A pg_dump that fails becomes a LOUD failed job on the poll (operator-facing message, no result) —
    never a silent success — and the slot frees so the operator can retry (honest loudness)."""

    def _boom(url, dest, *, timeout_s):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup, "_pg_dump", _boom)
    job_id = client.post("/admin/backup").json()["job_id"]
    body = client.get(f"/admin/backup/jobs/{job_id}").json()
    assert body["status"] == "failed" and body["result"] is None
    assert "disk full" in body["error"]
    # the slot was freed — the trigger isn't bricked (the re-kick 202s)
    assert client.post("/admin/backup").status_code == 202


def test_poll_unknown_job_is_404(client):
    r = client.get("/admin/backup/jobs/no-such-job")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


# --- the pure-ops invariant: the create path WRITES NOTHING ---


def test_backup_create_writes_NOTHING(client, db):
    """The slice's structural bound: a full create (with the file-writing stub) touches NO row — every
    public table holds exactly as many rows after as before (over real seeded state). pg_dump reads the
    DB; the runner opens no app connection and issues no SQL."""
    _thesis(db, "T")  # seed rows so the count actually traverses data
    before = _table_counts(db)

    job_id = client.post("/admin/backup").json()["job_id"]
    assert client.get(f"/admin/backup/jobs/{job_id}").json()["status"] == "done"
    assert client.get("/admin/backups").status_code == 200
    assert client.get("/admin/status").status_code == 200

    assert _table_counts(db) == before


# --- GET /admin/backups: the list read ---


def test_list_newest_first_labeled_and_skips_corrupt(client, backups_dir):
    """Newest-first ordering, the labeled flag, and skip-unreadable fail-open (a foreign .sql is dropped,
    never a failed list)."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "alphadeck-20260101T000000.sql").write_text("x", encoding="utf-8")
    (backups_dir / "alphadeck-20260103T000000-pre-migration.sql").write_text("x", encoding="utf-8")
    (backups_dir / "alphadeck-20260102T000000.sql").write_text("x", encoding="utf-8")
    (backups_dir / "garbage.sql").write_text("x", encoding="utf-8")  # skipped fail-open

    listing = client.get("/admin/backups").json()["backups"]
    assert [b["name"] for b in listing] == [
        "alphadeck-20260103T000000-pre-migration.sql",
        "alphadeck-20260102T000000.sql",
        "alphadeck-20260101T000000.sql",
    ]
    assert listing[0]["labeled"] is True and listing[1]["labeled"] is False


def test_list_empty_when_no_snapshots(client):
    assert client.get("/admin/backups").json() == {"backups": []}
    assert client.get("/admin/status").json()["last_backup"] is None
