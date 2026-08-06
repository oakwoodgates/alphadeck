"""CLI for the SPAC Radar pass (slices 1+2) — scan the EDGAR daily index, persist transition
events, and match DA-class filings against every thesis's term set.

    python -m pipeline.spac_radar --days 10          # backfill/scan the last 10 days (live)
    python -m pipeline.spac_radar --until 2026-08-01 --days 5
    python -m pipeline.spac_radar --no-live          # cache-only (dev; skips uncached days)
    python -m pipeline.spac_radar --no-match         # events only (skip the term matching)

Also runs nightly as the daily cron's universe-level leg (pipeline/daily.py — fail-open, its own
accounting, never touches the per-thesis freeze counters)."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from db.session import connect
from radar.spac import run_spac_radar


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SPAC Radar: scan + persist + match")
    p.add_argument("--days", type=int, default=3, help="how many days back to scan (default 3)")
    p.add_argument(
        "--until", type=date.fromisoformat, default=None, help="last day (default today)"
    )
    p.add_argument("--no-live", action="store_true", help="cache-only (no network)")
    p.add_argument("--no-match", action="store_true", help="skip the term-set matching leg")
    args = p.parse_args(argv)

    conn = connect()
    try:
        result = run_spac_radar(
            conn,
            until=args.until,
            days=args.days,
            allow_live=not args.no_live,
            match=not args.no_match,
        )
    finally:
        conn.close()

    print(f"SPAC radar: {result.summary}")
    if result.shells_admitted:
        print(f"  shells admitted: {', '.join(result.shells_admitted)}")
    if result.dates_skipped:
        print(f"  no-index days skipped: {', '.join(result.dates_skipped)}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
