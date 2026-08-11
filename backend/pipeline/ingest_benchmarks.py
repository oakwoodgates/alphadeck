"""Benchmark EOD backfill — pull SPY + IWM history into ``fact_price_eod`` (Signals §2.1).

A ONE-SHOT historical backfill: it seeds the benchmark master rows (``securities.benchmarks``) then
pulls each benchmark's EOD tape through the existing ``PriceSource`` seam and lands it with the
KNOWABILITY stamp the honest-replay contract requires — ``valid_from = recorded_at = the bar date d``
(``ingest_prices_backfill``), NEVER ``now()``. That is what lets a past-as-of backtest see each SPY/IWM
bar only on its own date.

Distinct from ``pipeline.ingest_thesis`` (the live per-thesis path, which leaves ``recorded_at = now()``
because it learns a bar today): a benchmark is not a thesis member, and its deep history is backdated on
purpose. Like the thesis ingest it is INCREMENTAL (only bars newer than the latest stored) and
FAIL-VISIBLE (one benchmark's error never aborts the other). It does NOT run the split re-version pass
(a broad-market ETF's adjusted tape is stable, and a lab one-shot backfill re-pulls the whole range if
it ever needs to) — kept deliberately minimal.

    python -m pipeline.ingest_benchmarks                 # live pull (needs ALPHADECK_USER_AGENT), ~5y
    python -m pipeline.ingest_benchmarks --range 10y     # deeper history for a longer backtest window
    python -m pipeline.ingest_benchmarks --no-live       # cache-only (no network)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from ingest.prices.eod_loader import ingest_prices_backfill, latest_bar_date
from ingest.prices.source import PriceSource, YahooPriceSource
from securities.benchmarks import seed_benchmarks

# a deep-but-bounded default for the lab: a broad-market ETF has liquid history well past this, and
# 5y comfortably covers the RS column's 13-week window plus a multi-year backtest sweep.
DEFAULT_RANGE = "5y"


@dataclass
class BenchmarkResult:
    """One benchmark's outcome: bars appended (the incremental tail, all backdated to their own bar
    date) and the captured error if the pull/append failed (fail-visible)."""

    ticker: str
    security_id: UUID
    bars_appended: int
    error: str | None = None


def ingest_benchmarks(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: PriceSource | None = None,
    range_: str = DEFAULT_RANGE,
) -> list[BenchmarkResult]:
    """Seed the benchmark rows then backfill each one's EOD history into ``fact_price_eod`` with
    ``valid_from = recorded_at = the bar date`` (``ingest_prices_backfill``). Incremental — only bars
    newer than the latest stored are appended, so a re-run appends ZERO (count-the-table safe). Each
    benchmark runs in its own try (COMMIT on success, ROLLBACK on failure) so one bad pull never
    discards the other's work. ``source`` is the swappable EOD source (defaults to Yahoo over ``range_``).
    Returns one ``BenchmarkResult`` per benchmark."""
    ids = seed_benchmarks(conn, tenant_id=tenant_id)
    conn.commit()
    src = source or YahooPriceSource(range_=range_)
    results: list[BenchmarkResult] = []
    for ticker, sid in ids.items():
        try:
            fresh = src.get_bars(ticker, allow_live=allow_live, force_refresh=force_refresh)
            last = latest_bar_date(conn, sid, tenant_id=tenant_id)
            new = [r for r in fresh if last is None or r["d"] > last]
            appended = ingest_prices_backfill(conn, sid, new, tenant_id=tenant_id)
            conn.commit()
            results.append(BenchmarkResult(ticker, sid, appended))
        except Exception as e:  # noqa: BLE001 — fail-visible: capture, roll back, keep going
            conn.rollback()
            results.append(BenchmarkResult(ticker, sid, 0, str(e)))
    return results


def _report(results: list[BenchmarkResult]) -> int:
    """Print a per-benchmark summary; return the number that errored (the process exit signal)."""
    errored = [r for r in results if r.error]
    for r in results:
        tail = f"   ERROR: {r.error}" if r.error else ""
        print(f"  {r.ticker}: +{r.bars_appended} bars (backdated to bar date){tail}")
    total = sum(r.bars_appended for r in results)
    print(f"done: {len(results)} benchmarks, +{total} price bars, {len(errored)} errored")
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill SPY + IWM EOD history into fact_price_eod for the RS column (Signals §2.1)."
    )
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache-only (no network); else live (needs ALPHADECK_USER_AGENT)",
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="re-pull live + overwrite the cache (bypass a stale cache hit)",
    )
    p.add_argument(
        "--range",
        default=DEFAULT_RANGE,
        help=f"Yahoo history range to pull (default {DEFAULT_RANGE}); e.g. 2y, 5y, 10y, max",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        results = ingest_benchmarks(
            conn,
            allow_live=not args.no_live,
            force_refresh=args.force_refresh,
            range_=args.range,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure to a wrapper, non-silently


if __name__ == "__main__":
    main()
