"""X1 — THE STAGED COLD-INGEST JOB: onboarding many new companies never runs inside the nightly window.

THE PROBLEM (MEASURED). A COLD name — one whose facts were never loaded — costs 300-600 s on its first ingest
(MEASURED 2026-10-21: median 396 s, n=10; prod's X5 average was ~110 s): the whole Form 4 history, one document
fetch per filing; every 13D/G filer identity; the 4/As. A warm one costs ~3.5 s. The 2026-09-01 pass that onboarded many names took 21,632 s; the bounded pass now stops at
5,400 s, so a big onboarding inside the 22:30 pass would time the night out, and more than ~360 new names in one
pass crosses the market open. Until X1 a cold name got its history from the Workbench's on-promote job (#308) or,
failing that, from the nightly itself.

THE SHAPE — four parts, one state table (``security_onboarding``, migration 0059):

1. **The queue.** A Workbench promote that adds COLD names (no row here yet, and no other live basket holds them —
   the nightly walks every live basket member every night, so such a name is already loaded) ENQUEUES them
   (``enqueue_cold``). One row per (tenant, security): onboarding is about a company's facts, shared by every
   thesis that holds it. An enqueue is ``INSERT ... ON CONFLICT DO NOTHING``: a re-enqueue never duplicates, and
   never resets a ``done`` row. A small promote still takes the FAST LANE (``plan_promote``): at most
   ``Settings.onboard_fast_lane_max`` cold names ingest at once in the background exactly as before X1, and the
   rows are marked from that run's results (``mark_from_results``) — so the job only ever resumes what the fast
   lane could not finish.

2. **The job** (``run_batch``; ``python -m pipeline.onboarding``; the cron sidecar fires it every
   ``ONBOARD_EVERY_S``). It walks the not-done rows whose security sits in a live basket (a name the operator
   has since removed is PARKED — never loaded for nothing, the cost thread), oldest first, a resumed
   ``in_progress`` one before any ``queued`` one. Per name, under that company's W1 write lock
   (``db/write_lock.py``; a held company is skipped this run, never waited for), it runs the legs NOT yet in
   ``legs_done`` — the per-name CHECKPOINT, written after each leg commits, so a crash mid-batch resumes at the
   next leg and the incremental, append-if-changed legs never append a fact twice (the tests COUNT THE TABLES).
   The HISTORY legs (Form 4 incl. 4/A, 8-K, 13D/G) decide ``done``; the TAIL legs (price, fund shares,
   fundamentals) run for completeness and never block it (they cost the same cold or warm, and the nightly runs
   them every night). A history-leg failure re-queues the name; ``Settings.onboard_max_attempts`` failures mark it
   ``failed`` (loud on Admin; Retry re-queues it).

3. **The window.** The job REFUSES to start inside the nightly window (``Settings.onboard_window_before_min``
   before RUN_AT to ``onboard_window_after_min`` after it, market time — 21:30-23:30 ET by default) and STOPS
   between names and between legs when it enters it, when a pass holds the call-of-record lock (it reads
   ``pg_locks``, never takes the lock — a probe that took it could make the nightly exit 75), when its batch
   budget (names / wall time) or the daily cap is spent. A yield is not a failure: the name stays
   ``in_progress`` with its checkpoint, and the next run resumes it. LOCKS: an onboarding-job lock (one batch at
   a time) then, per name, gate(shared)+company — and it never waits for the call-of-record lock, so the order
   call-of-record -> gate -> company holds and nothing can deadlock against the nightly.

4. **The nightly's treatment of a not-yet-warm name** (``pending_status`` -> ``pipeline.daily``): it is walked
   like any other, but its per-filing legs read only the filings filed within
   ``Settings.onboard_nightly_window_days`` of the as-of (``domain.onboarding.OnboardingWindow``) and its 4/A leg
   waits for the full load — about a warm name's cost, never the 300-600 s history walk; its thesis's row is stamped
   ``ingest_fresh=false`` and the name is listed as onboarding on the artifact and the report — visible, never
   dropped (#9).

THE SEC BUDGET. Every EDGAR read the job makes goes through ONE client built by ``_edgar_client``: the
recurring dial, which also places it in the global SEC budget's ``recurring`` lane (X2) — a bulk background
consumer, so the Workbench's interactive lane keeps its fair share against it. The job walks its names SERIALLY
(one leg at a time, its own legs — not ``ingest_thesis``, so #432's fetch-ahead and its
``ingest_fetch_concurrency`` workers do not apply): a background load has no deadline to buy down.

THE RUN RECORD. A run that did work writes ``data/onboard_runs/<started>.json`` (``pass_kind: "onboard"``,
``run_kind`` cron/manual, ``status`` running -> finished / crashed, why it stopped, one row per name, the X5
timings with a scope per name) — a SEPARATE directory from the nightly's ``cron_runs``, so no nightly reader
(the catch-up guard, the baselines, the Admin run history) can ever mistake it for a pass. A refusal (the window,
a running pass, another batch) or an idle run (nothing queued) writes nothing: one printed line.

    python -m pipeline.onboarding                        # one batch now (refuses inside the nightly window)
    python -m pipeline.onboarding --status               # the queue, read-only
    python -m pipeline.onboarding --enqueue-thesis <id>  # queue a thesis's cold members (a seed / CLI path)
    python -m pipeline.onboarding --retry-failed         # re-queue every failed name
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from db.session import connect, name_session, set_statement_timeout
from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.market_time import market_now, market_tz
from domain.security import Security
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.funds.ingest_security import ingest_fund_shares_for_security
from ingest.prices.ingest_security import ingest_bars_for_security
from ingest.prices.source import PriceSource, YahooPriceSource
from pipeline import ingest_thesis as IT
from pipeline import pass_timings
from pipeline.ingest_fundamentals import ingest_fundamentals_for_securities
from pipeline.pass_timings import timed
from pipeline.run_lock import LOCK_NAMESPACE, describe_holder
from pipeline.schedule import parse_run_at
from securities import master

_log = logging.getLogger("alphadeck.onboarding")

PASS_KIND = "onboard"

# The legs, in run order. HISTORY legs decide "warm" (the per-filing walks a cold name pays for); TAIL legs cost
# the same cold or warm and run for completeness only (the nightly runs them every night anyway).
HISTORY_LEGS: tuple[str, ...] = ("form4", "form8k", "schedule13")
TAIL_LEGS: tuple[str, ...] = ("price", "fund_shares", "fundamentals")
LEGS: tuple[str, ...] = HISTORY_LEGS + TAIL_LEGS

STATUS_QUEUED = "queued"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
# not warm yet: the nightly gives these the windowed treatment
PENDING_STATUSES: tuple[str, ...] = (STATUS_QUEUED, STATUS_IN_PROGRESS, STATUS_FAILED)

# The onboarding job's own advisory lock — the run-lock namespace ("ADK"): id 1 is the call-of-record lock, id 2
# the fact-writer gate, id 3 this. Held for a batch on the job's working connection (session-level, so a killed
# job loses it with its session). It only keeps two batches from running at once; it is never waited for.
ONBOARD_LOCK_ID = 3

# Runtime artifacts beside the nightly's (the repo's gitignored data/ == the container's /data); tests redirect.
_DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "onboard_runs"

_STATUS_RUNNING = "running"
_STATUS_FINISHED = "finished"
_STATUS_CRASHED = "crashed"


# --- the state table ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OnboardingRow:
    """One ``security_onboarding`` row (plus the master's ticker / name, for display)."""

    tenant_id: UUID
    security_id: UUID
    status: str
    legs_done: tuple[str, ...]
    attempts: int
    last_error: str | None
    source: str
    thesis_id: UUID | None
    enqueued_at: datetime
    last_attempt_at: datetime | None
    finished_at: datetime | None
    ticker: str | None = None
    name: str | None = None
    in_live_basket: bool = True

    @property
    def history_done(self) -> bool:
        return all(leg in self.legs_done for leg in HISTORY_LEGS)


_ROW_SQL = """SELECT so.tenant_id, so.security_id, so.status, so.legs_done, so.attempts, so.last_error,
                     so.source, so.thesis_id, so.enqueued_at, so.last_attempt_at, so.finished_at,
                     sm.ticker, sm.name,
                     EXISTS (SELECT 1 FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id
                              WHERE bm.security_id = so.security_id AND bm.tenant_id = so.tenant_id
                                AND t.archived_at IS NULL) AS in_live_basket
                FROM security_onboarding so
                LEFT JOIN security_master sm ON sm.id = so.security_id"""


def _row(r: Mapping[str, Any]) -> OnboardingRow:
    return OnboardingRow(
        tenant_id=r["tenant_id"],
        security_id=r["security_id"],
        status=r["status"],
        legs_done=tuple(r["legs_done"] or ()),
        attempts=int(r["attempts"] or 0),
        last_error=r["last_error"],
        source=r["source"],
        thesis_id=r["thesis_id"],
        enqueued_at=r["enqueued_at"],
        last_attempt_at=r["last_attempt_at"],
        finished_at=r["finished_at"],
        ticker=r.get("ticker"),
        name=r.get("name"),
        in_live_basket=bool(r.get("in_live_basket", True)),
    )


def enqueue(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID,
    source: str,
    thesis_id: UUID | None = None,
) -> set[UUID]:
    """Queue ``security_ids`` for onboarding; returns the ids NEWLY queued. IDEMPOTENT: one row per (tenant,
    security) — an id that already has a row (queued, in progress, done or failed) is left exactly as it is.
    The caller commits."""
    ids = sorted({s for s in security_ids if s is not None}, key=str)
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_onboarding (tenant_id, security_id, status, source, thesis_id)
               SELECT %s, s, 'queued', %s, %s FROM unnest(%s::uuid[]) AS s
               ON CONFLICT (tenant_id, security_id) DO NOTHING
               RETURNING security_id""",
            (tenant_id, source, thesis_id, ids),
        )
        return {r["security_id"] for r in cur.fetchall()}


def cold_ids(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID,
    thesis_id: UUID | None,
) -> set[UUID]:
    """The ids among ``security_ids`` whose history is NOT loaded — what a promote must onboard:

    - a PENDING row (queued / in progress / failed) -> cold (and already queued);
    - a ``done`` row -> warm;
    - NO row: warm when another LIVE thesis's basket holds it (the nightly walks every live basket member every
      night, so its history is loaded — the legacy names, and a name re-arranged between theses), else cold.

    ``thesis_id`` is the promoting thesis, excluded from "another" (its new members are in its basket already
    by the time the promote asks). Read-only."""
    ids = list({s for s in security_ids if s is not None})
    if not ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s AS security_id,
                      (SELECT so.status FROM security_onboarding so
                        WHERE so.tenant_id = %(t)s AND so.security_id = s) AS status,
                      EXISTS (SELECT 1 FROM basket_member bm JOIN thesis th ON th.id = bm.thesis_id
                               WHERE bm.tenant_id = %(t)s AND bm.security_id = s
                                 AND th.archived_at IS NULL
                                 AND (%(thesis)s::uuid IS NULL OR th.id <> %(thesis)s::uuid)) AS elsewhere
                 FROM unnest(%(ids)s::uuid[]) AS s""",
            {"t": tenant_id, "thesis": thesis_id, "ids": ids},
        )
        rows = cur.fetchall()
    out: set[UUID] = set()
    for r in rows:
        status = r["status"]
        if status in PENDING_STATUSES or (status is None and not r["elsewhere"]):
            out.add(r["security_id"])
    return out


def enqueue_cold(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID,
    source: str,
    thesis_id: UUID | None = None,
) -> set[UUID]:
    """``cold_ids`` + ``enqueue``: queue the cold ones and return EVERY cold id (the newly queued and the ones
    already pending) — the set the promote's lane decision counts. The caller commits."""
    cold = cold_ids(conn, security_ids, tenant_id=tenant_id, thesis_id=thesis_id)
    enqueue(conn, cold, tenant_id=tenant_id, source=source, thesis_id=thesis_id)
    return cold


def pending_status(conn: psycopg.Connection) -> dict[tuple[UUID, UUID], str]:
    """``(tenant_id, security_id) -> status`` for every row that is NOT warm (queued / in progress / failed) —
    the nightly reads this ONCE per pass. Read-only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, security_id, status FROM security_onboarding WHERE status = ANY(%s)",
            (list(PENDING_STATUSES),),
        )
        return {(r["tenant_id"], r["security_id"]): r["status"] for r in cur.fetchall()}


def rows(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | None = None,
    security_ids: Collection[UUID] | None = None,
) -> list[OnboardingRow]:
    """Every row (optionally one tenant's, optionally only ``security_ids``), oldest enqueue first. Read-only."""
    where, params = [], []
    if tenant_id is not None:
        where.append("so.tenant_id = %s")
        params.append(tenant_id)
    if security_ids is not None:
        where.append("so.security_id = ANY(%s::uuid[])")
        params.append(list(security_ids))
    sql = _ROW_SQL + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY so.enqueued_at, so.security_id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row(r) for r in cur.fetchall()]


def work_list(conn: psycopg.Connection) -> list[OnboardingRow]:
    """The job's work list: the queued / in-progress rows whose security a LIVE basket holds — a resumed
    ``in_progress`` row first, then the oldest enqueue. A row no live basket holds is PARKED (left queued, never
    loaded for nothing — the cost thread); re-adding the name resumes it. Read-only."""
    return [
        r
        for r in sorted(
            (
                r
                for r in rows(conn)
                if r.status in (STATUS_QUEUED, STATUS_IN_PROGRESS) and r.in_live_basket
            ),
            key=lambda r: (r.status != STATUS_IN_PROGRESS, r.enqueued_at, str(r.security_id)),
        )
    ]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def mark_started(conn: psycopg.Connection, row: OnboardingRow) -> None:
    """The name is being worked on: ``in_progress`` + ``last_attempt_at`` (the daily cap counts it). Commits."""
    conn.execute(
        """UPDATE security_onboarding
              SET status = 'in_progress', last_attempt_at = now(), updated_at = now()
            WHERE tenant_id = %s AND security_id = %s""",
        (row.tenant_id, row.security_id),
    )
    conn.commit()


def mark_leg_done(conn: psycopg.Connection, row: OnboardingRow, leg: str) -> None:
    """THE CHECKPOINT: ``leg`` completed (its writes already committed). Idempotent (never listed twice).
    Commits."""
    conn.execute(
        """UPDATE security_onboarding
              SET legs_done = CASE WHEN %s = ANY(legs_done) THEN legs_done
                                   ELSE array_append(legs_done, %s) END,
                  updated_at = now()
            WHERE tenant_id = %s AND security_id = %s""",
        (leg, leg, row.tenant_id, row.security_id),
    )
    conn.commit()


def finish(
    conn: psycopg.Connection,
    row: OnboardingRow,
    *,
    history_failed: Mapping[str, str],
    tail_failed: Mapping[str, str],
    max_attempts: int,
) -> str:
    """Close one name's run (every leg was attempted): ``done`` when no history leg failed (a tail-leg failure
    rides ``last_error`` as a note — the nightly's own monitors cover those feeds); else one more attempt
    counted and the name back to ``queued``, or ``failed`` once ``max_attempts`` runs failed it. Returns the
    new status. Commits."""
    if not history_failed:
        note = (
            "done; tail leg(s) failed (the nightly re-runs them): "
            + "; ".join(f"{leg}: {err}" for leg, err in tail_failed.items())
            if tail_failed
            else None
        )
        conn.execute(
            """UPDATE security_onboarding
                  SET status = 'done', finished_at = now(), last_error = %s, updated_at = now()
                WHERE tenant_id = %s AND security_id = %s""",
            (_clip(note), row.tenant_id, row.security_id),
        )
        conn.commit()
        return STATUS_DONE
    attempts = row.attempts + 1
    status = STATUS_FAILED if attempts >= max(1, max_attempts) else STATUS_QUEUED
    err = "; ".join(f"{leg}: {e}" for leg, e in {**history_failed, **tail_failed}.items())
    conn.execute(
        """UPDATE security_onboarding
              SET status = %s, attempts = %s, last_error = %s,
                  finished_at = CASE WHEN %s = 'failed' THEN now() ELSE finished_at END,
                  updated_at = now()
            WHERE tenant_id = %s AND security_id = %s""",
        (status, attempts, _clip(err), status, row.tenant_id, row.security_id),
    )
    conn.commit()
    return status


def retry(conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID) -> bool:
    """The operator's Retry: a ``failed`` name back to ``queued`` with its attempts reset (its checkpoint kept —
    the legs it completed are not re-run). Returns whether a row changed. The caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE security_onboarding
                  SET status = 'queued', attempts = 0, finished_at = NULL, updated_at = now()
                WHERE tenant_id = %s AND security_id = %s AND status = 'failed'""",
            (tenant_id, security_id),
        )
        return cur.rowcount > 0


def attempted_since(conn: psycopg.Connection, since: datetime) -> int:
    """How many names a runner started working on at/after ``since`` — the daily cap's count. Read-only."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM security_onboarding WHERE last_attempt_at >= %s", (since,)
        )
        return int(cur.fetchone()["n"])


def _clip(text: str | None, n: int = 2000) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def mark_from_results(
    conn: psycopg.Connection,
    results: Iterable[Any],
    *,
    tenant_id: UUID,
    security_ids: Collection[UUID],
) -> dict[UUID, str]:
    """THE FAST LANE's bookkeeping: after ``ingest_thesis`` walked a promote's new names in FULL (no window), mark
    each PENDING row among ``security_ids`` from its ``NameResult``: the history legs that did not fail are
    checkpointed; a name with no history-leg failure is ``done``. A name another writer held (``lock_busy``) or
    whose history leg failed is left PENDING — the staged job resumes it (a fast-lane failure is never counted
    as an attempt: the job's own run decides that). Returns ``security_id -> status`` for the rows it touched.
    Commits."""
    wanted = set(security_ids)
    by_id: dict[UUID, list[Any]] = {}
    for r in results:
        if r.security_id in wanted:
            by_id.setdefault(r.security_id, []).append(r)
    if not by_id:
        return {}
    current = {
        row.security_id: row
        for row in rows(conn, tenant_id=tenant_id, security_ids=list(by_id))
        if row.status != STATUS_DONE
    }
    out: dict[UUID, str] = {}
    for sid, row in current.items():
        results_for = by_id[sid]
        if any(getattr(r, "lock_busy", None) for r in results_for):
            continue
        failed = {leg for r in results_for for leg, _ in getattr(r, "leg_errors", ())}
        if "master" in failed:
            continue
        # the fast lane is `ingest_thesis`: Form 4 (+4/A), 8-K, 13D/G, price, fund shares — no fundamentals
        ok = [
            leg
            for leg in ("form4", "form8k", "schedule13", "price", "fund_shares")
            if leg not in failed
        ]
        for leg in ok:
            mark_leg_done(conn, row, leg)
        if all(leg in ok for leg in HISTORY_LEGS):
            tail = {
                leg: err
                for r in results_for
                for leg, err in getattr(r, "leg_errors", ())
                if leg in TAIL_LEGS
            }
            out[sid] = finish(conn, row, history_failed={}, tail_failed=tail, max_attempts=1)
        else:
            out[sid] = row.status
    return out


@dataclass(frozen=True)
class PromotePlan:
    """How a Workbench promote's new names get their history: ``cold`` = the ones that need onboarding (queued
    now, or already pending); ``fast`` = ingest the promote's new names at once in the background (the pre-X1
    on-promote job — at most ``Settings.onboard_fast_lane_max`` cold names, outside the nightly window), else
    they wait for the staged job and ``reason`` says why."""

    cold: frozenset[UUID]
    fast: bool
    reason: str | None = None


def plan_promote(
    conn: psycopg.Connection,
    new_ids: Iterable[UUID],
    *,
    tenant_id: UUID,
    thesis_id: UUID,
    now: datetime | None = None,
) -> PromotePlan:
    """THE PROMOTE'S LANE DECISION: queue the new names that are cold (``enqueue_cold``), then take the FAST LANE
    when there are at most ``Settings.onboard_fast_lane_max`` of them and the promote is outside the nightly
    window; otherwise the staged job loads them. No cold name at all (every new name is already loaded
    elsewhere) is the fast lane too — exactly the pre-X1 on-promote ingest. The caller commits."""
    s = get_settings()
    cold = enqueue_cold(conn, new_ids, tenant_id=tenant_id, source="promote", thesis_id=thesis_id)
    if not cold:
        return PromotePlan(frozenset(), True)
    w = window_bounds(
        now or market_now(),
        run_at=parse_run_at(s.cron_run_at),
        before_min=s.onboard_window_before_min,
        after_min=s.onboard_window_after_min,
    )
    if w is not None:
        return PromotePlan(
            frozenset(cold),
            False,
            f"saved inside the nightly window ({_window_label(*w)}) — their history loads after the pass",
        )
    if len(cold) > max(0, s.onboard_fast_lane_max):
        return PromotePlan(
            frozenset(cold),
            False,
            f"{len(cold)} new names is more than the {s.onboard_fast_lane_max} the fast lane loads at once — "
            "their history loads in batches outside the nightly window",
        )
    return PromotePlan(frozenset(cold), True)


# --- the window and the yield -------------------------------------------------------------------------------


def window_bounds(
    now: datetime, *, run_at: time, before_min: int, after_min: int
) -> tuple[datetime, datetime] | None:
    """The nightly window that ``now`` (AWARE, market time) falls in, as ``(start, end)``, or ``None`` when it
    is outside every window. A window is centered on RUN_AT of a calendar day (every day — a weekend just has an
    idle window), so a 23:00 RUN_AT whose window crosses midnight is found from either side. PURE.
    """
    before = timedelta(minutes=max(0, before_min))
    after = timedelta(minutes=max(0, after_min))
    if not before and not after:
        return None
    for day in (now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)):
        anchor = datetime.combine(day, run_at, tzinfo=now.tzinfo)
        start, end = anchor - before, anchor + after
        if start <= now < end:
            return start, end
    return None


def in_nightly_window(now: datetime, *, run_at: time, before_min: int, after_min: int) -> bool:
    """Is ``now`` (AWARE, market time) inside the nightly window? PURE (see ``window_bounds``)."""
    return window_bounds(now, run_at=run_at, before_min=before_min, after_min=after_min) is not None


def _window_label(start: datetime, end: datetime) -> str:
    return f"{start:%H:%M}-{end:%H:%M} {start.tzname() or ''}".strip()


def pass_holder(conn: psycopg.Connection) -> str | None:
    """Who holds the call-of-record lock right now (a nightly, its retry, an Admin "Run daily now", a backfill, a
    repair) — or ``None``. READ-ONLY: it reads ``pg_locks`` and NEVER takes the lock (a momentary take could make
    the nightly's own acquire fail and exit 75). Ends the read's transaction on an idle connection.
    """
    was_idle = not conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE
    try:
        return describe_holder(conn)
    finally:
        if was_idle and not conn.closed:
            conn.rollback()


def _try_job_lock(conn: psycopg.Connection) -> bool:
    got = conn.execute(
        "SELECT pg_try_advisory_lock(%s, %s) AS got", (LOCK_NAMESPACE, ONBOARD_LOCK_ID)
    ).fetchone()["got"]
    conn.commit()  # the session lock outlives the transaction
    return bool(got)


def _release_job_lock(conn: psycopg.Connection) -> None:
    try:
        if conn.closed:
            return
        if conn.info.transaction_status == TransactionStatus.INERROR:
            conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, ONBOARD_LOCK_ID))
        conn.commit()
    except Exception:  # noqa: BLE001 — closing the session releases it anyway
        _log.warning("onboarding: explicit job-lock unlock failed (it ends with the session)")


# --- one name -----------------------------------------------------------------------------------------------


@dataclass
class NameOutcome:
    """What one run did for one name. ``outcome``: ``done`` (warm now), ``requeued`` (a history leg failed,
    retried next run), ``failed`` (attempts exhausted), ``yielded`` (stopped between legs — resumes next run),
    ``busy`` (another writer held the company — skipped this run), ``missing`` (not in the tenant master).
    """

    security_id: UUID
    ticker: str | None
    outcome: str
    legs_run: list[str] = field(default_factory=list)
    legs_failed: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    appended: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None

    def to_json(self) -> dict:
        return {
            "security_id": str(self.security_id),
            "ticker": self.ticker,
            "outcome": self.outcome,
            "legs_run": list(self.legs_run),
            "legs_failed": dict(self.legs_failed),
            "seconds": round(self.seconds, 2),
            "appended": dict(self.appended),
            "stop_reason": self.stop_reason,
        }


def _end_transaction(conn: psycopg.Connection) -> None:
    if conn.info.transaction_status == TransactionStatus.INERROR:
        conn.rollback()
    elif not conn.autocommit:
        conn.commit()


def _run_leg(
    conn: psycopg.Connection,
    leg: str,
    sec: Security,
    *,
    tenant_id: UUID,
    client: EdgarClient,
    source: PriceSource,
) -> int:
    """Run ONE leg in FULL (no window) for ``sec`` — the SAME leg functions the nightly and the Workbench run
    (``pipeline.ingest_thesis``, ``ingest_bars_for_security``, ``ingest_fund_shares_for_security``,
    ``ingest_fundamentals_for_securities``), so the incremental / append-if-changed / no-lookahead rules cannot
    fork. Returns the rows it appended; raises on a leg failure (the caller rolls back and records it). The
    caller commits."""
    if leg == "form4":
        appended, _, _, _ = IT._form4_leg(conn, client, sec, tenant_id=tenant_id)
        return appended
    if leg == "form8k":
        appended, reversioned = IT._form8k_leg(conn, client, sec, tenant_id=tenant_id)
        return appended + reversioned
    if leg == "schedule13":
        appended, reversioned, _, _ = IT._schedule13_leg(conn, client, sec, tenant_id=tenant_id)
        return appended + reversioned
    if leg == "price":
        bars = timed("price")(ingest_bars_for_security)(
            conn,
            sec,
            tenant_id=tenant_id,
            allow_live=client.allow_live,
            force_refresh=True,
            source=source,
        )
        return bars.appended + bars.reversioned
    if leg == "fund_shares":
        shares = timed("fund_shares")(ingest_fund_shares_for_security)(
            conn, sec, tenant_id=tenant_id, allow_live=client.allow_live, force_refresh=True
        )
        return shares.appended + shares.reversioned
    if leg == "fundamentals":
        out = timed("fundamentals")(ingest_fundamentals_for_securities)(
            conn, [sec], client=client, tenant_id=tenant_id
        )
        res = out[0] if out else None
        if res is not None and res.error and not res.known_absent:
            raise RuntimeError(res.error)
        return res.appended if res is not None else 0
    raise ValueError(f"unknown onboarding leg {leg!r}")


def onboard_one(
    conn: psycopg.Connection,
    row: OnboardingRow,
    *,
    client: EdgarClient,
    source: PriceSource,
    should_stop: Callable[[], str | None],
    max_attempts: int,
    clock: Callable[[], float] = monotonic,
) -> NameOutcome:
    """Onboard ONE name: under its company's W1 lock (tried once — a held company is skipped this run), re-read
    its row (another runner — the Workbench fast lane — may have finished it since the work list was read), run
    the legs not yet checkpointed, committing each leg and then its checkpoint; check ``should_stop`` before
    every leg (a stop leaves the name ``in_progress`` with its checkpoint, never a failure); then ``finish``.
    """
    t0 = clock()
    _end_transaction(conn)  # never take or wait for a lock "idle in transaction"
    try:
        lock = acquire_company_lock(conn, row.security_id, wait_s=0)
    except CompanyLockBusy as busy:
        return NameOutcome(
            row.security_id, row.ticker, "busy", stop_reason=str(busy), seconds=clock() - t0
        )
    with lock:  # released after every leg's commit — the commits happen under the lock
        fresh = rows(conn, tenant_id=row.tenant_id, security_ids=[row.security_id])
        conn.rollback()
        if not fresh or fresh[0].status not in (STATUS_QUEUED, STATUS_IN_PROGRESS):
            now_status = fresh[0].status if fresh else "gone"
            return NameOutcome(
                row.security_id,
                row.ticker,
                "skipped",
                stop_reason=f"its row is now {now_status}",
                seconds=clock() - t0,
            )
        row = fresh[0]
        sec = master.get(conn, row.security_id, tenant_id=row.tenant_id)
        conn.rollback()
        if sec is None:
            status = finish(
                conn,
                row,
                history_failed={"master": "not in the tenant master"},
                tail_failed={},
                max_attempts=max_attempts,
            )
            return NameOutcome(
                row.security_id,
                row.ticker,
                "failed" if status == STATUS_FAILED else "missing",
                legs_failed={"master": "not in the tenant master"},
                seconds=clock() - t0,
            )
        out = NameOutcome(sec.id, sec.ticker, "done")
        mark_started(conn, row)
        done = set(row.legs_done)
        failed: dict[str, str] = {}
        for leg in LEGS:
            if leg in done:
                continue
            reason = should_stop()
            if reason is not None:
                out.outcome, out.stop_reason = "yielded", reason
                break
            try:
                out.appended[leg] = _run_leg(
                    conn, leg, sec, tenant_id=row.tenant_id, client=client, source=source
                )
                conn.commit()
            # fail-visible: capture, roll back this leg, keep going (the next leg still runs)
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                failed[leg] = _clip(str(e) or type(e).__name__, 400) or type(e).__name__
                out.legs_run.append(leg)
                continue
            out.legs_run.append(leg)
            mark_leg_done(conn, row, leg)
        out.legs_failed = failed
        if out.outcome != "yielded":
            status = finish(
                conn,
                row,
                history_failed={k: v for k, v in failed.items() if k in HISTORY_LEGS},
                tail_failed={k: v for k, v in failed.items() if k in TAIL_LEGS},
                max_attempts=max_attempts,
            )
            out.outcome = {STATUS_DONE: "done", STATUS_FAILED: "failed"}.get(status, "requeued")
    out.seconds = clock() - t0
    return out


# --- the batch ----------------------------------------------------------------------------------------------


@dataclass
class BatchOutcome:
    """One run of the job. ``refused`` = it did not start (``stopped`` says why: ``window`` / ``pass_running``
    / ``already_running`` / ``nothing_queued`` / ``daily_cap``); otherwise ``stopped`` is why the batch ended
    (``done`` = the work list ran out, ``window``, ``pass_running``, ``budget_names``, ``budget_time``,
    ``daily_cap``)."""

    started_at: datetime
    run_kind: str
    refused: bool = False
    stopped: str = "done"
    detail: str | None = None
    names: list[NameOutcome] = field(default_factory=list)
    queued_at_start: int = 0
    finished_at: datetime | None = None
    log_path: Path | None = None
    timings: dict | None = None
    # the split-history fill this run made for joining companies (``pipeline/split_history.py``), or None
    split_history: dict | None = None
    split_history_line: str | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.names:
            out[n.outcome] = out.get(n.outcome, 0) + 1
        return out

    def payload(self, status: str) -> dict:
        return {
            "pass_kind": PASS_KIND,
            "run_kind": self.run_kind,
            "status": status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_s": (
                round((self.finished_at - self.started_at).total_seconds(), 3)
                if self.finished_at
                else None
            ),
            "stopped": self.stopped,
            "detail": self.detail,
            "queued_at_start": self.queued_at_start,
            "counts": self.counts(),
            "names": [n.to_json() for n in self.names],
            "timings": self.timings,
            "split_history": self.split_history,
        }


def _edgar_client() -> EdgarClient:
    """THE SEC BUDGET SEAM: the job's ONE EDGAR client. Built on the RECURRING dial (``RECURRING_CACHE_TTL_S`` —
    a scheduled job never serves a stale index), which is also what puts it in the global SEC budget's
    ``recurring`` LANE (X2, ``ingest/sec_budget.py``: ``EdgarClient`` picks the lane from ``recurring``): the job
    is a BULK BACKGROUND consumer, so a Workbench discovery run (the ``interactive`` lane) keeps its fair half
    of the budget against it, and the job never runs beside the nightly (the window), the other recurring
    consumer. Every request also passes the client's own per-process cap (``Settings.edgar_rate_per_sec``).
    """
    return EdgarClient(allow_live=True, cache_ttl_s=RECURRING_CACHE_TTL_S)


# ``run_batch(split_source=...)`` left at this: the configured Massive.com split reference (``None`` with no key)
_DEFAULT_SPLIT_SOURCE: Any = object()


def _split_history_step(
    conn: psycopg.Connection,
    outcome: BatchOutcome,
    *,
    client: EdgarClient,
    source: Any,
    should_stop: Callable[[], str | None],
) -> None:
    """THE SPLIT-HISTORY FILL for joining companies (``pipeline/split_history.fill``), FAIL-OPEN: its outcome
    rides the batch (``outcome.split_history`` + one printed line); a fault is captured there, rolled back and
    logged — it never costs the batch its names."""
    from ingest.polygon_splits import default_split_source
    from pipeline import (
        split_history,  # lazy: the split modules are not on the job's import path otherwise
    )
    from pipeline.detect_splits import _memo_dir

    try:
        polygon = default_split_source() if source is _DEFAULT_SPLIT_SOURCE else source
        res = split_history.fill(
            conn,
            edgar=client,
            polygon=polygon,
            apply=True,
            allow_live=True,  # the reference pull (the day's cache first); the EDGAR client has its own dial
            should_stop=should_stop,
            memo_dir=_memo_dir(client),
        )
        outcome.split_history = res.to_json()
        outcome.split_history_line = res.summary()
    except Exception as e:  # noqa: BLE001 — fail-open
        if not conn.closed:
            conn.rollback()  # a company's half-done transaction; the companies already committed stay
        msg = _clip(f"{type(e).__name__}: {e}", 400)
        outcome.split_history = {"status": "error", "detail": msg}
        outcome.split_history_line = f"split history: FAILED — {msg} (the batch continues)"
        _log.exception("onboarding: the split-history fill failed (fail-open)")


def run_split_history_now(conn: psycopg.Connection, *, allow_live: bool = True) -> Any:
    """``python -m pipeline.split_history --apply``: ONE fill now, under the onboarding job's own lock (a batch
    never runs beside it) and the job's rules — refused inside the nightly window or while a pass holds the
    call-of-record lock. Returns the ``split_history.FillOutcome`` (status ``refused`` with the reason when it
    did not run)."""
    from ingest.polygon_splits import default_split_source
    from pipeline import split_history
    from pipeline.detect_splits import _memo_dir

    s = get_settings()
    run_at = parse_run_at(s.cron_run_at)

    def window_now() -> tuple[datetime, datetime] | None:
        return window_bounds(
            market_now(),
            run_at=run_at,
            before_min=s.onboard_window_before_min,
            after_min=s.onboard_window_after_min,
        )

    def refused(why: str) -> Any:
        out = split_history.FillOutcome(status="refused", detail=why)
        return out

    w = window_now()
    if w is not None:
        return refused(f"inside the nightly window ({_window_label(*w)})")
    if not _try_job_lock(conn):
        return refused("an onboarding batch is running (it fills split history itself)")
    try:
        holder = pass_holder(conn)
        if holder is not None:
            return refused(f"a pass holds the call-of-record lock ({holder})")
        client = EdgarClient(allow_live=allow_live, cache_ttl_s=RECURRING_CACHE_TTL_S)

        def should_stop() -> str | None:
            ww = window_now()
            if ww is not None:
                return f"window: entered the nightly window ({_window_label(*ww)})"
            h = pass_holder(conn)
            return None if h is None else f"pass_running: {h}"

        return split_history.fill(
            conn,
            edgar=client,
            polygon=default_split_source(),
            apply=True,
            allow_live=allow_live,
            should_stop=should_stop,
            memo_dir=_memo_dir(client),
        )
    finally:
        _release_job_lock(conn)


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _artifact(outcome: BatchOutcome, status: str, runs_dir: Path) -> Path | None:
    """Write (or finalize) this run's artifact — fail-open: a record that cannot be written never fails the
    job (the table is the durable state)."""
    try:
        path = runs_dir / f"{outcome.started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        _write_atomic(path, outcome.payload(status))
        return path
    except Exception:  # noqa: BLE001
        _log.exception("onboarding: the run artifact could not be written (fail-open)")
        return None


@dataclass(frozen=True)
class _Scope:
    """A per-name timing scope (``pass_timings.per_thesis`` reads ``id`` / ``name``)."""

    id: UUID
    name: str
    row: OnboardingRow


def run_batch(
    *,
    run_kind: str = "manual",
    max_names: int | None = None,
    budget_s: float | None = None,
    daily_max: int | None = None,
    now: Callable[[], datetime] = market_now,
    clock: Callable[[], float] = monotonic,
    connect_fn: Callable[[], psycopg.Connection] = connect,
    edgar_client: EdgarClient | None = None,
    price_source: PriceSource | None = None,
    runs_dir: Path | None = None,
    split_source: Any = _DEFAULT_SPLIT_SOURCE,
) -> BatchOutcome:
    """ONE run of the staged cold-ingest job (see the module docstring). Never raises for a name (each is
    captured); a crash escaping the batch marks its artifact ``crashed`` and re-raises. ``now`` is the AWARE
    market clock the window is judged on; ``clock`` the monotonic one the time budget is.

    Before the queue, every run fills the SPLIT HISTORY of any company that joined a live basket without one
    (``pipeline/split_history.py`` — fast-lane promotes included, so it runs when nothing is queued too);
    ``split_source`` is its Massive.com split reference (default: ``default_split_source()``, ``None`` = off).
    """
    s = get_settings()
    max_names = s.onboard_batch_max_names if max_names is None else max_names
    budget_s = s.onboard_batch_budget_s if budget_s is None else budget_s
    daily_max = s.onboard_daily_max_names if daily_max is None else daily_max
    run_at = parse_run_at(s.cron_run_at)
    outcome = BatchOutcome(started_at=_now_utc(), run_kind=run_kind)
    runs_dir = runs_dir or _DEFAULT_RUNS_DIR

    def window_now() -> tuple[datetime, datetime] | None:
        return window_bounds(
            now(),
            run_at=run_at,
            before_min=s.onboard_window_before_min,
            after_min=s.onboard_window_after_min,
        )

    def refuse(stopped: str, detail: str) -> BatchOutcome:
        outcome.refused, outcome.stopped, outcome.detail = True, stopped, detail
        outcome.finished_at = _now_utc()
        return outcome

    w = window_now()
    if w is not None:  # the cheapest check, before any connection
        return refuse(
            "window",
            f"inside the nightly window ({_window_label(*w)}) — onboarding yields to the 22:30 pass",
        )
    conn = connect_fn()
    try:
        name_session(conn, "alphadeck-writer:onboard")  # how a waiting writer names this one (W1)
        set_statement_timeout(conn, s.cron_statement_timeout_s)
        if not _try_job_lock(conn):
            return refuse("already_running", "another onboarding batch is running")
        try:
            holder = pass_holder(conn)
            if holder is not None:
                return refuse("pass_running", f"a pass holds the call-of-record lock ({holder})")
            client = edgar_client or _edgar_client()

            def fill_should_stop() -> str | None:
                w = window_now()
                if w is not None:
                    return f"window: entered the nightly window ({_window_label(*w)})"
                h = pass_holder(conn)
                return (
                    None
                    if h is None
                    else f"pass_running: a pass holds the call-of-record lock ({h})"
                )

            _split_history_step(
                conn, outcome, client=client, source=split_source, should_stop=fill_should_stop
            )
            work = work_list(conn)
            conn.rollback()
            outcome.queued_at_start = len(work)
            if not work:
                return refuse("nothing_queued", "no name is waiting to be onboarded")
            midnight = datetime.combine(now().date(), time(0, 0), tzinfo=now().tzinfo)
            done_today = attempted_since(conn, midnight) if daily_max > 0 else 0
            conn.rollback()
            if daily_max > 0 and done_today >= daily_max:
                return refuse(
                    "daily_cap",
                    f"{done_today} name(s) already attempted today (cap {daily_max}/day)",
                )
            return _run_work(
                conn,
                work,
                outcome,
                max_names=max_names,
                budget_s=budget_s,
                room_today=(daily_max - done_today) if daily_max > 0 else None,
                window_now=window_now,
                clock=clock,
                client=client,
                source=price_source or YahooPriceSource(),
                runs_dir=runs_dir,
                max_attempts=s.onboard_max_attempts,
            )
        finally:
            _release_job_lock(conn)
    finally:
        conn.close()


def _run_work(
    conn: psycopg.Connection,
    work: list[OnboardingRow],
    outcome: BatchOutcome,
    *,
    max_names: int,
    budget_s: float,
    room_today: int | None,
    window_now: Callable[[], tuple[datetime, datetime] | None],
    clock: Callable[[], float],
    client: EdgarClient,
    source: PriceSource,
    runs_dir: Path,
    max_attempts: int,
) -> BatchOutcome:
    t0 = clock()
    timer = pass_timings.start()
    token = pass_timings.activate(timer)
    _artifact(outcome, _STATUS_RUNNING, runs_dir)

    def should_stop() -> str | None:
        w = window_now()
        if w is not None:
            return f"window: entered the nightly window ({_window_label(*w)})"
        if budget_s > 0 and clock() - t0 >= budget_s:
            return f"budget_time: the run's {budget_s:.0f}s budget is spent"
        holder = pass_holder(conn)
        if holder is not None:
            return f"pass_running: a pass holds the call-of-record lock ({holder})"
        return None

    def checks(attempted: int) -> tuple[str, str] | None:
        if max_names > 0 and attempted >= max_names:
            return "budget_names", f"{max_names} name(s) this run"
        if room_today is not None and attempted >= room_today:
            return "daily_cap", "the daily cap is reached"
        reason = should_stop()
        if reason is not None:
            return reason.split(":", 1)[0], reason
        return None

    scopes = pass_timings.per_thesis(
        _Scope(r.security_id, r.ticker or str(r.security_id), r) for r in work
    )
    try:
        attempted = 0
        for row in work:
            stop = checks(attempted)
            if stop is not None:  # checked BEFORE the name's timing scope opens (no empty scope)
                outcome.stopped, outcome.detail = stop
                break
            scope = next(scopes)
            assert scope.row is row
            one = onboard_one(
                conn,
                scope.row,
                client=client,
                source=source,
                should_stop=should_stop,
                max_attempts=max_attempts,
                clock=clock,
            )
            outcome.names.append(one)
            if one.outcome not in ("busy", "skipped"):
                attempted += 1
            if one.outcome == "yielded" and one.stop_reason:
                outcome.stopped = one.stop_reason.split(":", 1)[0]
                outcome.detail = one.stop_reason
                break
        scopes.close()  # close the last name's timing scope before the snapshot
        outcome.finished_at = _now_utc()
        outcome.timings = pass_timings.snapshot(timer)
        outcome.log_path = _artifact(outcome, _STATUS_FINISHED, runs_dir)
        return outcome
    except BaseException:
        scopes.close()
        outcome.finished_at = _now_utc()
        outcome.timings = pass_timings.snapshot(timer)
        _artifact(outcome, _STATUS_CRASHED, runs_dir)
        raise
    finally:
        pass_timings.deactivate(token)


# --- the readout (Admin, the Workbench note, the CLI) --------------------------------------------------------


def list_runs(*, runs_dir: Path | None = None, limit: int = 5) -> list[dict]:
    """The newest ``limit`` run artifacts, newest first (fail-open per file). Read-only."""
    d = runs_dir or _DEFAULT_RUNS_DIR
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        if len(out) >= limit:
            break
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


@dataclass
class Readout:
    """The queue as the operator reads it: ``counts`` per status (+ ``parked`` = pending rows no live basket
    holds), the PENDING rows (failed first, then in progress, then queued), the latest done ones, the latest
    run, the window, and whether the queue looks STALLED (names waiting, no finished run for a while).
    """

    counts: dict[str, int]
    pending: list[OnboardingRow]
    recent_done: list[OnboardingRow]
    last_run: dict | None
    window: str
    stalled: str | None
    paused: str | None = None  # quiet: the daily cap is reached, loading resumes after midnight


def readout(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID | None = None,
    security_ids: Collection[UUID] | None = None,
    thesis_id: UUID | None = None,
    runs_dir: Path | None = None,
    now: datetime | None = None,
    done_limit: int = 20,
) -> Readout:
    """The queue readout. ``security_ids`` scopes it to those names; ``thesis_id`` (the Workbench's per-thesis
    progress) further keeps only the rows still PENDING or enqueued BY that thesis, so a name loaded long ago
    for another thesis does not inflate this promote's "loaded" count. Read-only."""
    s = get_settings()
    all_rows = rows(conn, tenant_id=tenant_id, security_ids=security_ids)
    if thesis_id is not None:
        all_rows = [r for r in all_rows if r.status in PENDING_STATUSES or r.thesis_id == thesis_id]
    counts = {k: 0 for k in (*PENDING_STATUSES, STATUS_DONE, "parked")}
    for r in all_rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.status in (STATUS_QUEUED, STATUS_IN_PROGRESS) and not r.in_live_basket:
            counts["parked"] += 1
    order = {STATUS_FAILED: 0, STATUS_IN_PROGRESS: 1, STATUS_QUEUED: 2}
    pending = sorted(
        (r for r in all_rows if r.status in PENDING_STATUSES),
        key=lambda r: (order[r.status], r.enqueued_at, str(r.security_id)),
    )
    recent_done = sorted(
        (r for r in all_rows if r.status == STATUS_DONE),
        key=lambda r: r.finished_at or r.enqueued_at,
        reverse=True,
    )[:done_limit]
    runs = list_runs(runs_dir=runs_dir, limit=10)
    last_run = runs[0] if runs else None
    run_at = parse_run_at(s.cron_run_at)
    tz = market_tz()
    now = now or _now_utc()
    anchor = datetime.combine(now.astimezone(tz).date(), run_at, tzinfo=tz)
    start = anchor - timedelta(minutes=max(0, s.onboard_window_before_min))
    end = anchor + timedelta(minutes=max(0, s.onboard_window_after_min))
    window = _window_label(start, end) if start != end else "off"
    stalled = None
    paused = None
    waiting = [
        r for r in pending if r.status in (STATUS_QUEUED, STATUS_IN_PROGRESS) and r.in_live_basket
    ]
    if waiting and s.onboard_daily_max_names > 0:
        midnight = datetime.combine(now.astimezone(tz).date(), time(0, 0), tzinfo=tz)
        today = attempted_since(conn, midnight)
        if today >= s.onboard_daily_max_names:
            # the job is ALIVE and idle on purpose (its refusals write no artifact): quiet, never "stalled"
            paused = (
                f"today's cap of {s.onboard_daily_max_names} names is reached — loading resumes after "
                f"midnight ({tz.key})"
            )
    if waiting and paused is None and s.onboard_stalled_after_h > 0:
        limit = timedelta(hours=s.onboard_stalled_after_h)
        oldest = min(r.enqueued_at for r in waiting)
        finished = [
            datetime.fromisoformat(d["finished_at"])
            for d in runs
            if d.get("status") == _STATUS_FINISHED and d.get("finished_at")
        ]
        last_finished = max(finished) if finished else None
        if now - oldest > limit and (last_finished is None or now - last_finished > limit):
            since = (
                f"since {last_finished.astimezone(tz):%Y-%m-%d %H:%M %Z}"
                if last_finished
                else "ever"
            )
            stalled = (
                f"{len(waiting)} name(s) waiting and no onboarding run has finished {since} — check the "
                "cron sidecar's onboard loop (`docker logs alphadeck-cron-1 | grep onboard-cron`)"
            )
    return Readout(counts, pending, recent_done, last_run, window, stalled, paused)


def describe_counts(counts: Mapping[str, int]) -> str:
    parts = [
        f"{counts.get(k, 0)} {k.replace('_', ' ')}" for k in (STATUS_QUEUED, STATUS_IN_PROGRESS)
    ]
    parts += [f"{counts.get(STATUS_DONE, 0)} done", f"{counts.get(STATUS_FAILED, 0)} failed"]
    if counts.get("parked"):
        parts.append(f"{counts['parked']} parked (in no live basket)")
    return " · ".join(parts)


# --- the CLI ------------------------------------------------------------------------------------------------


def _print_outcome(o: BatchOutcome) -> None:
    if o.split_history_line and (o.split_history or {}).get("status") != "idle":
        print(o.split_history_line)
    if o.refused:
        print(f"onboarding: not started — {o.detail}")
        return
    c = o.counts()
    parts = ", ".join(f"{n} {k}" for k, n in sorted(c.items())) or "nothing"
    dur = (o.finished_at - o.started_at).total_seconds() if o.finished_at else 0.0
    print(
        f"onboarding: {len(o.names)} name(s) in {dur:.0f}s ({parts}) of {o.queued_at_start} waiting; "
        f"stopped: {o.detail or o.stopped}"
    )
    for n in o.names:
        if n.outcome in ("failed", "requeued", "missing"):
            errs = "; ".join(f"{k}: {v}" for k, v in n.legs_failed.items())
            print(f"  {n.ticker or n.security_id}: {n.outcome.upper()} — {errs}")
        elif n.legs_failed:
            errs = "; ".join(f"{k}: {v}" for k, v in n.legs_failed.items())
            print(f"  {n.ticker or n.security_id}: {n.outcome} (tail leg failed: {errs})")


