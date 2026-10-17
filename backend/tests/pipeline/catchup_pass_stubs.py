"""The DB-free daily-pass harness the catch-up BASELINE tests share (``test_catchup_baseline.py`` and the
Admin agreement test in ``tests/app/test_admin_catchup_baseline.py``).

One pass = the REAL ``daily.run_daily_pass``: the REAL newly-stale and newly-failing diffs, the REAL run
artifact writer (``cron_run_log.write_run_start`` + ``write_cron_run_log``), the REAL baseline readers and
the REAL health page through a recording notifier. Only the seams that need a DB or the network are
stubbed (the refresh / radar / sweep legs, the split scan, the rename probe, the successor scan, the
self-audit, the lock), and ``run_daily`` returns the pass's ROSTER as the real one builds it: one
``ThesisRunResult`` per thesis — an ingested thesis with its names, its stale feeds and its per-leg errors,
or a thesis a ``--catch-up`` SKIPPED (M2: ``skipped`` set, nothing ingested). Those are the contract
inputs; nothing about the baseline is stubbed.
"""

from __future__ import annotations

import time as _time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from pipeline import daily
from pipeline.ingest_thesis import NameResult
from pipeline.rename_probe import RenameProbe
from pipeline.split_basis import SplitScan
from pipeline.successor_cik import SuccessorScan
from pipeline.tape_health import StaleTape

# The reason `run_daily` records on a thesis a catch-up skipped (its shape, `daily.run_daily`'s M2 branch).
ON_TIME = "on-time call-of-record already recorded 2026-10-15 22:41 EDT"

# How old a stopped tape's last bar is: well past the default 5-day threshold.
_STOPPED_DAYS = 40


@dataclass
class Pager:
    """The notifier: keeps every health PAGE the pass emits (what the operator is told)."""

    health: list = field(default_factory=list)

    def notify(self, event):  # transitions — none here (run_daily is the roster stub)
        pass

    def notify_health(self, event):
        self.health.append(event)

    def notify_spac_status(self, event):  # the radar leg is stubbed
        pass


class _Lock:
    def release(self):
        pass


def prime_pass(monkeypatch) -> Pager:
    """Stub the DB / network seams of ``run_daily_pass`` (see the module docstring) and pin the two monitor
    thresholds, so the stale-tape monitor is EVALUATED (5 days, the shipped default) whatever the host's
    env says. The caller points ``cron_run_log._DEFAULT_CRON_RUNS`` at its run home. Returns the pager.
    """
    monkeypatch.setattr(daily, "acquire_pass_lock", lambda **kw: _Lock())
    monkeypatch.setattr(
        daily,
        "connect",
        lambda: SimpleNamespace(
            close=lambda: None, execute=lambda *a, **k: None, commit=lambda: None, autocommit=False
        ),
    )
    monkeypatch.setattr(daily, "run_audit", lambda ctx, *a, **k: [])
    monkeypatch.setattr(daily, "run_split_scan", lambda **k: SplitScan())
    monkeypatch.setattr(daily, "run_rename_probe", lambda **k: RenameProbe(evaluated=True))
    monkeypatch.setattr(daily, "run_successor_scan", lambda **k: SuccessorScan(evaluated=True))
    monkeypatch.setattr("pipeline.ingest_benchmarks.ingest_benchmarks", lambda conn, **k: [])
    monkeypatch.setattr("pipeline.ingest_fundamentals.ingest_fundamentals", lambda conn, **k: [])
    monkeypatch.setattr(
        "radar.spac.run_spac_radar", lambda conn, **k: SimpleNamespace(summary="stub", errors=[])
    )
    monkeypatch.setattr(
        "radar.shell_sweep.run_shell_sweep",
        lambda conn, **k: SimpleNamespace(summary="stub", errors=[]),
    )
    pinned = daily.get_settings().model_copy(
        update={"tape_stale_days": 5, "fund_shares_stale_days": 5, "image_sha": "test-sha"}
    )
    monkeypatch.setattr(daily, "get_settings", lambda: pinned)
    return Pager()


