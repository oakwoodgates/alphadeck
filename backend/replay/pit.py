from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import duckdb

from db.bitemporal import _FACT_IDENTITY
from db.session import DEFAULT_TENANT_ID
from replay.export import FACT_TABLES
from signals.base import window_prices

# jsonb columns the export wrote as JSON strings — the accessor decodes them back to dicts so the
# detectors (e.g. dilution_clock -> ConvertTerms.model_validate) get the same shape live and in replay.
_JSON_COLS: dict[str, tuple[str, ...]] = {"fact_dilution": ("terms",)}


def connect_mirror(parquet_dir: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB over the Parquet mirror — one materialized table per fact table. The
    mirror is rebuildable + non-authoritative; this reads it for the fast as-of sweeps."""
    con = duckdb.connect()
    base = Path(parquet_dir)
    for table in FACT_TABLES:
        path = (base / f"{table}.parquet").as_posix().replace("'", "''")
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{path}')")
    return con


class ReplayPointInTimeData:
    """A DuckDB/Parquet-backed point-in-time view that **duck-types** ``signals.base.PointInTimeData`` —
    the same five accessors, same signatures, same ``list[dict]`` returns — so the unchanged detectors
    (and ``pipeline.core.assemble_from_pit``) consume it identically. Only the fact SOURCE differs.

    The as-of read mirrors ``db.bitemporal._as_of`` exactly: ``valid_from <= asof AND recorded_at <=
    known_at``, then the latest row per natural-key identity (``_FACT_IDENTITY``) by ``recorded_at DESC,
    id DESC`` (the same deterministic tiebreak). The cap is constructor-bound — every accessor query is
    upper-bounded by ``asof``/``known_at`` with no widening path (the lookahead boundary). A parity test
    asserts each accessor equals the live ``PointInTimeData`` accessor row-for-row.
    """

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        asof: date,
        known_at: datetime,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> None:
        self.con = con
        self.asof = asof
        self.known_at = known_at
        self.tenant_id = tenant_id

    def _as_of(self, table: str, scope_col: str, scope_id: UUID) -> list[dict[str, Any]]:
        if table not in _FACT_IDENTITY:
            raise ValueError(f"unknown fact table: {table!r}")
        ident = ", ".join(_FACT_IDENTITY[table])  # identity cols (trusted whitelist)
        query = (
            f"SELECT * FROM {table} "
            f"WHERE tenant_id = ? AND {scope_col} = ? "
            f"AND valid_from <= ? AND recorded_at <= ? "
            f"QUALIFY ROW_NUMBER() OVER "
            f"(PARTITION BY {ident} ORDER BY recorded_at DESC, id DESC) = 1"
        )
        res = self.con.execute(
            query, [str(self.tenant_id), str(scope_id), self.asof, self.known_at]
        )
        cols = [d[0] for d in res.description]
        rows = [dict(zip(cols, r)) for r in res.fetchall()]
        for jc in _JSON_COLS.get(table, ()):  # decode jsonb-as-string back to a dict
            for row in rows:
                if isinstance(row.get(jc), str):
                    row[jc] = json.loads(row[jc])
        return rows

    def insider_txns(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_insider_txn", "security_id", security_id)

    def price_history(
        self, security_id: UUID, lookback_days: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self._as_of("fact_price_eod", "security_id", security_id)
        return window_prices(rows, self.asof, lookback_days)  # the SAME sort/trim as the live PIT

    def dilution_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_dilution", "security_id", security_id)

    def catalyst_facts(self, security_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_catalyst", "security_id", security_id)

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict[str, Any]]:
        return self._as_of("fact_theme_conviction", "thesis_id", thesis_id)

    def security_name(self, security_id: UUID) -> str | None:
        """Satisfies the protocol; the replay mirror holds FACT tables only, not ``security_master``
        (identity). Returns ``None`` — the insider-detector's issuer-self screen falls back here to the
        CANONICAL CIK match (``rpt_owner_cik == issuer_cik``), which flows into the replay rows via the
        ``SELECT *`` insider-txn read, so a self-filing captured with CIKs is still excluded in replay.
        (A pre-capture row with no CIKs is not excluded in replay until the mirror is re-exported — a
        documented, rebuildable-mirror limitation, never a silent live-path drop.)"""
        return None
