"""The operator DB-snapshot runner (Slice 4) — CREATE a ``pg_dump`` snapshot, LIST existing ones, and
RETAIN a rolling window. The one-click safety net the 2026-07-21 truncation proved we needed (recovery
only worked because an ad-hoc ``pg_dump`` happened to exist).

Structural bound (the ``cron_run_log.py`` discipline): this module opens NO psycopg connection and
issues NO app SQL — ``pg_dump`` (a read-only subprocess) is the ONLY DB touch, and it mutates no row.
It imports nothing from ``calls/`` / ``repositories/`` / ``pipeline.daily`` or any spine-write path;
``db.session.database_url`` is a pure ``os.environ.get`` (a config-string read, not a connection).

RESTORE is CLI-only — deliberately NOT automated here and NEVER a button. A restore is destructive
(drop-schema + reload) and belongs in human hands. The exact sequence (the one used on 2026-07-21)::

    docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck < ./data/backups/<file>

The snapshots live under the repo's gitignored ``data/`` locally (``/data/backups`` in the container,
a WRITABLE host bind on both the backend and cron services), so a dump is copyable off-box and
restorable with the command above without a ``docker cp`` first.

Retention (VISIBLE pruning, never silent loss): after a SUCCESSFUL dump, keep the newest
``Settings.backup_keep`` (default 7) UNLABELED snapshots and delete older UNLABELED ones; a LABELED
(named) dump is EXEMPT — a deliberate recovery point like ``pre-shares-backfill`` is never auto-deleted.
A FAILED dump never prunes (it must never shrink the safety net), and the atomic ``tmp -> os.replace``
means a crashed dump never appears in the list. Every prune is logged.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db.session import database_url as _database_url
from domain.settings import get_settings

# Snapshots live under the repo's gitignored data/ (== the container's volume-mounted /data/backups);
# tests pass an explicit base_dir.
_DEFAULT_BACKUPS = Path(__file__).resolve().parents[2] / "data" / "backups"

_PREFIX = "alphadeck-"
_SUFFIX = ".sql"
# UTC, fixed 15-char width (%Y%m%d + 'T' + %H%M%S) — sortable + deterministic; it names the file AND is
# the created_at. A reverse filename sort is therefore a reverse-time sort (the cron_run_log idiom).
_TS = "%Y%m%dT%H%M%S"

_log = logging.getLogger("alphadeck.backup")


@dataclass
class BackupInfo:
    """One snapshot as a value-free file listing (the route maps it to the ``BackupOut`` wire model).
    ``labeled`` marks a named, prune-EXEMPT dump; ``created_at`` is parsed from the filename timestamp.
    """

    name: str
    bytes: int
    created_at: datetime
    labeled: bool


@dataclass
class BackupResult(BackupInfo):
    """A completed ``run_backup``: the listing fields plus the on-disk ``path`` and the names ``pruned``
    by retention (empty when nothing was over the keep-window)."""

    path: Path
    pruned: list[str]


def _slugify(label: str) -> str:
    """Lower-case, collapse any run of non-alphanumerics to a single ``-``, strip, truncate ~40 chars —
    a filesystem-safe label segment. An empty / punctuation-only label yields ``""`` (an unlabeled dump).
    """
    s = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return s[:40].strip("-")


def _parse_backup_name(name: str) -> tuple[datetime, bool] | None:
    """Parse ``alphadeck-<15ts>[-<label>].sql`` -> (created_at UTC, labeled). ``None`` for any name that
    is not a backup we wrote (bad prefix/suffix or an unparseable timestamp) — so a stray file in the dir
    is skipped fail-open by the readers and NEVER auto-pruned."""
    if not name.startswith(_PREFIX) or not name.endswith(_SUFFIX):
        return None
    core = name[len(_PREFIX) : -len(_SUFFIX)]  # <15ts>[-<label>]
    try:
        created_at = datetime.strptime(core[:15], _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    rest = core[15:]
    labeled = rest.startswith("-") and len(rest) > 1
    return created_at, labeled


def _pg_dump(url: str, dest: Path, *, timeout_s: float) -> None:
    """The DB touch — a READ-ONLY ``pg_dump`` subprocess (``--no-owner`` + plain-SQL default, the shape
    that restored cleanly on 2026-07-17). The ONLY seam that shells the binary, so a test injects a fake
    ``dump_runner`` and never needs it. A missing binary / non-zero exit is a LOUD, operator-facing raise
    (the job maps it to a ``failed`` job); the wall ``timeout_s`` turns a hang into a loud failure too.
    """
    try:
        subprocess.run(
            ["pg_dump", "--no-owner", "--dbname", url, "--file", str(dest)],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "pg_dump not found — the image needs postgresql-client-16 (see backend/Dockerfile)"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"pg_dump failed (exit {exc.returncode}): {(exc.stderr or '').strip()}"
        ) from exc


def run_backup(
    *,
    label: str | None = None,
    database_url: str | None = None,
    base_dir: Path | None = None,
    keep: int | None = None,
    now: datetime | None = None,
    dump_runner: Callable[..., None] = _pg_dump,
) -> BackupResult:
    """Create ONE snapshot: dump to ``<name>.tmp``, then atomically ``os.replace`` it to ``<name>`` (so a
    crashed dump never lists), then prune — ONLY after the dump succeeds. ``dump_runner`` is the
    testability hinge (the ``daily_job._DEFAULT_EXECUTOR`` / ``PriceSource`` seam): CI injects a fake that
    writes a dummy ``.sql`` (or raises), so no test needs the real binary. A raising ``dump_runner``
    propagates BEFORE the replace + prune — no ``.sql`` is published and the keep-window is untouched.
    """
    now = now or datetime.now(timezone.utc)  # ONE timestamp: names the file AND is created_at
    settings = get_settings()
    slug = _slugify(label) if label else ""
    name = f"{_PREFIX}{now.strftime(_TS)}" + (f"-{slug}" if slug else "") + _SUFFIX
    d = base_dir or _DEFAULT_BACKUPS
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    tmp = d / f"{name}.tmp"

    dump_runner(database_url or _database_url(), tmp, timeout_s=settings.backup_timeout_s)
    os.replace(tmp, dest)  # atomic publish — the *.sql glob only ever sees a COMPLETE dump
    size = dest.stat().st_size

    pruned = _prune(d, keep if keep is not None else settings.backup_keep)  # only after success
    _log.info(
        "backup created: %s (%d bytes)%s",
        name,
        size,
        f", pruned {len(pruned)}: {', '.join(pruned)}" if pruned else "",
    )
    return BackupResult(
        name=name,
        bytes=size,
        created_at=now,
        labeled=bool(slug),
        path=dest,
        pruned=pruned,
    )


def list_backups(*, base_dir: Path | None = None) -> list[BackupInfo]:
    """Every snapshot in the dir, NEWEST-FIRST (reverse filename sort == time sort). A pure file read: no
    DB, no network, writes nothing. Skip-unreadable fail-open (the run-log read discipline) — a stray
    file, an unparseable name, or a stat failure is skipped, never a failed listing; a missing dir is [].
    """
    d = base_dir or _DEFAULT_BACKUPS
    if not d.exists():
        return []
    out: list[BackupInfo] = []
    for p in sorted(d.glob(f"*{_SUFFIX}"), key=lambda x: x.name, reverse=True):
        parsed = _parse_backup_name(p.name)
        if parsed is None:
            continue
        created_at, labeled = parsed
        try:
            size = p.stat().st_size
        except OSError:  # a vanished/unreadable file is skipped, never a failed read
            continue
        out.append(BackupInfo(name=p.name, bytes=size, created_at=created_at, labeled=labeled))
    return out


def _prune(base_dir: Path, keep: int) -> list[str]:
    """Keep the newest ``keep`` UNLABELED snapshots; delete the older unlabeled ones; return the deleted
    names. PURE over the dir. LABELED dumps are EXEMPT (a deliberate recovery point is never auto-deleted),
    and an unparseable name is left untouched (never delete what we can't identify). ``keep >= count`` ->
    nothing deleted. Each deletion is logged — visible pruning, never silent loss."""
    unlabeled: list[Path] = []
    for p in base_dir.glob(f"*{_SUFFIX}"):
        parsed = _parse_backup_name(p.name)
        if parsed is None:
            continue  # a foreign / unparseable file is never auto-deleted
        if not parsed[1]:  # labeled == False
            unlabeled.append(p)
    unlabeled.sort(key=lambda x: x.name, reverse=True)  # newest-first
    deleted: list[str] = []
    for p in unlabeled[keep:]:
        try:
            p.unlink()
            deleted.append(p.name)
            _log.info("backup prune: removed %s (keep=%d unlabeled)", p.name, keep)
        except OSError:  # a prune that can't delete is logged, never a failed backup
            _log.exception("backup prune: failed to remove %s", p.name)
    return deleted


def main(argv: list[str] | None = None) -> None:
    """``python -m pipeline.backup [--label X]`` — the nightly + ad-hoc snapshot CLI (the cron sidecar
    invokes it fail-open). Restore stays a deliberate CLI act, documented in the module docstring.
    """
    p = argparse.ArgumentParser(
        description="Create a pg_dump snapshot of the database (the operator DB-snapshot / nightly backup)."
    )
    p.add_argument(
        "--label",
        default=None,
        help="name a prune-EXEMPT snapshot (e.g. pre-migration) — omit for a rolling nightly dump",
    )
    args = p.parse_args(argv)
    result = run_backup(label=args.label)
    tail = f" · pruned {len(result.pruned)}" if result.pruned else ""
    print(f"backup: wrote {result.name} ({result.bytes} bytes){tail}")


if __name__ == "__main__":
    main()
