from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

import psycopg

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
