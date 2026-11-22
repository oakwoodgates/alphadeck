"""THE NIGHTLY PASS'S PLATFORM-EXTRACTION LEG (V2 W3c): keep the shared share counts and cash/burn current, LAST
in the nightly pass (approved B2 (a)), on a budget against the pass's own time limit.

``docs/PLATFORM_EXTRACTION.md`` ("The nightly step"). ``pipeline.daily._run_daily_pass_locked`` calls
``run_nightly_extraction`` after EVERYTHING else in the pass: every thesis's call of record is written and committed
(``run_daily``), the run artifact is finished, the health page is sent and the passenger legs (shell sweep, SPAC
radar, fund radar) have run. Its summary is then added to the finished artifact as the ``platform_extraction``
block (``cron_run_log.amend_run_block``), which the Admin page shows as one line.

**The call of record cannot move.** Nothing here runs before the last thesis is recorded; nothing it writes
(platform values, candidates, attempts, the artifact block) is read by the call path (the source check in
``tests/pipeline/test_daily_platform_extraction.py``); it never raises into the pass (an unexpected fault is a
``status: error`` block, never a failed pass or a different exit status).

**The budget** (``leg_budget``): the smaller of ``Settings.platform_extraction_budget_s`` and what remains of the
pass's limit (``Settings.cron_run_timeout_s``, the sidecar's ``RUN_TIMEOUT_S``) after the time the pass has spent,
minus ``MARGIN_S``. Zero or less: ``no time left`` (the due companies are counted and wait for the next night).
Inside the run, ``platform_values.extraction.run_extraction`` checks between companies and never starts one the
slowest so far says it cannot finish; the rest is ``deferred`` and stays due. The margin also covers the first
company, which has no estimate yet.

**The client** is the other nightly legs': a RECURRING ``EdgarClient`` (``RECURRING_CACHE_TTL_S``), so its
requests take the recurring lane of the ONE global SEC budget, and the filing gate and negative cache apply. It is
built only when a company is due.

**By hand** (``python -m pipeline.nightly_extraction``): the same leg outside a pass, for the operator (the fork
has no cron and no SEC User-Agent). A dry run by default (the work list and the budget; reads only); ``--apply``
runs it; ``--no-live`` makes the client cache-only (stale copies served, a missing document is an ``error``
attempt, ``not cached``, and stays due). It refuses inside the nightly window and while a pass holds the
call-of-record lock, and stops between companies if either begins; it writes its summary to
``data/extraction_runs/`` (the Admin line reads the newer of that and the nightly block).
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg

from domain.market_time import market_now, market_tz
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from pipeline.onboarding import pass_holder, window_bounds
from pipeline.schedule import parse_run_at
from platform_values.extraction import ExtractionRun, WorkList, run_extraction, work_list

BLOCK_KEY = "platform_extraction"
RUN_KIND = "nightly"
# Seconds kept back from the pass's limit: the process start before ``started_at``, the first company (no
# estimate yet), closing the connection, and the timings amend after the leg. Plan Q5: 600 s.
MARGIN_S = 600.0
STATUS_NO_LIVE = "skipped: no-live"
STATUS_NO_TIME = "no time left"
EXIT_REFUSED = 75  # the hand run refused (the nightly window, or a pass running): the daily CLI's "already running"


def _now() -> datetime:
    """The wall clock the elapsed-time clip reads (a seam: the budget test fakes it)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LegBudget:
    """The step's time budget, with its arithmetic kept for the artifact."""

    setting_s: float
    pass_timeout_s: float
    elapsed_s: float
    margin_s: float = MARGIN_S

    @property
    def remaining_s(self) -> float:
        """What remains of the pass's limit after the time spent and the margin (may be negative)."""
        return self.pass_timeout_s - self.elapsed_s - self.margin_s

    @property
    def budget_s(self) -> float:
        return max(0.0, min(self.setting_s, self.remaining_s))

    def to_json(self) -> dict[str, float]:
        return {
            "setting_s": round(self.setting_s, 3),
            "pass_timeout_s": round(self.pass_timeout_s, 3),
            "elapsed_s": round(self.elapsed_s, 3),
            "margin_s": round(self.margin_s, 3),
            "remaining_s": round(self.remaining_s, 3),
            "budget_s": round(self.budget_s, 3),
        }


