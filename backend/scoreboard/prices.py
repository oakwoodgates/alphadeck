from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg


def _f(x: Any) -> float | None:
    """Coerce a nullable numeric column (psycopg returns ``Decimal``/``None``) to ``float``/``None``."""
    return float(x) if x is not None else None


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
    ) -> None:
        self.conn = conn
        self.tenant_id = tenant_id
        self.cap = cap
        self.known_at = known_at or datetime.now(timezone.utc)

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
