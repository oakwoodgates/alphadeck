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

ANY SECURITY, EXPLICIT (ticker, date) PAIRS (2026-10-21). The same repair for a thesis name whose stored
version is an early pull: ``--bars TICKER:YYYY-MM-DD[,...]`` (mixed pairs) or ``--ticker T --dates D[,...]``
(one ticker's dates). Never a cross product — only the pairs named are compared. The case it was built for,
MEASURED on prod (read-only, 2026-10-21): seven bars whose ONLY stored version was recorded before the
bar's 18:00 ET settle instant (the settle audit's history — ``pipeline/self_audit.py``):

    CBT, NEGG, APD 2026-07-09 (11:07 / 11:41 ET) · SCE-PG 2026-08-27 (14:09 and 16:42 ET)
    SCE-PG, ETHE, SAIC 2026-10-13 (17:04 - 17:08 ET, the boot catch-up)

What changes for a pair, and what does not:

- **Resolution** — the ticker is looked up in the tenant's master (read-only, never resolved or seeded); a
  ticker no row carries is reported missing. Each master row with that ticker is its own target (a
  duplicate row holding the same early pull gets the same repair, reported by id).
- **The vendor symbol is the nightly's** — ``price_symbol`` when the operator resolved one, else the ticker
  (``ingest_bars_for_security``'s rule), pulled through the same ``YahooPriceSource`` at the nightly price
  leg's own range (``ingest.prices.source.DEFAULT_RANGE``), live + force-refreshed into the throwaway cache.
- **A BASIS check before any append (both modes)** — the vendor's chart is split-adjusted, so after a split
  its value for an old date differs from ours by the split factor, and appending it for ONE date would plant
  a mixed-basis island (T4's own defect class). So every bar this would append is first checked against its
  neighbors: the stored latest close of each vendor session within ``_BASIS_SPAN_DAYS`` of it (excluding the
  dates being repaired) must agree with the vendor's within ``_BASIS_TOL`` (5%: a split re-bases every bar
  by its factor — 25% for the mildest 5:4 — while a settled close is revised by a fraction of a percent).
  A neighbor that disagrees marks the bar ``basis_differs``: reported, never appended — the tape's basis is
  the split list's job, not this repair's.
- Everything else is Q3's: the per-date scope guard, the exact OHLCV compare, append-only versions stamped
  ``now`` in one count-verified transaction, dry-run by default on a read-only connection, the
  call-of-record lock on ``--apply``, provenance (``<stamp>-price-bars.json``).

    python -m pipeline.repair_benchmark_bars --bars CBT:2026-07-09,SCE-PG:2026-08-27          # dry run
    python -m pipeline.repair_benchmark_bars --ticker SCE-PG --dates 2026-08-27,2026-10-13 --apply

A KNOWN FEED LIMITATION (0056). A name the operator marked "limited history" in Admin (the vendor serves no
history for its symbol — SCE-PG) can NEVER be repaired from the vendor: its ``no_vendor_bar`` is the
expected answer, not news. The report says so on that bar's line and in the summary, and the provenance's
skipped entry carries the mark's reason — the outcome itself is unchanged (still reported, never
fabricated, nothing appended). The marks are read once, read-only (``price_feed_repo.limited_history_marks``);
an UNMARKED name's ``no_vendor_bar`` reads exactly as before.
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
from ingest.prices.source import DEFAULT_RANGE as NIGHTLY_RANGE
from ingest.prices.source import PriceSource, YahooPriceSource
from pipeline.ingest_benchmarks import DEFAULT_RANGE
from pipeline.run_lock import EXIT_ALREADY_RUNNING, PassAlreadyRunning, acquire_repair_lock
from repositories.price_feed_repo import limited_history_marks
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
# BASIS_DIFFERS: the bar differs, but a NEIGHBOR's stored close disagrees with the vendor's too — the vendor
# tape is on another basis (a split re-base); appending would plant a mixed-basis bar, so it is skipped.
BASIS_DIFFERS = "basis_differs"
ACTIONS: tuple[str, ...] = (APPEND, MATCHES, NO_VENDOR_BAR, NO_STORED_BAR, BASIS_DIFFERS)

