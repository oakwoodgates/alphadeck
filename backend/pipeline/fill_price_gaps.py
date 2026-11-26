"""``pipeline.fill_price_gaps`` — the price gap fill (W6-3b) run by hand, over the basket union or named tickers.

The nightly price leg fills a session the vendor served no close for from Massive.com's raw bar, under one rule
(``ingest/prices/gap_fill.py``), looking back ``Settings.price_gap_fill_split_sessions`` sessions. This CLI runs
the SAME rule for holes older than that window (``--since``) and lists what it would do:

- **dry run (default)** — a READ-ONLY connection: per name, the planned fills (session, raw close, volume, why)
  and every gap the rule refuses ("not fillable by rule": before the newest split's first session, on/after a
  pending split's, no split in force, past the window, no Massive.com trade). Nothing is written; the vendor's
  pull is cache-first, Massive.com is read once per name that has a fillable gap (at most ``--max-names``).
- **``--apply``** — takes the call-of-record lock (``pipeline.run_lock``, like ``detect_splits --apply``) and
  refuses inside the nightly window (exit 75), then runs the price leg itself
  (``ingest_bars_for_security`` with the widened fill), each company under its write lock, one transaction each:
  the vendor's own bars land exactly as the nightly would land them, the fills with their ``price_bar_fill`` rows.

    python -m pipeline.fill_price_gaps --ticker SNDK                    # dry run, the nightly's window
    python -m pipeline.fill_price_gaps --ticker COSM --since 2026-11-01 # dry run, an older hole
    python -m pipeline.fill_price_gaps --since 2026-11-01 --apply       # the basket union, written

Exit codes: 0 done (or nothing to do); 2 refused (a bad argument; nothing written); 75 the nightly window or a
pass running (nothing written).
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import psycopg

from domain.market_time import market_now, market_tz
from domain.scopes import DATA_TENANT_ID
from domain.security import Security
from domain.settings import get_settings
from domain.splits import applicable
from domain.splits import pending as pending_splits
from ingest.massive_bars import default_bar_source
from ingest.prices.eod_loader import latest_bar_date, stored_bars
from ingest.prices.gap_fill import SCOPE_OFF, SCOPES, FillPlan, GapSource
from ingest.prices.ingest_security import BarsResult, ingest_bars_for_security, listed_splits
from ingest.prices.settle import withhold_closed_days, withhold_unsettled
from ingest.prices.source import PriceSource, YahooPriceSource
from pipeline.backfill_splits import Member, member_of, union_members
from pipeline.onboarding import window_bounds
from pipeline.schedule import parse_run_at

EXIT_REFUSED = 2
EXIT_BUSY = 75


@dataclass
class NamePlan:
    """One company's planned fills (a dry run) or what the price leg wrote (``--apply``)."""

    member: Member
    plan: FillPlan | None = None
    written: BarsResult | None = None
    error: str | None = None


def members_of(conn: psycopg.Connection, tickers: Iterable[str] | None) -> list[Member]:
    """The active basket union (``tickers`` narrows it); a named ticker outside every basket is read off the
    master (the data tenant) by its ticker or vendor price symbol."""
    want = [t.strip().upper() for t in tickers or () if t and t.strip()]
    out = union_members(conn, tickers=want or None)
    found = {s for m in out for s in m.symbols}
    missing = [t for t in want if t not in found]
    if missing:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, id AS security_id, ticker, price_symbol, cik, "
                "ARRAY[]::text[] AS former_tickers FROM security_master "
                "WHERE tenant_id = %s AND (upper(ticker) = ANY(%s) OR upper(price_symbol) = ANY(%s)) "
                "ORDER BY ticker, id",
                (DATA_TENANT_ID, missing, missing),
            )
            out.extend(member_of(r) for r in cur.fetchall())
    return out


