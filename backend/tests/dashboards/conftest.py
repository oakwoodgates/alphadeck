from __future__ import annotations

import pytest

from app.main import app
from tests.auth_fixtures import SignedInOverrides


@pytest.fixture(autouse=True)
def _signed_in_as_steward(monkeypatch):
    """The dashboard modules build their own ``TestClient(app)`` and clear every override inside their request
    helpers: sign each test in as a super admin on workspace ...0001 (the login door, docs/AUTH.md) with an
    override dict that stays signed in across those clears, so a dashboard test exercises the dashboard, not the
    login. The app's own override dict is put back after the test."""
    monkeypatch.setattr(app, "dependency_overrides", SignedInOverrides(app.dependency_overrides))
    yield