def leg_budget(
    *, setting_s: float, pass_timeout_s: float, elapsed_s: float, margin_s: float = MARGIN_S
) -> LegBudget:
    """PURE: ``min(setting_s, pass_timeout_s - elapsed_s - margin_s)``, never below zero."""
    return LegBudget(setting_s, pass_timeout_s, max(0.0, elapsed_s), margin_s)


def recurring_client(live: bool) -> EdgarClient:
    """The nightly legs' client: the recurring dial (the recurring lane of the global SEC budget), live or
    cache-only. Its User-Agent comes from the settings, as for every nightly leg."""
    return EdgarClient(allow_live=live, cache_ttl_s=RECURRING_CACHE_TTL_S)


def _head(at: datetime) -> str:
    local = at.astimezone(market_tz())
    return f"Shared facts · {local.strftime('%b')} {local.day}"


def _block(
    run: ExtractionRun,
    *,
    budget: LegBudget | None,
    wl: WorkList | None,
    line: str | None = None,
    live_completed: int | None = None,
) -> dict[str, Any]:
    """``run.to_json()`` plus what the nightly adds: the budget arithmetic, the companies the gate left out (held
    back until a backfill extracts a company (B4), held for a too-new filing, waiting after repeated failures) and
    the client's completed requests. ``attention`` (loud on Admin) = a failure tonight, the run itself failing, a
    company waiting after repeated failures, or a held-back backlog (the backfill is owed)."""
    doc = run.to_json()
    withheld = wl.withheld_never if wl is not None else 0
    waiting = wl.waiting if wl is not None else 0
    doc["budget"] = budget.to_json() if budget is not None else None
    doc["withheld_never"] = withheld
    doc["held"] = wl.held if wl is not None else 0
    doc["waiting"] = waiting
    doc["live_completed"] = live_completed
    text = line if line is not None else run.admin_line()
    extra = []
    if waiting:
        extra.append(f"{waiting} waiting after repeated failures")
    if withheld:
        extra.append(f"{withheld} never extracted, held until a backfill extracts a company")
    doc["admin_line"] = " · ".join([text, *extra])
    doc["attention"] = bool(run.attention or waiting or withheld)
    return doc


def skipped_no_live_block(started_at: datetime | None = None) -> dict[str, Any]:
    """A ``--no-live`` pass: the leg does not run (a cache-only pass records no call either)."""
    at = started_at or _now()
    run = ExtractionRun(run_kind=RUN_KIND, started_at=at, max_names=None, budget_s=None)
    run.status, run.finished_at = STATUS_NO_LIVE, at
    return _block(
        run,
        budget=None,
        wl=None,
        line=f"{_head(at)}: skipped (a --no-live pass fetches nothing)",
    )


def error_block(exc: BaseException, started_at: datetime | None = None) -> dict[str, Any]:
    """The leg failed before or outside the run loop (``run_extraction`` itself never raises)."""
    at = started_at or _now()
    run = ExtractionRun(run_kind=RUN_KIND, started_at=at, max_names=None, budget_s=None)
    run.status, run.finished_at = "error", _now()
    run.error = f"the step failed ({type(exc).__name__})"
    return _block(run, budget=None, wl=None)