def plan_one(
    conn: psycopg.Connection,
    m: Member,
    *,
    gap: GapSource,
    source: PriceSource,
    now: datetime,
    allow_live: bool,
) -> FillPlan:
    """A dry run for one company: the price leg's own inputs (the vendor's settled, session-dated pull; the
    store; the split list) through the fill rule. SELECTs only."""
    symbol = m.price_symbol or m.ticker
    fresh, _ = withhold_unsettled(source.get_bars(symbol, allow_live=allow_live), now=now)
    fresh, _ = withhold_closed_days(fresh)
    last = latest_bar_date(conn, m.security_id, tenant_id=m.tenant_id)
    stored = stored_bars(conn, m.security_id, tenant_id=m.tenant_id) if last is not None else {}
    listed = listed_splits(conn, m.security_id, tenant_id=m.tenant_id)
    day = now.astimezone(market_tz()).date()
    return gap.plan(
        fresh,
        stored,
        applicable(listed, on=day),
        symbol=symbol,
        now=now,
        pending=pending_splits(listed, on=day),
    )


def apply_one(
    conn: psycopg.Connection,
    m: Member,
    *,
    gap: GapSource,
    source: PriceSource,
    now: datetime,
    allow_live: bool,
) -> BarsResult:
    """The price leg for one company with the widened fill, under its write lock; the caller commits."""
    from db.write_lock import acquire_company_lock

    sec = Security(
        id=m.security_id, tenant_id=m.tenant_id, ticker=m.ticker, price_symbol=m.price_symbol
    )
    with acquire_company_lock(conn, m.security_id, wait_s=get_settings().ingest_lock_wait_s):
        return ingest_bars_for_security(
            conn,
            sec,
            tenant_id=m.tenant_id,
            allow_live=allow_live,
            force_refresh=False,
            source=source,
            now=now,
            gap_source=gap,
        )


def run(
    conn: psycopg.Connection,
    members: list[Member],
    *,
    gap: GapSource,
    source: PriceSource,
    now: datetime,
    allow_live: bool,
    apply: bool,
) -> list[NamePlan]:
    """Each company in its own transaction: planned (rolled back) or applied (committed). A fault is that
    company's ``error``; the rest go on."""
    out: list[NamePlan] = []
    for m in members:
        row = NamePlan(m)
        if not (m.ticker or m.price_symbol):
            row.error = "no symbol to price"
            out.append(row)
            continue
        try:
            if apply:
                row.written = apply_one(
                    conn, m, gap=gap, source=source, now=now, allow_live=allow_live
                )
                conn.commit()
            else:
                row.plan = plan_one(conn, m, gap=gap, source=source, now=now, allow_live=allow_live)
                conn.rollback()
        except Exception as e:  # noqa: BLE001 — one company's fault is its own line
            conn.rollback()
            row.error = f"{type(e).__name__}: {e}"[:300]
        out.append(row)
    return out


def _num(v: Any) -> str:
    return "-" if v is None else f"{v:,}" if isinstance(v, int) else str(v)


def report(rows: list[NamePlan], *, apply: bool, out: Any = None) -> list[str]:
    """The printed plan (or what was written), one block per company with anything to say, then a totals line."""
    lines: list[str] = []
    fills = refused = deferred = errors = 0
    for r in rows:
        label = r.member.ticker or r.member.price_symbol or str(r.member.security_id)
        if r.error:
            errors += 1
            lines.append(f"  {label}: ERROR {r.error}")
            continue
        if apply and r.written is not None:
            w = r.written
            if w.filled or w.fill_deferred or w.fill_error:
                fills += w.filled
                deferred += int(w.fill_deferred)
                days = ", ".join(d.isoformat() for d in w.filled_dates)
                lines.append(
                    f"  {label}: filled {w.filled} ({days or 'none'})"
                    + (f" — {w.fill_split}" if w.fill_split else "")
                    + (" · DEFERRED (range-read cap)" if w.fill_deferred else "")
                    + (f" · {w.fill_error}" if w.fill_error else "")
                )
            continue
        p = r.plan
        if p is None or not (p.fills or p.refused or p.deferred or p.note):
            continue
        fills += len(p.fills)
        refused += len(p.refused)
        deferred += int(p.deferred)
        head = f"  {label}" + (f" — {p.split_label}" if p.split_label else " — no split in force")
        lines.append(head + (" · DEFERRED (range-read cap)" if p.deferred else ""))
        for f in p.fills:
            lines.append(
                f"      FILL {f.d.isoformat()}  close {f.raw_close}  volume {_num(f.raw_volume)}  "
                f"({f.reason}; Massive.com {f.vendor_symbol})"
            )
        for x in p.refused:
            lines.append(
                f"      not fillable by rule: {x.d.isoformat()} ({x.why.replace('_', ' ')})"
            )
        if p.note:
            lines.append(f"      Massive.com: {p.note}")
    verb = "filled" if apply else "planned"
    lines.append(
        f"PLAN: {len(rows)} name(s) · {fills} {verb} · {refused} not fillable by rule · {deferred} deferred · "
        f"{errors} error(s)"
    )
    for line in lines:
        print(line, file=out)
    return lines


