"""The COMPANY-LIST REFRESH — SEC's current ticker file onto the per-tenant security master. DRY-RUN by default.

The broadener that lifts the loop from "the seeded basket" to "any name you just thought of", and since
2026-10-24 the job that keeps that list CURRENT: one polite GET of SEC's ``company_tickers_exchange.json``
(~10k rows, per-instrument exchange), planned against THIS tenant's master and — with ``--apply`` — written in
ONE transaction. The rules (one record per company; a rename updates the row IN PLACE so every fact stays
attached; ticker reuse is a conflict, never applied; the price feed follows a rename only when safe) are
``securities/master_refresh.py``'s.

    # DRY RUN (the default): print the plan — new companies, renames (old -> new), new classes, conflicts,
    # primary-flag changes, and the price pins / auto-adopt candidates that would follow. Writes NOTHING.
    python -m pipeline.populate_master            # against the CACHED copy of SEC's file
    python -m pipeline.populate_master --live     # against SEC's CURRENT file (needs ALPHADECK_USER_AGENT)
    # APPLY: write the plan in one transaction, cache the file it was planned from, refresh the fund list
    # (company_tickers_mf.json — one more GET, fail-open) and write a run artifact under data/master_runs/
    python -m pipeline.populate_master --live --apply
    # a non-default tenant (the production cut): pass an explicit id, else the deployment tenant is used
    python -m pipeline.populate_master --live --apply --tenant-id <uuid>

THE WEEKLY JOB. The cron sidecar fires ``--live --apply --run-kind cron`` every Saturday at 09:00 market time
(``master_refresh_loop`` in ``scripts/daily_cron.sh``), plus once at boot with ``--if-stale-days 7`` when the
newest applied run is more than a week old (a missed Saturday). An ``--apply`` run REFUSES (exit 75, one line,
nothing written) inside the nightly window (``Settings.onboard_window_*`` around RUN_AT), while another refresh
holds its lock (advisory id 5) or while a pass holds the call-of-record lock (a probe of ``pg_locks`` — it never
takes that lock, so it can never make a pass exit 75). Fail-open in the loop: any failure is one log line.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from db.session import connect, current_tenant_id
from domain.market_time import market_tz
from domain.settings import get_settings
from ingest import CacheMiss
from pipeline.onboarding import window_bounds
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    LOCK_ID_MASTER_REFRESH,
    JobAlreadyRunning,
    acquire_job_lock,
)
from pipeline.schedule import parse_run_at
from securities import fund_tickers, master_refresh, sec_tickers

PASS_KIND = "master_refresh"
MODE_APPLY = "apply"
# Runtime artifacts beside the nightly's (the repo's gitignored data/ == the container's /data); tests redirect.
DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "master_runs"

_STATUS_FINISHED = "finished"
_STATUS_FAILED = "failed"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def latest_master_run(*, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """The newest parseable APPLIED run artifact, or ``None``. Fail-open per file; no DB, no network."""
    d = runs_dir or DEFAULT_RUNS_DIR
    if not d.exists():
        return None
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed read
            continue
        if isinstance(doc, dict) and doc.get("kind") == PASS_KIND and doc.get("mode") == MODE_APPLY:
            return doc
    return None


def _write_artifact(doc: dict[str, Any], runs_dir: Path) -> Path | None:
    """Write one run artifact (``<started>.json``); fail-open — a write fault costs the record, never the run."""
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.fromisoformat(doc["started_at"])
        path = runs_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:  # noqa: BLE001
        print(f"company-list: could not write the run artifact ({type(e).__name__}: {e})")
        return None


def _market_date(t: datetime):
    return t.astimezone(market_tz()).date()


def run(
    *,
    live: bool,
    apply: bool,
    tenant_id: UUID,
    run_kind: str = "manual",
    if_stale_days: float | None = None,
    runs_dir: Path | None = None,
    cache_dir: Path | None = None,
    fund_cache_dir: Path | None = None,
    now: Callable[[], datetime] = _now_utc,
    fetch: Callable[[], dict] | None = None,
    fund_refresh: Callable[[], int] | None = None,
) -> int:
    """ONE refresh (see the module docstring); returns the process exit code — 0 done (or a quiet skip), 1
    failed, 75 refused (the window, another refresh, a running pass). ``fetch`` / ``fund_refresh`` default to the
    live SEC reads (``sec_tickers.fetch_raw`` / ``fund_tickers.refresh``); tests inject fakes."""
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    started = now()
    if if_stale_days is not None:
        last = latest_master_run(runs_dir=runs_dir)
        if last is None:
            print(
                "company-list: no applied refresh on record — the first one is the Saturday schedule's "
                "(or a hand run); nothing to catch up"
            )
            return 0
        try:
            ran = datetime.fromisoformat(str(last.get("finished_at") or last["started_at"]))
        except (KeyError, ValueError):
            ran = None
        if ran is not None and started - ran < timedelta(days=if_stale_days):
            print(
                f"company-list: refreshed {ran:%Y-%m-%d} (under {if_stale_days:g} days ago) — no catch-up"
            )
            return 0

    lock = None
    if apply:
        s = get_settings()
        w = window_bounds(
            started.astimezone(market_tz()),
            run_at=parse_run_at(s.cron_run_at),
            before_min=s.onboard_window_before_min,
            after_min=s.onboard_window_after_min,
        )
        if w is not None:
            print(
                f"company-list: REFUSED — inside the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M}); "
                f"nothing written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
        try:
            lock = acquire_job_lock(
                lock_id=LOCK_ID_MASTER_REFRESH, holder="master-refresh", what="company-list refresh"
            )
        except JobAlreadyRunning as e:
            print(f"company-list: REFUSED — {e}; nothing written (exit {EXIT_ALREADY_RUNNING})")
            return EXIT_ALREADY_RUNNING
        holder = lock.call_of_record_holder()
        if holder is not None:
            lock.release()
            print(
                f"company-list: REFUSED — a pass holds the call-of-record lock ({holder}); the refresh "
                f"yields to it, nothing written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
    try:
        return _refresh(
            live=live,
            apply=apply,
            tenant_id=tenant_id,
            run_kind=run_kind,
            started=started,
            runs_dir=runs_dir,
            cache_dir=cache_dir,
            fund_cache_dir=fund_cache_dir,
            now=now,
            fetch=fetch,
            fund_refresh=fund_refresh,
        )
    finally:
        if lock is not None:
            lock.release()


def _refresh(
    *,
    live: bool,
    apply: bool,
    tenant_id: UUID,
    run_kind: str,
    started: datetime,
    runs_dir: Path,
    cache_dir: Path | None,
    fund_cache_dir: Path | None,
    now: Callable[[], datetime],
    fetch: Callable[[], dict] | None,
    fund_refresh: Callable[[], int] | None,
) -> int:
    doc: dict[str, Any] = {
        "kind": PASS_KIND,
        "mode": MODE_APPLY,
        "run_kind": run_kind,
        "tenant_id": str(tenant_id),
        "started_at": started.isoformat(),
    }

    def failed(msg: str) -> int:
        print(f"COMPANY LIST REFRESH FAILED — {msg}")
        if apply:
            when = _market_date(started)
            doc.update(
                status=_STATUS_FAILED,
                finished_at=now().isoformat(),
                error=msg,
                attention=True,
                admin_line=f"Company list · refresh FAILED {when:%b} {when.day} — {msg}",
            )
            _write_artifact(doc, runs_dir)
        return 1

    # 1. SEC's file: the CURRENT one (--live), else the cached copy
    try:
        if live:
            raw = (fetch or sec_tickers.fetch_raw)()
            file_at = now()
        else:
            raw = sec_tickers.read_cached_raw(cache_dir)
            file_at = sec_tickers.cached_at(cache_dir)
        rows = sec_tickers.quadruples(raw)
    except CacheMiss:
        return failed("no cached copy of SEC's company_tickers_exchange.json — run with --live")
    except Exception as e:  # noqa: BLE001 — a fetch / parse fault is the run's failure, one line
        return failed(f"reading SEC's company file: {type(e).__name__}: {e}")
    doc["source"] = {
        "live": live,
        "file_at": file_at.isoformat() if file_at else None,
        "rows": len(rows),
    }
    print(
        f"company-list: SEC's company_tickers_exchange.json — {'fetched now' if live else 'the cached copy'}"
        + (f" ({file_at:%Y-%m-%d %H:%M} UTC)" if file_at else "")
        + f", {len(rows):,} rows; {'APPLY' if apply else 'DRY RUN'} for tenant {tenant_id}"
    )

    # 2. the plan (read-only), then — with --apply — the write, in ONE transaction
    conn = connect()
    try:
        if not apply:
            conn.read_only = True  # structurally a look: the dry run cannot write a row
        try:
            plan = master_refresh.plan_refresh(conn, rows, tenant_id=tenant_id)
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            return failed(f"planning: {type(e).__name__}: {e}")
        for line in plan.report_lines():
            print(line)
        if not apply:
            conn.rollback()
            print(
                "company-list: DRY RUN — nothing written (re-run with --apply to write this plan)"
            )
            return 0
        try:
            written = master_refresh.apply_plan(conn, plan)
            conn.commit()
        except Exception as e:  # noqa: BLE001 — rolled back whole: nothing written
            conn.rollback()
            return failed(f"applying (rolled back, nothing written): {type(e).__name__}: {e}")
    finally:
        conn.close()

    # 3. cache the file the master was refreshed FROM (the cached map the nightly's rename guard reads), then
    #    the fund list — each fail-open: the master write above already landed
    if live:
        try:
            sec_tickers.store_raw(raw, cache_dir)
        except Exception as e:  # noqa: BLE001
            print(
                f"company-list: could not cache SEC's file ({type(e).__name__}: {e}) (continuing)"
            )
        try:
            n = (fund_refresh or (lambda: fund_tickers.refresh(cache_dir=fund_cache_dir)))()
            doc["funds"] = {"refreshed": True, "rows": n}
        except Exception as e:  # noqa: BLE001
            doc["funds"] = {"refreshed": False, "error": f"{type(e).__name__}: {e}"}
            print(f"company-list: the fund list was NOT refreshed ({type(e).__name__}: {e})")
    when = _market_date(started)
    line = plan.admin_line(when)
    doc.update(
        status=_STATUS_FINISHED,
        finished_at=now().isoformat(),
        counts=plan.counts(),
        written=written,
        plan=plan.to_json(),
        admin_line=line,
        attention=bool(plan.conflicts),
    )
    path = _write_artifact(doc, runs_dir)
    if plan.conflicts:
        print(
            f"COMPANY LIST — {len(plan.conflicts)} CONFLICT(S) NOT APPLIED (another company holds the "
            "ticker); listed above and on the Admin page"
        )
    print(f"company-list: {line}" + (f" · {path}" if path else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Refresh the security master from SEC's company list (dry-run by default)."
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="fetch SEC's CURRENT company_tickers_exchange.json (needs ALPHADECK_USER_AGENT); else the "
        "cached copy",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="write the plan (one transaction) + a run artifact; without it the run is a dry run",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="target tenant UUID; defaults to the deployment tenant ($ALPHADECK_TENANT_ID / demo)",
    )
    p.add_argument(
        "--run-kind",
        choices=("manual", "cron"),
        default="manual",
        help="stamped on the artifact; the cron sidecar passes cron",
    )
    p.add_argument(
        "--if-stale-days",
        type=float,
        default=None,
        help="run only when an applied refresh exists and is older than this many days (the boot catch-up)",
    )
    a = p.parse_args(argv)
    tenant_id = UUID(a.tenant_id) if a.tenant_id else current_tenant_id()
    return run(
        live=a.live,
        apply=a.apply,
        tenant_id=tenant_id,
        run_kind=a.run_kind,
        if_stale_days=a.if_stale_days,
    )


if __name__ == "__main__":
    sys.exit(main())
