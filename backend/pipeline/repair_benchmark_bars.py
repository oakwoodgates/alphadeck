"""``pipeline.repair_benchmark_bars`` — Q3: append corrected versions of benchmark bars a market-hours pass
froze, for EXPLICITLY named session dates only.

WHAT IT REPAIRS. Before D1 + D2 (#393) a daytime pass stored the vendor's in-progress bar as a close, and the
nightly SPY/IWM leg had no re-version pass, so two such bars per benchmark were the only versions ever held
(MEASURED on prod, read-only, 2026-10-16 — one version each, ``recorded_at = d 00:00 UTC``):

    SPY 2026-08-25  close 765.270020 (vendor final 765.909973)  volume 13.59M (27.42M)
    SPY 2026-08-27  close 770.820129 (vendor final 771.099976)  volume 18.63M (34.56M)
    IWM 2026-08-25  close 298.665009 (vendor final 299.230011)  volume  8.43M (14.62M)
    IWM 2026-08-27  close 299.559998 (vendor final 299.809998)  volume  9.55M (13.66M)

The operator approved repairing exactly those two days (Q3, 2026-10-16). The other SPY/IWM bars that differ
from the vendor (small, upward volume-only revisions after the 22:30 pull) are deliberately out of scope —
the CLI touches only the ``--dates`` it is given, and D1's nightly window (5 sessions) never reaches back.

WHAT IT CANNOT CHANGE. The benchmark tape feeds ONLY the display-only relative-strength column:
``PointInTimeData.benchmark_prices`` is called only from ``signals/display/relative_strength.py``, it is not on
the detectors' ``SignalPointInTimeData`` protocol, the replay PIT has no such method, display signals are
computed on read and never persisted on a CallCard, and no basket member is a benchmark row (they are
reference securities, ``securities.benchmarks``). So a repair re-records NO call and moves no Scoreboard
number; only the RS column's view of those dates changes.

HOW — the shape of D1's restatement, by construction:

- **Source** — the ``PriceSource`` seam (``YahooPriceSource`` at the benchmark leg's own ``DEFAULT_RANGE``),
  ``allow_live=True, force_refresh=True``: the vendor's CURRENT bar, never a stale cache hit. Its cache
  directory is a THROWAWAY temp dir removed on exit, so the repair (dry run or apply) never rewrites the
  shared price cache the nightly and the dev tools read. A date the vendor has no bar for is reported and
  skipped — never fabricated.
- **Compare** — every OHLCV field, under the tolerance the restatement compare uses
  (``ingest.prices.ingest_security._moved``, the per-field rule ``_restated`` is built from). A stored bar
  that already matches appends nothing; one that differs in ANY field gets the vendor's whole bar, so
  open/high/low are corrected along with close and volume. A date with no stored bar is skipped (filling a
  hole is a backfill, not a repair).
- **Write** — ``eod_loader.ingest_prices(..., recorded_at=now)``: the same writer D1's restatement uses, so
  ``valid_from = d`` and a NEW version is INSERTED (never an UPDATE — the store is append-only). ``now`` is
  the DATABASE clock (``db.clock.db_now``, the one-clock rule), read after the vendor pull. All appends go
  in ONE transaction, verified by counting the stamped rows before the commit. A read pinned before the
  repair still sees the frozen value; a read after it sees the vendor's — transaction-time honesty.
- **Idempotent** — once applied, the stored bars match the vendor, so a second ``--apply`` appends zero.

SAFETY:

- **Dry-run is the DEFAULT.** It prints, per (benchmark, date), the stored version next to the vendor's
  current bar and what ``--apply`` would append. Its connection is READ ONLY, it takes no lock, and it writes
  no DB row, no provenance file and no shared price-cache file (the vendor pull's own cache file lands in
  the throwaway temp dir above and is removed on exit).
- **Scope guard** — only the ``securities.benchmarks`` rows (SPY, IWM; resolved read-only, never seeded),
  only the dates passed. It refuses an empty list, a weekend date, a date that is today or later in market
  time, and a date whose session has not settled (the D2 guard, ``ingest.prices.settle.is_settled``). With
  one clock the settle refusal can only fire alongside "today or later"; it stays as the price path's own
  rule, stated where the repair decides. A refusal writes nothing (exit 2).
- **The call-of-record lock** — ``--apply`` holds ``pipeline/run_lock.py``'s advisory lock for the whole run,
  so it cannot interleave with a nightly pass or a backfill; exit 75 when another process holds it.
- **Provenance** — ``--apply`` writes one JSON under ``data/repairs/`` (``/data/repairs`` in the container)
  listing each appended version: ticker, date, old -> new OHLCV, ``recorded_at``. Fail-open (the DB commit
  is the repair; a provenance fault is a logged warning).
- Run ``--apply`` on prod only after a labeled backup, from the deployed image.

    python -m pipeline.repair_benchmark_bars --dates 2026-08-25,2026-08-27           # dry run (default)
    python -m pipeline.repair_benchmark_bars --dates 2026-08-25,2026-08-27 --apply   # append the versions
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.clock import db_now
from db.session import DEFAULT_TENANT_ID, connect
from domain.market_time import market_tz
from domain.settings import get_settings
from ingest.prices.eod_loader import ingest_prices
from ingest.prices.ingest_security import _moved
from ingest.prices.settle import is_settled, settle_instant
from ingest.prices.source import PriceSource, YahooPriceSource
from pipeline.ingest_benchmarks import DEFAULT_RANGE
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_pass_lock
from securities.benchmarks import BENCHMARKS, benchmark_ids

_log = logging.getLogger("alphadeck.repair_benchmark_bars")

FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# Per-bar outcomes. APPEND: the stored version differs from the vendor's bar, so the vendor's is appended
# as a new version. MATCHES: already equal within the restatement tolerance, nothing to do. NO_VENDOR_BAR:
# the vendor has no bar for the date (a holiday, a gap), reported and skipped. NO_STORED_BAR: nothing is
# stored for the date; a hole is a backfill, not a repair, so it is skipped.
APPEND = "append"
MATCHES = "matches"
NO_VENDOR_BAR = "no_vendor_bar"
NO_STORED_BAR = "no_stored_bar"

EXIT_REFUSED = 2  # the scope guard said no (argparse's usage-error code); nothing was written

RULE = "q3-benchmark-frozen-intraday"

# Provenance lives beside the backfills' / annotations' (the repo's gitignored data/ == the container's /data).
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "repairs"


class ScopeRefused(ValueError):
    """The scope guard refused the request; ``reasons`` lists every refusal (one line per date)."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True)