def _inside_the_window() -> str | None:
    """Why an ``--apply`` may not start now (inside the nightly window), or None (``nightly_extraction``'s rule)."""
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


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> None:
    """The CLI. ``now`` (AWARE; default the UTC wall clock) is the pass's instant: the settle guard's clock and
    the end of the candidate sessions — a test pins it."""
    from db.session import connect
    from pipeline.run_lock import PassAlreadyRunning, acquire_pass_lock

    s = get_settings()
    p = argparse.ArgumentParser(
        description="W6-3b: fill sessions the price vendor served no close for from Massive.com's raw bar "
        "(the nightly's rule). Dry run (read-only) by default."
    )
    p.add_argument("--ticker", action="append", default=None, help="only this name (repeatable)")
    p.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        help="every session since this day (default: the nightly's window)",
    )
    p.add_argument(
        "--scope",
        choices=SCOPES,
        default=None,
        help=f"default: Settings.price_gap_fill ({s.price_gap_fill})",
    )
    p.add_argument(
        "--max-names",
        type=int,
        default=None,
        help=f"Massive.com range reads at most (default {s.price_gap_fill_max_names})",
    )
    p.add_argument("--apply", action="store_true", help="write (default: dry run, read-only)")
    p.add_argument(
        "--no-live", action="store_true", help="caches only (no Yahoo / Massive.com request)"
    )
    args = p.parse_args(argv)
    now = now or datetime.now(timezone.utc)
    if args.since is not None and args.since > now.astimezone(market_tz()).date():
        print(f"REFUSED: --since {args.since.isoformat()} is in the future (nothing was written)")
        raise SystemExit(EXIT_REFUSED)
    scope = args.scope or s.price_gap_fill
    if scope == SCOPE_OFF:
        print("fill_price_gaps: the fill is off (scope off). Nothing read or written.")
        return
    source = default_bar_source()
    if source is None:
        print(
            "fill_price_gaps: no MASSIVE_API_KEY — nothing to fill from. Nothing read or written."
        )
        return
    allow_live = not args.no_live
    gap = GapSource(
        source,
        scope=scope,
        split_sessions=s.price_gap_fill_split_sessions,
        max_names=s.price_gap_fill_max_names if args.max_names is None else args.max_names,
        allow_live=allow_live,
        since=args.since,
    )
    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    since = (
        args.since.isoformat()
        if args.since
        else f"the last {s.price_gap_fill_split_sessions} sessions"
    )
    print(f"fill_price_gaps · {mode} · scope {scope} · since {since}")
    lock = None
    if args.apply:
        why = _inside_the_window()
        if why is not None:
            print(f"fill_price_gaps: REFUSED, {why}. Nothing was written (exit {EXIT_BUSY})")
            raise SystemExit(EXIT_BUSY)
        try:
            lock = acquire_pass_lock(holder="fill_price_gaps")
        except PassAlreadyRunning as exc:
            print(
                f"fill_price_gaps: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_BUSY})"
            )
            raise SystemExit(EXIT_BUSY) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            members = members_of(conn, args.ticker)
            conn.rollback()
            rows = run(
                conn,
                members,
                gap=gap,
                source=YahooPriceSource(),
                now=now,
                allow_live=allow_live,
                apply=args.apply,
            )
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()
    report(rows, apply=args.apply)


if __name__ == "__main__":
    main()
