"""Notify per USER of a workspace (docs/FEED_LOOP.md step 3): each thesis's pushes go to every enabled user of ITS
workspace who set a webhook (``user_meta.slack_webhook_url``, migration 0079).

A workspace with no user webhook reaches the env ``SLACK_WEBHOOK_URL`` only when it holds an enabled super admin;
every other workspace gets the log notifier (the record, no push). The real SlackNotifier runs here with
``httpx.post`` captured, so what is asserted is what would have been POSTed.
"""

from __future__ import annotations

import uuid
from datetime import date

import httpx
import pytest

from domain.settings import get_settings
from notify import ArmedName, DigestEvent, DigestThesis, LogNotifier, SlackNotifier
from pipeline import daily
from repositories import user_meta_repo
from tests.pipeline.test_daily import _armed_card, _no_network
from tests.workspaces import new_workspace

_ASOF = date(2026, 6, 10)
_ENV_URL = "https://hooks.example.test/env-channel"
_URL_A1 = "https://hooks.slack.com/services/TEST/A1/invented-for-tests"
_URL_A2 = "https://hooks.slack.com/services/TEST/A2/invented-for-tests"
_URL_OFF = "https://hooks.slack.com/services/TEST/OFF/invented-for-tests"


@pytest.fixture(autouse=True)
def _fake_lock(monkeypatch):
    class _L:
        def release(self):
            pass

    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _L())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def _user_in(db, workspace, role="user", *, url=None, disabled=False):
    uid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id, disabled_at) "
            "VALUES (%s, %s, 'owner', 'x', %s, %s, CASE WHEN %s THEN now() END)",
            (uid, f"{uid.hex}@example.test", role, workspace, disabled),
        )
    if url is not None:
        user_meta_repo.set_slack_webhook(db, uid, url)
    db.commit()
    return uid


def _capture(monkeypatch, *, failing=()):
    posts: list[tuple[str, str]] = []

    def fake_post(url, **kw):
        posts.append((url, kw["json"]["text"]))
        if url in failing:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return posts


def test_a_three_workspace_pass_delivers_each_transition_to_its_own_users(
    db, security_id, monkeypatch
):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _ENV_URL)
    get_settings.cache_clear()
    _no_network(monkeypatch)
    ws_a = new_workspace(db, "ws a")
    ws_b = new_workspace(db, "ws b")
    ws_c = new_workspace(db, "ws c")
    _user_in(db, ws_a, url=_URL_A1)
    _user_in(db, ws_a, url=_URL_A2)
    _user_in(db, ws_a, url=_URL_OFF, disabled=True)  # a disabled user's URL is never posted to
    _user_in(db, ws_a)  # no user_meta row: adds nothing
    _user_in(db, ws_b, "user")  # no webhook, not a super admin: log only
    _user_in(db, ws_c, "super_admin")  # no webhook, a super admin: the env channel
    _thesis_in(db, ws_a, "Synthetic card A", security_id)
    _thesis_in(db, ws_b, "Synthetic card B", security_id)
    _thesis_in(db, ws_c, "Synthetic card C", security_id)

    daily.run_daily(db, asof=date(2026, 6, 5), allow_live=True)  # day 1: incubating, the prior
    monkeypatch.setattr(
        daily, "call_for_thesis", lambda conn, thid, asof, **k: _armed_card(thid, security_id, asof)
    )
    posts = _capture(monkeypatch)
    daily.run_daily(db, asof=_ASOF, allow_live=True)  # day 2: all three arm
    by_url: dict[str, list[str]] = {}
    for u, t in posts:
        by_url.setdefault(u, []).append(t)
    assert set(by_url) == {_URL_A1, _URL_A2, _ENV_URL}  # B's transition went nowhere but the log
    for url in (_URL_A1, _URL_A2):
        assert len(by_url[url]) == 1 and "Synthetic card A" in by_url[url][0]
        assert "Synthetic card B" not in by_url[url][0]
    assert (
        "Synthetic card C" in by_url[_ENV_URL][0] and "Synthetic card A" not in by_url[_ENV_URL][0]
    )
    assert len(posts) == 3


def test_two_users_on_one_url_get_one_post(db, monkeypatch):
    ws = new_workspace(db, "ws shared")
    _user_in(db, ws, url=_URL_A1)
    _user_in(db, ws, url=_URL_A1)
    dest = user_meta_repo.webhooks_by_workspace(db)[ws]
    assert dest.urls == (_URL_A1,) and dest.has_super_admin is False
    notifiers = daily._WorkspaceNotifiers(db)
    posts = _capture(monkeypatch)
    notifiers.for_workspace(ws).notify_digest(_digest())
    assert [u for u, _ in posts] == [_URL_A1]