class BarRepair:
    """One (benchmark, date): the latest stored version, the vendor's current bar, and the outcome.

    ``stored`` is the latest stored version (OHLCV + ``recorded_at`` + ``versions``, the count of versions
    held for the date); ``vendor`` is the vendor's bar (the row ``--apply`` writes); ``changed`` names the
    fields that differ (``APPEND`` only)."""

    ticker: str
    security_id: UUID
    d: date
    action: str
    stored: dict[str, Any] | None
    vendor: dict[str, Any] | None
    changed: tuple[str, ...] = ()


@dataclass
class RepairResult:
    """One invocation's outcome. ``recorded_at`` is the stamp the appended versions carry (``--apply`` with
    something to append); ``fetch_errors`` maps a benchmark to its failed vendor pull (on ``--apply`` any
    such error means NOTHING was written); ``missing`` lists benchmarks with no master row."""

    dates: list[date]
    applied: bool
    bars: list[BarRepair] = field(default_factory=list)
    appended: int = 0
    recorded_at: datetime | None = None
    fetch_errors: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def count(self, action: str) -> int:
        return sum(1 for b in self.bars if b.action == action)


# --- the scope guard (pure) ------------------------------------------------------------------------------


def scope_refusals(dates: Sequence[date], *, now: datetime) -> list[str]:
    """PURE: every reason the guard refuses ``dates`` at ``now`` (an AWARE instant); empty = allowed.

    Refuses an empty list, and per date: a weekend (no session), today or later in market time (the newest
    bars are the nightly leg's), and a session that has not settled by ``now`` (D2's rule)."""
    if not dates:
        return [
            "no dates given: pass --dates YYYY-MM-DD[,YYYY-MM-DD...] (the repair never defaults to a range)"
        ]
    today = now.astimezone(market_tz()).date()
    out: list[str] = []
    for d in sorted(set(dates)):
        why: list[str] = []
        if d.weekday() >= 5:
            why.append("a weekend date (no session)")
        if d >= today:
            why.append(f"today or later (market today is {today})")
        if not is_settled(d, now=now):
            why.append(f"not settled (its close settles at {settle_instant(d).isoformat()})")
        if why:
            out.append(f"{d}: " + "; ".join(why))
    return out


