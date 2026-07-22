"""Per-worktree test-DB resolution — the systemic shared-Postgres fix.

One Postgres (host ``:5544``) is shared by the demo DB (``alphadeck``) and the test DB across EVERY
worktree. Two failure modes bit repeatedly: an un-overridden ``pytest`` (no ``DATABASE_URL``) resolved to
the DEFAULT ``alphadeck`` and the ``db`` fixture's ``TRUNCATE`` wiped the demo (the 2026-07-21 catastrophe);
and multiple worktrees all pinning ``DATABASE_URL=…alphadeck_test`` contended on ONE DB (serialized
TRUNCATEs, cross-process deadlocks). This module makes the isolation **structural**, replacing the human
"remember to set ``DATABASE_URL``" memory rule.

Pure + unit-testable: importing this module has NO side effects and opens NO connection. The
``pytest_configure`` hook (``tests/conftest.py``) calls ``test_db_url`` + ``ensure_test_db`` at session
startup, before any fixture or ``connect()``. The single load-bearing safety property is the HARD GUARD in
``resolve_test_db_name``: the resolved name MUST start with ``alphadeck_test`` or it raises *before* any URL
is built or any socket opened — so no missing env, stale ``DATABASE_URL=…/alphadeck``, or typo can ever
point the suite at the demo.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from db.session import DEFAULT_DATABASE_URL

_TEST_DB_PREFIX = "alphadeck_test"  # the ONLY prefix the hard guard permits


def _worktree_root() -> str:
    """The physical checkout root, stable per worktree.

    ``git rev-parse --show-toplevel`` returns the LINKED worktree's own root (not the main checkout), so two
    worktrees hash to two names -> two DBs; the root is stable across branch switches within a worktree
    (unlike a branch name). Falls back to ``Path.cwd()`` when git is absent/errors (pytest runs from
    ``backend/``, a stable path).
    """
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return str(Path.cwd())


def resolve_test_db_name() -> str:
    """Resolve the per-worktree test DB name, then HARD-GUARD it (fail-closed).

    Precedence: an explicit ``ALPHADECK_TEST_DB`` pin wins (CI / a deliberate operator choice); otherwise
    auto-derive a name stable per worktree by hashing the worktree root (with a ``PYTEST_XDIST_WORKER``
    suffix so within-run parallelism gets its own DB too). The guard fires on BOTH paths, before any URL is
    built or socket opened: the name MUST start with ``alphadeck_test`` or this raises.
    """
    name = os.environ.get("ALPHADECK_TEST_DB")  # 1. explicit pin wins (CI / operator)
    if not name:
        root = _worktree_root()  # 2. auto-derive, stable per worktree
        name = f"{_TEST_DB_PREFIX}_" + hashlib.sha1(root.encode()).hexdigest()[:8]
        worker = os.environ.get(
            "PYTEST_XDIST_WORKER"
        )  # within-run parallelism -> its own DB (future-proof)
        if worker:
            name += "_" + worker
    if not name.startswith(_TEST_DB_PREFIX):  # 3. THE HARD GUARD — fail-closed, always
        raise RuntimeError(
            f"refusing to run the DB suite against {name!r} — the test DB name must start with "
            f"{_TEST_DB_PREFIX!r} (this guard exists because an un-guarded run TRUNCATED the demo on "
            "2026-07-21). Set ALPHADECK_TEST_DB to an alphadeck_test* name, or unset it to auto-derive."
        )
    return name


def _with_dbname(url: str, name: str) -> str:
    """Swap ONLY the URL's db-name (the path segment); keep scheme/host/port/creds/query intact."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def test_db_url() -> str:
    """The full per-worktree test DB URL: host/port/creds from ``DATABASE_URL`` (or the default), db-name
    ALWAYS the resolved + guarded test name. A stale ``DATABASE_URL=…/alphadeck`` is neutralized here (its
    db-name is replaced), and the retired ``DATABASE_URL=…/alphadeck_test`` habit becomes a harmless no-op.
    """
    return _with_dbname(
        os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL), resolve_test_db_name()
    )


def ensure_test_db(url: str) -> None:
    """CREATE the per-worktree test DB if absent. Idempotent + race-tolerant.

    ``CREATE DATABASE`` cannot run in a transaction, so this connects autocommit to the ``postgres``
    maintenance DB (same creds; the postgres image always exposes it). A concurrent worker that wins the
    race raises ``DuplicateDatabase`` here, which is swallowed. A Postgres-down ``OperationalError`` is NOT
    caught here — the caller (``pytest_configure``) catches it so the existing ``_migrated`` skip still fires.
    """
    maint = _with_dbname(url, "postgres")
    target = urlsplit(url).path.lstrip("/")
    with psycopg.connect(maint, autocommit=True) as conn:
        try:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
        except psycopg.errors.DuplicateDatabase:
            pass  # already present (or another worker won the race) — idempotent
