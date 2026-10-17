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
``Settings.backup_keep`` (default 7) UNLABELED snapshots and delete older UNLABELED ones. A LABELED (named)
dump — a deliberate recovery point like ``pre-shares-backfill`` — is deleted only when it is BOTH older than
``Settings.backup_labeled_max_age_days`` (90) AND outside the newest ``Settings.backup_labeled_keep`` (20)
labeled dumps (Q16, 2026-10-17: "90 days, or the last 20", whichever keeps more). Before Q16 labeled dumps
were kept forever, and MEASURED on prod that was 65 dumps holding 19.8 GB. A FAILED dump never prunes (it
must never shrink the safety net), and the atomic ``tmp -> os.replace`` means a crashed dump never appears in
the list. Every removed file is logged by name. ``--prune-dry-run`` prints exactly what a prune would remove
(``plan_prune``, the same pure plan the real prune applies) and removes nothing.

The AGE GATE (M3, 2026-10-16): ``--if-older-than-h H`` dumps only when the newest UNLABELED snapshot is at
least H hours old (or there is none), and otherwise prints one no-op line and exits 0. The cron sidecar
calls it on boot, after its catch-up. MEASURED: the nightly dump rode only the in-loop wake, so a night the
sidecar caught up at BOOT got no dump, and around 10-12 there was none for about 4 days. The gate lets the
boot dump without a crash-looping container dumping on every restart (at H=20, about one a day). The
in-loop nightly stays ungated on purpose: it must capture the run that just finished, which no earlier
dump holds (``scripts/daily_cron.sh``, M3). Retention is untouched: a gated dump IS a ``run_backup`` and
prunes the same way, and a skip prunes nothing.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    ``labeled`` marks a named dump (a deliberate recovery point, under the Q16 age-and-rank rule rather
    than the rolling keep-N); ``created_at`` is parsed from the filename timestamp.
    """

    name: str
    bytes: int
    created_at: datetime
    labeled: bool


@dataclass(frozen=True)
class PruneCandidate:
    """One snapshot a prune WOULD remove (``plan_prune``): the dry-run's row and the real prune's input, so
    the two can never disagree. ``reason`` is the rule that selected it, in words."""

    name: str
    bytes: int
    created_at: datetime
    labeled: bool
    reason: str


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
    dump_runner: Callable[..., None] | None = None,
) -> BackupResult:
    """Create ONE snapshot: dump to ``<name>.tmp``, then atomically ``os.replace`` it to ``<name>`` (so a
    crashed dump never lists), then prune — ONLY after the dump succeeds. ``dump_runner`` is the
    testability hinge (the ``daily_job._DEFAULT_EXECUTOR`` / ``PriceSource`` seam); when ``None`` it falls
    back to the module-level ``_pg_dump`` resolved AT CALL TIME (so a test that monkeypatches
    ``pipeline.backup._pg_dump`` takes effect, and no unit test needs the real binary). A raising runner
    propagates BEFORE the replace + prune — no ``.sql`` is published and the keep-window is untouched.
    """
    now = now or datetime.now(timezone.utc)  # ONE timestamp: names the file AND is created_at
    settings = get_settings()
    runner = dump_runner or _pg_dump  # resolved here, so a monkeypatched _pg_dump is honored
    slug = _slugify(label) if label else ""
    name = f"{_PREFIX}{now.strftime(_TS)}" + (f"-{slug}" if slug else "") + _SUFFIX
    d = base_dir or _DEFAULT_BACKUPS
    d.mkdir(parents=True, exist_ok=True)
    dest = d / name
    tmp = d / f"{name}.tmp"

    runner(database_url or _database_url(), tmp, timeout_s=settings.backup_timeout_s)
    os.replace(tmp, dest)  # atomic publish — the *.sql glob only ever sees a COMPLETE dump
    size = dest.stat().st_size

    pruned = _prune(  # only after success
        d,
        keep if keep is not None else settings.backup_keep,
        labeled_keep=settings.backup_labeled_keep,
        labeled_max_age_days=settings.backup_labeled_max_age_days,
        now=now,
    )
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


def fresh_snapshot(
    max_age_h: float, *, base_dir: Path | None = None, now: datetime | None = None
) -> BackupInfo | None:
    """The M3 age gate, PURE over the dir listing: the newest UNLABELED snapshot when it is younger than
    ``max_age_h`` hours (a dump is NOT due), else ``None`` (due: no unlabeled snapshot exists, or the newest
    one is at least that old). Age is read from the filename timestamp, the same ``created_at`` the list and
    the Admin "last snapshot" age show. A LABELED dump never counts: it is a deliberate recovery point outside
    the rolling window, not the nightly. A FUTURE-dated newest snapshot (the clock went backwards) counts as
    not fresh, so the gate fails toward dumping, never toward a silent skip.
    """
    now = now or datetime.now(timezone.utc)
    for info in list_backups(base_dir=base_dir):  # newest-first
        if info.labeled:
            continue
        age = now - info.created_at
        return info if timedelta(0) <= age < timedelta(hours=max_age_h) else None
    return None


def plan_prune(
    base_dir: Path,
    *,
    keep: int,
    labeled_keep: int = 0,
    labeled_max_age_days: float = 0.0,
    now: datetime | None = None,
) -> list[PruneCandidate]:
    """What a prune WOULD remove, OLDEST-first — PURE over the dir listing (no delete, no DB, no network).

    Two rules, one per kind of dump:

    - **UNLABELED** (the rolling nightlies): keep the newest ``keep``; every older one is selected.
      ``keep >= count`` selects nothing.
    - **LABELED** (Q16): selected only when it is BOTH older than ``labeled_max_age_days`` (by its filename
      timestamp against ``now``) AND outside the newest ``labeled_keep`` labeled dumps — "90 days, or the
      last 20", whichever keeps more. Either dial ``<= 0`` turns the labeled rule OFF (nothing labeled is
      ever selected, the pre-Q16 keep-forever), so a bad setting can only keep MORE. A future-dated dump
      (the clock went backwards) has a negative age and is never "older than" anything — kept.

    A name we cannot parse is never selected (a foreign file is never auto-deleted), and a file we cannot
    stat is skipped like ``list_backups`` skips it."""
    now = now or datetime.now(timezone.utc)
    infos = list_backups(
        base_dir=base_dir
    )  # newest-first, unparseable / unstat-able already skipped
    unlabeled = [i for i in infos if not i.labeled]
    labeled = [i for i in infos if i.labeled]
    out: list[PruneCandidate] = [
        PruneCandidate(
            name=i.name,
            bytes=i.bytes,
            created_at=i.created_at,
            labeled=False,
            reason=f"unlabeled, older than the newest {keep} unlabeled",
        )
        for i in unlabeled[keep:]
    ]
    if labeled_keep > 0 and labeled_max_age_days > 0:
        max_age = timedelta(days=labeled_max_age_days)
        for i in labeled[labeled_keep:]:
            age = now - i.created_at
            if age > max_age:
                out.append(
                    PruneCandidate(
                        name=i.name,
                        bytes=i.bytes,
                        created_at=i.created_at,
                        labeled=True,
                        reason=(
                            f"labeled, {age.days} days old (> {labeled_max_age_days:g}) and not among "
                            f"the newest {labeled_keep} labeled"
                        ),
                    )
                )
    out.sort(key=lambda c: c.name)  # oldest-first: the order a reader expects a deletion list in
    return out


def _prune(
    base_dir: Path,
    keep: int,
    *,
    labeled_keep: int = 0,
    labeled_max_age_days: float = 0.0,
    now: datetime | None = None,
) -> list[str]:
    """Apply ``plan_prune`` — delete exactly what it selected — and return the deleted names. The labeled
    dials default OFF here, so a direct caller that passes only ``keep`` keeps the pre-Q16 behavior (labeled
    dumps untouched); ``run_backup`` passes the settings. Each deletion is logged BY NAME with the rule that
    selected it — visible pruning, never silent loss — at WARNING, because nothing in the repo configures
    the root logger (no ``basicConfig`` / ``dictConfig``), so an INFO line from this logger was dropped in
    both the CLI and the API process: a deleted recovery point must reach the cron's log and the backend's,
    whichever path triggered it. A file that cannot be deleted is logged and skipped, never a failed
    backup."""
    deleted: list[str] = []
    for c in plan_prune(
        base_dir,
        keep=keep,
        labeled_keep=labeled_keep,
        labeled_max_age_days=labeled_max_age_days,
        now=now,
    ):
        try:
            (base_dir / c.name).unlink()
            deleted.append(c.name)
            _log.warning("backup prune: removed %s (%d bytes; %s)", c.name, c.bytes, c.reason)
        except OSError:  # a prune that can't delete is logged, never a failed backup
            _log.exception("backup prune: failed to remove %s", c.name)
    return deleted


def prune_dry_run_lines(
    *,
    base_dir: Path | None = None,
    now: datetime | None = None,
) -> list[str]:
    """The ``--prune-dry-run`` report, as lines (PURE apart from the dir listing; removes NOTHING): every
    file the NEXT prune would remove under the configured rules, with its size and the rule that selected
    it, then the bytes it would free. The real prune runs after a successful dump, and that dump adds one
    UNLABELED snapshot — so when the unlabeled window is already full, the report also names the one extra
    unlabeled file that dump would push out (an unchanged, pre-Q16 behavior, listed so the count is exact).
    """
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    d = base_dir or _DEFAULT_BACKUPS
    plan = plan_prune(
        d,
        keep=s.backup_keep,
        labeled_keep=s.backup_labeled_keep,
        labeled_max_age_days=s.backup_labeled_max_age_days,
        now=now,
    )
    infos = list_backups(base_dir=d)
    labeled_rule = (
        f"labeled: removed only when > {s.backup_labeled_max_age_days:g} days old AND not among the "
        f"newest {s.backup_labeled_keep} labeled"
        if s.backup_labeled_keep > 0 and s.backup_labeled_max_age_days > 0
        else "labeled: never removed (labeled pruning is OFF)"
    )
    lines = [
        f"backup prune DRY RUN — nothing is removed. dir={d} now={now.isoformat(timespec='seconds')}",
        f"rules: unlabeled: keep the newest {s.backup_keep} · {labeled_rule}",
        f"snapshots now: {len(infos)} ({sum(1 for i in infos if i.labeled)} labeled, "
        f"{sum(1 for i in infos if not i.labeled)} unlabeled), {sum(i.bytes for i in infos)} bytes",
    ]
    if plan:
        lines.append(f"a prune now would remove {len(plan)} file(s):")
        lines += [f"  {c.name}  {c.bytes} bytes  ({c.reason})" for c in plan]
    else:
        lines.append("a prune now would remove nothing")
    freed = sum(c.bytes for c in plan)
    lines.append(f"bytes freed: {freed} ({freed / 1e9:.2f} GB)")
    # The prune runs right after a dump, and that dump shifts one window by one. Name the extra file it
    # would push out, so the listing is exact for whichever kind of dump triggers the first prune.
    unlabeled = [i for i in infos if not i.labeled]
    if s.backup_keep > 0 and len(unlabeled) >= s.backup_keep:
        rolls = unlabeled[s.backup_keep - 1]
        lines.append(
            f"note: if an UNLABELED dump triggers the prune (the nightly), it also removes {rolls.name} "
            f"({rolls.bytes} bytes), the oldest unlabeled one inside the keep-{s.backup_keep} window "
            "(the rolling window, unchanged by Q16)"
        )
    labeled = [i for i in infos if i.labeled]
    if s.backup_labeled_keep > 0 and s.backup_labeled_max_age_days > 0:
        if len(labeled) >= s.backup_labeled_keep:
            edge = labeled[s.backup_labeled_keep - 1]
            if now - edge.created_at > timedelta(days=s.backup_labeled_max_age_days):
                lines.append(
                    f"note: if a LABELED dump triggers the prune, it also removes {edge.name} "
                    f"({edge.bytes} bytes), which then falls outside the newest {s.backup_labeled_keep}"
                )
    return lines


def main(argv: list[str] | None = None) -> None:
    """``python -m pipeline.backup [--label X | --if-older-than-h H | --prune-dry-run] [--dir D]`` — the
    nightly + ad-hoc snapshot CLI (the cron sidecar invokes it fail-open: age-gated on boot, ungated after
    each weekday wake). ``--prune-dry-run`` dumps nothing and removes nothing: it prints what the next prune
    would delete (Q16). Restore stays a deliberate CLI act, documented in the module docstring.
    """
    p = argparse.ArgumentParser(
        description="Create a pg_dump snapshot of the database (the operator DB-snapshot / nightly backup)."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--label",
        default=None,
        help="name a snapshot (e.g. pre-migration), kept 90 days or while among the newest 20 labeled "
        "(Q16) — omit for a rolling nightly dump",
    )
    mode.add_argument(
        "--prune-dry-run",
        action="store_true",
        help="dump NOTHING and remove NOTHING: list exactly what the next prune would delete (each file, "
        "its size and the rule that selected it) and the bytes it would free",
    )
    mode.add_argument(
        "--if-older-than-h",
        type=float,
        default=None,
        metavar="H",
        help="M3 age gate: dump ONLY if the newest UNLABELED snapshot is at least H hours old (or there "
        "is none); otherwise print a no-op line and exit 0 — the cron sidecar's boot call",
    )
    p.add_argument(
        "--dir",
        default=None,
        metavar="D",
        help="the snapshot directory (default: the repo's data/backups, /data/backups in the container) — "
        "e.g. to dry-run the prune against the host's copy of a bind-mounted dir",
    )
    args = p.parse_args(argv)
    base_dir = Path(args.dir) if args.dir else None
    if args.prune_dry_run:
        for line in prune_dry_run_lines(base_dir=base_dir):
            print(line)
        return
    if args.if_older_than_h is not None:
        now = datetime.now(timezone.utc)
        h = args.if_older_than_h
        fresh = fresh_snapshot(h, base_dir=base_dir, now=now)
        if fresh is not None:
            age_h = (now - fresh.created_at).total_seconds() / 3600
            print(
                f"backup: skipped — the newest unlabeled snapshot {fresh.name} is {age_h:.1f} h old "
                f"(< {h:g} h)"
            )
            return
        print(f"backup: due — no unlabeled snapshot younger than {h:g} h")
    result = run_backup(label=args.label, base_dir=base_dir)
    tail = f" · pruned {len(result.pruned)}" if result.pruned else ""
    print(f"backup: wrote {result.name} ({result.bytes} bytes){tail}")


if __name__ == "__main__":
    main()
