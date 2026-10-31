from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import psycopg

from db.bitemporal import as_of, as_of_many, as_of_thesis
from db.clock import db_now
from db.public_clock import Clock
from db.session import DEFAULT_TENANT_ID
from domain.config import CallConfig
from domain.market_time import day_clock_public_by, view_day
from domain.signal import SignalEvent
from domain.splits import on_view_scale
from domain.trading_days import closure_name
from repositories import split_repo


def window_prices(
    rows: list[dict[str, Any]], asof: date, lookback_days: int | None
) -> list[dict[str, Any]]:
    """Sort EOD bars ascending by ``d`` and trim to the lookback window. Shared by the live PIT and the
    replay mirror so the exact price view the breakout detector sees can't drift between Postgres and
    DuckDB (the as-of READ differs by engine; this post-process is identical for both).

    **A bar dated a market holiday is not in the view (F5, 2026-10-31).** The vendor printed flat OTC carries
    on Labor Day 2026 (MEASURED on dev: 10 bars, open = high = low, close = the prior close) and the store
    kept them; the price leg no longer writes such a bar (``ingest.prices.settle.withhold_closed_days``), and
    the ones already stored are skipped HERE — the one post-process every point-in-time price read shares
    (``price_history`` and ``benchmark_prices`` live, ``ReplayPointInTimeData.price_history`` in the
    mirror) — so no detector, display signal or dashboard counts a holiday as a session. Nothing is deleted:
    the row stays in ``fact_price_eod``, only the view leaves it out. The read skips the NYSE's full-day
    closures (``domain.trading_days.closure_name``), not weekends: a weekend-dated bar was never the stored
    anomaly, and many synthetic test tapes are calendar-day series (the write guard refuses both).
    """
    rows = sorted((r for r in rows if closure_name(r["d"]) is None), key=lambda r: r["d"])
    if lookback_days is not None:
        cutoff = asof - timedelta(days=lookback_days)
        rows = [r for r in rows if r["d"] >= cutoff]
    return rows