def test_the_repo_read_skips_disabled_users_and_lists_an_empty_workspace(db):
    ws = new_workspace(db, "ws mixed")
    empty = new_workspace(db, "ws empty")
    _user_in(db, ws, url=_URL_A2)
    _user_in(db, ws, url=_URL_A1)
    _user_in(db, ws, "super_admin", url=_URL_OFF, disabled=True)
    _user_in(db, ws)
    got = user_meta_repo.webhooks_by_workspace(db)
    assert got[ws].urls == (_URL_A1, _URL_A2)  # sorted, the disabled user's URL excluded
    assert got[ws].has_super_admin is False  # a DISABLED super admin is no super admin
    assert got[empty].urls == () and got[empty].has_super_admin is False


def _digest() -> DigestEvent:
    return DigestEvent(
        asof=_ASOF,
        theses=(
            DigestThesis(
                thesis_id=uuid.uuid4(),
                thesis_name="Synthetic card D",
                names=(ArmedName("DEVCO", ("insider",)),),
            ),
        ),
    )


def test_the_digest_for_a_workspace_reaches_every_url_and_logs_once(db, monkeypatch, caplog):
    ws = new_workspace(db, "ws digest")
    _user_in(db, ws, url=_URL_A1)
    _user_in(db, ws, url=_URL_A2)
    notifiers = daily._WorkspaceNotifiers(db)
    posts = _capture(monkeypatch)
    with caplog.at_level("INFO"):
        notifiers.for_workspace(ws).notify_digest(_digest())
    assert sorted(u for u, _ in posts) == [_URL_A1, _URL_A2]
    assert all("Synthetic card D" in t for _, t in posts)
    records = [r for r in caplog.records if r.getMessage().startswith("DIGEST")]
    assert len(records) == 1  # ONE record however many URLs


class _CountingConn:
    """A proxy over the test connection that counts every ``execute``."""

    def __init__(self, conn):
        self._conn = conn
        self.executes = 0

    def cursor(self, *a, **k):
        outer = self
        cur = self._conn.cursor(*a, **k)

        class _Cur:
            def __enter__(self):
                cur.__enter__()
                return self

            def __exit__(self, *exc):
                return cur.__exit__(*exc)

            def execute(self, *ea, **ek):
                outer.executes += 1
                return cur.execute(*ea, **ek)

            def __getattr__(self, name):
                return getattr(cur, name)

        return _Cur()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_the_pass_start_read_is_one_query_then_a_pure_lookup(db):
    ws = new_workspace(db, "ws count")
    _user_in(db, ws, url=_URL_A1)
    counting = _CountingConn(db)
    notifiers = daily._WorkspaceNotifiers(counting)
    assert counting.executes == 1
    assert isinstance(notifiers.for_workspace(ws), SlackNotifier)
    assert isinstance(
        notifiers.for_workspace(uuid.uuid4()), LogNotifier
    )  # created mid-pass: log only
    assert counting.executes == 1


def test_a_failed_pass_start_read_leaves_every_workspace_log_only(db, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _ENV_URL)
    get_settings.cache_clear()
    ws = new_workspace(db, "ws fail")
    _user_in(db, ws, "super_admin", url=_URL_A1)

    def boom(conn):
        raise RuntimeError("the read failed")

    monkeypatch.setattr(user_meta_repo, "webhooks_by_workspace", boom)
    notifiers = daily._WorkspaceNotifiers(db)  # never raises
    assert isinstance(notifiers.for_workspace(ws), LogNotifier)


def test_an_explicit_override_answers_for_every_workspace(db):
    ws = new_workspace(db, "ws override")
    _user_in(db, ws, url=_URL_A1)
    sentinel = LogNotifier()
    notifiers = daily._WorkspaceNotifiers(db, sentinel)
    assert notifiers.for_workspace(ws) is sentinel
    assert notifiers.for_workspace(uuid.uuid4()) is sentinel


def test_a_failing_first_url_still_posts_to_the_second(db, monkeypatch):
    ws = new_workspace(db, "ws failing")
    _user_in(db, ws, url=_URL_A1)
    _user_in(db, ws, url=_URL_A2)
    notifiers = daily._WorkspaceNotifiers(db)
    posts = _capture(monkeypatch, failing={_URL_A1})
    notifiers.for_workspace(ws).notify_digest(_digest())  # never raises
    assert [u for u, _ in posts] == [_URL_A1, _URL_A2]
