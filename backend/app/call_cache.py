"""Generation-gated in-process LRU cache for ``read_call`` and ``read_display_signals``.

The Board and Cockpit fetch ``GET /theses/{id}/call?asof=`` and ``GET /theses/{id}/display-signals?asof=``
repeatedly across page navigations, tab switches, and as-of slider scrubs. Both routes are strictly
read-only deterministic projections of the thesis spine and bitemporal fact tables at ``(asof, view)``.

To keep ``app/routers/theses.py`` decoupled from ``dashboards/`` (``tests/dashboards/test_isolation.py``'s
Leaf Rule forbids importing ``dashboards.*`` outside ``dashboards/`` and ``app/routers/dashboards/``),
this module owns a small bounded LRU cache dedicated to the thesis call and display-signal endpoints.
Each lookup validates a single fast database generation token covering the thesis's basket and thesis-scoped
tables, so any fact ingest, split, price repair, insider hold/amendment decision, operator decision,
or roster snapshot invalidates stale entries immediately.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from threading import Lock
from typing import Any, TypeVar
from uuid import UUID

import psycopg
from pydantic import BaseModel

from domain.scopes import DATA_TENANT_ID
from domain.thesis import Thesis

MAX_CALL_CACHE_ENTRIES = 256

T = TypeVar("T", bound=BaseModel)

_TOKEN_SQL = """
WITH scope_sids AS (
    SELECT unnest(%(sids)s::uuid[]) AS sid
    UNION
    SELECT id AS sid FROM security_master
    WHERE tenant_id = %(data_tenant)s AND ticker IN ('SPY', 'IWM')
)
SELECT
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_price_eod
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS price_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_insider_txn
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS insider_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_dilution
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS dilution_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_catalyst
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS catalyst_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_corporate_event
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS corp_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_activist_stake
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS activist_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_fundamentals
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS fund_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_fund_shares
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS fund_shares_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_filing_index
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS filing_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_theme_conviction
     WHERE thesis_id = %(thesis_id)s) AS theme_conv_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_term_mentions
     WHERE thesis_id = %(thesis_id)s) AS term_mentions_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_thesis_catalyst
     WHERE thesis_id = %(thesis_id)s) AS thesis_cat_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM operator_decision
     WHERE thesis_id = %(thesis_id)s) AS decision_tok,
    (SELECT count(*)::text || ':' || coalesce(max(taken_at)::text, '')
     FROM basket_snapshot
     WHERE thesis_id = %(thesis_id)s) AS snapshot_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM security_split
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS split_tok,
    (SELECT count(*)::text || ':' || coalesce(max(seq)::text, '')
     FROM data_repair) AS repair_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fundamentals_set_aside) AS set_aside_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS amend_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment_link
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS amend_link_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment_decision
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS amend_dec_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_txn_hold
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS hold_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_txn_hold_decision
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS hold_dec_tok,
    (SELECT count(*)::text
     FROM security_cik_change
     WHERE tenant_id = %(data_tenant)s AND security_id IN (SELECT sid FROM scope_sids)) AS cik_change_tok,
    (SELECT count(*)::text || ':' || coalesce(max(seq)::text, '') || ':' || coalesce(sum(seq)::text, '')
     FROM security_change
     WHERE security_id IN (SELECT sid FROM scope_sids)) AS change_tok,
    (SELECT coalesce(
        string_agg(
            id::text || ':' || coalesce(ticker, '') || ':' || coalesce(cik, '') || ':' ||
            coalesce(business_type, '') || ':' || coalesce(sector, ''),
            '|' ORDER BY id
        ),
        ''
     )
     FROM security_master
     WHERE tenant_id = %(data_tenant)s AND id IN (SELECT sid FROM scope_sids)) AS master_tok
"""


def _db_token(conn: psycopg.Connection, thesis: Thesis) -> tuple[Any, ...] | None:
    """Return a hashable generation tuple for ``thesis``'s call/display dependencies, or ``None`` if
    ``conn`` is a test double or cannot execute the token probe (fail-open: skip caching)."""
    if not isinstance(conn, psycopg.Connection):
        return None
    sids: list[UUID] = [m.security_id for m in thesis.basket if m.security_id is not None]
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                _TOKEN_SQL,
                {
                    "sids": sids,
                    "data_tenant": DATA_TENANT_ID,
                    "thesis_id": thesis.id,
                },
            )
            row = cur.fetchone()
    except Exception:
        return None
    if row is None:
        return None
    if isinstance(row, dict):
        return tuple(row.values())
    return tuple(row)


class CallCache:
    """Thread-safe bounded LRU cache for ``CallCardResponse`` and ``DisplaySignalsResponse``."""

    def __init__(self, max_entries: int = MAX_CALL_CACHE_ENTRIES) -> None:
        self._max_entries = max_entries
        self._lock = Lock()
        self._entries: OrderedDict[tuple[Any, ...], Any] = OrderedDict()

    def clear(self) -> None:
        """Drop all cached call and display-signal responses."""
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    @staticmethod
    def _key(
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None,
        fn_token: int,
        token: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return (
            kind,
            thesis.id,
            thesis.tenant_id,
            thesis.model_dump_json(),
            asof,
            known_at,
            clock,
            decisions_asof,
            fn_token,
            token,
        )

    def is_warm_call(
        self,
        conn: psycopg.Connection,
        *,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None = None,
        fn_token: int = 0,
    ) -> bool:
        """Return whether the cached ``call`` entry for ``thesis`` is present for the current DB token."""
        token = _db_token(conn, thesis)
        if token is None:
            return False
        key = self._key(
            kind="call",
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
            token=token,
        )
        with self._lock:
            return key in self._entries

    def get_or_compute(
        self,
        conn: psycopg.Connection,
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None = None,
        fn_token: int = 0,
        compute: Callable[[], T],
    ) -> T:
        token = _db_token(conn, thesis)
        if token is None:
            return compute()
        key = self._key(
            kind=kind,
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
            token=token,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached.model_copy(deep=True)
        result = compute()
        with self._lock:
            self._entries[key] = result.model_copy(deep=True)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return result


CACHE = CallCache()
