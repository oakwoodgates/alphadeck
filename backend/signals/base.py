from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from domain.signal import SignalEvent


class PointInTimeData:
    """The ONLY way a detector reads facts — a bitemporal as-of view fixed at (asof, known_at).

    A detector physically cannot see post-asof events or post-known_at knowledge, because every
    read goes through ``db.bitemporal.as_of``. ``known_at`` defaults to now (UTC) for live reads;
    the replay harness (M5) sets it to a simulated past transaction time.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        asof: date,
        known_at: datetime | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> None:
        self.conn = conn
        self.asof = asof
        self.known_at = known_at or datetime.now(timezone.utc)
        self.tenant_id = tenant_id

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]:
        return as_of(
            self.conn,
            "fact_insider_txn",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        rows = as_of(
            self.conn,
            "fact_price_eod",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )
        rows.sort(key=lambda r: r["d"])
        if lookback_days is not None:
            cutoff = self.asof - timedelta(days=lookback_days)
            rows = [r for r in rows if r["d"] >= cutoff]
        return rows


# A detector is pure: f(point_in_time_data, security_id, asof) -> SignalEvent | None (CLAUDE.md / CALL_LOGIC §1).
Detector = Callable[[PointInTimeData, UUID, date], SignalEvent | None]
