from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, NamedTuple
from uuid import UUID

import psycopg

from db.clock import db_now  # the ONE-CLOCK rule; see that module for why
from domain.trading_days import NYSE_CLOSURES, closure_name


class _Bar(NamedTuple):
    """One deduped, non-holiday, non-null-close bar held in the per-security tape cache."""

    d: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


# F5 — a bar dated an NYSE full-day closure is never a scoring bar: the vendor stored flat OTC carries on
# Labor Day 2026 (MEASURED on dev: 10 bars, open = high = low, close = the prior close), and there was no
# session. Every read below skips one — no entry, horizon, exit or tape-edge bar is ever a holiday — the
# same rule as the point-in-time view (``signals.base.window_prices``) and the DuckDB twin
# (``replay.scoring.RealizedPrices``). Nothing is deleted.


def _f(x: Any) -> float | None:
    """Coerce a nullable numeric column (psycopg returns ``Decimal``/``None``) to ``float``/``None``."""
    return float(x) if x is not None else None


def market_tape_edge(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    cap: date,
    known_at: datetime | None = None,
) -> date | None:
    """The latest bar date ANYWHERE in this tenant's tape, under the same double cap every other read
    here applies (``d <= cap`` on the valid axis, ``recorded_at <= known_at`` on the transaction
    axis, null closes skipped). ``None`` when the tenant has no priced bar at all.

    A REQUEST-level fact, deliberately exposed as a module function as well as a reader method: it is
    the same answer for every episode and every thesis in one tenant, and it costs a scan
    (``fact_price_eod`` has no ``(tenant_id, d)`` index, so this is a parallel seq scan — MEASURED at
    ~33 ms over 350k rows). Resolve it ONCE per tenant per request and thread it into the readers;
    letting each of them compute its own would put that scan on every thesis, which is the
    Board/Cockpit per-row-query mistake in miniature.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(d) AS edge FROM fact_price_eod "
            "WHERE tenant_id = %s AND d <= %s AND recorded_at <= %s AND close IS NOT NULL "
            "AND d <> ALL(%s)",  # a closure-dated bar never proves the market printed (F5)
            [
                tenant_id,
                cap,
                known_at or db_now(conn),
                list(NYSE_CLOSURES),
            ],  # the clock that STAMPS recorded_at
        )
        row = cur.fetchone()
    return row["edge"] if row else None


# The Postgres twin of ``replay.scoring.RealizedPrices`` — the same six-read surface
# ``score_episode`` duck-types against, but over the live SoR and CAPPED at the request asof
# (``d <= cap``): the Scoreboard reads realized closes only up to the day it is asked about, so a
# scrubbed-back asof can never see a later bar (no-lookahead, applied to a forward reader) and an
# in-flight episode's return naturally runs to the last bar <= asof (``truncated`` rides the Outcome).
# ``known_at`` caps the transaction axis (a re-versioned bar: the latest version recorded by then
# wins — the same ``recorded_at DESC, id DESC`` tiebreak as ``db.bitemporal._as_of``).


class PgRealizedPrices:
    """Realized EOD closes from ``fact_price_eod``, read forward within ``[.., cap]`` — the latest
    recorded version per ``(security_id, d)``, null closes and NYSE full-day closures skipped (parity
    with the DuckDB reader).

    Each touched security's capped tape is cached in memory as parallel sorted ``_Bar`` / ``date``
    lists and queried via ``bisect_left`` / ``bisect_right``. Callers scoring many securities (the
    Scoreboard, editions diff) call ``prefetch(security_ids)`` once to load all needed tapes in a
    single SQL query; un-prefetched touches load lazily per security and cache on first use.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        tenant_id: UUID,
        cap: date,
        known_at: datetime | None = None,
        market_edge: date | None = None,
    ) -> None:
        self.conn = conn
        self.tenant_id = tenant_id
        self.cap = cap
        # Pinned ONCE, from the clock that stamps ``recorded_at`` (see ``db_now``) — so every method of
        # this reader answers against one transaction-axis bound, and that bound can never sit in the
        # database's past because a host clock drifted.
        self.known_at = known_at or db_now(conn)
        # The request-level market edge, threaded in by the caller that already resolved it once for
        # this tenant. ``None`` means "not supplied" and this reader resolves (and caches) its own on
        # first use — which is the standalone / replay / test path, never the hot one.
        self._market_edge = market_edge
        self._market_edge_loaded = market_edge is not None
        self._tapes: dict[UUID, list[_Bar]] = {}
        self._dates: dict[UUID, list[date]] = {}

    def prefetch(self, security_ids: Iterable[UUID]) -> None:
        """Batch-load the capped, deduped OHLCV tapes for ``security_ids`` not yet cached, in ONE
        query. Securities with no qualifying bars are cached as empty so later lookups never re-query.

        Dedup is ``DISTINCT ON (security_id, d) ... ORDER BY security_id, d, recorded_at DESC, id DESC``
        WITHOUT filtering ``close IS NOT NULL`` in SQL so a later null-close restatement still
        supersedes an earlier bar before being skipped in Python — exact parity with the uncached read.
        """
        missing = list({sid for sid in security_ids if sid not in self._tapes})
        if not missing:
            return
        for sid in missing:
            self._tapes[sid] = []
            self._dates[sid] = []
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (security_id, d) "
                "security_id, d, open, high, low, close, volume "
                "FROM fact_price_eod "
                "WHERE tenant_id = %s AND security_id = ANY(%s) "
                "AND d <= %s AND recorded_at <= %s "
                "ORDER BY security_id, d, recorded_at DESC, id DESC",
                [self.tenant_id, missing, self.cap, self.known_at],
            )
            rows = cur.fetchall()
        for r in rows:
            if r["close"] is None or closure_name(r["d"]) is not None:  # F5: no holiday bar
                continue
            sid = r["security_id"]
            d = r["d"]
            self._tapes[sid].append(
                _Bar(
                    d=d,
                    open=_f(r["open"]),
                    high=_f(r["high"]),
                    low=_f(r["low"]),
                    close=float(r["close"]),
                    volume=_f(r["volume"]),
                )
            )
            self._dates[sid].append(d)

    def _tape(self, security_id: UUID) -> tuple[list[_Bar], list[date]]:
        if security_id not in self._tapes:
            self.prefetch([security_id])
        return self._tapes[security_id], self._dates[security_id]

    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None:
        tape, dates = self._tape(security_id)
        i = bisect_left(dates, d)
        return (tape[i].d, tape[i].close) if i < len(tape) else None

    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None:
        tape, dates = self._tape(security_id)
        j = bisect_right(dates, through)
        return (tape[j - 1].d, tape[j - 1].close) if j > 0 else None

    def closes_between(self, security_id: UUID, start: date, end: date) -> list[tuple[date, float]]:
        tape, dates = self._tape(security_id)
        i = bisect_left(dates, start)
        j = bisect_right(dates, end)
        return [(b.d, b.close) for b in tape[i:j]]

    def bars_between(self, security_id: UUID, start: date, end: date) -> list[dict]:
        """Full asof-capped OHLCV bars over ``[start, end]`` — the drawer sparkline's read (Slice 3). The
        line draws ``close``; open/high/low/volume ride the wire for a future candlestick. Same cap/known_at
        as ``closes_between``, so the no-lookahead property is identical (never a forked as-of path).
        """
        tape, dates = self._tape(security_id)
        i = bisect_left(dates, start)
        j = bisect_right(dates, end)
        return [
            {
                "d": b.d,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in tape[i:j]
        ]

    def tape_edge(self, security_id: UUID, on_or_after: date) -> date | None:
        """The date of this name's LAST available bar at or after ``on_or_after`` — where its price
        tape ends, as this reader can see it. ``None`` when the tape holds no such bar.

        Built on the cached tape, so it inherits THIS reader's double cap unchanged: ``d <= cap`` on the
        valid axis, ``recorded_at <= known_at`` on the transaction axis. That is load-bearing, not
        incidental. The scorer asks this method whether a horizon was covered by the tape; a read that
        reached past the cap would let a bar the operator could not have seen at the request as-of
        answer yes, which is invariant #1 broken in the one place it would look like a display fix.
        (The DuckDB twin's version is forward-unbounded, copying THAT side's existing shape — the two
        readers are duck-typed peers, never a harmonized pair.)"""
        tape, dates = self._tape(security_id)
        i = bisect_left(dates, on_or_after)
        return tape[-1].d if i < len(tape) else None

    def market_tape_edge(self, security_id: UUID) -> date | None:
        """The latest bar date anywhere in this tenant's tape — the ``truncated`` rule's second leg
        ("the market has printed past this horizon"). Uses the value threaded in at construction when
        there is one, else resolves once and caches it on this instance.

        ``security_id`` is unused here and taken deliberately: this reader is already scoped to one
        tenant, but the method is part of a duck-typed protocol whose other implementation
        (``_RoutedPrices``) routes per security, and it must be able to pick the owning tenant. The
        argument is what keeps the two peers the same shape.
        """
        if not self._market_edge_loaded:
            self._market_edge = market_tape_edge(
                self.conn, tenant_id=self.tenant_id, cap=self.cap, known_at=self.known_at
            )
            self._market_edge_loaded = True
        return self._market_edge
