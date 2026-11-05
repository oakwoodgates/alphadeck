"""CLI for the fund launch radar (docs/FUND_RADAR.md) — scan the EDGAR daily index for new fund filings, store
the facts, and read the matches per card.

    python -m pipeline.fund_radar --days 5                 # scan the last 5 days (live), then the symbol files
    python -m pipeline.fund_radar --until 2026-11-04 --days 3
    python -m pipeline.fund_radar --since 2024-11-01       # BACKFILL: walks the days oldest -> newest, then
                                                           # marks the two symbol files ONCE (tonight)
    python -m pipeline.fund_radar --no-live                # cache-only (dev; uncached headers go on the errors)
    python -m pipeline.fund_radar --report [--window-days 90] [--include-private]   # read-only

``--report`` writes nothing and makes no request: per live card, the theme matches (fund, filing, public date,
stage, tier, the SIGNAL / BROAD terms that hit) and the name hits (company, token, fund), counted by FILING and by SPONSOR
(not by fund, so one batch filing cannot look like a wave), plus the totals.

Also runs nightly as a passenger leg of the daily cron (pipeline/daily.py — fail-open, its own connection).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import psycopg

from db.session import connect
from domain.market_time import market_today
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from radar import fund_match, fund_repo, fund_stage
from radar.fund_cards import load_cards
from radar.fund_match import CardInput
from radar.funds import FundRadarResult, run_fund_radar, run_symbol_legs


def build_report(
    launches: list[fund_stage.FundLaunch],
    cards: list[CardInput],
    *,
    today: date,
    window_days: int = 90,
    include_private: bool = False,
) -> str:
    cutoff = today - timedelta(days=window_days)
    window = [lc for lc in launches if lc.public_date >= cutoff]
    held_back = [lc for lc in window if lc.private_to_public and not include_private]
    lines = [f"Fund launch radar report — {cutoff} to {today} ({window_days} days)"]
    matched_keys: set[str] = set()

    for card in cards:
        ms = fund_match.match_launches(window, card, include_private=include_private)
        theme = [m for m in ms if m.theme is not None]
        names = [m for m in ms if m.names]
        matched_keys.update(m.launch.key for m in ms)
        lines.append("")
        lines.append(f"== {card.name}")
        sig = [m for m in theme if m.theme is not None and m.theme.tier == "signal"]
        brd = [m for m in theme if m.theme is not None and m.theme.tier == "broad"]
        n_sig = len({m.launch.accession for m in sig})
        n_brd = len({m.launch.accession for m in brd})
        lines.append(
            f"  theme: {_counts(theme)} (signal {n_sig} · broad {n_brd}) · name hits: {_counts(names)}"
        )
        for m in theme:
            lc, th = m.launch, m.theme
            assert th is not None
            hit_parts = []
            if th.signal:
                hit_parts.append(f"SIGNAL: {', '.join(th.signal)}")
            if th.broad:
                hit_parts.append(f"BROAD: {', '.join(th.broad)}")
            lines.append(
                f"  THEME [{th.tier}] {lc.public_date} {lc.name} [{lc.stage.current}] {lc.trust} "
                f"{lc.form} {lc.accession} — {' | '.join(hit_parts)}"
            )
        for m in names:
            lc = m.launch
            for h in m.names:
                link = f" ({h.link})" if h.link else ""
                lines.append(
                    f"  NAME  {lc.public_date} {lc.name} [{lc.stage.current}] {lc.trust} "
                    f"{lc.accession} — {h.ticker}{link}: '{h.token}' via {h.via}"
                )
        if not ms:
            lines.append("  (no launches fit this card in the window)")

    lines.append("")
    lines.append("== Totals")
    lines.append(f"  new funds in the window: {len(window)}")
    lines.append(f"  matched to at least one card: {len(matched_keys)}")
    lines.append(f"  unmatched: {len(window) - len(held_back) - len(matched_keys)}")
    lines.append(
        f"  private-to-public held back: {len(held_back)} funds in "
        f"{len({lc.accession for lc in held_back})} filings"
        + (" (shown: --include-private)" if held_back and not include_private else "")
    )
    return "\n".join(lines)


def _counts(ms: list[fund_match.CardMatch]) -> str:
    filings = {m.launch.accession for m in ms}
    sponsors = {m.launch.trust for m in ms}
    return f"{len(filings)} filings from {len(sponsors)} sponsors"


def _print_result(result: FundRadarResult, prefix: str = "fund radar") -> None:
    print(f"{prefix}: {result.summary}")
    if result.dates_skipped:
        print(f"  no-index days skipped: {', '.join(result.dates_skipped)}")
    for s in result.symbols_skipped:
        print(f"  symbol file skipped: {s}")
    for e in result.errors:
        print(f"  ERROR: {e}")


def backfill(conn: psycopg.Connection, since: date, until: date, *, allow_live: bool) -> int:
    """Walk ``since`` .. ``until`` oldest -> newest (weekends skipped: no index), then mark the two symbol files
    ONCE, tonight — neither carries a date, so a replay cannot date them any earlier."""
    client = EdgarClient(allow_live=allow_live, cache_ttl_s=RECURRING_CACHE_TTL_S)
    errors = 0
    d = since
    while d <= until:
        if d.weekday() < 5:
            r = run_fund_radar(
                conn,
                until=d,
                days=1,
                allow_live=allow_live,
                edgar_client=client,
                symbol_files=False,
            )
            _print_result(r, prefix=f"fund radar {d}")
            errors += len(r.errors)
        d += timedelta(days=1)
    tail = FundRadarResult()
    run_symbol_legs(
        conn,
        client,
        night=market_today(),
        allow_live=allow_live,
        nasdaq_fetch=None,
        result=tail,
    )
    _print_result(tail, prefix="fund radar symbols")
    return 1 if errors + len(tail.errors) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fund launch radar: scan + store + report")
    p.add_argument("--days", type=int, default=3, help="how many days back to scan (default 3)")
    p.add_argument(
        "--until", type=date.fromisoformat, default=None, help="last day (default today)"
    )
    p.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        help="backfill from this date through --until (oldest first; symbol files marked once, tonight)",
    )
    p.add_argument("--no-live", action="store_true", help="cache-only (no network)")
    p.add_argument("--report", action="store_true", help="read-only: matches per live card")
    p.add_argument("--window-days", type=int, default=90, help="report window (default 90)")
    p.add_argument(
        "--include-private",
        action="store_true",
        help="report: also match private-to-public filings",
    )
    args = p.parse_args(argv)

    conn = connect()
    try:
        if args.report:
            views = fund_repo.series_views(conn)
            launches = fund_stage.derive_launches(views, fund_repo.symbol_rows(conn))
            print(
                build_report(
                    launches,
                    load_cards(conn),
                    today=market_today(),
                    window_days=args.window_days,
                    include_private=args.include_private,
                )
            )
            return 0
        if args.since is not None:
            return backfill(
                conn, args.since, args.until or market_today(), allow_live=not args.no_live
            )
        result = run_fund_radar(conn, until=args.until, days=args.days, allow_live=not args.no_live)
        _print_result(result)
        return 1 if result.errors else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
