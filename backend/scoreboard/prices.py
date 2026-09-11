from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg


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
            "WHERE tenant_id = %s AND d <= %s AND recorded_at <= %s AND close IS NOT NULL",
            [tenant_id, cap, known_at or datetime.now(timezone.utc)],
        )
        row = cur.fetchone()
    return row["edge"] if row else None


# The Postgres twin of ``replay.scoring.RealizedPrices`` — the same three-method surface
# ``score_episode`` duck-types against, but over the live SoR and CAPPED at the request asof
# (``d <= cap``): the Scoreboard reads realized closes only up to the day it is asked about, so a
# scrubbed-back asof can never see a later bar (no-lookahead, applied to a forward reader) and an
# in-flight episode's return naturally runs to the last bar <= asof (``truncated`` rides the Outcome).
# ``known_at`` caps the transaction axis (a re-versioned bar: the latest version recorded by then
# wins — the same ``recorded_at DESC, id DESC`` tiebreak as ``db.bitemporal._as_of``).


class PgRealizedPrices:
    """Realized EOD closes from ``fact_price_eod``, read forward within ``[.., cap]`` — the latest
    recorded version per ``(security_id, d)``, null closes skipped (parity with the DuckDB reader).
    Constructed per thesis with the thesis's own ``tenant_id`` (never the default on a live path).
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
        self.known_at = known_at or datetime.now(timezone.utc)
        # The request-level market edge, threaded in by the caller that already resolved it once for
        # this tenant. ``None`` means "not supplied" and this reader resolves (and caches) its own on
        # first use — which is the standalone / replay / test path, never the hot one. Conflating
        # "not supplied" with "the tenant has no bars" is harmless and unreachable on the live path:
        # the scorer only asks for the market edge once a non-empty window exists, and a non-empty
        # window means the tape is non-empty.
        self._market_edge = market_edge

    def _closes(self, security_id: UUID, extra: str, params: list) -> list[tuple[date, float]]:
        # ``extra`` is a trusted range literal from the three methods below, never caller input
        # (the same posture as the DuckDB twin's ``where`` argument).
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (d) d, close FROM fact_price_eod "
                "WHERE tenant_id = %s AND security_id = %s AND d <= %s AND recorded_at <= %s"
                f"{extra} "
                "ORDER BY d, recorded_at DESC, id DESC",
                [self.tenant_id, security_id, self.cap, self.known_at, *params],
            )
            rows = cur.fetchall()
        return [(r["d"], float(r["close"])) for r in rows if r["close"] is not None]

    def first_close_on_or_after(self, security_id: UUID, d: date) -> tuple[date, float] | None:
        rows = self._closes(security_id, " AND d >= %s", [d])
        return rows[0] if rows else None

    def last_close_through(self, security_id: UUID, through: date) -> tuple[date, float] | None:
        rows = self._closes(security_id, " AND d <= %s", [through])
        return rows[-1] if rows else None

    def closes_between(self, security_id: UUID, start: date, end: date) -> list[tuple[date, float]]:
        return self._closes(security_id, " AND d >= %s AND d <= %s", [start, end])

    def _bars(self, security_id: UUID, extra: str, params: list) -> list[dict]:
        # The OHLCV twin of ``_closes`` — the SAME as-of discipline (``d <= cap`` on the valid axis,
        # ``recorded_at <= known_at`` on the transaction axis, latest version per day), so a window's
        # full bars carry the identical no-lookahead guarantee. Null-CLOSE rows are skipped (parity with
        # ``_closes``); the other OHLCV columns are nullable per-column (a close-only free-EOD bar leaves
        # open/high/low/volume NULL — surfaced honestly as ``None``, never invented). ``extra`` is a
        # trusted range literal from ``bars_between``, never caller input.
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (d) d, open, high, low, close, volume FROM fact_price_eod "
                "WHERE tenant_id = %s AND security_id = %s AND d <= %s AND recorded_at <= %s"
                f"{extra} "
                "ORDER BY d, recorded_at DESC, id DESC",
                [self.tenant_id, security_id, self.cap, self.known_at, *params],
            )
            rows = cur.fetchall()
        return [
            {
                "d": r["d"],
                "open": _f(r["open"]),
                "high": _f(r["high"]),
                "low": _f(r["low"]),
                "close": float(r["close"]),
                "volume": _f(r["volume"]),
            }
            for r in rows
            if r["close"] is not None
        ]

    def bars_between(self, security_id: UUID, start: date, end: date) -> list[dict]:
        """Full asof-capped OHLCV bars over ``[start, end]`` — the drawer sparkline's read (Slice 3). The
        line draws ``close``; open/high/low/volume ride the wire for a future candlestick. Same cap/known_at
        as ``closes_between``, so the no-lookahead property is identical (never a forked as-of path).
        """
        return self._bars(security_id, " AND d >= %s AND d <= %s", [start, end])

    def tape_edge(self, security_id: UUID, on_or_after: date) -> date | None:
        """The date of this name's LAST available bar at or after ``on_or_after`` — where its price
        tape ends, as this reader can see it. ``None`` when the tape holds no such bar.

        Built on ``_closes``, so it inherits THIS reader's double cap unchanged: ``d <= cap`` on the
        valid axis, ``recorded_at <= known_at`` on the transaction axis. That is load-bearing, not
        incidental. The scorer asks this method whether a horizon was covered by the tape; a read that
        reached past the cap would let a bar the operator could not have seen at the request as-of
        answer yes, which is invariant #1 broken in the one place it would look like a display fix.
        (The DuckDB twin's version is forward-unbounded, copying THAT side's existing shape — the two
        readers are duck-typed peers, never a harmonized pair.)"""
        rows = self._closes(security_id, " AND d >= %s", [on_or_after])
        return rows[-1][0] if rows else None

    def market_tape_edge(self, security_id: UUID) -> date | None:
        """The latest bar date anywhere in this tenant's tape — the ``truncated`` rule's second leg
        ("the market has printed past this horizon"). Uses the value threaded in at construction when
        there is one, else resolves once and caches it on this instance.

        ``security_id`` is unused here and taken deliberately: this reader is already scoped to one
        tenant, but the method is part of a duck-typed protocol whose other implementation
        (``_RoutedPrices``) routes per security, and it must be able to pick the owning tenant. The
        argument is what keeps the two peers the same shape.
        """
        if self._market_edge is None:
            self._market_edge = market_tape_edge(
                self.conn, tenant_id=self.tenant_id, cap=self.cap, known_at=self.known_at
            )
        return self._market_edge