# The basis check (module docstring): neighbors within this many calendar days, and the relative close gap
# above which a neighbor says "another basis". 5%: a split moves every bar by its factor (>= 25% for 5:4),
# a settled close is revised by a fraction of a percent.
_BASIS_SPAN_DAYS = 7
_BASIS_TOL = 0.05

EXIT_REFUSED = 2  # the scope guard said no (argparse's usage-error code); nothing was written

RULE = "q3-benchmark-frozen-intraday"
RULE_BARS = "pre-settle-only-version"  # the explicit-pairs mode (2026-10-21)

MODE_BENCHMARKS = "benchmarks"
MODE_BARS = "bars"

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
    # BASIS_DIFFERS only: the neighbor that disagreed — "<date> stored <close> vs vendor <close>"
    basis: str | None = None


@dataclass(frozen=True)
class Target:
    """One security to repair: its label (the master ticker — with the id's first 8 hex when several rows
    share the ticker), its id, the VENDOR symbol it is priced under (``price_symbol or ticker``, the
    nightly's rule) and the session dates named for it."""

    ticker: str
    security_id: UUID
    symbol: str
    dates: tuple[date, ...]


@dataclass
class RepairResult:
    """One invocation's outcome. ``recorded_at`` is the stamp the appended versions carry (``--apply`` with
    something to append); ``fetch_errors`` maps a ticker to its failed vendor pull (on ``--apply`` any such
    error means NOTHING was written); ``missing`` lists tickers with no master row. ``mode`` is
    ``benchmarks`` (``--dates``: SPY/IWM) or ``bars`` (explicit ``pairs``); ``range_`` the vendor range.
    """

    dates: list[date]
    applied: bool
    bars: list[BarRepair] = field(default_factory=list)
    appended: int = 0
    recorded_at: datetime | None = None
    fetch_errors: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    mode: str = MODE_BENCHMARKS
    pairs: list[tuple[str, date]] = field(default_factory=list)
    range_: str = DEFAULT_RANGE
    # 0056 — security_id -> the operator's KNOWN FEED LIMITATION note ("marked <date>: <reason>") for the
    # targets marked "limited history": their no_vendor_bar is expected, and the report says so
    limitations: dict[UUID, str] = field(default_factory=dict)

    def count(self, action: str) -> int:
        return sum(1 for b in self.bars if b.action == action)

    def limited(self, b: BarRepair) -> str | None:
        """The limitation note for ``b`` when it is a NO_VENDOR_BAR on a marked name, else None."""
        return self.limitations.get(b.security_id) if b.action == NO_VENDOR_BAR else None


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


def neighbor_dates(d: date, vendor: list[dict[str, Any]], *, exclude: Iterable[date]) -> list[date]:
    """PURE: the vendor's session dates within ``_BASIS_SPAN_DAYS`` of ``d`` — the basis check's
    neighbors — leaving out ``d`` and every date being repaired (an early pull is not a reference).
    """
    skip = set(exclude) | {d}
    return sorted(
        r["d"] for r in vendor if r["d"] not in skip and abs((r["d"] - d).days) <= _BASIS_SPAN_DAYS
    )


def basis_disagreement(
    neighbors: Iterable[date],
    *,
    vendor: list[dict[str, Any]],
    stored: dict[date, dict[str, Any]],
) -> str | None:
    """PURE: the first neighbor whose stored latest close and the vendor's differ by more than
    ``_BASIS_TOL`` (relative) — the vendor tape is on another basis — as ``"<date> stored <c> vs vendor
    <c>"``; None when every comparable neighbor agrees (or none is comparable: no evidence of a re-base).
    """
    by_d = {r["d"]: r for r in vendor}
    for n in sorted(neighbors):
        v, s = by_d.get(n), stored.get(n)
        if v is None or s is None or v.get("close") is None or s.get("close") is None:
            continue
        vc, sc = float(v["close"]), float(s["close"])
        if sc == 0 or abs(vc / sc - 1.0) > _BASIS_TOL:
            return f"{n} stored {sc:.6f} vs vendor {vc:.6f}"
    return None