# --- reads + the planner -----------------------------------------------------------------------------------


def stored_versions(
    conn: psycopg.Connection,
    security_id: UUID,
    dates: Iterable[date],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[date, dict[str, Any]]:
    """The latest stored version per requested date (OHLCV + ``recorded_at``) and how many versions the
    date holds. The SAME latest-version pick as the as-of read (``recorded_at DESC, id DESC``)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (d) d, open, high, low, close, volume, recorded_at, "
            "count(*) OVER (PARTITION BY d) AS versions FROM fact_price_eod "
            "WHERE tenant_id = %s AND security_id = %s AND d = ANY(%s) "
            "ORDER BY d, recorded_at DESC, id DESC",
            (tenant_id, security_id, list(dates)),
        )
        return {r["d"]: dict(r) for r in cur.fetchall()}


def plan_repairs(
    ticker: str,
    security_id: UUID,
    dates: Iterable[date],
    *,
    vendor: list[dict[str, Any]],
    stored: dict[date, dict[str, Any]],
) -> list[BarRepair]:
    """PURE: the outcome per requested date. A bar is APPENDed only when the vendor has one, the store has
    one, and at least one OHLCV field moved beyond the restatement tolerance (``_moved``)."""
    by_d = {r["d"]: r for r in vendor}
    out: list[BarRepair] = []
    for d in sorted(set(dates)):
        v, s = by_d.get(d), stored.get(d)
        if v is None:
            out.append(BarRepair(ticker, security_id, d, NO_VENDOR_BAR, s, None))
        elif s is None:
            out.append(BarRepair(ticker, security_id, d, NO_STORED_BAR, None, v))
        else:
            changed = tuple(f for f in FIELDS if _moved(v, s, f))
            out.append(
                BarRepair(ticker, security_id, d, APPEND if changed else MATCHES, s, v, changed)
            )
    return out


def _pull(
    tickers: Iterable[str], source: PriceSource | None
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """The vendor's current bars per benchmark (live, force-refreshed) + the pulls that failed. With no
    injected ``source`` the Yahoo adapter writes its cache into a throwaway temp dir, removed on return —
    the shared price cache is never touched."""
    bars: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="alphadeck-q3-") as tmp:
        src = source or YahooPriceSource(cache_dir=Path(tmp), range_=DEFAULT_RANGE)
        for t in tickers:
            try:
                bars[t] = src.get_bars(t, allow_live=True, force_refresh=True)
            except Exception as e:  # noqa: BLE001 — fail-visible: reported; --apply writes nothing
                errors[t] = f"{type(e).__name__}: {e}"
    return bars, errors


def _count_stamped(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    dates: Iterable[date],
    stamp: datetime,
    *,
    tenant_id: UUID,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM fact_price_eod WHERE tenant_id = %s AND security_id = ANY(%s) "
            "AND d = ANY(%s) AND recorded_at = %s",
            (tenant_id, list(security_ids), list(dates), stamp),
        )
        return cur.fetchone()["n"]


# --- the run -----------------------------------------------------------------------------------------------


def repair_benchmark_bars(
    conn: psycopg.Connection,
    dates: Sequence[date],
    *,
    apply: bool = False,
    source: PriceSource | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    now: datetime | None = None,
) -> RepairResult:
    """Plan (and with ``apply``, append) the corrected versions for ``dates`` across the benchmarks.

    Raises ``ScopeRefused`` before reading or writing anything else. ``now`` is the guard's clock AND the
    appended versions' ``recorded_at``; default the DATABASE clock (read once for the guard, then again
    right after the vendor pull for the stamp). Injectable (AWARE) so a test can pin the instant.

    The caller owns the connection (a dry run should hand in a READ ONLY one) and, for ``apply``, the
    call-of-record lock. Every path ends its transaction: ``apply`` commits the appends after checking the
    stamped count (a mismatch rolls back and raises); everything else rolls back. On ``apply`` a failed
    vendor pull for ANY benchmark writes nothing (``fetch_errors`` says which)."""
    started = datetime.now(timezone.utc)
    if not dates:
        raise ScopeRefused(scope_refusals(dates, now=started))
    guard_now = now or db_now(conn)
    conn.rollback()
    refusals = scope_refusals(dates, now=guard_now)
    if refusals:
        raise ScopeRefused(refusals)
    wanted = sorted(set(dates))
    result = RepairResult(dates=wanted, applied=apply, started_at=started)

    ids = benchmark_ids(conn, tenant_id=tenant_id)  # read-only: a repair never seeds the master
    conn.rollback()  # no transaction stays open across the network pull
    result.missing = [t for t in BENCHMARKS if t not in ids]
    vendor, result.fetch_errors = _pull(ids, source)

    try:
        # read AFTER the pull: the stamp never predates the bars being in hand
        stamp = now or db_now(conn)
        for ticker, sid in ids.items():
            if ticker in result.fetch_errors:
                continue
            stored = stored_versions(conn, sid, wanted, tenant_id=tenant_id)
            result.bars += plan_repairs(ticker, sid, wanted, vendor=vendor[ticker], stored=stored)
        todo = [b for b in result.bars if b.action == APPEND]
        if apply and todo and not result.fetch_errors:
            for b in todo:  # D1's writer: valid_from = d, an INSERT stamped now — never an UPDATE
                result.appended += ingest_prices(
                    conn, b.security_id, [b.vendor], tenant_id=tenant_id, recorded_at=stamp
                )
            landed = _count_stamped(
                conn, {b.security_id for b in todo}, wanted, stamp, tenant_id=tenant_id
            )
            if landed != len(todo) or result.appended != len(todo):
                raise RuntimeError(
                    f"stamped-row count mismatch (planned {len(todo)}, wrote {result.appended}, "
                    f"found {landed}) — ROLLED BACK, nothing written"
                )
            conn.commit()
            result.recorded_at = stamp
        else:
            conn.rollback()
    except BaseException:
        conn.rollback()
        result.appended = 0
        raise
    result.finished_at = datetime.now(timezone.utc)
    return result


# --- provenance + report -----------------------------------------------------------------------------------


def _jsonable(x: Any) -> Any:
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    if x is None or isinstance(x, (bool, int, str)):
        return x
    return float(x)  # Decimal / float


def _bar_json(bar: dict[str, Any] | None, *, extra: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {k: _jsonable(bar.get(k)) for k in (*FIELDS, *extra)}


def build_payload(result: RepairResult) -> dict[str, Any]:
    """The provenance payload — PURE (no I/O)."""
    return {
        "kind": "repair_benchmark_bars",
        "rule": f"{RULE}@{get_settings().image_sha or 'unknown'}",
        "applied": result.applied,
        "dates": [d.isoformat() for d in result.dates],
        "started_at": _jsonable(result.started_at),
        "finished_at": _jsonable(result.finished_at),
        "recorded_at": _jsonable(result.recorded_at),
        "vendor": {"source": "yahoo chart (PriceSource seam)", "range": DEFAULT_RANGE},
        "summary": {
            "appended": result.appended,
            **{a: result.count(a) for a in (APPEND, MATCHES, NO_VENDOR_BAR, NO_STORED_BAR)},
        },
        "appended": [
            {
                "ticker": b.ticker,
                "security_id": str(b.security_id),
                "d": b.d.isoformat(),
                "valid_from": b.d.isoformat(),
                "recorded_at": _jsonable(result.recorded_at),
                "changed": list(b.changed),
                "old": _bar_json(b.stored, extra=("recorded_at", "versions")),
                "new": _bar_json(b.vendor),
            }
            for b in result.bars
            if b.action == APPEND and result.recorded_at is not None
        ],
        "skipped": [
            {"ticker": b.ticker, "d": b.d.isoformat(), "reason": b.action}
            for b in result.bars
            if b.action != APPEND
        ],
        "fetch_errors": result.fetch_errors,
        "missing_benchmarks": result.missing,
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-benchmark-bars.json``; return the path, or
    ``None`` fail-open (the committed appends ARE the repair; a provenance fault is logged, never raised).
    """
    try:
        run_dir = base_dir or _DEFAULT_LOG_DIR
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = run_dir / f"{stamp}-benchmark-bars.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract
        _log.exception("repair provenance write failed (fail-open — the appends are committed)")
        return None