def run_nightly_extraction(
    *,
    pass_started_at: datetime,
    live: bool,
    connect: Callable[[], psycopg.Connection],
    now: Callable[[], datetime] | None = None,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run the step once and return its block (``run.to_json()`` plus the nightly fields). NEVER raises.

    In order: the kill switch (``platform_extraction_max_names = 0``: ``off``, nothing read or written); the budget
    (``leg_budget`` from ``pass_started_at``); the work list (``work_list``, B4: never-extracted companies held back
    until a backfill has extracted a company, and counted); ``no time left`` when the budget is zero (the due count
    kept); otherwise ``run_extraction`` on a recurring client (built only when a company is due) under the budget
    and the cap.
    """
    clock_now = now or _now
    started = clock_now()
    try:
        s = get_settings()
        if s.platform_extraction_max_names == 0:
            run = run_extraction(connect, [], client=None, run_kind=RUN_KIND, clock=clock)
            return _block(run, budget=None, wl=None)
        budget = leg_budget(
            setting_s=s.platform_extraction_budget_s,
            pass_timeout_s=s.cron_run_timeout_s,
            elapsed_s=(started - pass_started_at).total_seconds(),
        )
        conn = connect()
        try:
            wl = work_list(conn, now=started)
        finally:
            conn.close()
        if budget.budget_s <= 0:
            run = ExtractionRun(
                run_kind=RUN_KIND,
                started_at=started,
                max_names=s.platform_extraction_max_names,
                budget_s=0.0,
                status=STATUS_NO_TIME,
                due_total=len(wl.items),
                due_by_reason=dict(Counter(i.reason for i in wl.items)),
                deferred=len(wl.items),
                finished_at=clock_now(),
            )
            line = (
                f"{_head(started)}: no time left in the pass · {len(wl.items)} due, "
                "taken next night"
            )
            return _block(run, budget=budget, wl=wl, line=line)
        client = recurring_client(live) if wl.items else None
        run = run_extraction(
            connect,
            wl.items,
            client=client,
            run_kind=RUN_KIND,
            should_stop=should_stop,
            budget_s=budget.budget_s,
            clock=clock,
        )
        completed = getattr(client, "live_completed", None)
        return _block(
            run,
            budget=budget,
            wl=wl,
            live_completed=completed if isinstance(completed, int) else None,
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 — the step is a passenger: its fault is its block, never the pass's
        return error_block(exc, started)


# --- by hand -------------------------------------------------------------------------------------------------------


def _outside_the_window() -> str | None:
    """Why a hand run may not start now (inside the nightly window), or None."""
    s = get_settings()
    bounds = window_bounds(
        market_now(),
        run_at=parse_run_at(s.cron_run_at),
        before_min=s.onboard_window_before_min,
        after_min=s.onboard_window_after_min,
    )
    if bounds is None:
        return None
    start, end = bounds
    return f"inside the nightly window ({start:%H:%M}-{end:%H:%M}); run it after {end:%H:%M}"


def _refusal(conn: psycopg.Connection) -> str | None:
    why = _outside_the_window()
    if why is not None:
        return why
    holder = pass_holder(conn)
    return f"a nightly pass is running ({holder})" if holder else None


def main(argv: list[str] | None = None) -> None:
    from db.session import connect
    from pipeline.extraction_run_log import write_extraction_run

    p = argparse.ArgumentParser(
        description="The nightly pass's platform-extraction step, by hand (dry run by default)."
    )
    p.add_argument("--apply", action="store_true", help="run it (default: print the work list)")
    p.add_argument(
        "--no-live", action="store_true", help="cache-only: no SEC request (the fork's rehearsal)"
    )
    args = p.parse_args(argv)
    s = get_settings()
    started = _now()
    conn = connect()
    try:
        wl = work_list(conn, now=started)
        refusal = _refusal(conn) if args.apply else None
    finally:
        conn.close()
    budget = leg_budget(
        setting_s=s.platform_extraction_budget_s,
        pass_timeout_s=s.cron_run_timeout_s,
        elapsed_s=0.0,
    )
    reasons = ", ".join(f"{k} {v}" for k, v in sorted(Counter(i.reason for i in wl.items).items()))
    print(
        f"platform extraction: {len(wl.items)} due ({reasons or 'none'}) · "
        f"{wl.withheld_never} never extracted, held until a backfill extracts a company · {wl.held} held · "
        f"{wl.waiting} waiting · cap {s.platform_extraction_max_names} · budget {budget.budget_s:.0f}s"
    )
    if not args.apply:
        print("dry run: nothing fetched or written (add --apply to run; --no-live for cache-only)")
        return
    if refusal is not None:
        print(f"platform extraction: REFUSED, {refusal}. Nothing fetched or written.")
        raise SystemExit(EXIT_REFUSED)

    def should_stop() -> bool:
        if _outside_the_window() is not None:
            return True
        c = connect()
        try:
            return pass_holder(c) is not None
        finally:
            c.close()

    block = run_nightly_extraction(
        pass_started_at=started, live=not args.no_live, connect=connect, should_stop=should_stop
    )
    write_extraction_run(block)
    print(block["admin_line"])


__all__ = [
    "BLOCK_KEY",
    "EXIT_REFUSED",
    "MARGIN_S",
    "STATUS_NO_LIVE",
    "STATUS_NO_TIME",
    "LegBudget",
    "error_block",
    "leg_budget",
    "main",
    "recurring_client",
    "run_nightly_extraction",
    "skipped_no_live_block",
]


if __name__ == "__main__":
    main()