def resolve_targets(
    conn: psycopg.Connection,
    pairs: Sequence[tuple[str, date]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> tuple[list[Target], list[str]]:
    """The explicit pairs -> one ``Target`` per master row carrying each ticker (READ-ONLY: a repair never
    resolves or seeds a security), with the dates named for that ticker, plus the tickers no row carries.
    The vendor symbol is ``price_symbol or ticker`` — what the nightly price leg pulls it under."""
    dates_by_ticker: dict[str, set[date]] = {}
    for t, d in pairs:
        dates_by_ticker.setdefault(t, set()).add(d)
    targets: list[Target] = []
    missing: list[str] = []
    with conn.cursor() as cur:
        for t in sorted(dates_by_ticker):
            cur.execute(
                "SELECT id, ticker, price_symbol FROM security_master "
                "WHERE tenant_id = %s AND ticker = %s ORDER BY recorded_at, id",
                (tenant_id, t),
            )
            rows = cur.fetchall()
            if not rows:
                missing.append(t)
            for r in rows:
                label = t if len(rows) == 1 else f"{t} ({str(r['id'])[:8]})"
                targets.append(
                    Target(
                        label,
                        r["id"],
                        r["price_symbol"] or r["ticker"],
                        tuple(sorted(dates_by_ticker[t])),
                    )
                )
    return targets, missing


def _pull(
    targets: Iterable[Target], source: PriceSource | None, *, range_: str = DEFAULT_RANGE
) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """The vendor's current bars per vendor SYMBOL (live, force-refreshed) + the pulls that failed, keyed by
    the target's ticker label. With no injected ``source`` the Yahoo adapter writes its cache into a
    throwaway temp dir, removed on return — the shared price cache is never touched."""
    bars: dict[str, list[dict]] = {}
    errors: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="alphadeck-q3-") as tmp:
        src = source or YahooPriceSource(cache_dir=Path(tmp), range_=range_)
        for t in targets:
            if t.symbol in bars:
                continue
            try:
                bars[t.symbol] = src.get_bars(t.symbol, allow_live=True, force_refresh=True)
            except Exception as e:  # noqa: BLE001 — fail-visible: reported; --apply writes nothing
                errors[t.ticker] = f"{type(e).__name__}: {e}"
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
    targets = [Target(t, sid, t, tuple(wanted)) for t, sid in ids.items()]
    return _run(conn, targets, result, source=source, tenant_id=tenant_id, now=now)


def repair_bars(
    conn: psycopg.Connection,
    pairs: Sequence[tuple[str, date]],
    *,
    apply: bool = False,
    source: PriceSource | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    now: datetime | None = None,
) -> RepairResult:
    """The same repair for EXPLICIT ``(ticker, date)`` pairs on any security (the module docstring): the
    scope guard over every named date, the tickers resolved READ-ONLY in the master, each priced under its
    nightly vendor symbol at the nightly range, only the named pairs compared. Same contract as
    ``repair_benchmark_bars`` (``ScopeRefused`` before anything else; the caller owns the connection and,
    for ``apply``, the lock; all-or-nothing on a failed pull; a count-verified commit)."""
    started = datetime.now(timezone.utc)
    dates = [d for _, d in pairs]
    if not pairs:
        raise ScopeRefused(
            [
                "no bars given: pass --bars TICKER:YYYY-MM-DD[,...] or --ticker T --dates YYYY-MM-DD[,...]"
            ]
        )
    guard_now = now or db_now(conn)
    conn.rollback()
    refusals = scope_refusals(dates, now=guard_now)
    if refusals:
        raise ScopeRefused(refusals)
    result = RepairResult(
        dates=sorted(set(dates)),
        applied=apply,
        started_at=started,
        mode=MODE_BARS,
        pairs=sorted(set(pairs)),
        range_=NIGHTLY_RANGE,
    )
    targets, result.missing = resolve_targets(conn, pairs, tenant_id=tenant_id)
    conn.rollback()  # no transaction stays open across the network pull
    return _run(conn, targets, result, source=source, tenant_id=tenant_id, now=now)


def _run(
    conn: psycopg.Connection,
    targets: list[Target],
    result: RepairResult,
    *,
    source: PriceSource | None,
    tenant_id: UUID,
    now: datetime | None,
) -> RepairResult:
    """Both modes' shared body: pull, plan (with the basis check), and with ``result.applied`` append."""
    apply = result.applied
    wanted = result.dates
    # 0056 — the targets' known feed limitations, read once (read-only) before the pull
    result.limitations = {
        sid: f"marked {mark.created_at.date().isoformat()}: {mark.reason}"
        for sid, mark in limited_history_marks(
            conn, {t.security_id for t in targets}, tenant_id=tenant_id
        ).items()
    }
    conn.rollback()  # no transaction stays open across the network pull
    vendor, result.fetch_errors = _pull(targets, source, range_=result.range_)

    try:
        # read AFTER the pull: the stamp never predates the bars being in hand
        stamp = now or db_now(conn)
        for t in targets:
            if t.ticker in result.fetch_errors:
                continue
            bars = vendor[t.symbol]
            stored = stored_versions(conn, t.security_id, t.dates, tenant_id=tenant_id)
            planned = plan_repairs(t.ticker, t.security_id, t.dates, vendor=bars, stored=stored)
            # the BASIS check: a bar to append whose neighbors disagree with the vendor is another basis
            near = {
                b.d: neighbor_dates(b.d, bars, exclude=t.dates)
                for b in planned
                if b.action == APPEND
            }
            ref = stored_versions(
                conn,
                t.security_id,
                sorted({n for ns in near.values() for n in ns}),
                tenant_id=tenant_id,
            )
            for b in planned:
                why = (
                    basis_disagreement(near[b.d], vendor=bars, stored=ref)
                    if b.action == APPEND
                    else None
                )
                result.bars.append(
                    BarRepair(
                        b.ticker,
                        b.security_id,
                        b.d,
                        BASIS_DIFFERS,
                        b.stored,
                        b.vendor,
                        b.changed,
                        why,
                    )
                    if why
                    else b
                )
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
    bars_mode = result.mode == MODE_BARS
    return {
        "kind": "repair_price_bars" if bars_mode else "repair_benchmark_bars",
        "rule": f"{RULE_BARS if bars_mode else RULE}@{get_settings().image_sha or 'unknown'}",
        "applied": result.applied,
        "dates": [d.isoformat() for d in result.dates],
        "pairs": [{"ticker": t, "d": d.isoformat()} for t, d in result.pairs],
        "started_at": _jsonable(result.started_at),
        "finished_at": _jsonable(result.finished_at),
        "recorded_at": _jsonable(result.recorded_at),
        "vendor": {"source": "yahoo chart (PriceSource seam)", "range": result.range_},
        "summary": {
            "appended": result.appended,
            **{a: result.count(a) for a in ACTIONS},
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
            | ({"basis": b.basis} if b.basis else {})
            | ({"feed_limitation": result.limited(b)} if result.limited(b) else {})
            for b in result.bars
            if b.action != APPEND
        ],
        "fetch_errors": result.fetch_errors,
        ("missing_tickers" if bars_mode else "missing_benchmarks"): result.missing,
    }


def write_payload(payload: dict[str, Any], base_dir: Path | None = None) -> Path | None:
    """Write the provenance JSON to ``<base>/<utc-timestamp>-benchmark-bars.json`` (the explicit-pairs
    mode: ``-price-bars.json``); return the path, or ``None`` fail-open (the committed appends ARE the
    repair; a provenance fault is logged, never raised).
    """
    try:
        run_dir = base_dir or _DEFAULT_LOG_DIR
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = "price-bars" if payload.get("kind") == "repair_price_bars" else "benchmark-bars"
        path = run_dir / f"{stamp}-{suffix}.json"
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
    what = "row in the master with this ticker" if result.mode == MODE_BARS else "benchmark row"
    for t in result.missing:
        print(f"  {t}: no {what} — nothing stored to repair")
    for t, err in result.fetch_errors.items():
        print(f"  {t}: VENDOR PULL FAILED — {err}")
    for b in result.bars:
        verb = {
            APPEND: f"APPEND ({', '.join(b.changed)} differ)",
            MATCHES: "matches the vendor — nothing to append",
            NO_VENDOR_BAR: "the vendor has NO bar for this date — skipped (never fabricated)",
            NO_STORED_BAR: "nothing stored for this date — skipped (a hole is a backfill, not a repair)",
            BASIS_DIFFERS: (
                f"differs ({', '.join(b.changed)}), but the vendor tape is on ANOTHER BASIS around it "
                f"({b.basis}) — a split re-base? skipped, never appended (repair the basis first)"
            ),
        }[b.action]
        note = result.limited(b)
        if note:
            # 0056 — expected, not news: the operator marked this feed "the vendor serves no history"
            verb = (
                "the vendor has NO bar for this date — a KNOWN FEED LIMITATION: the vendor serves no "
                f"history for this symbol, so this bar can never be repaired from it ({note}) — skipped "
                "(never fabricated)"
            )
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
    basis = f" · basis differs {result.count(BASIS_DIFFERS)}" if result.count(BASIS_DIFFERS) else ""
    known = sum(1 for b in result.bars if result.limited(b))
    limited = f" ({known} a known feed limitation)" if known else ""
    print(
        f"SUMMARY: {verb} {n} · matches {result.count(MATCHES)} · no vendor bar "
        f"{result.count(NO_VENDOR_BAR)}{limited} · no stored bar {result.count(NO_STORED_BAR)}"
        f"{basis}{stamp}"
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _parse_dates(raw: str) -> list[date]:
    return [date.fromisoformat(p.strip()) for p in raw.split(",") if p.strip()]


def _parse_bars(raw: str) -> list[tuple[str, date]]:
    """``TICKER:YYYY-MM-DD[,TICKER:YYYY-MM-DD...]`` -> explicit pairs (the ticker upper-cased, as the master
    stores it). A malformed entry raises ``ValueError`` naming it."""
    out: list[tuple[str, date]] = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        ticker, sep, d = part.rpartition(":")
        if not sep or not ticker.strip():
            raise ValueError(f"{part!r} is not TICKER:YYYY-MM-DD")
        out.append((ticker.strip().upper(), date.fromisoformat(d.strip())))
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Append corrected versions of EOD bars that differ from the vendor's current bar, for "
        "EXPLICIT bars only (append-only, stamped now; dry-run by default): --dates alone = the SPY/IWM "
        "benchmarks (Q3); --bars TICKER:DATE,... or --ticker T --dates ... = any security's named bars."
    )
    p.add_argument(
        "--dates",
        default=None,
        help="comma-separated session dates, YYYY-MM-DD[,YYYY-MM-DD...] — explicit, never a range; alone = "
        "the benchmarks, with --ticker = that ticker's bars",
    )
    p.add_argument("--ticker", default=None, help="one ticker whose --dates to repair")
    p.add_argument(
        "--bars",
        default=None,
        help="explicit pairs TICKER:YYYY-MM-DD[,TICKER:YYYY-MM-DD...] (never a cross product)",
    )
    p.add_argument(
        "--apply", action="store_true", help="write (default: dry-run, read-only, print only)"
    )
    args = p.parse_args(argv)
    if args.bars is not None and (args.dates is not None or args.ticker is not None):
        p.error("--bars names its own pairs: do not combine it with --dates / --ticker")
    if args.bars is None and args.dates is None:
        p.error("pass --dates (benchmarks, or with --ticker) or --bars")
    pairs: list[tuple[str, date]] | None = None
    dates: list[date] = []
    try:
        if args.bars is not None:
            pairs = _parse_bars(args.bars)
        else:
            dates = _parse_dates(args.dates)
            if args.ticker is not None:
                pairs = [(args.ticker.strip().upper(), d) for d in dates]
    except ValueError as exc:
        p.error(f"{'--bars' if args.bars is not None else '--dates'}: {exc}")

    mode = "APPLY" if args.apply else "DRY-RUN (read-only connection; nothing written)"
    if pairs is None:
        scope = f"dates {', '.join(d.isoformat() for d in dates) or '—'}"
        rng = DEFAULT_RANGE
    else:
        scope = f"bars {', '.join(f'{t}:{d.isoformat()}' for t, d in pairs) or '—'}"
        rng = NIGHTLY_RANGE
    print(
        f"repair_benchmark_bars · {mode} · {scope} · "
        f"vendor: live Yahoo pull ({rng}, force-refresh, throwaway cache dir)"
    )
    try:
        lock = acquire_repair_lock(holder="repair") if args.apply else None
    except PassAlreadyRunning as exc:
        print(f"repair: ALREADY RUNNING — {exc}. Nothing was written (exit {EXIT_ALREADY_RUNNING})")
        raise SystemExit(EXIT_ALREADY_RUNNING) from None
    try:
        conn = connect()
        try:
            conn.read_only = not args.apply  # a dry run PHYSICALLY cannot write
            if pairs is None:
                result = repair_benchmark_bars(conn, dates, apply=args.apply)
            else:
                result = repair_bars(conn, pairs, apply=args.apply)
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
