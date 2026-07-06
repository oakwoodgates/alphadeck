from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from workbench import draft_run_log


@pytest.fixture
def client(db):
    """A ``TestClient`` sharing the test's ``db`` connection (overrides ``get_conn``), with dependency
    overrides CLEARED on teardown — so a test can add its own (e.g. a fake LLM client) with no ``try/finally``
    and a forgotten cleanup is impossible. ``db`` comes from the root ``tests/conftest.py``; ``get_conn`` is
    request-cached, so the test's committed seeds are the same connection the app's requests read.
    """
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def draft_runs_dir(tmp_path, monkeypatch):
    """Redirect the draft run-of-record home (``workbench/draft_run_log``) to a per-test tmp dir for EVERY app
    test — a completed draft job now dumps an artifact, and the suite must never litter the real gitignored
    ``data/draft_runs``. Autouse so no draft-running test can forget it; request it by name to assert on the
    written artifact."""
    d = tmp_path / "draft_runs"
    monkeypatch.setattr(draft_run_log, "_DEFAULT_RUNS", d)
    return d
