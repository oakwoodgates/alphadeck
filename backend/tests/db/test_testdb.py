"""Meta-tests for the per-worktree test-DB resolver (``db/testdb.py``).

These call the pure functions DIRECTLY (never re-invoke ``pytest_configure``, which would abort the live
session). Env is set via ``monkeypatch`` (auto-restored); ``_worktree_root`` is faked via
``monkeypatch.setattr`` so the derived hash is deterministic and no git call is made. The single most
important case is ``test_hard_guard_rejects_non_test_names`` — the fail-closed guard that prevents the
2026-07-21 demo truncation.

The Postgres-gated smoke (``test_hook_lands_on_test_db`` / ``test_ensure_test_db_is_idempotent``) uses the
``db`` fixture, so it SKIPs when Postgres is unreachable, exactly like the rest of the DB suite.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from db import testdb
from db.session import DEFAULT_DATABASE_URL

# ---- resolve_test_db_name: auto-derive (no env, faked root) ----


def test_auto_derive_shape(monkeypatch):
    monkeypatch.delenv("ALPHADECK_TEST_DB", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/some/worktree/root")
    name = testdb.resolve_test_db_name()
    expected = "alphadeck_test_" + hashlib.sha1(b"/some/worktree/root").hexdigest()[:8]
    assert name == expected
    assert re.fullmatch(r"alphadeck_test_[0-9a-f]{8}", name)


def test_pin_wins_over_derivation(monkeypatch):
    monkeypatch.setenv("ALPHADECK_TEST_DB", "alphadeck_test")
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/ignored")
    assert testdb.resolve_test_db_name() == "alphadeck_test"


def test_xdist_worker_suffix(monkeypatch):
    monkeypatch.delenv("ALPHADECK_TEST_DB", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw3")
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/root")
    base = "alphadeck_test_" + hashlib.sha1(b"/root").hexdigest()[:8]
    assert testdb.resolve_test_db_name() == base + "_gw3"


def test_distinct_roots_give_distinct_names(monkeypatch):
    monkeypatch.delenv("ALPHADECK_TEST_DB", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/root/a")
    a = testdb.resolve_test_db_name()
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/root/b")
    b = testdb.resolve_test_db_name()
    assert a != b  # two worktrees -> two DBs (the isolation proof)


# ---- THE HARD GUARD (the key test) ----


@pytest.mark.parametrize("bad", ["alphadeck", "foo", "alphadeck_prod"])
def test_hard_guard_rejects_non_test_names(monkeypatch, bad):
    """A pin that does not start with ``alphadeck_test`` aborts before any URL/socket — fail-closed."""
    monkeypatch.setenv("ALPHADECK_TEST_DB", bad)
    with pytest.raises(RuntimeError) as exc:
        testdb.resolve_test_db_name()
    msg = str(exc.value)
    assert "must start with" in msg  # names the guard
    assert "2026-07-21" in msg  # names the reason (the demo truncation)
    assert bad in msg


@pytest.mark.parametrize("ok", ["alphadeck_test", "alphadeck_test_x", "alphadeck_test_deadbeef"])
def test_hard_guard_allows_test_names(monkeypatch, ok):
    monkeypatch.setenv("ALPHADECK_TEST_DB", ok)
    assert testdb.resolve_test_db_name() == ok


# ---- _with_dbname: swap only the db-name ----


def test_with_dbname_swaps_only_name():
    url = "postgresql://alphadeck:alphadeck@localhost:5544/alphadeck"
    out = testdb._with_dbname(url, "alphadeck_test_x")
    assert out == "postgresql://alphadeck:alphadeck@localhost:5544/alphadeck_test_x"


def test_with_dbname_preserves_creds_host_port_and_query():
    url = "postgresql://u:p@h:5544/alphadeck?sslmode=require"
    out = testdb._with_dbname(url, "alphadeck_test_x")
    assert out == "postgresql://u:p@h:5544/alphadeck_test_x?sslmode=require"


def test_with_dbname_on_default_url():
    out = testdb._with_dbname(DEFAULT_DATABASE_URL, "alphadeck_test_abc")
    assert out.endswith("/alphadeck_test_abc")
    assert "@localhost:5544/" in out


# ---- test_db_url: host/creds from DATABASE_URL, name swapped + guarded ----


def test_test_db_url_keeps_host_swaps_name(monkeypatch):
    monkeypatch.delenv("ALPHADECK_TEST_DB", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5544/alphadeck")
    monkeypatch.setattr(testdb, "_worktree_root", lambda: "/root")
    h = hashlib.sha1(b"/root").hexdigest()[:8]
    assert testdb.test_db_url() == f"postgresql://u:p@h:5544/alphadeck_test_{h}"


# ---- Postgres-gated smoke (uses the db/_migrated fixtures -> skips if Postgres is unreachable) ----


def test_hook_lands_on_test_db(db):
    """After the live pytest_configure hook, the suite connects to an alphadeck_test* DB, never the demo."""
    with db.cursor() as cur:
        cur.execute("SELECT current_database()")
        current = cur.fetchone()["current_database"]
    assert current.startswith("alphadeck_test")  # the override reached the fixture's connection
    assert current != "alphadeck"  # never the demo


def test_ensure_test_db_is_idempotent(_migrated):
    """A second ensure_test_db on the already-created DB is a no-op (DuplicateDatabase swallowed, no raise)."""
    testdb.ensure_test_db(testdb.test_db_url())
