"""The signed-in caller for API tests that are not about the login door (auth PR 1b, docs/AUTH.md).

Every router is mounted behind ``require_user`` / ``require_super_admin``. A test about a route's behavior signs
in by overriding both gates with a super admin on workspace ...0001 (``DEFAULT_TENANT_ID``), the one workspace
every user sits on in this release, so every existing read is unchanged. The gate itself is proved WITHOUT this
override in ``tests/app/test_auth_gate.py``.
"""

from __future__ import annotations

from uuid import UUID

from app.auth import CurrentUser, require_super_admin, require_user
from db.session import DEFAULT_TENANT_ID

TEST_STEWARD = CurrentUser(
    id=UUID("00000000-0000-0000-0000-00000000a001"),
    email="steward@test.invalid",
    display_name="Test steward",
    role="super_admin",
    workspace_id=DEFAULT_TENANT_ID,
    principal="session",
)


def sign_in_as_steward(app) -> None:
    """Override both gates on ``app`` with ``TEST_STEWARD``."""
    app.dependency_overrides[require_user] = lambda: TEST_STEWARD
    app.dependency_overrides[require_super_admin] = lambda: TEST_STEWARD


def sign_out(app) -> None:
    """Drop the two gate overrides (leaving any other override in place)."""
    app.dependency_overrides.pop(require_user, None)
    app.dependency_overrides.pop(require_super_admin, None)


class SignedInOverrides(dict):
    """An ``app.dependency_overrides`` that stays signed in across a ``clear()``.

    Many dashboard modules clear EVERY override inside a per-request helper and then make the next request, so a
    plain override set once per test would be gone by the second request. This dict re-applies the two gate
    overrides whenever it is cleared. Test-only; installed by ``tests/dashboards/conftest.py`` for one test and
    swapped back out after it."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._sign_in()

    def _sign_in(self) -> None:
        self[require_user] = lambda: TEST_STEWARD
        self[require_super_admin] = lambda: TEST_STEWARD

    def clear(self) -> None:
        super().clear()
        self._sign_in()
