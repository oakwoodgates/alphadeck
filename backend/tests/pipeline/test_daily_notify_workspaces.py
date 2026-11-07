"""Notify per workspace (docs/FEED_LOOP.md, auth PR 2 B1): each thesis's pushes go to ITS workspace's destination.

A workspace's own ``tenant.notify_webhook`` wins; a workspace without one reaches the env ``SLACK_WEBHOOK_URL``
only when its owner is a super admin; every other workspace gets the log notifier (the record, no push). The
real SlackNotifier runs here with ``httpx.post`` captured, so what is asserted is what would have been POSTed.
"""

from __future__ import annotations

import uuid
from datetime import date

import httpx
import pytest

from domain.settings import get_settings
from pipeline import daily
from tests.pipeline.test_daily import _armed_card, _no_network
from tests.workspaces import new_workspace

_ASOF = date(2026, 6, 10)
_ENV_URL = "https://hooks.example.test/env-channel"
_URL_A = "https://hooks.example.test/workspace-a"


@pytest.fixture(autouse=True)
def _fake_lock(monkeypatch):
    class _L:
        def release(self):
            pass

    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _L())


def _thesis_in(db, workspace, name, security_id):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, 'n')",
            (tid, workspace, name),
        )
        cur.execute(
            "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, %s, 0, 'DEVCO', '-', %s)",
            (uuid.uuid4(), workspace, tid, security_id),
        )
    db.commit()
    return tid


def _user_in(db, workspace, role):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'owner', 'x', %s, %s)",
            (uuid.uuid4(), f"{uuid.uuid4().hex}@test.invalid", role, workspace),
        )
    db.commit()


def test_a_three_workspace_pass_delivers_each_transition_to_its_own_destination(
    db, security_id, monkeypatch
):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _ENV_URL)
    get_settings.cache_clear()
    _no_network(monkeypatch)
    ws_a = new_workspace(db, "ws a")
    ws_b = new_workspace(db, "ws b")
    ws_c = new_workspace(db, "ws c")
    with db.cursor() as cur:
        cur.execute("UPDATE tenant SET notify_webhook = %s WHERE id = %s", (_URL_A, ws_a))
    _user_in(db, ws_a, "user")
    _user_in(db, ws_b, "user")  # no webhook, not a super admin: log only
    _user_in(db, ws_c, "super_admin")  # no webhook, a super admin: the env channel
    _thesis_in(db, ws_a, "Synthetic card A", security_id)
    _thesis_in(db, ws_b, "Synthetic card B", security_id)
    _thesis_in(db, ws_c, "Synthetic card C", security_id)

    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # day 1: incubating, the prior
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )
    posts: list[tuple[str, str]] = []

    def fake_post(url, **kw):
        posts.append((url, kw["json"]["text"]))

        class _R:
            def raise_for_status(self):
                pass

        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    try:
        daily.run_daily(db, asof=_ASOF, allow_live=True)  # day 2: all three arm
        by_url = {u: t for u, t in posts}
        assert set(by_url) == {_URL_A, _ENV_URL}  # B's transition went nowhere but the log
        assert "Synthetic card A" in by_url[_URL_A] and "Synthetic card B" not in by_url[_URL_A]
        assert "Synthetic card C" in by_url[_ENV_URL] and "Synthetic card A" not in by_url[_ENV_URL]
        assert len(posts) == 2
    finally:
        get_settings.cache_clear()
