"""The daily call-of-record cron (M2b) — the platform feeding itself.

Once a day, for every thesis (tenant intrinsic per-thesis):
  1. refresh its back-half facts — ``ingest_thesis`` (incremental + fail-visible; M2a);
  2. assemble TODAY's call WITHOUT writing — ``call_for_thesis(asof=today, known_at=now, record=False)``;
  3. append the call-of-record ONLY if it changed — ``calls_repo.record_if_changed``.

Discipline:
- **Per-thesis isolation** — each thesis is its own unit; one thesis's failure is captured + skipped,
  never fatal to the run (the cron must finish the rest).
- **Idempotent** — a same-day re-run on unchanged facts appends ZERO rows; a genuine change appends EXACTLY
  ONE new versioned row (``record_if_changed``). Safe to re-run / catch up a missed day.
- **No-lookahead** — ``asof=today``, ``known_at=now`` (never backdated).
- **Option B** — it ingests FACTS and appends the call-of-record (the write-only log); it builds NO
  read-serving signal/score cache. Calls still re-derive on read.
- **Scoreboard-ready, not coupled** — one clean versioned row per (thesis, day); same-day re-runs collapse
  via ``latest_for_thesis``' DISTINCT ON. No Scoreboard code here.

    python -m pipeline.daily                 # asof=today, live ingest
    python -m pipeline.daily --asof 2026-06-10 --no-live
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

import psycopg

from db.session import connect
from pipeline.call_for_thesis import call_for_thesis
from pipeline.ingest_thesis import NameResult, ingest_thesis
from repositories import calls_repo, thesis_repo


@dataclass
class ThesisRunResult:
    """Per-thesis outcome. ``recorded``: True = a new call-of-record was appended, False = unchanged (no
    row), None = the call step failed (see ``error``)."""

    thesis_id: UUID
    name: str
    ingested: list[NameResult] = field(default_factory=list)
    recorded: bool | None = None
    error: str | None = None


def run_daily(
    conn: psycopg.Connection,
    *,
    asof: date | None = None,
    known_at: datetime | None = None,
    allow_live: bool = True,
    force_refresh: bool = True,
    user_agent: str | None = None,
) -> list[ThesisRunResult]:
    """Run the daily pass over every thesis. ``asof`` defaults to today, ``known_at`` to now (a live read).
    Returns one ``ThesisRunResult`` per thesis. Never raises for a single thesis — failures are captured.

    ``force_refresh`` defaults to **True**: the daily path is recurring, so it re-pulls fresh bars (bypassing
    a stale cache hit) — otherwise the cron would re-ingest the same frozen cache every day and never see a
    new bar. It threads to the price source (``eod_loader.fetch_eod``).
    """
    asof = asof or date.today()
    out: list[ThesisRunResult] = []
    for thesis in thesis_repo.list_all(conn):
        res = ThesisRunResult(thesis_id=thesis.id, name=thesis.name)
        # (1) refresh facts — ingest_thesis already isolates per-name; wrap defensively so even a thesis-level
        # failure (e.g. a malformed thesis) is captured, not fatal. A fact failure does NOT block the call.
        try:
            res.ingested = ingest_thesis(
                conn,
                thesis.id,
                allow_live=allow_live,
                force_refresh=force_refresh,
                user_agent=user_agent,
            )
        except Exception as e:  # noqa: BLE001 — one thesis's ingest never aborts the cron
            conn.rollback()
            res.error = f"ingest: {e}"
        # (2)+(3) assemble today's call WITHOUT writing, then append only if it changed.
        try:
            card = call_for_thesis(conn, thesis.id, asof, known_at=known_at, record=False)
            res.recorded = calls_repo.record_if_changed(conn, card, thesis.tenant_id)
            conn.commit()
        except Exception as e:  # noqa: BLE001 — one thesis's call never aborts the cron
            conn.rollback()
            res.error = (f"{res.error}; " if res.error else "") + f"call: {e}"
        out.append(res)
    return out


def _report(results: list[ThesisRunResult]) -> int:
    """Print a per-thesis summary; return the number that errored (the process exit signal)."""
    appended = sum(1 for r in results if r.recorded)
    unchanged = sum(1 for r in results if r.recorded is False)
    errored = [r for r in results if r.error]
    for r in results:
        if r.error:
            mark = f"ERROR: {r.error}"
        elif r.recorded:
            mark = "call-of-record APPENDED"
        else:
            mark = "unchanged (no new row)"
        facts = sum(x.form4_appended + x.price_bars_appended for x in r.ingested)
        print(f"  {r.name}: +{facts} facts · {mark}")
    print(
        f"done: {len(results)} theses · {appended} appended · {unchanged} unchanged · "
        f"{len(errored)} errored"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Daily cron: refresh facts + append the call-of-record per thesis."
    )
    p.add_argument("--asof", default=None, help="as-of date YYYY-MM-DD (default: today)")
    p.add_argument("--no-live", action="store_true", help="cache-only ingest (no network)")
    args = p.parse_args(argv)
    asof = date.fromisoformat(args.asof) if args.asof else None

    conn = connect()
    try:
        results = run_daily(conn, asof=asof, allow_live=not args.no_live)
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a scheduler / wrapper, non-silently


if __name__ == "__main__":
    main()
