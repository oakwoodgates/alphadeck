"""Polygon HISTORICAL backfill for ``fact_fund_shares`` — flow lands with real history from day one.

The forward leg samples one count per sleeve per day, so the etf_flow display windows (1w/1m) would
otherwise take a month of cron nights to fill. This CLI walks Polygon's dated reference read back over
a range (daily across the last 90 days by default — both windows fully populated with margin) and
appends the historical samples. An EXPLICIT operator-run command, never ambient — the cost thread: the
free tier is 5 requests/min, so a full 90-day daily walk is ~91 spaced calls ≈ 20 minutes per sleeve
(deliberate spend; the forward daily sample stays the one ambient GET). A RE-RUN costs nothing: the
per-(ticker, date) cache serves it and the idempotent write appends nothing.

    python -m pipeline.backfill_fund_shares --thesis <uuid>
    python -m pipeline.backfill_fund_shares --thesis <uuid> --ticker URA --days 60 --cadence 2

Rules:
- **No-lookahead (#1).** Every row carries ``valid_from = d`` (the historical QUERIED date) and
  ``recorded_at = now()`` — never backdated — so a replay pinned before the backfill ran honestly sees
  none of it; the range end is capped at today (no future-dated samples).
- **Idempotent (count-the-table).** The write is the SAME ``ingest_fund_shares_for_security`` leg the
  forward path uses: an already-stored ``(d, count)`` appends nothing; a changed count re-versions.
- **Gaps skipped visibly (#9).** A null-count date (Polygon carries occasional holes — URA 2026-06-15,
  measured) is skipped and COUNTED: no row, no fake zero, no error. A real failure (bad key / plan /
  network) is a captured, visible error that stops that member's walk — no point spending ninety more
  spaced calls into a 401 — while the run continues to the next sleeve.
- **Polygon-only.** History is the one thing the scraper pair cannot give; without a key this refuses
  loudly at start.
- **Display-only (#5).** The rows feed the etf_flow display read; nothing here touches the call path.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

import psycopg

from db.session import connect
from domain.enums import InstrumentKind
from domain.settings import get_settings
from ingest.funds.ingest_security import ingest_fund_shares_for_security
from ingest.funds.polygon import PolygonFundSource
from ingest.funds.source import FundSharesUnavailable
from repositories import thesis_repo
from securities import master


@dataclass
class BackfillResult:
    """One sleeve's walk: what landed, what re-versioned, and the gap dates skipped (visible, never
    silent). ``error`` = the captured failure that stopped this member's walk (fail-visible)."""

    ticker: str | None
    security_id: UUID
    appended: int = 0
    reversioned: int = 0
    gaps_skipped: int = 0
    dates_queried: int = 0
    error: str | None = None


class _PinnedDateSource:
    """Adapts the dated polygon read onto the ``FundSharesSource`` shape the shared ingest leg takes,
    so the backfill reuses the ONE idempotent write (the same-(d, count) skip / re-version / provenance
    discipline — never a forked second writer)."""

    def __init__(self, polygon: PolygonFundSource, d: date) -> None:
        self._polygon = polygon
        self._d = d

    def get_snapshot(
        self, ticker: str, *, allow_live: bool = False, force_refresh: bool = False
    ) -> dict | None:
        return self._polygon.get_snapshot_at(
            ticker, self._d, allow_live=allow_live, force_refresh=force_refresh
        )


def backfill_dates(end: date, days: int, cadence: int) -> list[date]:
    """The dates to sample: END-ANCHORED (the newest date is always sampled — the display read cares
    most about the recent edge), walking back every ``cadence`` days to ``end - days``, returned
    oldest-first so partial progress accrues forward."""
    if days < 1 or cadence < 1:
        raise ValueError("days and cadence must both be >= 1")
    start = end - timedelta(days=days)
    out: list[date] = []
    d = end
    while d >= start:
        out.append(d)
        d -= timedelta(days=cadence)
    out.reverse()
    return out