class PointInTimeData:
    """The ONLY way a detector reads facts — a bitemporal as-of view fixed at (asof, known_at).

    A detector physically cannot see post-asof events or post-known_at knowledge, because every
    read goes through ``db.bitemporal.as_of`` / ``as_of_many``. ``known_at`` defaults to now (UTC) for
    live reads; the replay harness (M5) sets it to a simulated past transaction time.

    **The clock (operator decision 2026-10-21, ``docs/INVARIANTS.md`` §4).** ``clock="record"`` (the default)
    is the LIVE read: ``recorded_at <= known_at``. ``clock="public"`` is a HISTORY view — a past-asof
    recompute (serve, backfill): every fact read is judged on each version's PUBLIC instant
    (``db.public_clock`` — its filing / acceptance / settled session; a correction from its own publication)
    ``<= known_at``, so the view shows what was PUBLIC then, not what our database happened to hold. Fixed
    at construction like ``asof`` / ``known_at``, so a memo can never cross a clock.

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
        clock: Clock = "record",
    ) -> None:
        self.conn = conn
        self.asof = asof
        # THE TRANSACTION-AXIS BOUND, from the clock that STAMPS ``recorded_at`` (``db.clock.db_now``),
        # never the app host's. The gate below is ``recorded_at <= known_at`` against a database-stamped
        # column, so a host clock lagging the database made a just-ingested fact invisible to a live
        # call. Pinned ONCE here, so every accessor of this view answers against one bound.
        self.known_at = known_at or db_now(conn)
        self.tenant_id = tenant_id
        # which axis ``known_at`` bounds (the record clock live, the public clock for a history view)
        self.clock: Clock = clock
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
        # sid -> the split list this view reads from: recorded by known_at (record), or the list's current
        # reading with each split's PUBLIC instant as recorded_at (public — gated per read, S2)
        self._splits: dict[UUID, list[dict[str, Any]]] = {}
        # the VIEW's market day — the scale a history view's bars are put on (``domain.splits``, S2): the
        # night ``known_at`` bounds (``view_day``, F6), never the cap's own date (a Friday cap is Monday's)
        self._view_day = view_day(self.known_at)

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
                    clock=self.clock,
                )
                for sid, rows in batch.items():
                    self._memo[(table, sid)] = self._on_view_scale(
                        table, sid, rows  # an empty list too: "nothing on file", memoized
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
                    clock=self.clock,
                )
                lower = self._lower(table)
                if lower is not None:  # the memo rule: hold only the bounded window, on every path
                    rows = [r for r in rows if r["valid_from"] >= lower]
                self._memo[key] = self._on_view_scale(table, security_id, rows)
        return list(
            self._memo[key]
        )  # a fresh list per call; the memo's container is never handed out

    def _on_view_scale(
        self, table: str, security_id: UUID, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """S2 — a HISTORY view's price bars on the VIEW's scale (``domain.splits.on_view_scale``, the SAME pure
        function the replay mirror runs): the splits in force by the view's day applied, none after it. The
        live read (the record clock) is returned as stored."""
        if table != "fact_price_eod" or self.clock != "public" or not rows:
            return rows
        return on_view_scale(rows, self._split_rows(security_id), through=self._view_day)

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
                clock=self.clock,
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

        **First knowability (D3a).** Each row also carries ``knowable_from`` + ``knowable_accession``
        (``db.bitemporal.first_knowable_versions``): the date — and the filing — that FIRST made the row's
        current value knowable, walked back through the value-identical versions visible at this view's
        ``(asof, known_at)``. A later 10-Q re-reporting the period as its prior-year comparative is a new
        version with a later ``valid_from``, but not new knowledge; detectors date, cite and age a fact by
        ``knowable_from``, never by the picked version's ``valid_from``.
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

    def filing_index(self, security_id: UUID) -> list[dict[str, Any]]:
        """The company's EDGAR filing index (migration 0070) — every form its submissions index lists, one row
        per accession (``form`` / ``accession`` / ``filed`` / ``primary_doc`` / ``accepted`` / ``source_ref``),
        as-of. The research dashboards' Filing Stream reads it (``dashboards/filings.py``).

        Deliberately NOT on the detectors' ``SignalPointInTimeData`` protocol below (the ``fund_shares``
        precedent): it is DISPLAY data, never a call input, and keeping the accessor off the protocol keeps a
        detector from quietly growing a dependency on it (#3/#4 — a trigger traces to a computation over the
        facts a detector declares). It is also kept out of the replay mirror
        (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES``). Both clocks come from ``_rows``: on the record clock (the
        live read) a row counts once ``valid_from <= asof AND recorded_at <= known_at``; on the public clock (a
        history view) from its ``filed`` date (``db.public_clock``)."""
        return self._rows("fact_filing_index", security_id)

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]:
        """Thesis-scoped (not co-located): the operator-ratified theme convictions for a thesis (M5b)."""
        return self._thesis_rows("fact_theme_conviction", thesis_id)

    def term_mentions(self, thesis_id: UUID) -> list[dict[str, Any]]:
        """Thesis-scoped (not co-located): the card's monthly EDGAR full-text counts per SIGNAL term (migration
        0071, ``pipeline.term_mentions``) — one row per (term, month), the newest version (``documents``, the
        four form groups, ``filers`` / ``filers_sampled`` / ``filer_ciks`` / ``new_filers``, ``source_ref``),
        as-of. The Narrative Adoption Curve dashboard reads it.

        A DISPLAY accessor (the ``fund_shares`` / ``theme_conviction_facts`` precedents), deliberately NOT on
        the detectors' ``SignalPointInTimeData`` protocol below: it is never a call input, and keeping it off
        the protocol keeps a detector from quietly growing a dependency on it (#3/#4). It is also kept out of
        the replay mirror (``db.bitemporal.DISPLAY_ONLY_FACT_TABLES``). Both clocks come from ``_thesis_rows``:
        on the record clock (the live read) a row counts once ``valid_from <= asof AND recorded_at <=
        known_at`` — ``valid_from`` is the month's LAST day, so at a past as-of the curve ends at the last
        month that had closed by then; on the public clock (a history view) from its ``valid_from``
        (``db.public_clock``)."""
        return self._thesis_rows("fact_term_mentions", thesis_id)

    def _split_rows(self, security_id: UUID) -> list[dict[str, Any]]:
        """The split list this view reads from (memoized; the basket's lists load in ONE query on the first
        touch): on the record clock the newest version of each logical split recorded by ``known_at``; on the
        public clock the list's current reading, each row's ``recorded_at`` = the split's PUBLIC instant
        (``split_repo.current_rows``)."""
        if security_id not in self._splits:
            ids = list(self._basket) if security_id in self._basket else [security_id]
            if self.clock == "public":
                self._splits.update(
                    split_repo.current_rows(self.conn, tenant_id=self.tenant_id, security_ids=ids)
                )
            else:
                self._splits.update(
                    split_repo.as_of_rows(
                        self.conn,
                        tenant_id=self.tenant_id,
                        security_ids=ids,
                        known_at=self.known_at,
                    )
                )
        return self._splits.get(security_id, [])

    def split_events(self, security_id: UUID) -> list[dict[str, Any]]:
        """The SPLIT LIST for this security as this view knows it (split plan S2): the newest version of each
        logical split — every status; a reader applies ``domain.splits`` (``applicable`` / ``scale_after``) to
        decide which are in force by the view's day.

        Why a reader needs it: a Form 4 states the price AS TRADED, the bars a view reads are on the view's
        scale (the stored tape is on the list's scale, ``ingest.prices.split_normalize``; a history view puts
        it on its own day's, ``domain.splits.on_view_scale``). Comparing the two needs the factor between
        them. No lookahead (#1): on the LIVE read (the record clock) a split recorded after ``known_at`` is
        invisible, exactly like a fact; on a HISTORY view (the public clock) a split counts from its OWN
        publication — its SEC filing, else its first session (``db.public_clock``), never the day we added
        the row — and the view reads the split's newest version (today's reading)."""
        rows = self._split_rows(security_id)
        if self.clock == "public":
            # a split is DATE-clocked: it counts from its own day's night, never the night before (its instant
            # is that day's 06:00; the day gate also holds for a row stamped at 00:00 before that change)
            rows = [r for r in rows if day_clock_public_by(r["recorded_at"], self.known_at)]
        return list(rows)

    def security_name(self, security_id: UUID) -> str | None:
        """The security's display name from ``security_master`` — an IDENTITY read, NOT a bitemporal fact.

        The master is identity (CIK ↔ ticker ↔ name), read directly (never as-of): the name is stable, so a
        direct read leaks no future EVENT — the no-lookahead boundary is about facts, not identity (migration
        0001: "nothing reads the master as-of"). The insider detector uses this to recognize a self-filing —
        the reporting owner IS the issuer — on rows ingested before ``rpt_owner_cik``/``issuer_cik`` were
        captured (those newer rows carry the CIKs and match canonically instead). ``None`` if unknown → the
        name screen simply keeps the row (recall-safe, #9)."""
        return self._identity_row(security_id)[0]

    def security_cik(self, security_id: UUID) -> str | None:
        """The security's SEC CIK from ``security_master`` — an IDENTITY read, NOT a bitemporal fact (the
        ``security_name`` precedent: the master is identity, read directly, never as-of — a CIK is stable,
        so a direct read leaks no future EVENT). The activist-stake detector uses this to recognize a
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

    def split_events(self, security_id: UUID) -> list[dict[str, Any]]: ...

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
    # The Form 4 transaction codes this detector reads from ``fact_insider_txn`` (``P`` for insider_conviction,
    # ``S`` for insider_sell) — REQUIRED of any detector that declares ``fact_insider_txn`` in its horizons.
    # The union over the registry is the R8-A amendment classifier's signal code set
    # (``signals.horizons.insider_signal_codes``): a future detector reading another code widens it by declaring
    # it here, and ``tests/signals/test_insider_signal_codes.py`` fails a detector that reads an undeclared one.
    txn_codes: frozenset[str] = frozenset()
    # The ROLE READ (the 13D holder screen, 2026-10-28): a detector that reads ``fact_insider_txn`` only for WHO a
    # Form 4 names — ``rpt_owner_cik`` + ``insider_role``, on rows of ANY code — and never for a code's trade
    # declares ``reads_insider_roles=True`` instead of codes (``activist_stake``: is the 13D's filer an officer
    # or director?). It adds NO code to the amendment classifier's signal set — a 4/A that changes no signal-code
    # row still resolves itself, and the read keeps showing the role the original filing stated.
    # ``tests/signals/test_insider_signal_codes.py`` probes that a role reader is code-blind: given the same
    # rows in each SEC code, its output never depends on the code.
    reads_insider_roles: bool = False
    # S5 — the SPLIT-NIGHT HOLD (``signals/split_hold.py``): a detector that fires on the PRICE PATH (a breakout,
    # a breakdown, an SMA cross) declares its name on the card here ("Breakout"); on a held company it then
    # reads only the bars before an unconfirmed split-shaped step. ``None`` = not a price-path trigger (an
    # insider detector reads the day's low only to screen a Form 4 price — it fires on the filing).
    # ``tests/signals/test_split_hold.py`` fails a detector that reads the price path without declaring one.
    price_trigger: str | None = None

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
