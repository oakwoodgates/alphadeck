"""Block until Postgres accepts a connection: ``python -m db.wait``.

The image's boot command runs this before ``db.migrate``. After a Docker VM restart every container
starts in the same second (restart policies ignore ``depends_on``) and Postgres needs ~30 s before it
accepts connections, so a single ``connect()`` in migrate would exit 1 and ride Docker's growing
restart backoff. Only ``psycopg.OperationalError`` is retried (refused, "starting up", host not
resolvable yet); anything else is a real fault and propagates at once.

Exit 0 = ready, 1 = still not ready at the bound (deliberately NOT fail-open: migrate would fail
anyway, and the restart policy stays the backstop). The bound is ``ALPHADECK_DB_WAIT_S`` (whole
seconds; unset, empty, non-numeric or < 1 means 300).
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping

import psycopg

from db.session import database_url

DEFAULT_TIMEOUT_S = 300


def default_probe() -> None:
    """One connection attempt against the same DATABASE_URL every CLI uses."""
    psycopg.connect(database_url(), connect_timeout=5).close()


def _one_line(e: BaseException) -> str:
    return type(e).__name__ + ": " + " ".join(str(e).split())


def wait_for_db(
    *,
    timeout_s: float,
    poll_s: float = 1.0,
    probe: Callable[[], None] = default_probe,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> bool:
    """True once ``probe`` passes within ``timeout_s`` (monotonic clock), else False."""
    start = monotonic()
    try:
        probe()
        return True
    except psycopg.OperationalError as e:
        err = _one_line(e)
    out(f"db-wait: database not ready ({err}); waiting up to {timeout_s:g}s")
    while True:
        remaining = start + timeout_s - monotonic()
        if remaining <= 0:
            out(f"db-wait: database still not ready after {monotonic() - start:.0f}s ({err})")
            return False
        sleep(min(poll_s, remaining))
        try:
            probe()
        except psycopg.OperationalError as e:
            err = _one_line(e)
            continue
        out(f"db-wait: database ready after {monotonic() - start:.0f}s")
        return True


def timeout_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get("ALPHADECK_DB_WAIT_S", "").strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return n if n >= 1 else DEFAULT_TIMEOUT_S


def main() -> int:
    return 0 if wait_for_db(timeout_s=timeout_from_env()) else 1


if __name__ == "__main__":
    sys.exit(main())
