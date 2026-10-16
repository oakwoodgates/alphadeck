"""Benchmark EOD ingest — the SPY + IWM tape in ``fact_price_eod`` (Signals §2.1), on TWO paths.

Seeds the benchmark master rows (``securities.benchmarks``) then pulls each benchmark's EOD tape through
the ``PriceSource`` seam. The tape feeds the relative-strength DISPLAY column (``benchmark_rs``) — not a
call input. Two callers, two transaction-time stamps (D1, 2026-10-16):

- **The RECURRING path — the nightly leg in ``pipeline.daily.run_daily_pass`` (the cron, the Admin "Run
  daily now", a boot catch-up) and the default here.** A new bar lands with ``recorded_at = now`` — the
  instant this system learned it, read from the DATABASE clock (``db.clock.db_now``) right after the pull,
  so it is the same clock that stamps every other ``recorded_at`` and that a live read's ``known_at``
  comes from (the one-clock rule). HONEST transaction time, the contract every thesis-name bar keeps.
  It used to reuse the one-shot backfill's stamp below, so every nightly SPY/IWM bar claimed
  ``recorded_at = D 00:00 UTC`` — about 26.5 h before it was really ingested, and before its own session
  opened. The visibility contract is unchanged for every honest reader: the bar for day D, ingested at
  ~22:35 ET on D, is inside ``known_at_for_asof(D)`` (the end of D's MARKET day — the serve-path
  scrub-back and the Scoreboard's dated overlays) and inside the night's own call (``known_at = now``,
  later), and outside every read with ``asof < D`` (``valid_from = D``) or pinned before it was ingested.
  (The replay/backtest read no benchmark bar at all — RS is display-only — and a public-clock mirror
  rewrites ``recorded_at`` to the bar date anyway.)
- **The ONE-SHOT historical backfill — ``python -m pipeline.ingest_benchmarks`` (this CLI).** Deep history
  lands with ``valid_from = recorded_at = the bar date d`` (``ingest_prices_backfill``): the documented
  replay contract — "an EOD bar is knowable that evening" — which is what lets a record-clock replay
  pinned in the past see each historical SPY/IWM bar on its own date instead of on the day the backfill
  ran (the no-backfill trap). Opt-in only (``historical_backfill=True``); nothing recurring backdates.

Both paths share the rest:

- **Settled bars only (D2)** — the fresh pull goes through ``ingest.prices.settle.withhold_unsettled``
  first, so today's in-progress bar is never stored as a close before ``Settings.price_settle_time``
  (MEASURED: a market-hours pass froze SPY 2026-08-25 at 765.27 against a final 765.91, and the same for
  08-27 and for IWM, each as the only version ever held). A withheld bar is appended by the next pass.
- **Incremental** — bars newer than the latest stored one append; a re-run appends ZERO
  (count-the-table safe).
- **The restatement re-version, inside a trailing WINDOW** — the thesis path's compare
  (``ingest.prices.ingest_security._restated``, tolerance unchanged) over the vendor's newest
  ``Settings.benchmark_restate_window`` settled sessions (default 5): a stored bar the vendor revised gets
  a NEW version stamped ``now`` (never backdated — a correction is news, and a backdated one would collide
  with a backdated original), and an unseen date inside the window backfills with the path's own stamp.
  Bars OLDER than the window are deliberately left alone — re-versioning those is the operator's call
  (Q3: the 08-25/08-27 frozen closes, and the nightly volumes the vendor revised after 22:30), never a
  nightly side effect. ``plan_benchmark_bars`` is the pure planner (what each pass would write), so a
  read-only dry run can show exactly that against a live store.
- **Fail-visible** — one benchmark's error never aborts the other (each commits or rolls back alone).

    python -m pipeline.ingest_benchmarks                 # one-shot backfill, live (needs ALPHADECK_USER_AGENT), ~5y
    python -m pipeline.ingest_benchmarks --range 10y     # deeper history for a longer backtest window
    python -m pipeline.ingest_benchmarks --no-live       # cache-only (no network)
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

import psycopg

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID, connect
from domain.settings import get_settings
from ingest.prices.eod_loader import ingest_prices, ingest_prices_backfill, stored_bars
from ingest.prices.ingest_security import _restated
from ingest.prices.settle import withhold_unsettled
from ingest.prices.source import PriceSource, YahooPriceSource
from securities.benchmarks import seed_benchmarks

# a deep-but-bounded default for the lab: a broad-market ETF has liquid history well past this, and
# 5y comfortably covers the RS column's 13-week window plus a multi-year backtest sweep.
DEFAULT_RANGE = "5y"


@dataclass
class BenchmarkResult:
    """One benchmark's outcome (fail-visible: ``error`` is the captured failure, if any).

    ``bars_appended`` = FIRST versions stored (the incremental tail, plus any date the vendor filled
    inside the restatement window); ``bars_restated`` = NEW versions of stored bars the vendor revised
    inside the window; ``bars_withheld`` = fresh bars not stored because their session had not settled
    (D2 — appended by the next pass)."""

    ticker: str
    security_id: UUID
    bars_appended: int
    error: str | None = None
    bars_restated: int = 0
    bars_withheld: int = 0


@dataclass(frozen=True)
class BenchmarkPlan:
    """What ONE pass would write for one benchmark — the pure output of ``plan_benchmark_bars``.

    ``tail`` = settled bars newer than the latest stored; ``holes`` = settled in-window dates with no
    stored bar; ``restated`` = in-window stored bars the vendor now reports differently (the new values);
    ``withheld`` = unsettled bars dropped (D2); ``window`` = the session dates compared (ascending).
    """

    tail: list[dict] = field(default_factory=list)
    holes: list[dict] = field(default_factory=list)
    restated: list[dict] = field(default_factory=list)
    withheld: int = 0
    window: list[date] = field(default_factory=list)


def plan_benchmark_bars(
    fresh: list[dict],
    stored: dict[date, dict],
    *,
    now: datetime,
    window_sessions: int,
) -> BenchmarkPlan:
    """PURE: split a fresh vendor pull against the stored tape into what a pass writes. ``stored`` is the
    latest stored version per date (``eod_loader.stored_bars`` — the as-of read's own dedup).

    The window is the vendor's newest ``window_sessions`` SETTLED session dates — "recent" as the market
    counts it, so weekends/holidays need no calendar — and only stored dates inside it are compared.
    ``window_sessions <= 0`` disables the compare (tail only). A date that is both new and in the window is
    TAIL (the window adds only the compare + the in-window holes)."""
    settled, withheld = withhold_unsettled(fresh, now=now)
    last = max(stored) if stored else None
    tail = [r for r in settled if last is None or r["d"] > last]
    if window_sessions <= 0 or last is None:
        return BenchmarkPlan(tail=tail, withheld=withheld)
    window = sorted({r["d"] for r in settled})[-window_sessions:]
    start = window[0] if window else None
    inside = [r for r in settled if start is not None and start <= r["d"] <= last]
    holes = [r for r in inside if r["d"] not in stored]
    restated = [r for r in inside if r["d"] in stored and _restated(r, stored[r["d"]])]
    return BenchmarkPlan(
        tail=tail, holes=holes, restated=restated, withheld=withheld, window=window
    )


def ingest_benchmarks(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: PriceSource | None = None,
    range_: str = DEFAULT_RANGE,
    historical_backfill: bool = False,
    window_sessions: int | None = None,
    now: datetime | None = None,
) -> list[BenchmarkResult]:
    """Seed the benchmark rows then ingest each one's EOD tape into ``fact_price_eod`` per
    ``plan_benchmark_bars``. FIRST versions (tail + in-window holes) are stamped ``recorded_at = now`` on
    the recurring path (the default) or at the bar date with ``historical_backfill=True`` (the one-shot CLI
    only — see the module docstring); RESTATED versions are always stamped ``now``.

    Incremental and window-bounded, so an identical re-run appends ZERO (count-the-table safe). Each
    benchmark runs in its own try (COMMIT on success, ROLLBACK on failure) so one bad pull never discards
    the other's work. ``source`` is the swappable EOD source (defaults to Yahoo over ``range_``);
    ``window_sessions`` defaults to ``Settings.benchmark_restate_window``.

    ``now`` is the pass's instant — the settle guard's clock AND the recurring stamp, so one instant decides
    "is it a close yet?" and records "when we learned it". Default (production): the DATABASE clock, read
    per benchmark right after its pull. Injectable (AWARE) so a test can pin "ingested at 22:35 ET on D".
    Returns one ``BenchmarkResult`` per benchmark."""
    window = get_settings().benchmark_restate_window if window_sessions is None else window_sessions
    ids = seed_benchmarks(conn, tenant_id=tenant_id)
    conn.commit()
    src = source or YahooPriceSource(range_=range_)
    results: list[BenchmarkResult] = []
    for ticker, sid in ids.items():
        try:
            fresh = src.get_bars(ticker, allow_live=allow_live, force_refresh=force_refresh)
            # read AFTER the pull: the stamp must not predate the moment the bars were in hand
            stamp = now or db_now(conn)
            plan = plan_benchmark_bars(
                fresh,
                stored_bars(conn, sid, tenant_id=tenant_id),
                now=stamp,
                window_sessions=window,
            )
            first_versions = plan.tail + plan.holes
            if historical_backfill:
                appended = ingest_prices_backfill(conn, sid, first_versions, tenant_id=tenant_id)
            else:
                appended = ingest_prices(
                    conn, sid, first_versions, tenant_id=tenant_id, recorded_at=stamp
                )
            # a restatement is news learned NOW on either path — never backdated (a backdated correction
            # would also collide with a backdated original on the (security, d, recorded_at) key)
            restated = ingest_prices(
                conn, sid, plan.restated, tenant_id=tenant_id, recorded_at=stamp
            )
            conn.commit()
            results.append(
                BenchmarkResult(
                    ticker,
                    sid,
                    appended,
                    bars_restated=restated,
                    bars_withheld=plan.withheld,
                )
            )
        except Exception as e:  # noqa: BLE001 — fail-visible: capture, roll back, keep going
            conn.rollback()
            results.append(BenchmarkResult(ticker, sid, 0, str(e)))
    return results


def _report(results: list[BenchmarkResult], *, historical_backfill: bool) -> int:
    """Print a per-benchmark summary; return the number that errored (the process exit signal). The
    restated / withheld counts ride only when nonzero (loudness marks the exception)."""
    errored = [r for r in results if r.error]
    stamp = "backdated to bar date" if historical_backfill else "recorded now"
    for r in results:
        tail = f"   ERROR: {r.error}" if r.error else ""
        rs = f", {r.bars_restated} RESTATED (vendor revised)" if r.bars_restated else ""
        wh = f", {r.bars_withheld} unsettled bar(s) withheld" if r.bars_withheld else ""
        print(f"  {r.ticker}: +{r.bars_appended} bars ({stamp}){rs}{wh}{tail}")
    total = sum(r.bars_appended for r in results)
    print(f"done: {len(results)} benchmarks, +{total} price bars, {len(errored)} errored")
    return len(errored)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="One-shot backfill of SPY + IWM EOD history into fact_price_eod for the RS column "
        "(Signals §2.1) — first versions are backdated to their bar date (the replay contract)."
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
        # the ONE caller that backdates: this CLI is the explicit historical backfill (module docstring)
        results = ingest_benchmarks(
            conn,
            allow_live=not args.no_live,
            force_refresh=args.force_refresh,
            range_=args.range,
            historical_backfill=True,
        )
    finally:
        conn.close()
    if _report(results, historical_backfill=True):
        raise SystemExit(1)  # surface partial failure to a wrapper, non-silently


if __name__ == "__main__":
    main()
