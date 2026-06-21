from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app


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
