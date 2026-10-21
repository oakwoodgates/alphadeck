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
  that way per reason (most write nothing), into the run artifact and the pass summary; a divide-back or a
  first night is also logged at WARNING.

Price bars are FEED data (like Form 4s), not operator-ratified facts — the recommend→confirm seam does
not apply to them; what they feed (the market cap) stays display until the operator acts.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from uuid import UUID

import psycopg

from domain.market_time import market_tz
from domain.security import Security
from domain.splits import ListedSplit, applicable, listed_from_row
from ingest.prices.eod_loader import bar_versions, ingest_prices, latest_bar_date, stored_bars
from ingest.prices.restatement import (
    HeldRevert,
    classify,
    decide,
    restated_old,
    trailing_window_start,
)
from ingest.prices.settle import withhold_unsettled
from ingest.prices.source import PriceSource, YahooPriceSource
from ingest.prices.split_normalize import WARN_REASONS, normalize
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
    written once a pull on a later market date agrees). Neither is part of ``total``: nothing landed.
    """

    appended: int
    reversioned: int
    withheld: int = 0
    held: int = 0
    # S2 — fresh bars put onto the split list's scale before the compare (``split_normalize``), PER REASON
    # (vendor_raw / vendor_doubled / mis_factored / first_night): a vendor flip back to an unadjusted basis it
    # absorbs writes nothing, so these count bars READ differently, not written. Carried into the run artifact
    # and the pass summary (``pipeline.ingest_thesis.print_split_lines``) — never silent (the B1 review fix).
    split_normalized: dict[str, int] = field(default_factory=dict)
    # the splits applied to the stored history for the FIRST time this pass (``Normalized.first_night``)
    split_first_night: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return self.appended + self.reversioned

    @property
    def normalized(self) -> int:
        return sum(self.split_normalized.values())


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


def plan_overlap(fresh: list[dict], stored: dict[date, dict], *, last: date) -> OverlapPlan:
    """PURE: split the fresh settled pull's OVERLAP (``d <= last``, the latest stored date) against the
    latest stored version per date. The trailing window is the pull's newest ``RECENT_SESSIONS`` session
    dates (``restatement.trailing_window_start`` — D1's count): a bar on/after its first date is compared
    exactly, an older one under the T6 tolerance."""
    window = trailing_window_start(fresh)
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
    happened."""
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
    last = latest_bar_date(conn, sec.id, tenant_id=tenant_id)
    stored = stored_bars(conn, sec.id, tenant_id=tenant_id) if last is not None else {}

    # S2 — the split list: every fresh bar before a split in force is put on the store's (the list's) scale
    # BEFORE the tail / overlap split, so the restatement compare below sees the vendor's flip back to an
    # unadjusted basis as no change at all (``split_normalize``)
    split_normalized: dict[str, int] = {}
    split_first_night: tuple[str, ...] = ()
    in_force = splits_in_force(
        conn, sec.id, tenant_id=tenant_id, on=now.astimezone(market_tz()).date()
    )
    if in_force:
        norm = normalize(fresh, stored, in_force)
        fresh, split_normalized, split_first_night = (
            norm.bars,
            dict(norm.scaled),
            tuple(norm.first_night),
        )
        # a divide-back or a first-time rescale of history is the exception: WARNING (reaches stderr with no
        # logging config — the level ``pipeline.backup`` uses for the same reason); the steady vendor-raw
        # absorb is counted in the pass summary only (``BarsResult.split_normalized``)
        loud = {k: n for k, n in norm.scaled.items() if k in WARN_REASONS}
        if loud or norm.first_night:
            _log.warning(
                "price split normalization: %s — %s%s",
                sec.ticker,
                ", ".join(f"{n} {why.replace('_', ' ')}" for why, n in sorted(loud.items())),
                f" (first night: {'; '.join(norm.first_night)})" if norm.first_night else "",
            )

    appended = ingest_prices(
        conn,
        sec.id,
        [r for r in fresh if last is None or r["d"] > last],
        tenant_id=tenant_id,
    )

    # the re-version pass: only meaningful when the fresh pull overlaps stored history (a cache-first
    # re-click returns the very series already ingested → zero diffs → a no-op)
    reversioned = held = 0
    if last is not None and any(r["d"] <= last for r in fresh):
        plan = plan_overlap(fresh, stored, last=last)
        refreshed = force_refresh and allow_live
        old: list[dict] = []
        if plan.old or refreshed:
            old, held = _resolve_older(
                conn, sec, plan.old, fresh, tenant_id=tenant_id, now=now, refreshed=refreshed
            )
        reversioned = ingest_prices(
            conn, sec.id, plan.holes + plan.recent + old, tenant_id=tenant_id
        )

    return BarsResult(
        appended,
        reversioned,
        withheld,
        held,
        split_normalized=split_normalized,
        split_first_night=split_first_night,
    )


def splits_in_force(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID, on: date
) -> list[ListedSplit]:
    """The split list's splits IN FORCE for one security on market day ``on`` — the newest version of each
    (``split_repo.latest_versions``, as of the database clock now) that is confirmed, or announced with its
    first session arrived (``domain.splits.applicable``)."""
    rows = split_repo.latest_versions(conn, tenant_id=tenant_id, security_ids=[security_id])
    return applicable([listed_from_row(v.model_dump()) for v in rows], on=on)


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
