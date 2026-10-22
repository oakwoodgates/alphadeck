from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class CacheMiss(Exception):
    """A requested resource isn't cached and live pulls are disabled.

    The ingestion etiquette guard: the test transport never hits the network — a cache miss with
    live pulls off raises this instead.
    """


def write_atomic(path: Path, text: str) -> None:
    """Write a cache file so a reader never sees it half-written: the text goes to a sibling temp file which
    then REPLACES the target in one rename (atomic on the Linux container's filesystem). A concurrent reader
    gets the old document or the new one, never a torn mix — the fetch-ahead's worker threads write the
    caches while the writer thread reads them (``pipeline/fetch_ahead.py``), and the cron and the backend
    share the ``/data`` caches across processes. The EDGAR client and the Yahoo price loader both write
    through it.

    Windows (the suite's host) refuses a rename over a file another handle has open; that is retried briefly
    and, if it persists, the write falls back to the plain overwrite these caches always used (never lost).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.01 * (attempt + 1))
    try:
        path.write_text(text, encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)
