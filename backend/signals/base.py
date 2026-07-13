from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

import psycopg

from db.bitemporal import as_of, as_of_thesis
from db.session import DEFAULT_TENANT_ID
from domain.config import CallConfig
from domain.signal import SignalEvent


def window_prices(
    rows: list[dict[str, Any]], asof: date, lookback_days: int | None
) -> list[dict[str, Any]]:
    """Sort EOD bars ascending by ``d`` and trim to the lookback window. Shared by the live PIT and the
    replay mirror so the exact price view the breakout detector sees can't drift between Postgres and
    DuckDB (the as-of READ differs by engine; this post-process is identical for both)."""
    rows = sorted(rows, key=lambda r: r["d"])
    if lookback_days is not None:
        cutoff = asof - timedelta(days=lookback_days)
        rows = [r for r in rows if r["d"] >= cutoff]
    return rows


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
        return window_prices(rows, self.asof, lookback_days)

    def dilution_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return as_of(
            self.conn,
            "fact_dilution",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return as_of(
            self.conn,
            "fact_catalyst",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def revenue_mix_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench purity basis — operator-ratified revenue-mix facts (10-K segments), as-of."""
        return as_of(
            self.conn,
            "fact_revenue_mix",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def shares_outstanding_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench market-cap basis — operator-ratified shares-outstanding facts (10-Q), as-of."""
        return as_of(
            self.conn,
            "fact_shares_outstanding",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def cash_burn_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench runway basis — operator-ratified cash + quarterly-burn facts (10-Q), as-of."""
        return as_of(
            self.conn,
            "fact_cash_burn",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]:
        """Thesis-scoped (not co-located): the operator-ratified theme convictions for a thesis (M5b)."""
        return as_of_thesis(
            self.conn,
            "fact_theme_conviction",
            thesis_id=thesis_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )


class SignalPointInTimeData(Protocol):
    """The structural fact-view contract consumed by the current signal pipeline.

    Both the Postgres-backed ``PointInTimeData`` above and replay's DuckDB-backed
    ``ReplayPointInTimeData`` satisfy this protocol. It names only the accessors the existing four
    per-security detectors plus the thesis-level theme broadcast use — no future plugin surface.
    """

    asof: date
    known_at: datetime
    tenant_id: UUID

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]: ...

    def dilution_facts(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]: ...


DetectorFn = Callable[
    [SignalPointInTimeData, UUID, date, CallConfig],
    SignalEvent | None,
]


@dataclass(frozen=True, slots=True)
class Detector:
    """One registered per-security detector with the exact current pipeline contract."""

    name: str
    detect: DetectorFn

    def __call__(
        self,
        pit: SignalPointInTimeData,
        security_id: UUID,
        asof: date,
        cfg: CallConfig,
    ) -> SignalEvent | None:
        event = self.detect(pit, security_id, asof, cfg)
        if event is not None and event.detector != self.name:
            raise ValueError(f"detector {self.name!r} emitted event stamped by {event.detector!r}")
        return event