def backfill_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    days: int = 90,
    cadence: int = 1,
    end: date | None = None,
    ticker: str | None = None,
    allow_live: bool = True,
    force_refresh: bool = False,
    polygon: PolygonFundSource | None = None,
) -> list[BackfillResult]:
    """Walk every ETF sleeve member of ``thesis_id`` across the date range (see the module docstring
    for the rules). ``ticker`` restricts to one sleeve; ``polygon`` is injectable for tests (defaults
    to the keyed adapter — no key raises loudly: the backfill is Polygon-only)."""
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis {thesis_id} not found")
    if polygon is None:
        key = get_settings().polygon_api_key
        if not key:
            raise RuntimeError(
                "POLYGON_API_KEY is not set — the backfill is Polygon-only (the scraper pair has no "
                "history). Put the key in .env / the environment and re-run."
            )
        polygon = PolygonFundSource(api_key=key)
    end = min(end or date.today(), date.today())  # never a future-dated sample (#1)
    dates = backfill_dates(end, days, cadence)
    results: list[BackfillResult] = []
    for m in thesis.basket:
        if m.security_id is None:
            continue  # unresolved placement — no exact member to backfill against
        sec = master.get(conn, m.security_id, tenant_id=thesis.tenant_id)
        if sec is None:
            results.append(BackfillResult(m.ticker, m.security_id, error="not in tenant master"))
            continue
        if sec.instrument_kind != InstrumentKind.ETF or not sec.ticker:
            continue  # the backfill targets ETF sleeves only — other members aren't part of this run
        if ticker is not None and sec.ticker.upper() != ticker.upper():
            continue
        res = BackfillResult(ticker=sec.ticker, security_id=sec.id)
        for d in dates:
            res.dates_queried += 1
            try:
                r = ingest_fund_shares_for_security(
                    conn,
                    sec,
                    tenant_id=thesis.tenant_id,
                    allow_live=allow_live,
                    force_refresh=force_refresh,
                    source=_PinnedDateSource(polygon, d),
                )
                conn.commit()  # per-date: a long spaced walk keeps its partial progress on a crash
                res.appended += r.appended
                res.reversioned += r.reversioned
            except FundSharesUnavailable:
                conn.rollback()
                res.gaps_skipped += (
                    1  # the null-count hole — no row, counted, never fabricated (#9)
                )
            except (
                Exception
            ) as e:  # noqa: BLE001 — fail-visible; a 401 would fail every later date too
                conn.rollback()
                res.error = f"{d.isoformat()}: {e}"
                break
        results.append(res)
    return results


def _report(results: list[BackfillResult]) -> int:
    """Per-sleeve summary; returns the number that errored (the process exit signal). Gaps and
    re-versions surface only when nonzero — loudness marks the exception."""
    if not results:
        print("no ETF sleeve members matched — nothing to backfill")
        return 0
    errored = [r for r in results if r.error]
    for r in results:
        gaps = (
            f", {r.gaps_skipped} gap dates skipped (no count on the wire)" if r.gaps_skipped else ""
        )
        rv = f", {r.reversioned} re-versioned (restated)" if r.reversioned else ""
        tail = f"   ERROR: {r.error}" if r.error else ""
        print(
            f"  {r.ticker or r.security_id}: +{r.appended} samples over {r.dates_queried} dates"
            f"{rv}{gaps}{tail}"
        )
    total = sum(r.appended for r in results)
    total_gaps = sum(r.gaps_skipped for r in results)
    print(
        f"done: {len(results)} sleeve(s), +{total} samples, {total_gaps} gaps skipped, "
        f"{len(errored)} errored"
    )
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Backfill historical ETF fund shares outstanding from Polygon (explicit operator "
        "spend: the free tier is 5 req/min, ~13s per uncached date; a re-run is free)."
    )
    p.add_argument("--thesis", required=True, help="thesis id (uuid)")
    p.add_argument("--ticker", default=None, help="restrict to ONE sleeve ticker within the thesis")
    p.add_argument(
        "--days", type=int, default=90, help="how far back from the end date (default 90)"
    )
    p.add_argument(
        "--cadence", type=int, default=1, help="sample every Nth calendar day (default 1 = daily)"
    )
    p.add_argument(
        "--end", default=None, help="range end YYYY-MM-DD (default today; capped at today)"
    )
    p.add_argument(
        "--no-live", action="store_true", help="cache-only (no network) — replays an earlier walk"
    )
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="re-pull live + overwrite cached dates (a deliberate re-spend)",
    )
    args = p.parse_args(argv)
    end = min(date.fromisoformat(args.end) if args.end else date.today(), date.today())
    n = len(backfill_dates(end, args.days, args.cadence))
    # `->` not `→`: the operator runs this CLI from a Windows console (cp1252), where a non-encodable
    # glyph crashes the banner print BEFORE any work — caught by the live e2e walk, not the tests
    # (pytest's capture is encoding-tolerant, a real console is not).
    print(
        f"backfilling {n} dates per sleeve, {end - timedelta(days=args.days)} -> {end} "
        f"(~{n * 13 / 60:.0f} min of live spacing per sleeve at the 5/min free tier; "
        "cached dates are free)"
    )
    conn = connect()
    try:
        results = backfill_thesis(
            conn,
            UUID(args.thesis),
            days=args.days,
            cadence=args.cadence,
            end=end,
            ticker=args.ticker,
            allow_live=not args.no_live,
            force_refresh=args.force_refresh,
        )
    finally:
        conn.close()
    if _report(results):
        raise SystemExit(1)  # surface partial failure non-silently


if __name__ == "__main__":
    main()
