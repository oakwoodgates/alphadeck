from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql

from db.bitemporal import _FACT_IDENTITY
from db.session import DEFAULT_TENANT_ID

# The bitemporal fact tables, taken from the as-of read's own whitelist (single source of truth — the
# mirror can never drift from the set of tables the live reader knows).
FACT_TABLES: tuple[str, ...] = tuple(_FACT_IDENTITY)

MANIFEST_NAME = "manifest.json"

# Postgres type OID -> Arrow type, so the Parquet schema is EXPLICIT (correct even for an empty table — a
# tenant missing one fact kind still gets a date-typed `valid_from`, so the as-of date filter binds). numeric
# -> float64 (we float() it; every detector floats numerics anyway, so this is lossless for the call); uuid /
# jsonb / text -> string; date -> date32; timestamptz -> tz-aware timestamp. Unknown OIDs fall back to string.
_OID_ARROW: dict[int, pa.DataType] = {
    16: pa.bool_(),  # bool
    21: pa.int64(),  # int2
    23: pa.int64(),  # int4
    20: pa.int64(),  # int8
    25: pa.string(),  # text
    1043: pa.string(),  # varchar
    1082: pa.date32(),  # date  (valid_from / valid_to / d / horizon_end)
    1114: pa.timestamp("us"),  # timestamp (no tz)
    1184: pa.timestamp("us", tz="UTC"),  # timestamptz (recorded_at)
    700: pa.float64(),  # float4
    701: pa.float64(),  # float8
    1700: pa.float64(),  # numeric (close/usd/shares/... -> float)
    2950: pa.string(),  # uuid
    114: pa.string(),  # json
    3802: pa.string(),  # jsonb (terms -> JSON string; the accessor json.loads it back)
}


def _coerce(v: Any) -> Any:
    """Coerce a Postgres value to the Parquet schema's Python type. Lossless for what the call cares about:
    numerics float (every detector ``float(...)``s them), uuids stringify, jsonb (dict/list — fact_dilution
    .terms) JSON-dumps (the accessor ``json.loads`` it back so ``ConvertTerms.model_validate`` still gets a
    dict). dates / datetimes / strings / ints / None pass through."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, default=str)
    return v


def _read_table(
    conn: psycopg.Connection, table: str, tenant_id: UUID
) -> tuple[pa.Schema, list[dict]]:
    """Read ALL columns + ALL rows of one fact table for the tenant, plus an explicit Arrow schema derived
    from the Postgres column types (table name from the trusted ``_FACT_IDENTITY`` whitelist)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {} WHERE tenant_id = %(tenant_id)s").format(
                sql.Identifier(table)
            ),
            {"tenant_id": tenant_id},
        )
        rows = cur.fetchall()
        schema = pa.schema(
            [(d.name, _OID_ARROW.get(d.type_code, pa.string())) for d in cur.description]
        )
    return schema, rows


def export_snapshot(
    conn: psycopg.Connection,
    out_dir: str | Path,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Export the bitemporal fact tables (ALL columns, ALL rows for the tenant) from Postgres (the SoR) to
    one Parquet file per table — the rebuildable, NON-authoritative analytical mirror DuckDB sweeps.

    One-shot truncate-and-rewrite. The PIN (``known_at`` ceiling) is **not** applied here: it is a READ-time
    filter in ``ReplayPointInTimeData``, so the mirror faithfully reproduces the SoR's ``as_of`` for *any*
    ``known_at`` (the parity + transaction-time no-lookahead tests require this). Every column is exported so
    no detector-read field can be silently dropped (the parity gate then catches any divergence). Returns a
    manifest (per-table row count + max ``recorded_at``) — derived from the data, so it carries no clock.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"tenant_id": str(tenant_id), "tables": {}}
    for table in FACT_TABLES:
        schema, rows = _read_table(conn, table, tenant_id)
        coerced = [{k: _coerce(v) for k, v in row.items()} for row in rows]
        pq.write_table(pa.Table.from_pylist(coerced, schema=schema), out / f"{table}.parquet")
        max_rec = max((r["recorded_at"] for r in rows), default=None)
        manifest["tables"][table] = {
            "rows": len(rows),
            "max_recorded_at": max_rec.isoformat() if max_rec is not None else None,
        }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest
