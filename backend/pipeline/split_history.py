"""``pipeline.split_history`` — a company that JOINS a basket gets its split history (2026-10-24).

THE GAP (MEASURED on prod 2026-10-24, read-only). The split list (``security_split``) was filled once, by the S1
backfill of 2026-10-20 (``pipeline/backfill_splits.py``: Massive.com's split reference since 2024-10-01, every split
checked against the company's own SEC filing) over the basket union of that day. The nightly (S4,
``pipeline/detect_splits.py``) looks back only 30 days (Massive.com) / 60 days (SEC filings), and nothing filled the
history of a company that joined a basket later: 32 splits Massive.com lists since 2025-03 on 29 companies were missing
— ALL on companies that joined after the backfill (350 of them: CRWD 4-for-1, POWL 3-for-1, LHSW 1-for-16, ABTS
1-for-15 …); companies in a basket at the backfill: 0 missing. The list feeds the price basis (S2), the insider
price screens and the market cap.

THE FILL. Every live basket member with no ``security_split_history`` row (migration 0067: the S1 backfill's own
coverage is seeded there, and every ``backfill_splits --apply`` run marks what it covered) gets the S1 backfill
scoped to it — the SAME decision (``backfill_splits.plan_members``: ``domain.splits.decide``, the targeted wide-form
reader included), the SAME plan against the stored list (``plan_against_stored``: a split the nightly or the
operator already listed is never touched), the SAME append (``append_planned``: author ``system:backfill_splits``,
count-verified), the SAME journal (``data_repair``, tool ``backfill_splits``, when it appended). It covers a
fast-lane promote, a hand-added name, a name moved between theses — anything that puts a company in a live
basket — because it reads the baskets, not the onboarding queue.

THE COST. Massive.com: ONE market-wide reference pull (``since`` 2024-10-01, a date and nothing else) per run, served
from the day's cache after the first (``ingest/polygon_splits.py`` keys each page by URL + market day), and only
when some member needs a fill — never one request per company. SEC: only for a company Massive.com lists a split
for, the filings in each split's windows, each read once ever (the immutable ``forms/`` cache, the extraction
memo). Most joiners have no split: their fill costs no request at all.

WHERE IT RUNS. Inside the staged onboarding job (``pipeline.onboarding.run_batch``, fired by the cron sidecar
every 30 minutes): after the job's own lock and its window / running-pass checks, BEFORE the queue (so it runs
when nothing is queued too) — outside the nightly window, never beside a pass. Per company, under that company's
W1 write lock (tried once; a held company waits for the next run). FAIL-OPEN: a fault is printed and never costs
the batch its names; a company whose SEC filing index (or a filing in a split's window) could not be read is
marked INCOMPLETE and retried, at most ``MAX_ATTEMPTS`` runs; with no ``MASSIVE_API_KEY`` nothing is filled or
marked (the next run with a key fills it).

    python -m pipeline.split_history              # dry run: the members that need a fill and what it would append
    python -m pipeline.split_history --apply      # fill them now (the onboarding job's lock, its window rule)
    python -m pipeline.split_history --status     # the members that need a fill, read-only
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

import psycopg

from db.write_lock import CompanyLockBusy, acquire_company_lock
from domain.market_time import market_today
from ingest import CacheMiss
from ingest.edgar.client import EdgarClient
from ingest.polygon_splits import PolygonSplitsError, PolygonSplitSource, default_split_source
from pipeline.backfill_splits import (
    APPEND,
    DEFAULT_SINCE,
    FORMER_TICKERS_SQL,
    Member,
    SecReader,
    append_planned,
    match_polygon,
    member_of,
    plan_against_stored,
    plan_members,
)
from pipeline.split_basis import _UNION
from repositories import data_repair_repo, split_history_repo, split_repo

_log = logging.getLogger("alphadeck.split_history")

# an INCOMPLETE fill (an unreadable index / filing) is retried at most this many runs, then left as it is
# (marked, the note says what was missing — the operator's ``backfill_splits --ticker X`` re-runs it)
MAX_ATTEMPTS = 3

_NEEDING_SQL = f"""
    WITH u AS ({_UNION})
    SELECT u.tenant_id, u.security_id, sm.ticker, sm.price_symbol, sm.cik, {FORMER_TICKERS_SQL}
      FROM u
      JOIN security_master sm ON sm.id = u.security_id
      LEFT JOIN security_split_history h
             ON h.tenant_id = u.tenant_id AND h.security_id = u.security_id
     WHERE h.security_id IS NULL OR (NOT h.complete AND h.attempts < %s)
     ORDER BY sm.ticker NULLS LAST, u.security_id
"""


def needing(conn: psycopg.Connection) -> list[Member]:
    """The live basket members whose split history is not filled: no row, or an incomplete one with attempts
    left. Read-only."""
    with conn.cursor() as cur:
        cur.execute(_NEEDING_SQL, (MAX_ATTEMPTS,))
        rows = cur.fetchall()
    return [member_of(r) for r in rows]


@dataclass
class CompanyFill:
    """What the fill did for one company."""

    member: Member
    polygon_splits: int = 0
    appended: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    complete: bool = True
    note: str | None = None
    outcome: str = "filled"  # filled | incomplete | busy | planned (a dry run)

    def line(self) -> str:
        who = self.member.ticker or str(self.member.security_id)
        st = ", ".join(f"{k} {v}" for k, v in sorted(self.statuses.items()))
        tail = f" ({st})" if st else ""
        extra = f" — {self.note}" if self.note else ""
        return f"{who}: {self.polygon_splits} split(s), {self.appended} appended{tail}{extra} [{self.outcome}]"


@dataclass
class FillOutcome:
    """One run of the fill. ``status``: ``idle`` (no member needs one), ``off`` (no Massive.com key),
    ``polygon_failed``, ``done``, ``stopped`` (``should_stop`` asked it to yield between companies), ``refused``
    (``onboarding.run_split_history_now`` did not start it: the window, a running pass or batch).
    """

    status: str = "idle"
    detail: str | None = None
    needing: int = 0
    companies: list[CompanyFill] = field(default_factory=list)
    polygon_rows: int = 0
    polygon_live_pages: int = 0
    applied: bool = False

    @property
    def appended(self) -> int:
        return sum(c.appended for c in self.companies)

    def summary(self) -> str:
        if self.status in ("idle", "off", "polygon_failed", "refused"):
            return f"split history: {self.status}" + (f" — {self.detail}" if self.detail else "")
        filled = sum(1 for c in self.companies if c.outcome == "filled")
        verb = "appended" if self.applied else "would append"
        parts = [
            f"{len(self.companies)} of {self.needing} joining compan(ies)",
            f"{filled} filled",
            f"{verb} {self.appended} list version(s)",
        ]
        for k in ("incomplete", "busy"):
            n = sum(1 for c in self.companies if c.outcome == k)
            if n:
                parts.append(f"{n} {k}")
        live = (
            f" · Massive.com {self.polygon_live_pages} live page(s)"
            if self.polygon_live_pages
            else ""
        )
        stop = f" · stopped: {self.detail}" if self.status == "stopped" and self.detail else ""
        return "split history: " + " · ".join(parts) + live + stop

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "needing": self.needing,
            "appended": self.appended,
            "polygon_rows": self.polygon_rows,
            "polygon_live_pages": self.polygon_live_pages,
            "companies": [
                {
                    "security_id": str(c.member.security_id),
                    "ticker": c.member.ticker,
                    "polygon_splits": c.polygon_splits,
                    "appended": c.appended,
                    "statuses": c.statuses,
                    "outcome": c.outcome,
                    "note": c.note,
                }
                for c in self.companies
            ],
        }


def _count_company(tenant_id: UUID, security_id: UUID) -> Callable[[psycopg.Connection], int]:
    def count(conn: psycopg.Connection) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM security_split WHERE tenant_id = %s AND security_id = %s",
                (tenant_id, security_id),
            )
            return int(cur.fetchone()["n"])

    return count


def _incomplete(reader: SecReader, m: Member, planned: list[Any]) -> str | None:
    """Why this company's fill could not read everything (``None`` when it could)."""
    why = []
    if m.cik and m.cik in reader.submissions and reader.submissions[m.cik] is None:
        why.append("its SEC filing index could not be read")
    unread = sum(p.filings_unread for p in planned)
    if unread:
        why.append(f"{unread} SEC filing(s) in its splits' windows could not be read")
    return "; ".join(why) or None


def fill(
    conn: psycopg.Connection,
    *,
    edgar: EdgarClient,
    polygon: PolygonSplitSource | None,
    asof: date | None = None,
    apply: bool = True,
    allow_live: bool = True,
    should_stop: Callable[[], str | None] | None = None,
    memo_dir: Any = None,
    members: list[Member] | None = None,
) -> FillOutcome:
    """THE FILL (the module docstring) for every member that needs one (``needing``; ``members`` overrides it).
    With ``apply`` each company is planned, then appended + marked in its own transaction under its W1 lock, and
    a run that appended writes ONE journal row; a dry run reads only (and rolls back). Returns the outcome.
    """
    asof = asof or market_today()
    out = FillOutcome()
    todo = needing(conn) if members is None else members
    conn.rollback()  # never hold the read open across network I/O
    out.needing = len(todo)
    if not todo:
        return out
    if polygon is None:
        out.status, out.detail = "off", "no MASSIVE_API_KEY — nothing filled or marked"
        return out
    try:
        pull = polygon.splits_since(DEFAULT_SINCE, allow_live=allow_live)
    except (PolygonSplitsError, CacheMiss) as e:
        out.status, out.detail = "polygon_failed", f"{type(e).__name__}: {e}"[:300]
        return out
    out.polygon_rows, out.polygon_live_pages = len(pull.rows), pull.live_pages
    matched = match_polygon(pull.rows, todo)
    reader = SecReader(edgar, memo_dir=memo_dir)
    out.status = "done"
    out.applied = apply
    try:
        for m in todo:
            reason = should_stop() if should_stop is not None else None
            if reason is not None:
                out.status, out.detail = "stopped", reason
                break
            c = CompanyFill(member=m)
            out.companies.append(c)
            _fill_one(conn, reader, c, matched.get(m, []), asof=asof, apply=apply)
    finally:
        # a run that appended journals it — even when a later company raised (its own transaction rolled back;
        # the companies before it are committed and must be on the journal)
        if apply and out.appended:
            data_repair_repo.journal(
                conn,
                tool="backfill_splits",
                summary=(
                    f"split history for {sum(1 for c in out.companies if c.appended)} newly joined "
                    f"compan(ies): appended {out.appended} split-list version(s) checked against SEC"
                ),
                rows_changed=out.appended,
                counts={
                    "appended": out.appended,
                    "companies": len(out.companies),
                    "tickers": sorted(c.member.ticker or "" for c in out.companies if c.appended),
                },
            )
    return out


def _fill_one(
    conn: psycopg.Connection,
    reader: SecReader,
    c: CompanyFill,
    rows: list[Any],
    *,
    asof: date,
    apply: bool,
) -> None:
    """One company: S1's decisions (reads only, no lock held), then — with ``apply`` — under its W1 lock the
    plan against the stored list, the append (count-verified) and the marker, in ONE transaction."""
    m = c.member
    planned = plan_members(conn, reader, {m: rows} if rows else {}, asof=asof)
    conn.rollback()
    c.polygon_splits = len(planned)
    c.note = _incomplete(reader, m, planned)
    c.complete = c.note is None
    if not apply:
        stored = split_repo.latest_versions(
            conn, tenant_id=m.tenant_id, security_ids=[m.security_id]
        )
        conn.rollback()
        plan_against_stored(planned, {m.security_id: stored})
        c.appended = sum(1 for p in planned if p.action == APPEND)
        c.statuses = _statuses(planned, appended_only=True)
        c.outcome = "planned"
        return
    try:
        lock = acquire_company_lock(conn, m.security_id, wait_s=0)
    except CompanyLockBusy as busy:
        c.outcome, c.note = "busy", f"another writer holds the company ({busy})"[:300]
        return
    with lock:
        try:
            stored = split_repo.latest_versions(
                conn, tenant_id=m.tenant_id, security_ids=[m.security_id]
            )
            plan_against_stored(planned, {m.security_id: stored})
            appends = [p for p in planned if p.action == APPEND]
            append_planned(conn, appends, count_of=_count_company(m.tenant_id, m.security_id))
            split_history_repo.mark(
                conn,
                tenant_id=m.tenant_id,
                security_id=m.security_id,
                since=DEFAULT_SINCE,
                source=split_history_repo.SOURCE_JOB,
                complete=c.complete,
                polygon_splits=c.polygon_splits,
                appended=len(appends),
                note=c.note,
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    c.appended = len(appends)
    c.statuses = _statuses(appends, appended_only=False)
    c.outcome = "filled" if c.complete else "incomplete"


def _statuses(planned: list[Any], *, appended_only: bool) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in planned:
        if appended_only and p.action != APPEND:
            continue
        out[p.decision.status] = out.get(p.decision.status, 0) + 1
    return out


# --- the CLI ----------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    from db.session import connect
    from pipeline import onboarding

    p = argparse.ArgumentParser(
        description="Fill the split history of every live basket member that has none (the S1 backfill, "
        "scoped to it). Dry-run (read-only) by default; the staged onboarding job runs it every batch."
    )
    p.add_argument("--apply", action="store_true", help="fill now (default: dry run, read-only)")
    p.add_argument(
        "--status", action="store_true", help="list the members that need a fill and exit"
    )
    p.add_argument(
        "--no-live", action="store_true", help="caches only (no SEC / Massive.com request)"
    )
    args = p.parse_args(argv)
    conn = connect()
    try:
        if args.status:
            ms = needing(conn)
            print(f"split history: {len(ms)} live basket member(s) need a fill")
            for m in ms[:200]:
                print(f"  {m.ticker or m.security_id} (cik {m.cik or '—'})")
            return
        if not args.apply:
            conn.read_only = True  # a dry run PHYSICALLY cannot write
            edgar = EdgarClient(allow_live=not args.no_live, max_per_sec=4.0)
            out = fill(
                conn,
                edgar=edgar,
                polygon=default_split_source(),
                apply=False,
                allow_live=not args.no_live,
            )
        else:
            out = onboarding.run_split_history_now(conn, allow_live=not args.no_live)
    finally:
        conn.close()
    for c in out.companies:
        if c.polygon_splits or c.note:
            print("  " + c.line())
    print(out.summary())


if __name__ == "__main__":
    main()
