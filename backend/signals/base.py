from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

import psycopg

from db.bitemporal import as_of, as_of_many, as_of_thesis
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
    read goes through ``db.bitemporal.as_of`` / ``as_of_many``. ``known_at`` defaults to now (UTC) for
    live reads; the replay harness (M5) sets it to a simulated past transaction time.

    **The per-request memo + per-basket prefetch (Board/Cockpit perf PR-1b).** One PIT is built per
    request / per cron assemble, and every detector + display member reads the same few tables for the
    same names, so this view memoizes and batches — WITHOUT changing which rows any reader sees:

    - **memo** — each ``(table, security_id)`` as-of result is fetched ONCE for the PIT's lifetime
      (identity reads — ``security_name`` / ``security_cik`` / ``_benchmark_id`` — likewise). The memo key
      is ONLY the table + the scope id: ``asof`` / ``known_at`` / ``tenant_id`` are fixed at construction,
      so a memo can never cross a time or tenant boundary, and a per-CALL value (a caller's
      ``lookback_days``) is NEVER part of a key. Every accessor hands back a FRESH list (the row dicts
      are shared; readers are audited never to mutate a row).
    - **prefetch** — with a ``basket`` (the thesis's resolved security ids), the FIRST read of a
      security-scoped table for a basket member loads that table for the WHOLE basket in one
      ``as_of_many`` query and fills the memo (an EMPTY list for a member with no rows, so it is never
      re-queried). An id outside the basket (the SPY/IWM benchmark) falls back to the per-security
      ``as_of``, memoized. ``basket=None`` is the plain per-security read, memoized.
    - **bounds / the memo rule** — ``bounds`` is the registry-DERIVED event-time floor per table
      (``signals/horizons.py``: ``max(declared reader horizons) + MARGIN_DAYS``, or ``None`` =
      unbounded). The memo holds ONLY that max-horizon window per ``(table, security_id)`` — the prefetch
      applies it as a ``valid_from >=`` floor and the per-security fallback trims to the SAME floor in
      Python — and every caller trims its OWN window from it (``window_prices`` for prices; the insider
      readers filter by date). A per-call bound is never a memo input: two readers of one table with
      different horizons share one memo entry and each sees at least its declared window. A reader
      whose horizon is not declared fails the registry test (``tests/signals/test_horizons.py``), never
      a truncated read. See ``docs/INVARIANTS.md`` §4.
    """

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        asof: date,
        known_at: datetime | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        basket: Iterable[UUID] | None = None,
        bounds: Mapping[str, int | None] | None = None,
    ) -> None:
        self.conn = conn
        self.asof = asof
        self.known_at = known_at or datetime.now(timezone.utc)
        self.tenant_id = tenant_id
        # the resolved basket (prefetch scope); empty == no basket == per-security reads, memoized
        self._basket: frozenset[UUID] = frozenset(basket or ())
        # registry-derived read bounds, DAYS before asof per table (None / absent = unbounded)
        self._bounds: dict[str, int | None] = dict(bounds or {})
        self._memo: dict[tuple[str, UUID], list[dict[str, Any]]] = {}
        self._thesis_memo: dict[tuple[str, UUID], list[dict[str, Any]]] = {}
        self._prefetched: set[str] = set()
        self._identity: dict[UUID, tuple[str | None, str | None]] = {}  # sid -> (name, cik)
        self._identity_prefetched = False
        self._bench_ids: dict[str, UUID | None] = {}

    # --- the memo + prefetch seam (every fact accessor below goes through here) ---------------------

    def _lower(self, table: str) -> date | None:
        """The event-time floor for ``table`` under this PIT's bounds, or None (unbounded)."""
        days = self._bounds.get(table)
        return None if days is None else self.asof - timedelta(days=days)

    def _rows(self, table: str, security_id: UUID) -> list[dict[str, Any]]:
        """The memoized as-of rows for ``(table, security_id)`` — prefetched for the basket on the first
        touch of the table, per-security otherwise; both paths hold exactly the bounded window."""
        key = (table, security_id)
        if key not in self._memo:
            if security_id in self._basket and table not in self._prefetched:
                batch = as_of_many(
                    self.conn,
                    table,
                    security_ids=self._basket,
                    asof=self.asof,
                    known_at=self.known_at,
                    tenant_id=self.tenant_id,
                    valid_from_lower=self._lower(table),
                )
                for sid, rows in batch.items():
                    self._memo[(table, sid)] = (
                        rows  # an empty list too: "nothing on file", memoized
                    )
                self._prefetched.add(table)
            if key not in self._memo:  # outside the basket (a benchmark), or no basket at all
                rows = as_of(
                    self.conn,
                    table,
                    security_id=security_id,
                    asof=self.asof,
                    known_at=self.known_at,
                    tenant_id=self.tenant_id,
                )
                lower = self._lower(table)
                if lower is not None:  # the memo rule: hold only the bounded window, on every path
                    rows = [r for r in rows if r["valid_from"] >= lower]
                self._memo[key] = rows
        return list(
            self._memo[key]
        )  # a fresh list per call; the memo's container is never handed out

    def _thesis_rows(self, table: str, thesis_id: UUID) -> list[dict[str, Any]]:
        key = (table, thesis_id)
        if key not in self._thesis_memo:
            self._thesis_memo[key] = as_of_thesis(
                self.conn,
                table,
                thesis_id=thesis_id,
                asof=self.asof,
                known_at=self.known_at,
                tenant_id=self.tenant_id,
            )
        return list(self._thesis_memo[key])

    def _identity_row(self, security_id: UUID) -> tuple[str | None, str | None]:
        """``(name, cik)`` from ``security_master`` — an IDENTITY read (never as-of), memoized; the basket's
        rows are loaded in ONE tenant-filtered query on the first touch, an id outside it per-id."""
        if security_id not in self._identity:
            if security_id in self._basket and not self._identity_prefetched:
                for sid in self._basket:
                    self._identity[sid] = (None, None)  # unknown -> None, exactly the per-id shape
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, name, cik FROM security_master "
                        "WHERE tenant_id = %s AND id = ANY(%s)",
                        (self.tenant_id, list(self._basket)),
                    )
                    for row in cur.fetchall():
                        self._identity[row["id"]] = (row["name"], row["cik"])
                self._identity_prefetched = True
            if security_id not in self._identity:
                with self.conn.cursor() as cur:
                    cur.execute(
                        "SELECT name, cik FROM security_master WHERE id = %s AND tenant_id = %s",
                        (security_id, self.tenant_id),
                    )
                    row = cur.fetchone()
                self._identity[security_id] = (row["name"], row["cik"]) if row else (None, None)
        return self._identity[security_id]

    # --- the accessors (the protocol surface — unchanged signatures) ---------------------------------

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._rows("fact_insider_txn", security_id)

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        return window_prices(self._rows("fact_price_eod", security_id), self.asof, lookback_days)

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
        ``security_name``. A benchmark is never a basket member, so its bars come through the
        per-security fallback (memoized, trimmed to the same bound)."""
        sid = self._benchmark_id(symbol)
        if sid is None:
            return []
        return window_prices(self._rows("fact_price_eod", sid), self.asof, lookback_days)

    def _benchmark_id(self, symbol: str) -> UUID | None:
        """Resolve a benchmark ticker (SPY/IWM) to its ``security_master`` id within this tenant — the
        ``instrument_kind = 'etf'`` filter matches the ``securities.benchmarks`` seed so a benchmark can
        never collide with an operating company that shares the ticker. Identity read, never as-of;
        memoized per symbol for the PIT's lifetime.
        """
        key = symbol.upper()
        if key not in self._bench_ids:
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM security_master "
                    "WHERE tenant_id = %s AND ticker = %s AND instrument_kind = 'etf' "
                    "ORDER BY recorded_at DESC, id DESC LIMIT 1",
                    (self.tenant_id, key),
                )
                row = cur.fetchone()
            self._bench_ids[key] = row["id"] if row else None
        return self._bench_ids[key]

    def dilution_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._rows("fact_dilution", security_id)

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._rows("fact_catalyst", security_id)

    def fundamentals_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """The as-of quarterly financial series (§2.2) — the revenue-acceleration detector's input.

        A bitemporal read like the others: each row is knowable only from its 10-Q/10-K `filed` date
        (``valid_from``), so a past-as-of read sees a period's revenue EXACTLY when it was filed, never at
        the period end (no-lookahead, #1). The as-of read dedups to the latest VERSION per (security,
        metric_key, period_end), so a restatement supersedes the original. Rows carry ``metric_key`` /
        ``period_end`` / ``value`` (a general shape so §2.4 adds EPS/margin/FCF as ROWS, not columns).
        """
        return self._rows("fact_fundamentals", security_id)

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
        return self._rows("fact_corporate_event", security_id)

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
        return self._rows("fact_activist_stake", security_id)

    def revenue_mix_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench purity basis — operator-ratified revenue-mix facts (10-K segments), as-of."""
        return self._rows("fact_revenue_mix", security_id)

    def shares_outstanding_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench market-cap basis — operator-ratified shares-outstanding facts (10-Q), as-of."""
        return self._rows("fact_shares_outstanding", security_id)

    def cash_burn_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        """Workbench runway basis — operator-ratified cash + quarterly-burn facts (10-Q), as-of."""
        return self._rows("fact_cash_burn", security_id)

    def fund_shares(self, security_id: UUID) -> list[dict[str, Any]]:
        """ETF sleeve shares-outstanding samples (ETF net flow) — the DISPLAY rail's flow basis.

        Deliberately NOT on the detectors' ``SignalPointInTimeData`` protocol below: the flow read is
        display context (``signals/display/etf_flow.py``), never a call input, and keeping the accessor
        off the protocol keeps a future detector from quietly growing a dependency on it (#4/#5 —
        promoting flow to a signal is a separate, operator-signed F4). Rows are the deduped latest
        version per (security, d), ascending by d."""
        return self._rows("fact_fund_shares", security_id)

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]:
        """Thesis-scoped (not co-located): the operator-ratified theme convictions for a thesis (M5b)."""
        return self._thesis_rows("fact_theme_conviction", thesis_id)

    def security_name(self, security_id: UUID) -> str | None:
        """The security's display name from ``security_master`` — an IDENTITY read, NOT a bitemporal fact.

        The master is identity (CIK ↔ ticker ↔ name), read directly (never as-of): the name is stable, so a
        direct read leaks no future EVENT — the no-lookahead boundary is about facts, not identity (migration
        0001: "nothing reads the master as-of"). The insider detector uses this to recognise a self-filing —
        the reporting owner IS the issuer — on rows ingested before ``rpt_owner_cik``/``issuer_cik`` were
        captured (those newer rows carry the CIKs and match canonically instead). ``None`` if unknown → the
        name screen simply keeps the row (recall-safe, #9)."""
        return self._identity_row(security_id)[0]

    def security_cik(self, security_id: UUID) -> str | None:
        """The security's SEC CIK from ``security_master`` — an IDENTITY read, NOT a bitemporal fact (the
        ``security_name`` precedent: the master is identity, read directly, never as-of — a CIK is stable,
        so a direct read leaks no future EVENT). The activist-stake detector uses this to recognise a
        MIS-ATTRIBUTED 13D — the filer IS the subject company (``filer_cik`` == the subject's CIK, a
        self-filed schedule the ingest fanned onto the wrong subject). ``None`` if unknown → the screen
        simply keeps the row (recall-safe, #9)."""
        return self._identity_row(security_id)[1]


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

    def security_cik(self, security_id: UUID) -> str | None: ...


DetectorFn = Callable[
    [SignalPointInTimeData, UUID, date, CallConfig],
    SignalEvent | None,
]
# A detector's READ-HORIZON declaration: ``CallConfig -> {fact table: max days before asof it reads, or
# None = unbounded}``, derived from the SAME dials the detector runs with (``signals/horizons.py``).
HorizonsFn = Callable[[CallConfig], Mapping[str, int | None]]


@dataclass(frozen=True, slots=True)
class Detector:
    """One registered per-security detector with the exact current pipeline contract.

    ``horizons`` is REQUIRED (no default): every table a detector reads must be declared with the
    furthest-back event date it can ever need (``None`` for an unbounded read). The point-in-time
    view's read bound for a table is DERIVED from these declarations (``max + MARGIN_DAYS``), so a
    detector with no declaration fails the registry test — never a silently truncated read."""

    name: str
    detect: DetectorFn
    horizons: HorizonsFn

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