def next_second() -> None:
    """An artifact is named by its started-at SECOND (the start write and the finalize land on one file);
    two passes never start in the same second in production (the run lock, a fresh interpreter per pass),
    but in-process they can — so each pass waits for the clock to tick (< 1 s)."""
    s = datetime.now(timezone.utc).replace(microsecond=0)
    while datetime.now(timezone.utc).replace(microsecond=0) == s:
        _time.sleep(0.01)


def run_pass(
    monkeypatch,
    pager: Pager,
    roster: Sequence[daily.ThesisRunResult],
    *,
    asof: date,
    catch_up: bool = False,
) -> tuple[daily.DailyPassOutcome, list]:
    """One REAL pass over ``roster`` -> ``(outcome, the health pages THIS pass emitted)``."""
    next_second()
    monkeypatch.setattr(daily, "run_daily", lambda conn, **k: list(roster))
    before = len(pager.health)
    outcome = daily.run_daily_pass(
        asof=asof, allow_live=True, notifier=pager, catch_up=catch_up, run_kind="cron"
    )
    return outcome, pager.health[before:]


@dataclass(frozen=True)
class Name:
    """A basket member: the ticker the page shows and the security id every diff keys on."""

    ticker: str
    security_id: UUID


def covered(
    thesis_id: UUID,
    *,
    asof: date,
    healthy: Name,
    stale: Sequence[Name] = (),
    failing: Sequence[Name] = (),
    never_priced: Sequence[Name] = (),
) -> daily.ThesisRunResult:
    """A thesis the pass INGESTED, as ``run_daily`` returns it: every member's ``NameResult`` (a healthy
    member always — a thesis whose EVERY name failed would be withheld, which is a different story), the
    stopped price tapes the recency judgment found (``stale``: a last bar 40 days back; ``never_priced``:
    no stored bar at all — the unpriceable class), and the names whose PRICE leg raised (``failing``: a
    vendor 404 — the per-leg error O1 keys on)."""
    stopped = asof - timedelta(days=_STOPPED_DAYS)
    names: dict[UUID, NameResult] = {
        healthy.security_id: NameResult(
            ticker=healthy.ticker,
            security_id=healthy.security_id,
            form4_appended=0,
            price_bars_appended=1,
            tape_edge=asof,
        )
    }
    edges = {n.security_id: stopped for n in stale} | {n.security_id: None for n in never_priced}
    for n in (*stale, *never_priced, *failing):
        msg = f"HTTP 404 Not Found for {n.ticker}" if n in failing else None
        names[n.security_id] = NameResult(
            ticker=n.ticker,
            security_id=n.security_id,
            form4_appended=0,
            price_bars_appended=0,
            error=f"price: {msg}" if msg else None,
            leg_errors=(("price", msg),) if msg else (),
            tape_edge=edges.get(n.security_id, asof),
        )
    tapes = tuple(
        StaleTape(ticker=n.ticker, security_id=n.security_id, edge=edges[n.security_id])
        for n in (*stale, *never_priced)
    )
    return daily.ThesisRunResult(
        thesis_id=thesis_id,
        name=f"thesis {str(thesis_id)[:8]}",
        ingested=list(names.values()),
        recorded=False,
        tape_stale=tapes,
    )


def skipped(thesis_id: UUID) -> daily.ThesisRunResult:
    """A thesis a ``--catch-up`` SKIPPED (M2 — its on-time row stood): no ingest, no call, nothing judged
    — exactly ``run_daily``'s skip branch."""
    return daily.ThesisRunResult(
        thesis_id=thesis_id, name=f"thesis {str(thesis_id)[:8]}", skipped=ON_TIME
    )


def paged_tapes(pages: list) -> set[str]:
    """The newly-stale labels the pass PAGED (``HealthEvent.tape_stale_new``)."""
    return {s.label for e in pages for s in e.tape_stale_new}


def paged_name_errors(pages: list) -> set[str]:
    """The newly-failing labels the pass PAGED (``HealthEvent.name_errors_new``)."""
    return {label for e in pages for label in e.name_errors_new}