def _fmt(bar: dict[str, Any] | None) -> str:
    if bar is None:
        return "—"
    px = " ".join(
        f"{k[0].upper()} {float(bar[k]):.6f}" if bar.get(k) is not None else f"{k[0].upper()} —"
        for k in ("open", "high", "low", "close")
    )
    vol = bar.get("volume")
    return f"{px}  V {int(float(vol)):,}" if vol is not None else f"{px}  V —"


def report(result: RepairResult) -> None:
    """Print the per-bar comparison and the summary."""
    for t in result.missing:
        print(f"  {t}: no benchmark row in the master — nothing stored to repair")
    for t, err in result.fetch_errors.items():
        print(f"  {t}: VENDOR PULL FAILED — {err}")
    for b in result.bars:
        verb = {
            APPEND: f"APPEND ({', '.join(b.changed)} differ)",
            MATCHES: "matches the vendor — nothing to append",
            NO_VENDOR_BAR: "the vendor has NO bar for this date — skipped (never fabricated)",
            NO_STORED_BAR: "nothing stored for this date — skipped (a hole is a backfill, not a repair)",
        }[b.action]
        print(f"  {b.ticker} {b.d}  {verb}")
        if b.stored is not None:
            print(
                f"      stored  {_fmt(b.stored)}  (latest of {b.stored['versions']} version(s), "
                f"recorded_at {b.stored['recorded_at'].isoformat()})"
            )
        if b.vendor is not None:
            print(f"      vendor  {_fmt(b.vendor)}")
    verb = "appended" if result.applied else "would append"
    n = result.appended if result.applied else result.count(APPEND)
    stamp = f" · recorded_at {result.recorded_at.isoformat()}" if result.recorded_at else ""
    print(
        f"SUMMARY: {verb} {n} · matches {result.count(MATCHES)} · no vendor bar "
        f"{result.count(NO_VENDOR_BAR)} · no stored bar {result.count(NO_STORED_BAR)}{stamp}"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _parse_dates(raw: str) -> list[date]:
    return [date.fromisoformat(p.strip()) for p in raw.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Q3: append corrected versions of SPY/IWM bars that differ from the vendor's current "
        "bar, for the EXPLICIT --dates only (append-only, stamped now). Dry-run by default."
    )
    p.add_argument(
        "--dates",
        required=True,
        help="comma-separated session dates, YYYY-MM-DD[,YYYY-MM-DD...] — explicit, never a range",
    )
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, print only)"
    )
    args = p.parse_args(argv)
    try:
        dates = _parse_dates(args.dates)
    except ValueError as exc:
        p.error(f"--dates: {exc}")

    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    print(
        f"repair_benchmark_bars · {mode} · dates {', '.join(d.isoformat() for d in dates) or '—'} · "
        f"vendor: live Yahoo pull ({DEFAULT_RANGE}, force-refresh, throwaway cache dir)"
    )
    try:
        lock = acquire_pass_lock(holder="repair") if args.apply else None
    except PassAlreadyRunning as exc:
        print(f"repair: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})")
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            result = repair_benchmark_bars(conn, dates, apply=args.apply)
        except ScopeRefused as exc:
            print("REFUSED (nothing was written):")
            for line in exc.reasons:
                print(f"  {line}")
            raise SystemExit(EXIT_REFUSED) from None
        finally:
            conn.close()
    finally:
        if lock is not None:
            lock.release()

    report(result)
    if args.apply:
        print(f"provenance: {write_payload(build_payload(result))}")
    if result.fetch_errors:
        if args.apply:
            print(
                "ABORTED: a vendor pull failed — nothing was written (re-run when the vendor answers)"
            )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
