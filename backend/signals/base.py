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

    def benchmark_prices(
        self, symbol: str, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        """The as-of EOD bars for a BENCHMARK security (SPY/IWM), resolved by ticker within this tenant
        — the reference series the relative-strength DISPLAY column prices each member against.

        A SANCTIONED widening of the DISPLAY contract (``DISPLAY_SIGNALS.md``): a display member cannot
        do a master/db lookup inside the seam (the seam forbids db imports), so the concrete PIT — which
        already reads the master directly for ``security_name`` — resolves the benchmark here. Deliberately
        NOT on the detectors' ``SignalPointInTimeData`` protocol: promoting RS to a CALL input is a later,
        separately-signed step (it would widen ``SignalPointInTimeData`` AND ``ReplayPointInTimeData``).

        Still as-of capped on ``fact_price_eod`` (no lookahead, #1): the benchmark bars honor the same
        ``valid_from <= asof`` / ``recorded_at <= known_at`` gate as every price read. An unknown symbol
        or an unseeded/unpriced benchmark returns ``[]`` — the RS column reports an honest gap, never a
        fabricated ratio (#6/#9). The master read is identity (stable), never as-of, exactly like
        ``security_name``."""
        sid = self._benchmark_id(symbol)
        if sid is None:
            return []
        rows = as_of(
            self.conn,
            "fact_price_eod",
            security_id=sid,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )
        return window_prices(rows, self.asof, lookback_days)

    def _benchmark_id(self, symbol: str) -> UUID | None:
        """Resolve a benchmark ticker (SPY/IWM) to its ``security_master`` id within this tenant — the
        ``instrument_kind = 'etf'`` filter matches the ``securities.benchmarks`` seed so a benchmark can
        never collide with an operating company that shares the ticker. Identity read, never as-of.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM security_master "
                "WHERE tenant_id = %s AND ticker = %s AND instrument_kind = 'etf' "
                "ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (self.tenant_id, symbol.upper()),
            )
            row = cur.fetchone()
        return row["id"] if row else None

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

    def fundamentals_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The as-of quarterly financial series (§2.2) — the revenue-acceleration detector's input.

        A bitemporal read like the others: each row is knowable only from its 10-Q/10-K `filed` date
        (``valid_from``), so a past-as-of read sees a period's revenue EXACTLY when it was filed, never at
        the period end (no-lookahead, #1). The as-of read dedups to the latest VERSION per (security,
        metric_key, period_end), so a restatement supersedes the original. Rows carry ``metric_key`` /
        ``period_end`` / ``value`` (a general shape so §2.4 adds EPS/margin/FCF as ROWS, not columns).
        """
        return as_of(
            self.conn,
            "fact_fundamentals",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def corporate_event_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The as-of 8-K item-code tape (Band 03 S3) — the corporate-catalyst + corporate-risk
        detectors' input.

        Each row is one 8-K filing with its SEC ``items`` codes, knowable from its ``filed`` date
        (``valid_from`` — the acceptance date IS the knowability, so a past-as-of read never sees a
        filing early; #1). The as-of read dedups to the latest VERSION per accession, so a filing
        whose items resolved after first ingest reads with its real codes. Rows carry
        ``form`` / ``items`` / ``accession`` / ``filed`` / ``source_ref`` — the detectors apply the
        item-code policy map (``CallConfig.corporate_event_items``) on READ, never at ingest (the
        evidence/policy seam)."""
        return as_of(
            self.conn,
            "fact_corporate_event",
            security_id=security_id,
            asof=self.asof,
            known_at=self.known_at,
            tenant_id=self.tenant_id,
        )

    def activist_stake_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The as-of SC 13D/G ownership tape (Band 03 S5) — the activist-stake detector's input.

        Each row is one 13D/G-family filing ABOUT this security (the SUBJECT), knowable from its
        ``filed`` date (``valid_from`` — the acceptance date IS the knowability; the stake crossing
        inside the filing predates it, gold-doc trap #4, so a past-as-of read never sees a stake
        early; #1). The as-of read dedups to the latest VERSION per accession, so a filing whose
        filer identity resolved after first ingest reads with the activist named. Rows carry
        ``form`` / ``filer_cik`` / ``filer_name`` / ``pct_owned`` / ``accession`` / ``filed`` /
        ``source_ref`` — the detector applies the fire policy (13D-family originals only) on READ,
        never at ingest (the evidence/policy seam)."""
        return as_of(
            self.conn,
            "fact_activist_stake",
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

    def fund_shares(self, security_id: UUID) -> list[dict[str, Any]]:
        """ETF sleeve shares-outstanding samples (ETF net flow) — the DISPLAY rail's flow basis.

        Deliberately NOT on the detectors' ``SignalPointInTimeData`` protocol below: the flow read is
        display context (``signals/display/etf_flow.py``), never a call input, and keeping the accessor
        off the protocol keeps a future detector from quietly growing a dependency on it (#4/#5 —
        promoting flow to a signal is a separate, operator-signed F4). Rows are the deduped latest
        version per (security, d), ascending by d."""
        return as_of(
            self.conn,
            "fact_fund_shares",
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

    def security_name(self, security_id: UUID) -> str | None:
        """The security's display name from ``security_master`` — an IDENTITY read, NOT a bitemporal fact.

        The master is identity (CIK ↔ ticker ↔ name), read directly (never as-of): the name is stable, so a
        direct read leaks no future EVENT — the no-lookahead boundary is about facts, not identity (migration
        0001: "nothing reads the master as-of"). The insider detector uses this to recognise a self-filing —
        the reporting owner IS the issuer — on rows ingested before ``rpt_owner_cik``/``issuer_cik`` were
        captured (those newer rows carry the CIKs and match canonically instead). ``None`` if unknown → the
        name screen simply keeps the row (recall-safe, #9)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM security_master WHERE id = %s AND tenant_id = %s",
                (security_id, self.tenant_id),
            )
            row = cur.fetchone()
        return row["name"] if row else None


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

    def fundamentals_facts(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def corporate_event_facts(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def activist_stake_facts(self, security_id: UUID) -> list[dict[str, Any]]: ...

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]: ...

    def security_name(self, security_id: UUID) -> str | None: ...


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