def _print_status(conn: psycopg.Connection) -> None:
    r = readout(conn)
    print(f"onboarding queue: {describe_counts(r.counts)} · nightly window {r.window}")
    if r.stalled:
        print(f"STALLED — {r.stalled}")
    for row in r.pending[:50]:
        legs = ",".join(row.legs_done) or "-"
        err = f" — {row.last_error}" if row.last_error else ""
        park = " (parked: in no live basket)" if not row.in_live_basket else ""
        print(
            f"  {row.ticker or row.security_id}: {row.status} · legs {legs} · attempts {row.attempts}{park}{err}"
        )
    if len(r.pending) > 50:
        print(f"  … and {len(r.pending) - 50} more")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="X1 — the staged cold-ingest job: load queued names' history outside the nightly window."
    )
    p.add_argument("--run-kind", choices=("cron", "manual"), default="manual")
    p.add_argument(
        "--max-names", type=int, default=None, help="names this run (default: the setting)"
    )
    p.add_argument(
        "--budget-s", type=float, default=None, help="wall-time budget (default: the setting)"
    )
    p.add_argument("--status", action="store_true", help="print the queue and exit (read-only)")
    p.add_argument(
        "--enqueue-thesis", default=None, help="queue a thesis's COLD members (by thesis id)"
    )
    p.add_argument("--retry-failed", action="store_true", help="re-queue every failed name")
    args = p.parse_args(argv)

    if args.status or args.enqueue_thesis or args.retry_failed:
        conn = connect()
        try:
            if args.enqueue_thesis:
                from repositories import thesis_repo

                thesis = thesis_repo.get(conn, UUID(args.enqueue_thesis))
                if thesis is None:
                    p.error(f"thesis {args.enqueue_thesis} not found")
                ids = {m.security_id for m in thesis.basket if m.security_id}
                cold = enqueue_cold(
                    conn, ids, tenant_id=thesis.tenant_id, source="cli", thesis_id=thesis.id
                )
                conn.commit()
                print(f"onboarding: {len(cold)} of {len(ids)} member(s) are cold and queued")
            if args.retry_failed:
                n = 0
                for row in rows(conn):
                    if row.status == STATUS_FAILED and retry(
                        conn, row.security_id, tenant_id=row.tenant_id
                    ):
                        n += 1
                conn.commit()
                print(f"onboarding: {n} failed name(s) re-queued")
            if args.status:
                _print_status(conn)
        finally:
            conn.close()
        return
    outcome = run_batch(run_kind=args.run_kind, max_names=args.max_names, budget_s=args.budget_s)
    _print_outcome(outcome)
    timings = pass_timings.summary_line(outcome.timings)
    if timings:
        print(timings)


if __name__ == "__main__":
    main()
