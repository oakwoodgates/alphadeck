"""The per-security price leg — ONE implementation, two callers (the decoupling the finalize screen needed).

Moved verbatim out of ``pipeline.ingest_thesis._price_leg`` so the price ingest is callable OUTSIDE the
per-thesis back-half loop: the Workbench's finalize screen pulls bars per name / per section BEFORE the
operator promotes (real market caps inform the finalize decisions), while
``pipeline.ingest_thesis`` and the daily cron keep calling the same function inside their loops. One
implementation means the invariants can't fork:

- **Incremental** — only bars newer than the latest stored one are appended; a re-run of a current name
  appends ZERO rows (the append-only table never silently grows; COUNT-the-table guarded).
- **Cache-first** — ``force_refresh=False`` (the interactive default) serves a same-day re-click from the
  cache; a FIRST pull on a fresh name is a cache miss and fetches live. The recurring/daily path passes
  ``force_refresh=True`` (the #72 stale-cache rule) — that dial stays the caller's.
- **No-lookahead** — ``recorded_at`` stays the DB default ``now()``; nothing is backdated.
- **RE-VERSION on restatement (source-strategy Option A, operator pick 2026-07-11)** — Yahoo re-bases the
  WHOLE history on every split, while this ingest is incremental, so a name splitting mid-thesis used to
  accumulate mixed-basis stored bars (old-basis history + new-basis appends → a false cliff for the
  breakout detector, a mis-graded volume gate). Now the fresh pull's OVERLAP with stored history is
  compared per date: where the fresh bar was RESTATED, a NEW VERSION is appended (same ``d``, a new
  ``recorded_at`` — the bitemporal store's native move; the as-of read's DISTINCT ON picks the latest, so
  the series snaps to the new basis within one pass, price AND volume). A replay with a ``known_at`` BEFORE
  the re-version still sees the old basis — transaction-time honesty, exactly what the bitemporal store
  exists for. A hole (a date inside the overlap that was never stored) is always stored.
- **What counts as restated (Q11, operator decision 2026-10-17 — the rules live in ``restatement.py``)**:
  a bar in the fresh pull's trailing ``RECENT_SESSIONS`` (3) session dates keeps the EXACT compare
  (``_restated``, rel_tol 1e-9 — the vendor's late volume settling lands there); an OLDER bar re-versions
  only when its close moved >= 0.5% or its volume >= 5% (T6 — MEASURED: of the 27,367 member-bar
  re-versions stored 2026-09-01..10-16, 8,772 were sub-tolerance wiggles on older bars). A split moves both
  by an integer factor, so it still lands, in the same pass.
- **Revert hysteresis (Q11 / T5)** — the older restated set is decided ALL-OR-NOTHING per (security, pass):
  when EVERY bar of it goes back to a value the store already held and replaced (a REVERT — the vendor
  flipping a history between the adjusted and the unadjusted basis), the set is HELD until a pull on a
  later market date agrees; ``price_restatement_hold`` (migration 0047, operational state) remembers what
  the previous pull said. One bar the store never held makes the set FIRST-TIME → applied now, so a genuine
  split re-base is never held back. A hold is never silent: ``BarsResult.held`` carries the count into the
  run artifact and the cron prints one quiet line per held name; this module logs one line per hold too.
- **Settled bars only (D2)** — the fresh pull goes through ``settle.withhold_unsettled`` FIRST, so a bar
  for a session that has not closed and settled (today's, before ``Settings.price_settle_time``) is
  neither appended nor allowed to re-version a stored bar. An Admin "Run daily now" in market hours, a
  daytime boot catch-up, or a Workbench pull mid-session therefore stores yesterday's close and nothing of
  today; the next settled pass appends today's bar as an ordinary tail bar (withholding never loses one).
  ``BarsResult.withheld`` carries the count so the pass can say so ONCE, quietly.
- **Session-dated bars only (F5, 2026-10-31)** — right after the settle guard, ``settle.withhold_closed_days``
  drops a fresh bar dated a day with no session (a weekend, an NYSE full-day closure — the vendor's flat OTC
  carries on Labor Day 2026). Never stored by any pass; ``BarsResult.closed_day`` carries the count.
- **The split list (S2, 2026-10-20)** — after the settle guard and BEFORE the tail / overlap split, every fresh
  bar dated before a split IN FORCE (``domain.splits.applicable``: confirmed, or announced once its first
  session has arrived) is put on the store's scale (``split_normalize.normalize``): a vendor still / again
  serving the unadjusted basis is scaled by the listed factor and then equals the stored bar, so nothing is
  written; a vendor that applied a split twice, or a consistent different factor, is divided back; a genuine
  correction passes unchanged into T6/T5. Without it a REPAIRED tape was undone in two nights (the T5 guard
  holds a flip back once, then re-applies it). Bars on or after the last split are untouched. Nothing is
  rescaled on a read alone: only a run the tape PROVES (the store flat where the vendor steps; a "twice" read
  only where the store positively carries the split), so a vendor's genuine re-base of a raw store, or of an
  unlisted later split, always lands (the B1 review fix). ``BarsResult.split_normalized`` counts the bars read
  that way per reason (most write nothing), into the run artifact and the pass summary; the summary names a
  divide-back (and logs it at WARNING) only when it changes from the last pass, and every first night.
- **A vendor's missing close, filled (W6-3b)** — with a ``gap_source`` (the nightly's; the Workbench passes none),
  a session the vendor served NO close for is filled from Massive.com's raw bar under ``gap_fill``'s rule (on or
  after the newest split in force's first session, never on/after a pending split's, only where Massive.com
  traded, never over a vendor or a stored bar). The fills join the vendor's settled pull right after
  ``withhold_closed_days`` and BEFORE ``normalize``, so the normalizer reads them as the vendor's post-split tail
  (bars on/after the last split are never rescaled); a fill after the stored edge rides the tail append, one
  inside it the hole path. The trailing exact-compare window and T5's covered dates are still the VENDOR's own
  bars, so no vendor bar is compared differently. Each filled bar's provenance is a ``price_bar_fill`` row written
  in the same transaction (``bar_recorded_at`` = the bar's ``recorded_at``); a later vendor close for the date
  re-versions it through the ordinary compare. A Massive.com fault never costs the vendor's bars (``fill_error``).

Price bars are FEED data (like Form 4s), not operator-ratified facts — the recommend→confirm seam does
not apply to them; what they feed (the market cap) stays display until the operator acts.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import psycopg

from domain.market_time import market_tz
from domain.security import Security
from domain.splits import ListedSplit, applicable, listed_from_row
from domain.splits import pending as pending_splits
from ingest.prices.eod_loader import bar_versions, ingest_prices, latest_bar_date, stored_bars
from ingest.prices.gap_fill import SOURCE_MASSIVE, FillBar, FillPlan, GapSource
from ingest.prices.restatement import (
    HeldRevert,
    classify,
    decide,
    restated_old,
    trailing_window_start,
)
from ingest.prices.settle import withhold_closed_days, withhold_unsettled
from ingest.prices.source import PriceSource, YahooPriceSource
from ingest.prices.split_normalize import REASON_HOLE_AS_SERVED, normalize
from repositories import split_repo

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BarsResult:
    """The price leg's receipt: ``appended`` = bars newer than the latest stored (the incremental
    tail); ``reversioned`` = overlap bars re-stored because the source RESTATED them (a split
    re-base) or backfilled a hole — the exceptional path, surfaced loudly only when nonzero;
    ``withheld`` = fresh bars NOT stored because their session had not settled (D2 — today's bar on a
    pass before the settle time; written by the next settled pass); ``held`` = older restated bars NOT
    stored because they REVERT to a value the store held before and only one pull has said so (Q11 / T5 —
    written once a pull on a later market date agrees); ``closed_day`` = fresh bars NOT stored because they are
    dated a day with no session (F5 — a weekend or an NYSE full-day closure, e.g. the vendor's flat OTC carries
    on Labor Day 2026; never written by any pass). None of them is part of ``total``: nothing landed.
    """

    appended: int
    reversioned: int
    withheld: int = 0
    held: int = 0
    closed_day: int = 0
    # S2 — fresh bars put onto the split list's scale before the compare (``split_normalize``), PER REASON
    # (vendor_raw / vendor_doubled / mis_factored / first_night): a vendor flip back to an unadjusted basis it
    # absorbs writes nothing, so these count bars READ differently, not written. Carried into the run artifact
    # and the pass summary (``pipeline.ingest_thesis.print_split_lines``) — never silent (the B1 review fix).
    split_normalized: dict[str, int] = field(default_factory=dict)
    # the splits applied to the stored history for the FIRST time this pass (``Normalized.first_night``)
    split_first_night: tuple[str, ...] = ()
    # W6-3b — sessions the vendor served no close for, filled from Massive.com's raw bar and WRITTEN this pass
    # (``gap_fill``; part of ``appended`` / ``reversioned``, never extra): the dates, the newest split in force
    # (``FillPlan.split_label``), ``fill_deferred`` = a range read past the night's cap (filled a later night),
    # ``fill_error`` = a Massive.com read that failed or a fault in the fill step (the vendor's bars still landed)
    filled_dates: tuple[date, ...] = ()
    fill_split: str | None = None
    fill_deferred: bool = False
    fill_error: str | None = None

    @property
    def total(self) -> int:
        return self.appended + self.reversioned

    @property
    def normalized(self) -> int:
        return sum(self.split_normalized.values())

    @property
    def filled(self) -> int:
        return len(self.filled_dates)


def _num(x: object) -> float | None:
    return None if x is None else float(x)  # numeric columns arrive as Decimal; fresh bars as float


def _moved(fresh: dict, stored: dict, field: str) -> bool:
    """Did ONE bar field move beyond float noise? Both absent = unchanged; one absent = moved; else
    ``math.isclose`` at rel_tol 1e-9 (repr/Decimal round-trip jitter is ~1e-12). The EXACT rule shared by
    ``_restated`` and the Q3 benchmark repair (``pipeline.repair_benchmark_bars``) — deliberately left
    exact by Q11: the repair compares stored vs vendor for EXPLICIT dates, and the old-bar tolerance lives
    in ``restatement.restated_old``, applied only by this module's older-bar compare.
    """
    a, b = _num(fresh.get(field)), _num(stored.get(field))
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _restated(fresh: dict, stored: dict) -> bool:
    """A restatement = close or volume moved beyond float noise. A split re-base moves both ~10×;
    repr/Decimal round-trip jitter is ~1e-12 — rel_tol 1e-9 separates them cleanly. Close + volume
    are the fields the detectors read (the breakout close, the volume-confirmation gate); OHL cannot
    be re-based without close moving, so they don't need their own compare. The EXACT compare: this
    module's trailing window and the SPY/IWM leg's (``pipeline.ingest_benchmarks``, unchanged by Q11).
    """
    return any(_moved(fresh, stored, field) for field in ("close", "volume"))


@dataclass(frozen=True)
class OverlapPlan:
    """The fresh pull's overlap with the stored tape, split three ways (pure — ``plan_overlap``):
    ``holes`` = overlap dates never stored (always stored); ``recent`` = trailing-window bars restated
    under the EXACT compare (always stored); ``old`` = older bars restated beyond the T6 tolerance (the
    set T5 decides, all-or-nothing)."""

    holes: list[dict]
    recent: list[dict]
    old: list[dict]


def plan_overlap(
    fresh: list[dict],
    stored: dict[date, dict],
    *,
    last: date,
    window_rows: list[dict] | None = None,
) -> OverlapPlan:
    """PURE: split the fresh settled pull's OVERLAP (``d <= last``, the latest stored date) against the
    latest stored version per date. The trailing window is the pull's newest ``RECENT_SESSIONS`` session
    dates (``restatement.trailing_window_start`` — D1's count): a bar on/after its first date is compared
    exactly, an older one under the T6 tolerance. ``window_rows`` (default ``fresh``) are the rows that window
    is counted over — the vendor's own bars when ``fresh`` carries W6-3b fills."""
    window = trailing_window_start(fresh if window_rows is None else window_rows)
    holes: list[dict] = []
    recent: list[dict] = []
    old: list[dict] = []
    for r in fresh:
        if r["d"] > last:
            continue  # the tail, appended by the caller
        prior = stored.get(r["d"])
        if prior is None:
            # unseen d inside the overlap = a hole the source backfilled — store it (new info, not a
            # restatement, but the same honest append)
            holes.append(r)
        elif window is not None and r["d"] >= window:
            if _restated(r, prior):
                recent.append(r)
        elif restated_old(r, prior):
            old.append(r)
    return OverlapPlan(holes, recent, old)


# --- the T5 pending state (migration 0047): operational, replaced and deleted freely ----------------------


def load_hold(conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID) -> HeldRevert | None:
    """The held revert carried from an earlier pass for this security, or None."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pull_date, bars FROM price_restatement_hold "
            "WHERE tenant_id = %s AND security_id = %s",
            (tenant_id, security_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return HeldRevert(
        row["pull_date"], {date.fromisoformat(d): bar for d, bar in (row["bars"] or {}).items()}
    )


def save_hold(
    conn: psycopg.Connection,
    security_id: UUID,
    state: HeldRevert | None,
    *,
    tenant_id: UUID,
) -> None:
    """Persist (upsert) or clear (``None``) this security's held revert. The caller owns the transaction,
    so the state commits or rolls back WITH the bars of the same pass."""
    with conn.cursor() as cur:
        if state is None:
            cur.execute(
                "DELETE FROM price_restatement_hold WHERE tenant_id = %s AND security_id = %s",
                (tenant_id, security_id),
            )
            return
        bars = {
            d.isoformat(): {"close": _num(b.get("close")), "volume": _num(b.get("volume"))}
            for d, b in state.bars.items()
        }
        cur.execute(
            "INSERT INTO price_restatement_hold (tenant_id, security_id, pull_date, bars) "
            "VALUES (%s, %s, %s, %s::jsonb) "
            "ON CONFLICT (tenant_id, security_id) DO UPDATE SET "
            "pull_date = EXCLUDED.pull_date, bars = EXCLUDED.bars, updated_at = now()",
            (tenant_id, security_id, state.pull_date, json.dumps(bars, sort_keys=True)),
        )


def ingest_bars_for_security(
    conn: psycopg.Connection,
    sec: Security,
    *,
    tenant_id: UUID,
    allow_live: bool = True,
    force_refresh: bool = False,
    source: PriceSource | None = None,
    now: datetime | None = None,
    gap_source: GapSource | None = None,
) -> BarsResult:
    """Ingest this security's EOD bars: the incremental tail (newer than the latest stored bar) plus
    the RE-VERSION pass over the overlap (restated bars get a new version; see the module docstring).
    Reads bars through the injected ``PriceSource`` (the seam); ``force_refresh`` makes the recurring
    path bypass a stale cache hit. A ticker-less security contributes no bars. Idempotent by
    construction: a second run over the same fresh series appends the tail (now empty) and finds no
    overlap diffs — COUNT-the-table guarded. The caller owns the transaction.

    ``now`` (an AWARE instant; default the UTC wall clock) is the settle guard's clock AND the T5 pull
    date (its market date): bars whose session has not settled by ``now`` are withheld (D2), and a held
    revert is confirmed only by a pull on a later market date. ``recorded_at`` still comes from the DB
    default ``now()``, unchanged. Only a pull that really re-fetched (``force_refresh`` with
    ``allow_live``) may start, confirm or clear a hold — a cache-served read re-reads a pull that already
    happened.

    ``gap_source`` (W6-3b; ``None`` = no fill, the Workbench's and the onboarding job's call) fills a session the
    vendor served no close for from Massive.com's raw bar (the module docstring)."""
    if not sec.ticker:
        return BarsResult(0, 0)
    now = now or datetime.now(timezone.utc)
    src = source or YahooPriceSource()
    # Price under the RESOLVED vendor symbol when set (FDCT's history lives under FDCTD: 251 bars vs 16),
    # else the canonical SEC ticker. ``price_symbol`` is stored ONLY when it differs (migration 0032), and
    # only for a name that HAS a ticker — so ``price_symbol or ticker`` is the effective symbol, and the
    # ticker guard above still holds. The bars land under the same ``security_id`` regardless of which
    # symbol fetched them, so the detectors simply start seeing the fuller history (no call-path touch).
    price_symbol = sec.price_symbol or sec.ticker
    # D2 — settled bars only, BEFORE the tail/overlap split: an in-progress bar must neither append nor
    # re-version a stored one (a legacy intraday version of today stays until a settled pass restates it)
    fresh, withheld = withhold_unsettled(
        src.get_bars(price_symbol, allow_live=allow_live, force_refresh=force_refresh), now=now
    )
    # F5 — a bar dated a day with no session (a weekend, an NYSE closure) is never a close: dropped before
    # every write path, counted, never stored (``settle.withhold_closed_days``)
    fresh, closed_day = withhold_closed_days(fresh)
    last = latest_bar_date(conn, sec.id, tenant_id=tenant_id)
    stored = stored_bars(conn, sec.id, tenant_id=tenant_id) if last is not None else {}

    # S2 — the split list: every fresh bar before a split in force is put on the store's (the list's) scale
    # BEFORE the tail / overlap split, so the restatement compare below sees the vendor's flip back to an
    # unadjusted basis as no change at all (``split_normalize``)
    split_normalized: dict[str, int] = {}
    split_first_night: tuple[str, ...] = ()
    day = now.astimezone(market_tz()).date()
    listed = listed_splits(conn, sec.id, tenant_id=tenant_id)
    in_force = applicable(listed, on=day)
    # W6-3b — a session the vendor served no close for, filled from Massive.com's raw bar, joins the vendor's
    # settled pull HERE: after the settle / closed-day guards, BEFORE the normalizer (which reads it as the
    # vendor's post-split tail and never rescales it). ``vendor`` keeps the vendor's own bars for the compare's
    # trailing window and T5's covered dates. Fail-open: a Massive.com fault costs only the fill.
    vendor = fresh
    fill = FillPlan()
    fill_error: str | None = None
    if gap_source is not None:
        try:
            fill = gap_source.plan(
                fresh,
                stored,
                in_force,
                symbol=price_symbol,
                now=now,
                pending=pending_splits(listed, on=day),
            )
        except Exception as e:  # noqa: BLE001 — a fill fault never costs the vendor's bars
            fill, fill_error = FillPlan(), f"{type(e).__name__}: {e}"[:200]
        if fill.fills:
            fresh = sorted([*fresh, *(f.bar() for f in fill.fills)], key=lambda r: r["d"])
        fill_error = fill_error or fill.note
    if in_force:
        norm = normalize(fresh, stored, in_force)
        fresh, split_normalized, split_first_night = (
            norm.bars,
            dict(norm.scaled),
            tuple(norm.first_night),
        )
        if norm.as_served:
            split_normalized[REASON_HOLE_AS_SERVED] = norm.as_served
        # no log line here: a divide-back that stands night after night (ATRO's mis-factor, PBM's double
        # adjustment, once repaired) is a standing condition, not news. The pass summary names a name — and
        # logs it at WARNING — only when its divide-back CHANGES from the last pass, or on a first night
        # (``pipeline.ingest_thesis.print_split_lines``, the re-review's N2)

    appended = ingest_prices(
        conn,
        sec.id,
        [r for r in fresh if last is None or r["d"] > last],
        tenant_id=tenant_id,
    )

    # the re-version pass: only meaningful when the fresh pull overlaps stored history (a cache-first
    # re-click returns the very series already ingested → zero diffs → a no-op)
    reversioned = held = 0
    filled_dates = {f.d for f in fill.fills}
    if last is not None and any(r["d"] <= last for r in vendor):
        own = [r for r in fresh if r["d"] not in filled_dates]  # the vendor's bars, normalized
        plan = plan_overlap(fresh, stored, last=last, window_rows=own)
        refreshed = force_refresh and allow_live
        old: list[dict] = []
        if plan.old or refreshed:
            old, held = _resolve_older(
                conn, sec, plan.old, own, tenant_id=tenant_id, now=now, refreshed=refreshed
            )
        reversioned = ingest_prices(
            conn, sec.id, plan.holes + plan.recent + old, tenant_id=tenant_id
        )
    elif last is not None and fill.fills:
        # the vendor served nothing inside the stored window: a fill there is a hole, stored as one
        reversioned = ingest_prices(
            conn, sec.id, [f.bar() for f in fill.fills if f.d <= last], tenant_id=tenant_id
        )
    if fill.fills:
        record_fills(conn, sec.id, fill.fills, tenant_id=tenant_id)

    return BarsResult(
        appended,
        reversioned,
        withheld,
        held,
        closed_day=closed_day,
        split_normalized=split_normalized,
        split_first_night=split_first_night,
        filled_dates=tuple(sorted(filled_dates)),
        fill_split=fill.split_label if fill.fills else None,
        fill_deferred=fill.deferred,
        fill_error=fill_error,
    )


def record_fills(
    conn: psycopg.Connection, security_id: UUID, fills: list[FillBar], *, tenant_id: UUID
) -> int:
    """W6-3b — one ``price_bar_fill`` row per filled bar, in the caller's transaction, AFTER the bar was
    written: ``bar_recorded_at`` is read off that bar (its ``recorded_at``, the transaction's ``now()``), so the
    row names exactly the version it explains; an INSERT that finds no such bar fails the transaction (NOT NULL),
    never a fill without its provenance."""
    with conn.cursor() as cur:
        for f in fills:
            cur.execute(
                "INSERT INTO price_bar_fill (tenant_id, security_id, d, bar_recorded_at, source, "
                "vendor_symbol, raw_close, raw_volume, factor, reason) "
                "SELECT %s, %s, %s, (SELECT recorded_at FROM fact_price_eod WHERE tenant_id = %s "
                "AND security_id = %s AND d = %s AND recorded_at = now()), %s, %s, %s, %s, %s, %s",
                (
                    tenant_id,
                    security_id,
                    f.d,
                    tenant_id,
                    security_id,
                    f.d,
                    SOURCE_MASSIVE,
                    f.vendor_symbol,
                    f.raw_close,
                    f.raw_volume,
                    Decimal(f.factor.numerator) / Decimal(f.factor.denominator),
                    f.reason,
                ),
            )
    return len(fills)


def listed_splits(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID
) -> list[ListedSplit]:
    """The split list for one security: the newest version of each split (``split_repo.latest_versions``, as of
    the database clock now), every status."""
    rows = split_repo.latest_versions(conn, tenant_id=tenant_id, security_ids=[security_id])
    return [listed_from_row(v.model_dump()) for v in rows]


def splits_in_force(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID, on: date
) -> list[ListedSplit]:
    """The split list's splits IN FORCE for one security on market day ``on`` — the newest version of each
    that is confirmed, or announced with its first session arrived (``domain.splits.applicable``).
    """
    return applicable(listed_splits(conn, security_id, tenant_id=tenant_id), on=on)


def _resolve_older(
    conn: psycopg.Connection,
    sec: Security,
    old: list[dict],
    fresh: list[dict],
    *,
    tenant_id: UUID,
    now: datetime,
    refreshed: bool,
) -> tuple[list[dict], int]:
    """T5 for one pass: classify the older restated set against its superseded versions, decide with the
    pending state, persist the state when it changed. Returns ``(bars to write, bars held)`` — the whole
    set or none of it."""
    pending = load_hold(conn, sec.id, tenant_id=tenant_id)
    history = bar_versions(conn, sec.id, (r["d"] for r in old), tenant_id=tenant_id)
    kind, _ = classify(old, {d: vs[:-1] for d, vs in history.items()})
    decision = decide(
        kind,
        old=old,
        pending=pending,
        covered={r["d"] for r in fresh},
        pull_date=now.astimezone(market_tz()).date(),
        refreshed=refreshed,
    )
    if decision.state is not pending:
        save_hold(conn, sec.id, decision.state, tenant_id=tenant_id)
    if not decision.apply:
        _log.info(
            "price restatement held: %s — %d older bar(s) revert to a value stored before; stored once "
            "a pull on a later market date agrees (Q11/T5)",
            sec.ticker,
            decision.held,
        )
        return [], decision.held
    return old, 0
